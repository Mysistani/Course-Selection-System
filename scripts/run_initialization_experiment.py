"""Non-interactive Phase 4 experiment; writes statistics without editing Excel."""
import argparse
from dataclasses import asdict, fields
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from scheduler import DatasetError, load_dataset
from scheduler.evaluation_context import EvaluationContext
from scheduler.initialization import InitializationSettings, initialize_population


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', type=Path, default=ROOT / 'data/UniversityDataset_Corrected.xlsx')
    parser.add_argument('--population-size', type=int, default=100)
    parser.add_argument('--seed', type=int, default=2026)
    parser.add_argument('--max-attempts', type=int, default=8)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/INITIALIZATION_REPORT.json')
    args = parser.parse_args()
    if args.output.resolve() == args.workbook.resolve():
        parser.error('output cannot overwrite the workbook')
    try:
        dataset = load_dataset(args.workbook)
        started = perf_counter()
        context = EvaluationContext.from_dataset(dataset)
        context_seconds = perf_counter() - started
        settings = InitializationSettings(max_attempts_per_individual=args.max_attempts)
        result = initialize_population(context, args.population_size, args.seed, settings)
    except (DatasetError, ValueError) as exc:
        parser.error(str(exc))
    statistics = {f.name: getattr(result.statistics, f.name) for f in fields(result.statistics)}
    statistics['strategy_counts'] = dict(result.statistics.strategy_counts)
    statistics['issue_counts'] = dict(result.statistics.issue_counts)
    report = {
        'source': args.workbook.name, 'source_sha256': dataset.sha256,
        'context_id': context.context_id, 'policy_version': context.policy.version,
        'random_seed': result.random_seed, 'settings': asdict(result.settings),
        'context_preparation_seconds': context_seconds,
        'statistics': statistics, 'diversity': asdict(result.diversity),
        'limitations': [asdict(issue) for issue in result.limitations],
        'individuals': [{'index': index, 'fingerprint': individual.fingerprint,
                         'strategy': individual.strategy, 'attempts': individual.attempts,
                         'hard_penalty': individual.evaluation.hard.total_penalty,
                         'soft_score': individual.evaluation.soft.total_score,
                         'coverage': individual.evaluation.soft.request_coverage,
                         'active_sections': sum(s.active for s in individual.chromosome.sections.values()),
                         'unassigned_requests': len(individual.evaluation.hard.metrics.unassigned_requests),
                         'issues': [asdict(issue) for issue in individual.issues]}
                        for index, individual in enumerate(result.individuals)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(args.output), 'statistics': statistics,
                      'diversity': report['diversity']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
