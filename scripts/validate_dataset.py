"""Run from any working directory with the project's Python sources."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from scheduler.__main__ import main

if __name__ == '__main__':
    if len(sys.argv) == 1 or sys.argv[1].startswith('--'):
        sys.argv.insert(1, str(ROOT / 'data/UniversityDataset_Corrected.xlsx'))
    sys.exit(main())
