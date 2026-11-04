"""Run the complete test suite from any working directory.

This entry point keeps the source tree compact without installing the GUI as a
Python package. A missing/empty test directory is an error, never a green run.
"""
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
for directory in reversed((ROOT/'src', ROOT/'tests', ROOT/'packaging', ROOT)):
    sys.path.insert(0, str(directory))


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'), pattern='test_*.py')
    if not suite.countTestCases():
        raise RuntimeError('No tests were discovered in tests/.')
    return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
