"""Generate Dataset C; fail closed before publishing a feasibility certificate."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from scheduler.university_dataset import DEFAULT_SEED, create_dataset_c


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'data')
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    for path in create_dataset_c(args.output_dir, args.seed, preview=args.preview):
        print(path)
