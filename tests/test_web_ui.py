"""Real engine monitoring, bounded snapshots, projections, export and HTTP contracts."""
from dataclasses import replace
from io import BytesIO
import hashlib
import json
from pathlib import Path
import random
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from scheduler.chromosome import validate_structure
from scheduler.domain import Request
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionFailure, EvolutionSettings, run_evolution
from scheduler.evolution_reporting import solution_payload
from scheduler.fitness import evaluate_fitness
from scheduler.initialization import InitializedIndividual, chromosome_fingerprint
from scheduler_web.app import create_app
from scheduler_web.projection import WARNING, export_excel, project
from scheduler_web.services import RunManager, configuration, atomic_json
from test_constraints import sample, scheduled, with_rows, overlap_second_slot

ROOT = Path(__file__).resolve().parents[1]


def settings(**kw):
    return EvolutionSettings(**{'population_size':6,'elite_count':1,'max_generations':3,'random_seed':11,**kw})


def snapshot(context, chromosome=None):
    chromosome = chromosome or scheduled(context.dataset)
    fitness = evaluate_fitness(chromosome,context)
    member = InitializedIndividual(chromosome,fitness,chromosome_fingerprint(chromosome),'fixture',1,())
    return solution_payload(member,context.context_id)


def install_fixture(manager):
    identifier = 'a'*32
    manager.contexts[identifier] = EvaluationContext.from_dataset(sample(2,16))
    manager.datasets[identifier] = {'id':identifier,'name':'fixture.xlsx','valid':True,
                                  'sha256':'fixture','counts':{},'warnings':[],'errors':[]}
    return identifier


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.context = EvaluationContext.from_dataset(sample(2,16))

    def test_identical_genotypes_scores_costs_and_global_rng(self):
        before = random.getstate()
        plain = run_evolution(self.context,settings())
        progress = []
        monitored = run_evolution(self.context,settings(),on_generation=progress.append)
        self.assertEqual(random.getstate(),before)
        self.assertEqual([i.fingerprint for i in plain.population],[i.fingerprint for i in monitored.population])
        self.assertEqual(plain.evaluations,monitored.evaluations)
        self.assertEqual(plain.best_overall.evaluation,monitored.best_overall.evaluation)
        self.assertEqual([p.statistics.generation_index for p in progress],[0,1,2,3])
        for a,b in zip(plain.generations,monitored.generations):
            self.assertEqual((a.best_fingerprint,a.operators,a.evaluations),(b.best_fingerprint,b.operators,b.evaluations))

    def test_callback_cannot_mutate_engine_and_previous_snapshots(self):
        saved=[]
        def observer(p):
            saved.append(p)
            first=next(iter(p.population[0].chromosome.enrollments))
            p.population[0].chromosome.assign(first,None)
        plain=run_evolution(self.context,settings())
        monitored=run_evolution(self.context,settings(),on_generation=observer)
        self.assertEqual(plain.best_overall.fingerprint,monitored.best_overall.fingerprint)
        self.assertEqual([i.fingerprint for i in plain.population],[i.fingerprint for i in monitored.population])
        self.assertIsNot(saved[0].population[0].chromosome,saved[1].population[0].chromosome)

    def test_cooperative_stop_at_completed_generation(self):
        stop=[]
        def observer(p):
            if p.statistics.generation_index==1:stop.append(True)
        result=run_evolution(self.context,settings(max_generations=10),on_generation=observer,stop_requested=lambda:bool(stop))
        self.assertIn('manual_stop',result.stop_reasons)
        self.assertEqual(len(result.generations),2)
        self.assertIsNone(result.partial_generation)
        self.assertTrue(all(not validate_structure(i.chromosome,self.context) for i in result.population))

    def test_observer_failure_keeps_completed_population(self):
        def broken(p):raise RuntimeError('observer error')
        with self.assertRaises(EvolutionFailure) as caught:
            run_evolution(self.context,settings(),on_generation=broken)
        self.assertEqual(caught.exception.result.generations[-1].generation_index,0)

    def test_no_extra_fitness_calls(self):
        from scheduler import initialization,operator_support,repair
        calls=[]
        def wrap(fn):
            def observed(*a,**kw):calls.append(1);return fn(*a,**kw)
            return observed
        with patch.object(initialization,'evaluate_fitness',wrap(initialization.evaluate_fitness)),patch.object(operator_support,'evaluate_fitness',wrap(operator_support.evaluate_fitness)),patch.object(repair,'evaluate_fitness',wrap(repair.evaluate_fitness)):
            result=run_evolution(self.context,settings(),on_generation=lambda p:None)
        self.assertEqual(len(calls),result.evaluations.total)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.manager=RunManager(self.temp.name)
        self.dataset=install_fixture(self.manager)

    def tearDown(self):
        self.manager.shutdown();self.temp.cleanup()

    def start(self,**kw):
        return self.manager.start(self.dataset,{'population_size':6,'elite_count':1,'max_generations':3,'random_seed':11,**kw})

    def test_lifecycle_complete_and_reconnection(self):
        run=self.start();self.manager.worker.join(15)
        data=self.manager.events(run['id'])
        self.assertEqual(data['run']['status'],'Completed')
        self.assertEqual([e['generation_index'] for e in data['events']],[0,1,2,3])
        self.assertEqual([e['generation_index'] for e in self.manager.events(run['id'],1)['events']],[2,3])
        for row in data['events'][1:]:
            self.assertTrue(row['best_preserved']);self.assertEqual(len(row['elites']),1)
            self.assertEqual(row['operators']['children'],5)
        self.assertEqual(self.manager.events(run['id'],3)['events'],[])

    def test_retention_best_per_generation_and_independence(self):
        run=self.start(previous_populations=0);self.manager.worker.join(15);rid=run['id']
        self.assertFalse(self.manager.population(rid,0)['full_population_available'])
        self.assertTrue(self.manager.population(rid,3)['full_population_available'])
        p=self.manager.snapshot(rid,0,'best');original=p['sections'][0]['active'];p['sections'][0]['active']=not original
        self.assertEqual(self.manager.snapshot(rid,0,'best')['sections'][0]['active'],original)
        self.assertEqual(self.manager.snapshot(rid,0,'best')['context_id'],list(self.manager.contexts[self.dataset].context_id))

    def test_stop_and_concurrent_start_rejection(self):
        original=__import__('scheduler_web.services',fromlist=['run_evolution']).run_evolution
        from threading import Event
        entered,release=Event(),Event()
        def gated(*a,**kw):entered.set();release.wait(5);return original(*a,**kw)
        with patch('scheduler_web.services.run_evolution',gated):
            run=self.start(max_generations=100);entered.wait(5)
            with self.assertRaises(RuntimeError):self.start()
            self.assertEqual(self.manager.stop(run['id'])['status'],'Stopping');release.set();self.manager.worker.join(15)
        self.assertEqual(self.manager.get_run(run['id'])['status'],'Stopped')
        self.assertTrue((self.manager.path('runs',run['id'])/'report.json').exists())

    def test_failure_state(self):
        with patch('scheduler_web.services.run_evolution',side_effect=RuntimeError('intentional')):
            run=self.start();self.manager.worker.join(10)
        self.assertEqual(self.manager.get_run(run['id'])['status'],'Failed')
        self.assertIn('intentional',self.manager.get_run(run['id'])['error'])

    def test_persist_generation_and_restart_history(self):
        run=self.start();self.manager.worker.join(15);rid=run['id']
        self.manager.persist_generation(rid,1)
        loaded=RunManager(self.temp.name)
        self.assertEqual(loaded.get_run(rid)['status'],'Completed')
        self.assertEqual(loaded.snapshot(rid,1,'best')['fingerprint'],self.manager.snapshot(rid,1,'best')['fingerprint'])

    def test_explicitly_saved_generation_survives_eviction(self):
        from scheduler_web import services
        real=services.run_evolution
        def wrapped(*a,**kw):
            publish=kw['on_generation']
            def observe(p):
                publish(p)
                if p.statistics.generation_index==0:
                    self.manager.persist_generation(self.manager.active_id,0)
            return real(*a,**{**kw,'on_generation':observe})
        with patch('scheduler_web.services.run_evolution',wrapped):
            run=self.start(previous_populations=0);self.manager.worker.join(15)
        self.assertTrue(self.manager.population(run['id'],0)['full_population_available'])
        self.assertFalse(self.manager.population(run['id'],1)['full_population_available'])

    def test_interruption_recovery(self):
        rid='b'*32
        atomic_json(self.manager.path('runs',rid)/'metadata.json',{'id':rid,'status':'Running'})
        self.assertEqual(RunManager(self.temp.name).get_run(rid)['status'],'Failed')

    def test_invalid_settings(self):
        for kw in ({'population_size':2,'tournament_size':3},{'elite_count':100},{'mutation_probability':2},{'previous_populations':21},{'made_up':3}):
            with self.subTest(kw=kw),self.assertRaises((ValueError,TypeError)):configuration(kw)
        self.assertIsNone(configuration({})[0].max_fitness_evaluations)

    def test_stagnation_and_first_feasible_states(self):
        d=sample(1,8);d=with_rows(d,TimeSlots=d.tables['TimeSlots'][:1])
        self.manager.contexts[self.dataset]=EvaluationContext.from_dataset(d)
        r=self.start(max_generations=20,mutation_probability=0,repair_enabled=False,stagnation_generations=1)
        self.manager.worker.join(15)
        self.assertIn('stagnation',self.manager.get_run(r['id'])['stop_reasons'])
        r=self.start(stop_on_first_feasible=True)
        self.manager.worker.join(15)
        self.assertIn('first_feasible',self.manager.get_run(r['id'])['stop_reasons'])
        self.assertEqual(self.manager.get_run(r['id'])['completed_generations'],0)

    def test_invalid_upload_and_safe_identifiers(self):
        with self.assertRaises(ValueError):self.manager.upload('test.csv',b'1')
        invalid=self.manager.upload('../../bad.xlsx',b'not zip')
        self.assertFalse(invalid['valid']);self.assertEqual(invalid['name'],'bad.xlsx')
        with self.assertRaises(ValueError):self.manager.start(invalid['id'],{})
        with self.assertRaises(KeyError):self.manager.path('runs','../../secret')


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.context=EvaluationContext.from_dataset(sample(2,sessions=2));self.payload=snapshot(self.context)

    def test_student_multisession_credits_and_none(self):
        p=project(self.payload,self.context,'student','S1')
        self.assertEqual(len(p['events']),2);self.assertEqual(p['detail']['credits'],3)
        self.assertEqual([(e['start'],e['end']) for e in p['events']],[('08:00','09:30'),('08:00','09:30')])
        c=scheduled(self.context.dataset);c.assign(('S1','C1'),None)
        p=project(snapshot(self.context,c),self.context,'student','S1')
        self.assertEqual(p['events'],[]);self.assertEqual(p['detail']['unassigned'],['C1'])

    def test_professor_minutes_and_room_capacity(self):
        p=project(self.payload,self.context,'professor','P1')
        self.assertEqual(p['detail']['teaching_minutes'],180)
        r=project(self.payload,self.context,'room','R1')
        self.assertEqual(r['detail']['capacity'],20);self.assertEqual(r['events'][0]['enrollment_count'],8)

    def test_overlaps_and_violation_associations(self):
        dataset=overlap_second_slot(sample(2))
        context=EvaluationContext.from_dataset(dataset);c=scheduled(dataset)
        c.replace_section(replace(c.sections['C2-01'],professor_id='P1',room_id='R1'))
        payload=snapshot(context,c);p=project(payload,context,'professor','P1')
        self.assertEqual(p['events'][0]['overlaps'],[1])
        self.assertTrue(any(v['constraint_type']=='H04_PROFESSOR_OVERLAP' for v in p['violations']))
        self.assertEqual(project(payload,context,'professor','P2')['events'],[])

    def test_export_fingerprint_sheets_sessions_and_warning(self):
        c=scheduled(self.context.dataset);c.assign(('S1','C1'),None)
        payload=snapshot(self.context,c);book=load_workbook(BytesIO(export_excel(payload,self.context,'run',7)))
        self.assertEqual(book.sheetnames,['Faculty Timetable','Student Enrollments','Student Weekly Timetables','Validation Report'])
        self.assertEqual(book['Faculty Timetable'].max_row,5)
        self.assertEqual(book['Student Enrollments'].max_row,17)
        report=list(book['Validation Report'].values)
        self.assertEqual(report[1][3],payload['fingerprint']);self.assertEqual(report[1][-1],WARNING)
        self.assertTrue(any(r[-1]=='Unassigned' for r in book['Student Enrollments'].values))
        self.assertEqual(book['Student Weekly Timetables'].max_row,31)

    def test_export_formula_strings_are_text(self):
        courses=tuple(replace(c,course_name='=1+2') for c in self.context.dataset.tables['Courses'])
        ctx=EvaluationContext.from_dataset(with_rows(self.context.dataset,Courses=courses))
        book=load_workbook(BytesIO(export_excel(snapshot(ctx),ctx,'r',0)))
        self.assertEqual(book['Faculty Timetable']['B2'].data_type,'s')

    def test_actual_slot_duration_and_inactive_section(self):
        d=with_rows(self.context.dataset,TimeSlots=tuple(replace(s,end_time=s.start_time+75) for s in self.context.dataset.tables['TimeSlots']))
        ctx=EvaluationContext.from_dataset(d);p=project(snapshot(ctx),ctx,'professor','P1')
        self.assertEqual(p['detail']['teaching_minutes'],150)
        self.assertEqual(p['events'][0]['end'],'09:15')
        self.assertEqual(p['days'][0],'Saturday')
        c=scheduled(ctx.dataset)
        for key in list(c.enrollments):
            if key[1]=='C1':c.assign(key,None)
        from scheduler.chromosome import SectionGene
        c.replace_section(SectionGene('C1-01','C1'))
        self.assertEqual(project(snapshot(ctx,c),ctx,'professor','P1')['events'],[])


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=create_app(self.temp.name)
        self.client=TestClient(self.app);self.client.__enter__();self.headers={'x-local-token':self.app.state.token}
        self.dataset=install_fixture(self.app.state.manager)

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def start(self):
        response=self.client.post('/api/runs',headers=self.headers,json={'dataset_id':self.dataset,'settings':{'population_size':6,'elite_count':1,'max_generations':1}})
        self.assertEqual(response.status_code,200)
        self.app.state.manager.worker.join(15)
        return response.json()['id']

    def test_page_rtl_assets_and_state(self):
        page=self.client.get('/');self.assertEqual(page.status_code,200)
        self.assertIn('lang="fa" dir="rtl"',page.text);self.assertIn('نسل',page.text)
        for path in ('app.js','app.css','vendor/chart.umd.js','vendor/bootstrap.rtl.min.css'):
            self.assertEqual(self.client.get('/static/'+path).status_code,200)
        self.assertEqual(self.client.get('/api/state').json()['status'],'Idle')

    def test_write_token_and_host_validation(self):
        self.assertEqual(self.client.post('/api/runs',json={}).status_code,403)
        self.assertEqual(self.client.get('/',headers={'host':'evil.example'}).status_code,400)

    def test_api_progress_snapshot_calendar_exports_and_stop(self):
        rid=self.start()
        events=self.client.get(f'/api/runs/{rid}/events').json()
        self.assertEqual([r['generation_index'] for r in events['events']],[0,1])
        self.assertEqual(self.client.get(f'/api/runs/{rid}/generations/1').status_code,200)
        payload=self.client.get(f'/api/runs/{rid}/snapshot?generation=1').json()['snapshot']
        p=self.client.get(f'/api/runs/{rid}/timetable',params={'generation':1,'role':'student','entity_id':'S1'}).json()
        self.assertEqual(p['fingerprint'],payload['fingerprint'])
        export=self.client.get(f'/api/runs/{rid}/download/xlsx?generation=1')
        self.assertEqual(export.status_code,200)
        book=load_workbook(BytesIO(export.content))
        self.assertEqual(book['Validation Report']['D2'].value,payload['fingerprint'])
        self.assertEqual(self.client.get(f'/api/runs/{rid}/download/report').status_code,200)
        self.assertEqual(self.client.post(f'/api/runs/{rid}/stop',headers=self.headers).status_code,200)

    def test_invalid_api_settings_and_download_path(self):
        self.assertEqual(self.client.post('/api/runs',headers=self.headers,json={'dataset_id':self.dataset,'settings':{'population_size':1}}).status_code,422)
        self.assertEqual(self.client.get('/api/runs/not-an-id/download/report').status_code,404)
        rid=self.start()
        self.assertEqual(self.client.get(f'/api/runs/{rid}/download/secret').status_code,404)
        self.assertEqual(self.client.get(f'/api/runs/{rid}/events?limit=101').status_code,422)
        self.assertEqual(self.client.get(f'/api/runs/{rid}/snapshot?generation=999').status_code,404)

    def test_upload_invalid_workbook(self):
        r=self.client.post('/api/datasets/upload',headers=self.headers,files={'file':('wrong.xlsx',b'bad')})
        self.assertEqual(r.status_code,200);self.assertFalse(r.json()['valid'])

    def test_real_dataset_b_end_to_end_and_no_mutation(self):
        path=ROOT/'data/Dataset_B_Constructive_seed20261103.xlsx';digest=hashlib.sha256(path.read_bytes()).hexdigest()
        r=self.client.post('/api/datasets/upload',headers=self.headers,files={'file':('Dataset_B.xlsx',path.read_bytes())})
        self.assertEqual(r.status_code,200);self.assertTrue(r.json()['valid']);self.assertEqual(r.json()['sha256'],digest)
        rid=self.client.post('/api/runs',headers=self.headers,json={'dataset_id':r.json()['id'],'settings':{'population_size':6,'elite_count':1,'max_generations':2,'random_seed':11}}).json()['id']
        self.app.state.manager.worker.join(30)
        run=self.client.get(f'/api/runs/{rid}/events').json()
        self.assertEqual(run['run']['status'],'Completed');self.assertEqual(run['run']['completed_generations'],2)
        context=self.app.state.manager.dataset_context(r.json()['id'])
        for role,entity in [('student',next(iter(context.students))),('professor',next(iter(context.professors))),('room',next(iter(context.rooms)))]:
            projected=self.client.get(f'/api/runs/{rid}/timetable',params={'generation':2,'role':role,'entity_id':entity})
            self.assertEqual(projected.status_code,200)
        exported=self.client.get(f'/api/runs/{rid}/download/xlsx?generation=2')
        self.assertEqual(exported.status_code,200);self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
