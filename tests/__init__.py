"""Shared sys.path bootstrap for the test suite. This package's __init__ runs
before any test module is imported (discover/dotted-name/file-path
invocations of `python -m unittest` all import `tests` first) so every test
module gets the repo root plus agents/ and scripts/ on sys.path -- previously
9 files each duplicated their own inconsistent version of this. Note this
project runs tests via unittest, not pytest, so a conftest.py would silently
never execute; this __init__.py is the correct place for it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, ROOT / "agents", ROOT / "scripts"):
    sys.path.insert(0, str(_path))
