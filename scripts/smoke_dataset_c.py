"""Limited compatibility run. Reads only the XLSX, never the reference witness.

Run on a Git snapshot to exclude unrelated local engine edits. Paths and loaded
engine hashes are written to the result for verification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from scheduler.dataset import load_dataset
from scheduler.chromosome import validate_structure
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionSettings, run_evolution
from scheduler.evolution_reporting import result_payload
from scheduler.initialization import initialize_population


def smoke(workbook, *, population_size=6, generations=3, seed=11):
    started = perf_counter()
    dataset = load_dataset(workbook)
    load_seconds = perf_counter()-started
    started = perf_counter()
    context = EvaluationContext.from_dataset(dataset)
    context_seconds = perf_counter()-started
    initial = initialize_population(context, population_size, seed)
    if any(validate_structure(i.chromosome, context) for i in initial.individuals):
        raise RuntimeError('invalid initial chromosome')
    settings = EvolutionSettings(population_size=population_size, elite_count=2,
        tournament_size=3, max_generations=generations, random_seed=seed,
        mutation_probability=.30, crossover_probability=.70, repair_enabled=True,
        max_fitness_evaluations=None, stagnation_generations=None, stop_on_first_feasible=False)
    result = run_evolution(context, settings)
    if any(validate_structure(i.chromosome, context) for i in result.population):
        raise RuntimeError('invalid evolved chromosome')
    modules = ('initialization', 'evolution', 'selection', 'crossover', 'mutation', 'repair',
               'repair_relocation', 'fitness', 'soft_fitness', 'constraints', 'evaluation_context')
    import scheduler
    source = Path(scheduler.__file__).resolve().parent
    hashes = {name: hashlib.sha256((source/(name+'.py')).read_bytes()).hexdigest() for name in modules}
    report = result_payload(result)
    # Keep the benchmark compact; canonical counts and record counts are enough
    # for a smoke test, rather than three duplicate multi-megabyte violation lists.
    for key in ('best_overall', 'best_feasible', 'final_population_best'):
        if report[key] is not None:
            report[key]['violation_record_count'] = len(report[key].pop('violations'))
    return {'schema_version': 'dataset-c-smoke-v1', 'engine_hashes': hashes,
        'dataset_sha256': dataset.sha256, 'load_seconds': load_seconds, 'context_seconds': context_seconds,
        'initial_population_fingerprints': [i.fingerprint for i in initial.individuals],
        'initial_structure_issues': 0, 'final_structure_issues': 0,
        'separate_initial_population_evaluations': population_size,
        'separate_initial_population_seconds': initial.statistics.elapsed_seconds,
        'evolution': report,
        'purpose': 'compatibility only; no convergence claim; no witness read'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT/'data/Dataset_C_University_seed20261104.xlsx')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/DATASET_C_SMOKE.json')
    parser.add_argument('--population-size', type=int, default=6)
    parser.add_argument('--generations', type=int, default=3)
    parser.add_argument('--seed', type=int, default=11)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('refusing to overwrite a smoke report')
    report = smoke(args.dataset, population_size=args.population_size, generations=args.generations, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'generations': report['evolution']['completed_generations'],
                      'best_hard': report['evolution']['best_overall']['hard_penalty'],
                      'fitness_calls': report['evolution']['evaluation_usage']['total']}))
