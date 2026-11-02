"""JSON reporting and lossless best-genotype export (not a GA resume checkpoint)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import json
from pathlib import Path

from .chromosome import SectionGene
from .evolution import EvolutionResult
from .initialization import chromosome_fingerprint
from .operator_support import commit_draft


def json_value(value):
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('JSON object keys must be strings; encode tuple keys as rows')
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def solution_payload(individual, context_id):
    c, fitness = individual.chromosome, individual.evaluation
    return {
        'schema_version': 'scheduler-solution-v1', 'context_id': list(context_id),
        'fingerprint': individual.fingerprint, 'feasible': fitness.hard.is_feasible,
        'hard_penalty': fitness.hard.total_penalty, 'soft_score': fitness.soft.total_score,
        'sections': [json_value(section) for _, section in sorted(c.sections.items())],
        'enrollments': [{'student_id': key[0], 'course_id': key[1], 'section_id': sid}
                        for key, sid in sorted(c.enrollments.items())],
        'hard_evaluation': json_value(fitness.hard), 'soft_evaluation': json_value(fitness.soft),
    }


def restore_solution(payload, context):
    """Validate context, duplicates, structure and fingerprint; score by evaluator separately."""
    if payload.get('schema_version') != 'scheduler-solution-v1':
        raise ValueError('unsupported solution schema')
    if tuple(payload.get('context_id', ())) != context.context_id:
        raise ValueError('solution belongs to a different EvaluationContext')
    sections, enrollments = {}, {}
    for row in payload['sections']:
        section = SectionGene(**row)
        if section.section_id in sections:
            raise ValueError('duplicate SectionID in saved solution')
        sections[section.section_id] = section
    for row in payload['enrollments']:
        key = row['student_id'], row['course_id']
        if key in enrollments:
            raise ValueError('duplicate RequestKey in saved solution')
        enrollments[key] = row['section_id']
    chromosome = commit_draft(sections, enrollments, context)
    if chromosome_fingerprint(chromosome) != payload['fingerprint']:
        raise ValueError('saved solution fingerprint mismatch')
    return chromosome


def _summary(individual):
    if individual is None:
        return None
    return {'fingerprint': individual.fingerprint, 'hard_penalty': individual.evaluation.hard.total_penalty,
            'soft_score': individual.evaluation.soft.total_score,
            'request_coverage': individual.evaluation.soft.request_coverage,
            'feasible': individual.evaluation.hard.is_feasible,
            'violations': json_value(individual.evaluation.hard.violations),
            'hard_counts_by_type': json_value(individual.evaluation.hard.counts_by_type)}


def result_payload(result: EvolutionResult):
    generations = []
    for generation in result.generations:
        row = json_value(generation)
        row['self_mating_rate'] = generation.operators.self_mating_rate
        generations.append(row)
    return {
        'schema_version': 'scheduler-evolution-report-v1', 'settings': json_value(result.settings),
        'dataset_sha256': result.dataset_sha256, 'context_id': list(result.context_id),
        'policy_version': result.policy_version, 'stop_reasons': list(result.stop_reasons),
        'failure': result.failure, 'evaluation_accounting_complete': result.failure is None,
        'feasible_found': result.feasible_found,
        'best_overall': _summary(result.best_overall), 'best_feasible': _summary(result.best_feasible),
        'final_population_best': _summary(result.final_population_best),
        'final_population_size': len(result.population),
        'completed_generations': len(result.generations) - 1, 'generations': generations,
        'partial_generation': json_value(result.partial_generation),
        'evaluation_usage': {**json_value(result.evaluations), 'known_calls': result.evaluations.total,
                             'total': result.evaluations.total if result.failure is None else None,
                             'hard_calls': result.evaluations.total if result.failure is None else None,
                             'soft_calls': result.evaluations.total if result.failure is None else None},
        'timings': {'context_seconds': result.context_elapsed_seconds,
                    'initialization_seconds': result.initialization_elapsed_seconds,
                    'evolution_seconds': result.evolution_elapsed_seconds},
        'initialization_limitations': json_value(result.initialization_limitations),
    }


def write_report(result, output_path, solution_path=None):
    output_path = Path(output_path)
    solution_path = Path(solution_path) if solution_path is not None else output_path.with_name(output_path.stem + '_solutions.json')
    if output_path.resolve() == solution_path.resolve():
        raise ValueError('report and solution paths must differ')
    report = result_payload(result)
    report['solutions_path'] = str(solution_path.resolve())
    solutions = {name: solution_payload(individual, result.context_id) if individual else None
                 for name, individual in (('best_overall', result.best_overall), ('best_feasible', result.best_feasible),
                                          ('final_population_best', result.final_population_best))}
    for path, content in ((solution_path, solutions), (output_path, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    return report
