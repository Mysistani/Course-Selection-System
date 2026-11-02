"""python -m scheduler [workbook] [--output report.json]"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .dataset import DatasetError, load_dataset
from .policies import DEFAULT_POLICY


def main():
    parser = argparse.ArgumentParser(description='Validate scheduler input without changing Excel.')
    parser.add_argument('workbook', nargs='?', default='data/UniversityDataset_Corrected.xlsx')
    parser.add_argument('--output')
    parser.add_argument('--diagnostics', action='store_true', help='Include optional sample feasibility diagnostics.')
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.workbook, include_feasibility_diagnostics=args.diagnostics)
        report = {'valid_input': True, 'schedule_feasibility': 'not_evaluated',
                  'diagnostics_included': args.diagnostics,
                  'sha256': dataset.sha256, 'rows': {k: len(v) for k, v in dataset.tables.items()},
                  'warnings': [asdict(w) for w in dataset.warnings], 'policy': asdict(DEFAULT_POLICY)}
        status = 0
    except DatasetError as exc:
        report = {'valid_input': False, 'errors': [asdict(e) for e in exc.issues]}
        status = 1
    text = json.dumps(report, ensure_ascii=True, indent=2)
    if args.output:
        target = Path(args.output)
        if target.resolve() == Path(args.workbook).resolve():
            parser.error('output must not overwrite input workbook')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + '\n', encoding='utf-8')
    print(text)
    return status


if __name__ == '__main__':
    sys.exit(main())
