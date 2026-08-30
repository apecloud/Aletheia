"""Aletheia API server -- facade module.

The implementation was split out of this file (which used to be 12,947
lines) into aletheia/interfaces/api/{helpers,http_server,handler}.py and
aletheia/interfaces/api/repositories/*.py. This module re-exports
everything so every existing `from aletheia.interfaces.api.server import
X` caller keeps working unchanged. No behavior change from the original
monolithic file.

ReasoningEngine is imported directly here (not just transitively via
handler.py) because tests/test_reasoning_deep_graph.py does
`mock.patch("aletheia.interfaces.api.server.ReasoningEngine", ...)` --
that only affects the code path actually executing if the name is bound
in *this* module's own namespace.
"""

from aletheia.reasoning.engine import ReasoningEngine  # noqa: F401

from aletheia.interfaces.api.helpers import *  # noqa: F401,F403
from aletheia.interfaces.api.helpers import (  # noqa: F401
    _apply_edge_source_identity_presentation_guard,
    _apply_possible_duplicate_presentation_guard,
    _dedup_audit_from_payload,
    _is_current_graph_proposal,
    _knowledge_candidate_profile,
)
from aletheia.interfaces.api.http_server import LocalThreadingHTTPServer  # noqa: F401
from aletheia.interfaces.api.repositories.base import _TenantScopedEngineCache  # noqa: F401
from aletheia.interfaces.api.repositories.review import ReviewRepository  # noqa: F401
from aletheia.interfaces.api.repositories.instance import InstanceRepository  # noqa: F401
from aletheia.interfaces.api.repositories.reasoning import ReasoningRepository  # noqa: F401
from aletheia.interfaces.api.repositories.agent_gateway import AgentGatewayRepository  # noqa: F401
from aletheia.interfaces.api.handler import AletheiaServerHandler, main  # noqa: F401


if __name__ == "__main__":
    main()
