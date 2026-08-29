"""Shared sys.path bootstrap for the test suite. This package's __init__ runs
before any test module is imported (discover/dotted-name/file-path
invocations of `python -m unittest` all import `tests` first) so every test
module gets scripts/ on sys.path for the handful of operational scripts
(relation_catalog, hotpotqa_entity_ids, etc.) that tests still import
flat -- previously 9 files each duplicated their own inconsistent version
of this. Everything under the aletheia package is resolved via the
editable install (`pip install -e .`) instead, so agents/, db/, and llm/
no longer need to be on sys.path. Note this project runs tests via
unittest, not pytest, so a conftest.py would silently never execute; this
__init__.py is the correct place for it.

Appended (not inserted at index 0): unittest's own discover/file-based
module loading overwrites sys.path[0] while importing each individual
test file, so anything placed there gets silently clobbered before it's
ever used -- this bit us directly during this migration (previously
agents/db/llm/scripts were all inserted at 0, and scripts/ happened to
survive at a safe non-zero index only because of the other now-removed
inserts pushing it down).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "scripts"))
