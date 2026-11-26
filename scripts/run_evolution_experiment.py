"""Run one GA experiment or the A/B/C matrix on a fixed workbook; no UI."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from scheduler import load_dataset
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionFailure, EvolutionSettings, run_evolution
from scheduler.evolution_reporting import write_report
from scheduler.repair import RepairSettings


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workbook', type=Path, default=ROOT / 'data/UniversityDataset_Corrected.xlsx')
    p.add_argument('--population-size', type=int, default=100)
    p.add_argument('--max-generations', type=int, default=500)
    p.add_argument('--random-seed', type=int, default=55)
    p.add_argument('--tournament-size', type=int, default=3)
    p.add_argument('--elite-count', type=int, default=2)
    p.add_argument('--mutation-probability', type=float, default=.30)
    p.add_argument('--crossover-probability', type=float, default=.70)
    p.add_argument('--repair-enabled', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--repair-evaluation-budget', type=int, default=50)
    p.add_argument('--repair-local-budget', type=int, default=5)
    p.add_argument('--repair-attempt-budget', type=int, default=100)
    p.add_argument('--stop-on-first-feasible', action=argparse.BooleanOptionalAction, default=False)
    p.add_argument('--stagnation-generations', type=int)
    p.add_argument('--max-fitness-evaluations', type=int)
    p.add_argument('--output', type=Path, default=ROOT / 'outputs/EVOLUTION_REPORT.json')
    p.add_argument('--matrix', action='store_true', help='A/B/C for each seed; explicit population/generation limits recommended')
    p.add_argument('--seeds', type=int, nargs='+', default=[11, 22, 33])
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        settings = EvolutionSettings(population_size=args.population_size, max_generations=args.max_generations,
            random_seed=args.random_seed, tournament_size=args.tournament_size, elite_count=args.elite_count,
            mutation_probability=args.mutation_probability, crossover_probability=args.crossover_probability,
            repair_enabled=args.repair_enabled, stop_on_first_feasible=args.stop_on_first_feasible,
            stagnation_generations=args.stagnation_generations, max_fitness_evaluations=args.max_fitness_evaluations,
            repair_settings=RepairSettings(max_candidate_evaluations=args.repair_evaluation_budget,
                max_local_candidates=args.repair_local_budget, max_candidate_attempts=args.repair_attempt_budget))
    except (ValueError, TypeError) as exc:
        p.error(str(exc))
    variants = [('single', settings)]
    if args.matrix:
        variants = [(f'{label}_seed{seed}', replace(settings, random_seed=seed,
                     mutation_probability=0 if label == 'A' else settings.mutation_probability,
                     repair_enabled=label == 'C')) for seed in args.seeds for label in ('A', 'B', 'C')]
    paths = []
    for name, _ in variants:
        path = args.output if not args.matrix else args.output.with_name(args.output.stem + '_' + name + '.json')
        paths.extend((path, path.with_name(path.stem + '_solutions.json')))
    if len({path.resolve() for path in paths + [args.output]}) != len(paths) + int(args.matrix):
        p.error('output paths must be unique (including distinct matrix seeds)')
    if args.workbook.resolve() in {path.resolve() for path in paths + [args.output]}:
        p.error('output cannot overwrite the input workbook')
    load_start = perf_counter()
    dataset = load_dataset(args.workbook)
    load_seconds = perf_counter() - load_start
    context_start = perf_counter()
    context = EvaluationContext.from_dataset(dataset)
    context_seconds = perf_counter() - context_start
    rows = []
    for index, (name, configuration) in enumerate(variants):
        output = paths[2 * index]
        try:
            result = run_evolution(context, configuration, context_elapsed_seconds=context_seconds if index == 0 else 0)
        except EvolutionFailure as exc:
            if exc.result is not None:
                write_report(exc.result, output)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({'failure': str(exc), 'stage': 'initialization',
                    'evaluation_accounting_complete': False}, indent=2), encoding='utf-8')
            print(str(exc), file=sys.stderr)
            return 1
        write_report(result, output)
        row = {'name': name, 'seed': configuration.random_seed, 'report_path': str(output.resolve()),
            'initial_hard': result.generations[0].best_hard_penalty,
            'best_hard': result.best_overall.evaluation.hard.total_penalty,
            'best_soft': result.best_overall.evaluation.soft.total_score,
            'feasible_found': result.feasible_found, 'generations': len(result.generations) - 1,
            'fitness_calls': result.evaluations.total, 'repair_candidates': result.evaluations.repair_candidates,
            'evolution_seconds': result.evolution_elapsed_seconds, 'stop_reasons': list(result.stop_reasons)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    if hashlib.sha256(args.workbook.read_bytes()).hexdigest() != dataset.sha256:
        raise RuntimeError('input workbook changed during experiment')
    if args.matrix:
        summary = {'schema_version': 'scheduler-experiment-matrix-v1', 'dataset_sha256': dataset.sha256,
                   'context_id': list(context.context_id), 'dataset_load_seconds': load_seconds,
                   'context_seconds_shared': context_seconds, 'runs': rows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
