"""Run all repository tests against the approved engine plus new experiment modules."""
from pathlib import Path
import sys
import unittest

from run_pinned_dataset_comparison import prepare_engine

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    target = prepare_engine()
    sys.path.insert(0, str(target/'src'))
    import scheduler
    # Approved modules resolve first; only newly added modules come from worktree.
    scheduler.__path__.append(str(ROOT/'src/scheduler'))
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    outcome = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
