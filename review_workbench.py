"""Compatibility launcher for the Aletheia workbench server.

The implementation lives in `server.workbench_server`. Keep this shim so
existing scripts, tests, and operator habits that run `review_workbench.py`
continue to work while the server entry is renamed.
"""

from server.workbench_server import *  # noqa: F401,F403
from server.workbench_server import main


if __name__ == "__main__":
    main()
