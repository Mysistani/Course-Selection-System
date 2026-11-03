"""Read-only Git snapshot of the approved engine, independent of worktree edits."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMMIT = 'd2a27231323eb10cdff00e72334fd3a4f5e72f10'


def prepare_engine(commit=DEFAULT_COMMIT):
    sha = subprocess.check_output(['git', 'rev-parse', commit+'^{commit}'], cwd=ROOT, text=True).strip()
    target = ROOT / 'outputs' / ('dataset_comparison_engine_' + sha[:12])
    paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', sha, 'src/scheduler'], cwd=ROOT, text=True).splitlines()
    if not paths:
        raise ValueError('commit has no scheduler source')
    for relative in paths:
        path = target / relative
        if not path.resolve().is_relative_to(target.resolve()):
            raise ValueError('snapshot path escaped intended output directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(['git', 'show', sha+':'+relative], cwd=ROOT))
    (target/'scripts').mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'scripts/run_dataset_comparison.py', target/'scripts/run_dataset_comparison.py')
    (target/'BASELINE.json').write_text(json.dumps({'commit':sha,'scope':'tracked scheduler source; experimental scripts copied separately'}),encoding='utf-8')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--engine-commit', default=DEFAULT_COMMIT)
    args, rest = parser.parse_known_args()
    target = prepare_engine(args.engine_commit)
    command = [sys.executable, str(target/'scripts/run_dataset_comparison.py'),
        '--dataset-a',str(ROOT/'data/UniversityDataset_Corrected.xlsx'),
        '--dataset-b',str(ROOT/'data/Dataset_B_Constructive_seed20261103.xlsx'),
        '--output',str(ROOT/'outputs/DATASET_COMPARISON_PINNED.json'),*rest]
    return subprocess.call(command,cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
