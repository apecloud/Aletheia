"""ReasoningRepository, extracted from server.py. No behavior change.

Originally a single ~3600-line module; split into this package (schema,
mappers, tasks, autopilot, findings_registry, findings_workflow, traversal)
one mixin per concern, composed below. Two deliberate, called-out refactors
happened during the split -- review_autopilot_candidate was decomposed into a
dispatcher plus per-decision branch methods (see autopilot.py), and
run_scoped_graph_task was rewritten as a thin wrapper over
run_scoped_graph_task_streaming instead of duplicating its control flow (see
traversal.py) -- everything else moved verbatim.
"""

from aletheia.interfaces.api.repositories.base import _TenantScopedEngineCache
from aletheia.interfaces.api.repositories.reasoning.schema import SchemaMixin
from aletheia.interfaces.api.repositories.reasoning.mappers import MappersMixin
from aletheia.interfaces.api.repositories.reasoning.tasks import TasksMixin
from aletheia.interfaces.api.repositories.reasoning.autopilot import AutopilotMixin
from aletheia.interfaces.api.repositories.reasoning.findings_registry import FindingsRegistryMixin
from aletheia.interfaces.api.repositories.reasoning.findings_workflow import FindingsWorkflowMixin
from aletheia.interfaces.api.repositories.reasoning.traversal import TraversalMixin


class ReasoningRepository(
    SchemaMixin, MappersMixin, TasksMixin, AutopilotMixin,
    FindingsRegistryMixin, FindingsWorkflowMixin, TraversalMixin,
    _TenantScopedEngineCache,
):
    def __init__(self, tenant_registry, instance_repository, ensure_schema=False):
        super().__init__(tenant_registry, ensure_schema)
        self.instance_repository = instance_repository
        self._autopilot_schema_ready = set()
        self._finding_experience_schema_ready = set()
