"""Fixed Phase 5A children with atomic mutation and bounded repair; no evolution."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from random import Random
from statistics import fmean
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from scheduler import load_dataset
from scheduler.chromosome import validate_structure
from scheduler.crossover import course_based_crossover
from scheduler.evaluation_context import EvaluationContext
from scheduler.initialization import chromosome_fingerprint, initialize_population
from scheduler.mutation import MutationSettings, maybe_mutate, mutate
from scheduler.operator_support import evaluate_snapshot
from scheduler.repair import RepairSettings, repair_conflicts
from scheduler.selection import tournament_selection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', type=Path, default=ROOT / 'data/UniversityDataset_Corrected.xlsx')
    parser.add_argument('--population-size', type=int, default=100)
    parser.add_argument('--children', type=int, default=100)
    parser.add_argument('--initialization-seed', type=int, default=2026)
    parser.add_argument('--operator-seed', type=int, default=55)
    parser.add_argument('--unconditional', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/MUTATION_REPAIR_5B_REPORT.json')
    args = parser.parse_args()
    if args.children < 1 or args.population_size < 3:
        parser.error('children must be positive; tournament needs population size >=3')
    if args.output.resolve() == args.workbook.resolve():
        parser.error('output cannot overwrite input workbook')
    dataset = load_dataset(args.workbook)
    context = EvaluationContext.from_dataset(dataset)
    population = initialize_population(context, args.population_size, args.initialization_seed)
    parent_state = [(i.fingerprint, i.evaluation, i.chromosome.hard_score, i.chromosome.soft_score) for i in population.individuals]
    rng = Random(args.operator_seed)
    mutation_settings, repair_settings = MutationSettings(), RepairSettings()
    rows = []
    started = perf_counter()
    for index in range(args.children):
        start = perf_counter()
        a, b = tournament_selection(population, rng), tournament_selection(population, rng)
        child = course_based_crossover(a.chromosome, b.chromosome, context, rng)
        before = evaluate_snapshot(child, context)  # explicit external baseline #1
        function = mutate if args.unconditional else maybe_mutate
        mutation = function(child, context, rng, mutation_settings, evaluation=before)
        changed = chromosome_fingerprint(mutation.chromosome) != before.fingerprint
        assert changed == (mutation.report.status == 'applied')
        after_mutation = evaluate_snapshot(mutation.chromosome, context) if changed else before
        repair = repair_conflicts(mutation.chromosome, context, rng, repair_settings, evaluation=after_mutation)
        assert not validate_structure(mutation.chromosome, context)
        assert not validate_structure(repair.chromosome, context)
        assert repair.evaluation.fitness.comparison_key <= after_mutation.fitness.comparison_key
        assert chromosome_fingerprint(child) == before.fingerprint
        assert mutation.chromosome.hard_score is None and repair.chromosome.hard_score is None
        assert repair.report.evaluations.candidates <= repair_settings.max_candidate_evaluations
        assert all(n <= repair_settings.max_local_candidates for n in repair.report.candidates_per_conflict)
        rows.append({
            'index': index, 'mutation_status': mutation.report.status, 'operation': mutation.report.operation,
            'reason': mutation.report.reason, 'targeted': mutation.report.targeted, 'fallback': mutation.report.fallback,
            'genotype_changed': changed, 'fingerprint': chromosome_fingerprint(repair.chromosome),
            'hard_child': before.fitness.hard.total_penalty,
            'hard_before_repair': after_mutation.fitness.hard.total_penalty,
            'hard_after_repair': repair.evaluation.fitness.hard.total_penalty,
            'soft_after_repair': repair.evaluation.fitness.soft.total_score,
            'repair_status': repair.report.status,
            'mutation_attempts': mutation.report.attempts, 'discovery_proposals': mutation.report.discovery_proposals,
            'repair_attempts': repair.report.attempts,
            'evaluations_external': 1 + int(changed),
            'evaluations_mutation': mutation.report.evaluations.fitness_calls,
            'evaluations_repair': repair.report.evaluations.fitness_calls,
            'candidate_evaluations_repair': repair.report.evaluations.candidates,
            'candidates_per_conflict': repair.report.candidates_per_conflict,
            'rollbacks': mutation.report.rollbacks + repair.report.rollbacks,
            'elapsed_seconds': perf_counter() - start,
        })
    for i, previous in zip(population.individuals, parent_state):
        assert (chromosome_fingerprint(i.chromosome), i.evaluation, i.chromosome.hard_score, i.chromosome.soft_score) == previous
    assert hashlib.sha256(args.workbook.read_bytes()).hexdigest() == dataset.sha256
    external = sum(r['evaluations_external'] for r in rows)
    mutation_calls = sum(r['evaluations_mutation'] for r in rows)
    repair_calls = sum(r['evaluations_repair'] for r in rows)
    total = args.population_size + external + mutation_calls + repair_calls
    report = {
        'source_sha256': dataset.sha256, 'context_id': context.context_id,
        'initialization_seed': args.initialization_seed, 'operator_seed': args.operator_seed,
        'population_size': args.population_size, 'children': args.children, 'unconditional': args.unconditional,
        'mutation_settings': asdict(mutation_settings), 'repair_settings': asdict(repair_settings),
        'mutation_status_counts': dict(Counter(r['mutation_status'] for r in rows)),
        'successful_operation_counts': dict(Counter(r['operation'] for r in rows if r['mutation_status'] == 'applied')),
        'genotype_change_rate': fmean(r['genotype_changed'] for r in rows),
        'targeted_count': sum(r['targeted'] for r in rows), 'fallback_count': sum(r['fallback'] for r in rows),
        'mean_hard_before_repair': fmean(r['hard_before_repair'] for r in rows),
        'mean_hard_after_repair': fmean(r['hard_after_repair'] for r in rows),
        'repair_status_counts': dict(Counter(r['repair_status'] for r in rows)),
        'fitness_evaluations': {'initialization': args.population_size, 'external': external,
                                'mutation': mutation_calls, 'repair': repair_calls, 'total': total},
        'hard_evaluations_total': total, 'soft_evaluations_total': total,
        'rollbacks': sum(r['rollbacks'] for r in rows),
        'structurally_valid_count': len(rows), 'parents_caches_dataset_unchanged': True,
        'mean_pipeline_seconds': fmean(r['elapsed_seconds'] for r in rows),
        'elapsed_pipeline_seconds': perf_counter() - started,
        'rows': rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('rows', 'mutation_settings', 'repair_settings')}, indent=2))


if __name__ == '__main__':
    main()
