"""Compatibility launcher for the Aletheia server.

The implementation lives in `aletheia.interfaces.api.server`. Keep this
shim so existing scripts, tests, and operator habits that run
`review_workbench.py` continue to work while the server entry is renamed.
"""

from aletheia.interfaces.api.server import *  # noqa: F401,F403
from aletheia.interfaces.api.server import main


if __name__ == "__main__":
    main()
