"""Unmodified GA on A/B workbooks. This module never opens a feasibility certificate."""
import argparse
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from scheduler import load_dataset
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionSettings, run_evolution
from scheduler.evolution_reporting import write_report
from scheduler.fitness import evaluate_fitness

PROTECTED_MODULES = ('evolution', 'initialization', 'mutation', 'repair', 'constraints',
                     'soft_fitness', 'fitness', 'evaluation_context', 'selection', 'crossover')


def engine_hashes():
    return {name: hashlib.sha256((ROOT / f'src/scheduler/{name}.py').read_bytes()).hexdigest()
            for name in PROTECTED_MODULES}


def profile_dataset(dataset, context):
    requested = {sid: sum(context.courses[cid].credits for s, cid in context.requests if s == sid)
                 for sid in context.students}
    return {'sha256': dataset.sha256, 'context_id': list(context.context_id),
        'sheet_rows': {name: len(rows) for name, rows in dataset.tables.items()},
        'potential_sections': len(context.expected_sections),
        'request_credits_min': min(requested.values()), 'request_credits_max': max(requested.values()),
        'students_requesting_above_max': sum(requested[sid] > s.max_credits for sid, s in context.students.items()),
        'min_credits_range': [min(s.min_credits for s in context.students.values()), max(s.min_credits for s in context.students.values())],
        'max_credits_range': [min(s.max_credits for s in context.students.values()), max(s.max_credits for s in context.students.values())],
        'room_types': {t: sum(r.room_type == t for r in context.rooms.values()) for t in sorted({r.room_type for r in context.rooms.values()})},
        'qualified_options_per_course': {cid: sum(c == cid for _, c in context.qualified) for cid in context.courses}}


def observed_run(context, settings):
    """Delegate every score unchanged; only record actual call order and elapsed time.

    The three approved call sites are observed without changing operators, data,
    RNG or replacement. Instrumentation is identical for both datasets.
    """
    started = perf_counter()
    calls = {'initialization': 0, 'external': 0, 'repair': 0}
    first = None

    def observer(phase):
        def evaluate(chromosome, supplied_context):
            nonlocal first
            result = evaluate_fitness(chromosome, supplied_context)
            calls[phase] += 1
            if result.hard.is_feasible and first is None:
                first = {'fitness_evaluations': sum(calls.values()),
                         'elapsed_seconds_including_initialization': perf_counter() - started,
                         'phase': phase}
            return result
        return evaluate

    with ExitStack() as stack:
        for target, phase in (('scheduler.initialization.evaluate_fitness', 'initialization'),
                              ('scheduler.operator_support.evaluate_fitness', 'external'),
                              ('scheduler.repair.evaluate_fitness', 'repair')):
            stack.enter_context(patch(target, new=observer(phase)))
        result = run_evolution(context, settings)
    if sum(calls.values()) != result.evaluations.total:
        raise RuntimeError('actual fitness calls differ from runner accounting')
    if (first is not None) != result.feasible_found:
        raise RuntimeError('feasibility observation differs from best-feasible tracking')
    return result, {'actual_calls': calls, 'total_calls': sum(calls.values()),
                    'first_feasible': first, 'observation': 'delegating wrappers; no scoring or RNG changes'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-a', type=Path, default=ROOT / 'data/UniversityDataset_Corrected.xlsx')
    parser.add_argument('--dataset-b', type=Path, default=ROOT / 'data/Dataset_B_Constructive_seed20261103.xlsx')
    parser.add_argument('--only', choices=('A', 'B', 'both'), default='both')
    parser.add_argument('--seeds', nargs='+', type=int, default=[11, 22, 33])
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/DATASET_COMPARISON.json')
    args = parser.parse_args(argv)
    sources = {'A': args.dataset_a, 'B': args.dataset_b}
    labels = ['A', 'B'] if args.only == 'both' else [args.only]
    hashes = engine_hashes()
    summary = {'schema_version': 'controlled-dataset-comparison-v1', 'engine_hashes': hashes,
               'datasets': {}, 'runs': []}
    settings = EvolutionSettings(population_size=100, tournament_size=3, elite_count=2,
        max_generations=500, mutation_probability=.30, crossover_probability=.70,
        repair_enabled=True, max_fitness_evaluations=4000, stop_on_first_feasible=False)
    outputs = [args.output]
    for label in labels:
        for seed in args.seeds:
            path = args.output.with_name(f'{args.output.stem}_{label}_seed{seed}.json')
            outputs.extend((path, path.with_name(path.stem + '_solutions.json')))
    if len({p.resolve() for p in outputs}) != len(outputs):
        parser.error('duplicate output paths or seeds')
    if {p.resolve() for p in outputs} & {p.resolve() for p in sources.values()}:
        parser.error('output cannot overwrite a workbook')
    for label in labels:
        dataset = load_dataset(sources[label])
        started = perf_counter()
        context = EvaluationContext.from_dataset(dataset)
        summary['datasets'][label] = {**profile_dataset(dataset, context), 'context_seconds': perf_counter() - started}
        for seed in args.seeds:
            result, observations = observed_run(context, replace(settings, random_seed=seed))
            output = args.output.with_name(f'{args.output.stem}_{label}_seed{seed}.json')
            write_report(result, output)
            report = json.loads(output.read_text(encoding='utf-8'))
            report['experimental_observations'] = observations
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            row = {'dataset': label, 'seed': seed, 'report_path': str(output.resolve()),
                   'initial_hard': result.generations[0].best_hard_penalty,
                   'best_hard': result.best_overall.evaluation.hard.total_penalty,
                   'best_soft': result.best_overall.evaluation.soft.total_score,
                   'request_coverage': result.best_overall.evaluation.soft.request_coverage,
                   'feasible_found': result.feasible_found, 'first_feasible': observations['first_feasible'],
                   'fitness_evaluations': result.evaluations.total,
                   'completed_generations': len(result.generations) - 1,
                   'evolution_seconds': result.evolution_elapsed_seconds,
                   'initialization_seconds': result.initialization_elapsed_seconds,
                   'final_unique': result.generations[-1].unique_chromosomes,
                   'hard_counts': dict(result.best_overall.evaluation.hard.counts_by_type),
                   'stop_reasons': result.stop_reasons}
            summary['runs'].append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
        if hashlib.sha256(sources[label].read_bytes()).hexdigest() != dataset.sha256:
            raise RuntimeError(f'dataset {label} changed during experiments')
    if hashes != engine_hashes():
        raise RuntimeError('approved engine modules changed during comparison')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
