"""Integration check on a fixed population; no evolution, replacement or repair."""
import argparse
from collections import defaultdict
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
from scheduler.fitness import evaluate_fitness
from scheduler.initialization import chromosome_fingerprint, initialize_population
from scheduler.selection import tournament_selection


def blocks(chromosome, context):
    sections, enrollments = defaultdict(list), defaultdict(list)
    for sid, section in sorted(chromosome.sections.items()):
        sections[section.course_id].append((sid, section))
    for key, sid in sorted(chromosome.enrollments.items()):
        enrollments[key[1]].append((key, sid))
    return {cid: (tuple(sections[cid]), tuple(enrollments[cid])) for cid in context.courses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', type=Path, default=ROOT / 'data/UniversityDataset_Corrected.xlsx')
    parser.add_argument('--population-size', type=int, default=100)
    parser.add_argument('--children', type=int, default=100)
    parser.add_argument('--initialization-seed', type=int, default=2026)
    parser.add_argument('--operator-seed', type=int, default=55)
    parser.add_argument('--tournament-size', type=int, default=3)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/GENETIC_OPERATORS_5A_REPORT.json')
    args = parser.parse_args()
    if args.children < 1 or not 1 <= args.tournament_size <= args.population_size:
        parser.error('children must be positive and tournament size must fit the population')
    if args.output.resolve() == args.workbook.resolve():
        parser.error('output cannot overwrite the input workbook')
    dataset = load_dataset(args.workbook)
    context = EvaluationContext.from_dataset(dataset)
    population = initialize_population(context, args.population_size, args.initialization_seed)
    before = [(i.fingerprint, i.evaluation, i.chromosome.hard_score, i.chromosome.soft_score) for i in population.individuals]
    indices = {id(i): index for index, i in enumerate(population.individuals)}
    parent_blocks = [blocks(i.chromosome, context) for i in population.individuals]
    rng = Random(args.operator_seed)
    children, self_matings = [], 0
    started = perf_counter()
    for index in range(args.children):
        a, b = tournament_selection(population, rng, args.tournament_size), tournament_selection(population, rng, args.tournament_size)
        ai, bi = indices[id(a)], indices[id(b)]
        self_matings += a is b
        child = course_based_crossover(a.chromosome, b.chromosome, context, rng)
        assert not validate_structure(child, context)
        child_blocks = blocks(child, context)
        assert all(child_blocks[cid] in (parent_blocks[ai][cid], parent_blocks[bi][cid]) for cid in context.courses)
        assert child.hard_score is None and child.soft_score is None
        result = evaluate_fitness(child, context)
        children.append({'index': index, 'parent_a': ai, 'parent_b': bi,
                         'fingerprint': chromosome_fingerprint(child),
                         'hard_penalty': result.hard.total_penalty, 'soft_score': result.soft.total_score,
                         'coverage': result.soft.request_coverage,
                         'worse_than_both_parents': result.comparison_key > max(a.evaluation.comparison_key, b.evaluation.comparison_key)})
    for individual, previous in zip(population.individuals, before):
        assert (chromosome_fingerprint(individual.chromosome), individual.evaluation,
                individual.chromosome.hard_score, individual.chromosome.soft_score) == previous
    report = {
        'source_sha256': dataset.sha256, 'context_id': context.context_id,
        'initialization_seed': args.initialization_seed, 'operator_seed': args.operator_seed,
        'population_size': args.population_size, 'tournament_size': args.tournament_size,
        'child_count': len(children), 'structurally_valid_count': len(children),
        'whole_course_blocks_preserved': True, 'parents_and_caches_unchanged': True,
        'unique_children': len({c['fingerprint'] for c in children}),
        'self_matings': self_matings, 'worse_than_both_parents_count': sum(c['worse_than_both_parents'] for c in children),
        'min_hard_penalty': min(c['hard_penalty'] for c in children),
        'mean_hard_penalty': fmean(c['hard_penalty'] for c in children),
        'max_hard_penalty': max(c['hard_penalty'] for c in children),
        'mean_soft_score': fmean(c['soft_score'] for c in children),
        'feasible_count': sum(c['hard_penalty'] == 0 for c in children),
        'elapsed_operator_check_seconds': perf_counter() - started,
        'children': children,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'children'}, indent=2))


if __name__ == '__main__':
    main()
