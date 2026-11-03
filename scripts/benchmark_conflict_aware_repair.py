"""Pinned-engine Phase 8 A/B measurements; no witness is passed to evolution."""
import argparse
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
from random import Random
import subprocess
import sys
from time import perf_counter
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
BASELINE_COMMIT='ed8a3436664b487cee3e0ebef0447507b511a93c'
B_HASH='6b90c1c6784cbb81527dfc196e1a5e4347cd56ff988f6ce703a1452093576391'


def prepare():
    target=ROOT/'outputs/phase8_benchmark_engine'
    files=subprocess.check_output(['git','ls-tree','-r','--name-only',BASELINE_COMMIT,'src/scheduler'],cwd=ROOT,text=True).splitlines()
    for relative in files:
        p=target/relative;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(subprocess.check_output(['git','show',BASELINE_COMMIT+':'+relative],cwd=ROOT))
    for name in ('repair.py','repair_relocation.py'):
        (target/'src/scheduler'/name).write_bytes((ROOT/'src/scheduler'/name).read_bytes())
    (target/'baseline_repair.py').write_bytes(subprocess.check_output(['git','show',BASELINE_COMMIT+':src/scheduler/repair.py'],cwd=ROOT))
    book=target/'dataset_b.xlsx'
    book.write_bytes(subprocess.check_output(['git','show',BASELINE_COMMIT+':data/Dataset_B_Constructive_seed20261103.xlsx'],cwd=ROOT))
    if hashlib.sha256(book.read_bytes()).hexdigest()!=B_HASH:raise RuntimeError('Dataset B hash mismatch')
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generations',type=int,default=10)
    parser.add_argument('--population',type=int,default=30)
    parser.add_argument('--fitness-budget',type=int,default=4000)
    parser.add_argument('--repetitions',type=int,default=30)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/PHASE8_COMPARISON.json')
    args=parser.parse_args()
    if min(args.generations,args.population,args.fitness_budget,args.repetitions)<1:parser.error('limits must be positive')
    if args.output.exists():raise FileExistsError('refusing to overwrite measured results')
    target=prepare();sys.path.insert(0,str(target/'src'));sys.path.insert(0,str(ROOT/'tests'))
    from scheduler import load_dataset,evolution
    from scheduler.evaluation_context import EvaluationContext
    from scheduler.evolution_reporting import json_value,result_payload,restore_solution,solution_payload
    from scheduler.operator_support import evaluate_snapshot
    from scheduler.repair import RepairSettings,repair_conflicts
    from test_constraints import sample,scheduled
    context=EvaluationContext.from_dataset(load_dataset(target/'dataset_b.xlsx'))
    payload=json.loads((ROOT/'tests/fixtures/phase8_dataset_b_hard4.json').read_text(encoding='utf-8'))
    actual=restore_solution(payload,context);actual_score=evaluate_snapshot(actual,context)
    assert actual_score.fitness.hard.total_penalty==4
    spec=importlib.util.spec_from_file_location('scheduler._phase8_reference',target/'baseline_repair.py')
    reference=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reference;spec.loader.exec_module(reference)
    fixture_context=EvaluationContext.from_dataset(sample(2))
    hand=scheduled(fixture_context.dataset)
    hand.replace_section(replace(hand.sections['C2-01'],room_id='R1',meeting_slots=(('Saturday','T1'),)))
    report={'baseline_commit':BASELINE_COMMIT,'dataset_b_sha256':B_HASH,'regression_fingerprint':actual_score.fingerprint,
        'regression_source':'actual saved Web UI run 69c4cb75262144f2a341d750848544b3, completed generation 500',
        'engine_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((target/'src/scheduler').glob('*.py'))},
        'baseline_repair_sha256':hashlib.sha256((target/'baseline_repair.py').read_bytes()).hexdigest(),
        'arguments':vars(args)|{'output':str(args.output)},'variants':{}}
    for name, function, enabled in [('baseline',reference.repair_conflicts,False),('enhanced',repair_conflicts,True)]:
        variant={'focused':{}};settings=RepairSettings(conflict_aware_relocation=enabled)
        for label,c,ctx in [('actual_hard4',actual,context),('hand_room_overlap',hand,fixture_context)]:
            baseline=evaluate_snapshot(c,ctx);times=[];result=None
            for _ in range(args.repetitions):
                started=perf_counter();result=function(c,ctx,Random(11),settings,evaluation=baseline)
                times.append(perf_counter()-started)
            variant['focused'][label]={'repetitions':args.repetitions,'mean_repair_seconds':sum(times)/len(times),
                'min_seconds':min(times),'max_seconds':max(times),'before_counts':dict(baseline.fitness.hard.counts_by_type),
                'after_counts':dict(result.evaluation.fitness.hard.counts_by_type),'report':json_value(result.report),
                'remaining_violations':json_value(result.evaluation.fitness.hard.violations),'fingerprint':result.evaluation.fingerprint}
            print(json.dumps({'variant':name,'case':label,'hard_before':baseline.fitness.hard.total_penalty,
                              'hard_after':result.evaluation.fitness.hard.total_penalty,'mean_seconds':sum(times)/len(times)}),flush=True)
            if label=='actual_hard4':
                args.output.parent.mkdir(parents=True,exist_ok=True)
                (args.output.parent/f'PHASE8_{name}_REGRESSION.json').write_text(json.dumps(solution_payload(
                    evolution.InitializedIndividual(result.chromosome,result.evaluation.fitness,result.evaluation.fingerprint,'repair',1,()),ctx.context_id),indent=2),encoding='utf-8')
        totals={'repair_calls':0,'repair_seconds':0.0,'candidate_evaluations':0,'attempts':0,'room_conflicts_resolved':0,
                'net_hard_improvement_inside_repair':0,'relocation_index_seconds':0.0,'relocation_index_builds':0,'relocation_filter_seconds':0.0}
        def observed(*a,**kw):
            started=perf_counter();r=function(*a,**kw);totals['repair_seconds']+=perf_counter()-started
            totals['repair_calls']+=1;totals['candidate_evaluations']+=r.report.evaluations.candidates
            totals['attempts']+=r.report.attempts
            totals['net_hard_improvement_inside_repair']+=r.report.hard_before-r.report.hard_after
            # Count actual canonical input-vs-output records for BOTH versions.
            before=kw['evaluation'].fitness.hard.violations
            after=r.evaluation.fitness.hard.violations
            totals['room_conflicts_resolved']+=len({v for v in before if v.constraint_type=='H04_ROOM_OVERLAP'}-{v for v in after if v.constraint_type=='H04_ROOM_OVERLAP'})
            for key in ('relocation_index_seconds','relocation_filter_seconds','relocation_index_builds'):totals[key]+=getattr(r.report,key,0.0)
            return r
        configuration=evolution.EvolutionSettings(population_size=args.population,tournament_size=3,elite_count=2,
            max_generations=args.generations,random_seed=11,mutation_probability=.30,crossover_probability=.70,
            repair_enabled=True,max_fitness_evaluations=args.fitness_budget,repair_settings=settings)
        with patch.object(evolution,'repair_conflicts',observed):
            result=evolution.run_evolution(context,configuration)
        variant['evolution']=result_payload(result);variant['repair_totals']=totals
        variant['mean_complete_generation_seconds']=sum(g.generation_elapsed_seconds for g in result.generations[1:])/max(1,len(result.generations)-1)
        variant['mean_repair_seconds']=totals['repair_seconds']/max(1,totals['repair_calls'])
        report['variants'][name]=variant
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'variant':name,'completed_generations':len(result.generations)-1,
            'best_hard':result.best_overall.evaluation.hard.total_penalty,'fitness_calls':result.evaluations.total,
            'evolution_seconds':result.evolution_elapsed_seconds}),flush=True)
    # Feature disabled must remain the exact legacy path on the regression.
    legacy=reference.repair_conflicts(actual,context,Random(11),RepairSettings(conflict_aware_relocation=False),evaluation=actual_score)
    disabled=repair_conflicts(actual,context,Random(11),RepairSettings(conflict_aware_relocation=False),evaluation=actual_score)
    assert legacy.evaluation==disabled.evaluation and legacy.report.evaluations==disabled.report.evaluations
    return 0


if __name__=='__main__':raise SystemExit(main())
