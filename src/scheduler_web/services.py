"""File-backed, single-worker local runs with bounded full-population retention."""
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
from threading import Event, RLock, Thread
from uuid import uuid4
import zipfile

from scheduler import load_dataset
from scheduler.dataset import DatasetError
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionFailure, EvolutionSettings, run_evolution
from scheduler.evolution_reporting import json_value, result_payload, solution_payload
from scheduler.initialization import chromosome_fingerprint
from scheduler.repair import RepairSettings

ROOT = Path(__file__).resolve().parents[2]
MAX_UPLOAD = 10*1024*1024
ID = re.compile(r'^[0-9a-f]{32}$')
AUTO_SEED_UPPER_BOUND = 2**32  # Exclusive; exactly representable in browser JSON.


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def configuration(values):
    values = dict(values)
    budget = values.pop('repair_candidate_budget', 50)
    retention = values.pop('previous_populations', 10)
    if type(retention) is not int or not 0 <= retention <= 20:
        raise ValueError('previous_populations must be in 0..20')
    allowed = {'population_size','max_generations','tournament_size','elite_count','mutation_probability',
               'crossover_probability','random_seed','repair_enabled','stop_on_first_feasible',
               'stagnation_generations','max_fitness_evaluations'}
    if values.keys()-allowed:
        raise ValueError('unknown run settings')
    return EvolutionSettings(**values,repair_settings=RepairSettings(max_candidate_evaluations=budget)), retention


class RunManager:
    def __init__(self, storage, repository=ROOT):
        self.storage = Path(storage); self.repository = Path(repository)
        self.storage.mkdir(parents=True,exist_ok=True)
        self.lock = RLock(); self.cancel = Event(); self.worker = None
        self.active_id = None; self.contexts = {}; self.datasets = {}; self.runs = {}
        for p in sorted((self.storage/'datasets').glob('*/metadata.json')):
            item = read_json(p); self.datasets[item['id']] = item
        for p in sorted((self.storage/'runs').glob('*/metadata.json')):
            item = read_json(p)
            if item['status'] in ('Running','Stopping'):
                item.update(status='Failed',error='اجرای قبلی با بسته‌شدن برنامه قطع شده است؛ آخرین نسل کامل محفوظ است.')
                atomic_json(p,item)
            self.runs[item['id']] = item

    def path(self, kind, identifier):
        if not ID.fullmatch(identifier):
            raise KeyError('invalid internal identifier')
        return self.storage/kind/identifier

    def dataset_context(self, identifier):
        with self.lock:
            if identifier not in self.datasets or not self.datasets[identifier]['valid']:
                raise ValueError('dataset is not structurally valid')
            if identifier not in self.contexts:
                path = self.path('datasets',identifier)/'input.xlsx'
                if hashlib.sha256(path.read_bytes()).hexdigest() != self.datasets[identifier]['sha256']:
                    raise ValueError('stored input hash changed')
                self.contexts[identifier] = EvaluationContext.from_dataset(load_dataset(path))
            return self.contexts[identifier]

    def upload(self, name, content):
        if not name.lower().endswith('.xlsx') or len(content)>MAX_UPLOAD:
            raise ValueError('فقط فایل xlsx با حجم حداکثر ۱۰ مگابایت مجاز است.')
        identifier = uuid4().hex; directory = self.path('datasets',identifier)
        directory.mkdir(parents=True)
        path = directory/'input.xlsx'; path.write_bytes(content)
        safe_name = re.sub(r'[^\w.\- ()]', '_', name.replace('\\','/').split('/')[-1])[:120]
        metadata = {'id':identifier,'name':safe_name,'sha256':hashlib.sha256(content).hexdigest(),
                    'valid':False,'errors':[],'warnings':[],'counts':{}}
        try:
            with zipfile.ZipFile(path) as archive:
                if len(archive.infolist())>10000 or sum(i.file_size for i in archive.infolist())>100*1024*1024:
                    raise ValueError('Workbook expands beyond the safe local upload limit')
            dataset = load_dataset(path)
            context = EvaluationContext.from_dataset(dataset)
            metadata.update(valid=True,counts={'students':len(context.students),'professors':len(context.professors),
                'courses':len(context.courses),'rooms':len(context.rooms),'requests':len(context.requests)},
                warnings=[json_value(w) for w in dataset.warnings],diagnostics=json_value(context.diagnostics))
            self.contexts[identifier] = context
        except DatasetError as exc:
            metadata['errors'] = [json_value(i) for i in exc.issues]
        except (ValueError,zipfile.BadZipFile,KeyError) as exc:
            metadata['errors'] = [{'message':str(exc)}]
        with self.lock:
            self.datasets[identifier] = metadata
            atomic_json(directory/'metadata.json',metadata)
        return metadata

    def example(self, label):
        names = {'A':'UniversityDataset_Corrected.xlsx','B':'Dataset_B_Constructive_seed20261103.xlsx'}
        if label not in names:
            raise KeyError('unknown example')
        path = self.repository/'data'/names[label]
        return self.upload(path.name,path.read_bytes())

    def _persist(self, run):
        atomic_json(self.path('runs',run['id'])/'metadata.json',run)

    def start(self, dataset_id, values, *, seed_mode=None):
        values = dict(values)
        # Legacy explicit integer seeds retain their exact meaning.
        mode = seed_mode if seed_mode is not None else ('manual' if 'random_seed' in values else 'auto')
        if mode not in ('auto','manual'):
            raise ValueError('seed_mode must be auto or manual')
        if mode == 'manual' and 'random_seed' not in values:
            raise ValueError('manual seed mode requires an integer random_seed')
        if mode == 'auto' and 'random_seed' in values:
            raise ValueError('auto seed mode must omit random_seed')
        settings, retention = configuration({**values, **({'random_seed':0} if mode == 'auto' else {})})
        context = self.dataset_context(dataset_id)
        with self.lock:
            if self.active_id is not None:
                raise RuntimeError('یک اجرای فعال وجود دارد؛ ابتدا آن را متوقف کنید.')
            if mode == 'auto':
                settings = replace(settings,random_seed=secrets.randbelow(AUTO_SEED_UPPER_BOUND))
            identifier = uuid4().hex
            run = {'id':identifier,'dataset_id':dataset_id,'dataset_name':self.datasets[dataset_id]['name'],
                'dataset_sha256':context.dataset.sha256,'context_id':list(context.context_id),
                'created':datetime.now(timezone.utc).isoformat(),'settings':json_value(settings),'seed_mode':mode,
                'previous_populations':retention,'status':'Running','events':[], 'completed_generations':None,
                'stagnant_generations':0,'best_overall':None,'best_feasible':None,'current_best':None,
                'error':None,'stop_reasons':[],'persisted_generations':[]}
            self.runs[identifier] = run; self.active_id = identifier; self.cancel = Event()
            self._persist(run)
            self.worker = Thread(target=self._execute,args=(identifier,context,settings),daemon=True,name='local-ga-worker')
            self.worker.start()
            return self.summary(run)

    @staticmethod
    def summary(run):
        return {k:v for k,v in run.items() if k != 'events'}

    def _execute(self, identifier, context, settings):
        run = self.runs[identifier]; directory = self.path('runs',identifier)
        retained = OrderedDict()
        def publish(progress):
            stats = progress.statistics; generation = stats.generation_index
            snapshots = []
            elite_count = settings.elite_count if generation else 0
            for index, member in enumerate(progress.population):
                if chromosome_fingerprint(member.chromosome) != member.fingerprint or member.evaluation.hard.context_id != context.context_id:
                    raise ValueError('snapshot genotype/evaluation/context mismatch')
                payload = solution_payload(member,context.context_id)
                payload.update(index=index,elite=index<elite_count,observed_generation=generation)
                snapshots.append(payload)
            current = min(snapshots,key=lambda x:(x['hard_penalty'],-x['soft_score']))
            row = json_value(stats); row['self_mating_rate'] = stats.operators.self_mating_rate
            row['stagnant_generations'] = progress.stagnant_generations
            row['elites'] = [self.snapshot_summary(s) for s in snapshots[:elite_count]]
            row['best_preserved'] = generation == 0 or run['events'][-1]['best_fingerprint'] in stats.elite_fingerprints
            overall = solution_payload(progress.best_overall,context.context_id)
            feasible = solution_payload(progress.best_feasible,context.context_id) if progress.best_feasible else None
            for name, payload in [('best_overall',overall),('best_feasible',feasible)]:
                if payload is not None:
                    prior = directory/(name+'.json')
                    previous = read_json(prior) if prior.exists() else None
                    payload['observed_generation'] = previous['observed_generation'] if previous and previous['fingerprint']==payload['fingerprint'] else generation
            with self.lock:
                atomic_json(directory/f'population_{generation}.json',snapshots)
                atomic_json(directory/f'best_{generation}.json',current)
                atomic_json(directory/'best_overall.json',overall)
                if feasible is not None:
                    atomic_json(directory/'best_feasible.json',feasible)
                retained[generation] = True
                while len(retained)>run['previous_populations']+1:
                    old,_ = retained.popitem(last=False)
                    if old not in run['persisted_generations']:
                        (directory/f'population_{old}.json').unlink(missing_ok=True)
                run['events'].append(row)
                run.update(completed_generations=generation,stagnant_generations=progress.stagnant_generations,
                    best_overall=self.snapshot_summary(overall),best_feasible=self.snapshot_summary(feasible) if feasible else None,
                    current_best=self.snapshot_summary(current))
                self._persist(run)
        try:
            result = run_evolution(context,settings,on_generation=publish,stop_requested=self.cancel.is_set)
            with self.lock:
                atomic_json(directory/'report.json',{**result_payload(result),'seed_mode':run['seed_mode']})
                for name, member in [('best_overall',result.best_overall),('best_feasible',result.best_feasible),('final_population_best',result.final_population_best)]:
                    if member:
                        path = directory/(name+'.json')
                        payload = solution_payload(member,context.context_id)
                        previous = read_json(path) if path.exists() else None
                        payload['observed_generation'] = previous['observed_generation'] if previous and previous['fingerprint']==payload['fingerprint'] else (
                            result.partial_generation.generation_index if result.partial_generation else len(result.generations)-1)
                        atomic_json(path,payload)
                run.update(status='Stopped' if 'manual_stop' in result.stop_reasons else 'Completed',
                    stop_reasons=list(result.stop_reasons),fitness_calls=result.evaluations.total,
                    elapsed_seconds=result.initialization_elapsed_seconds+result.evolution_elapsed_seconds,
                    partial_generation=json_value(result.partial_generation))
        except Exception as exc:
            with self.lock:
                if isinstance(exc,EvolutionFailure) and exc.result is not None:
                    atomic_json(directory/'report.json',{**result_payload(exc.result),'seed_mode':run['seed_mode']})
                    for name, member in [('best_overall',exc.result.best_overall),('best_feasible',exc.result.best_feasible),('final_population_best',exc.result.final_population_best)]:
                        if member:
                            payload = solution_payload(member,context.context_id)
                            payload['observed_generation'] = exc.result.partial_generation.generation_index if exc.result.partial_generation else len(exc.result.generations)-1
                            atomic_json(directory/(name+'.json'),payload)
                    run.update(best_overall=self.snapshot_summary(solution_payload(exc.result.best_overall,context.context_id)),
                        best_feasible=self.snapshot_summary(solution_payload(exc.result.best_feasible,context.context_id)) if exc.result.best_feasible else None)
                run.update(status='Failed',error=f'{type(exc).__name__}: {exc}',stop_reasons=['failure'])
        finally:
            with self.lock:
                self._persist(run); self.active_id = None

    @staticmethod
    def snapshot_summary(snapshot):
        assigned = sum(r['section_id'] is not None for r in snapshot['enrollments'])
        return {**{k:snapshot.get(k) for k in ('index','elite','fingerprint','hard_penalty','soft_score','feasible')},
            'request_coverage':snapshot['soft_evaluation']['request_coverage'], 'assigned':assigned,
            'unassigned':len(snapshot['enrollments'])-assigned,
            'active_sections':sum(s['active'] for s in snapshot['sections']),
            'inactive_sections':sum(not s['active'] for s in snapshot['sections'])}

    def stop(self, identifier):
        with self.lock:
            run = self.get_run(identifier)
            if self.active_id == identifier:
                self.cancel.set(); run['status'] = 'Stopping'; self._persist(run)
            return self.summary(run)

    def get_run(self, identifier):
        self.path('runs',identifier)
        if identifier not in self.runs:
            raise KeyError('run not found')
        return self.runs[identifier]

    def events(self, identifier, after=-1, limit=100):
        with self.lock:
            run = self.get_run(identifier)
            rows = run['events'][max(0,after+1):max(0,after+1)+limit]
            return {'run':self.summary(run),'events':list(rows),'next':rows[-1]['generation_index'] if rows else after}

    def population(self, identifier, generation):
        with self.lock:
            run = self.get_run(identifier); directory = self.path('runs',identifier)
            if generation<0 or generation>=len(run['events']):
                raise KeyError('generation has not completed')
            path = directory/f'population_{generation}.json'
            full = path.exists()
            snapshots = read_json(path) if full else [read_json(directory/f'best_{generation}.json')]
            return {'generation':generation,'full_population_available':full,
                    'chromosomes':[self.snapshot_summary(s) for s in snapshots]}

    def snapshot(self, identifier, generation, selection):
        with self.lock:
            run = self.get_run(identifier); directory = self.path('runs',identifier)
            if selection in ('best_overall','best_feasible','final_population_best'):
                path = directory/(selection+'.json')
            else:
                if generation<0 or generation>=len(run['events']):
                    raise KeyError('generation unavailable')
                if selection == 'best':
                    path = directory/f'best_{generation}.json'
                else:
                    index = int(selection)
                    rows = read_json(directory/f'population_{generation}.json')
                    if index<0 or index>=len(rows):
                        raise KeyError('chromosome unavailable')
                    return rows[index]
            if not path.exists():
                raise KeyError('snapshot unavailable')
            return read_json(path)

    def persist_generation(self, identifier, generation):
        with self.lock:
            run = self.get_run(identifier)
            if not (self.path('runs',identifier)/f'population_{generation}.json').exists():
                raise KeyError('full population was evicted')
            if generation not in run['persisted_generations']:
                run['persisted_generations'].append(generation); self._persist(run)

    def shutdown(self):
        self.cancel.set()
        if self.worker:
            self.worker.join()  # complete current generation and persist; never terminate abruptly
