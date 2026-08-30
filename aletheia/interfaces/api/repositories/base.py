"""_TenantScopedEngineCache, extracted from server.py -- shared base for
ReviewRepository, InstanceRepository, ReasoningRepository, AgentGatewayRepository."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import sessionmaker
from aletheia.ontology.store import ensure_artifact_schema, upsert_artifact
from aletheia.graph_store.nebula_client import NebulaGraphClient, insert_with_schema_retry
from aletheia.enrichment.schema_sync import sync_tenant_schema
from aletheia.interfaces.api.helpers import _safe_error_message


class _TenantScopedEngineCache:
    """Shared tenant()/metadata_engine_for() behavior for the 4 repository
    classes below. Caches one SQLAlchemy engine per metadata_db_url, guarded
    by a lock since the server runs under ThreadingHTTPServer (each request
    is a new thread) and an unlocked read-check-then-write on the cache dict
    is a real race.

    _register_tenant_on_engine_create controls whether creating an engine
    also calls tenant_registry.ensure_metadata() (a Postgres-flavored
    CREATE TABLE/upsert against aletheia_tenants). InstanceRepository sets
    this False: its tests construct it directly against sqlite metadata_db_url
    fixtures, and ensure_metadata's DDL (NOW() default) isn't SQLite-portable."""

    _register_tenant_on_engine_create = True

    def __init__(self, tenant_registry, ensure_schema=False):
        self.tenant_registry = tenant_registry
        self.ensure_schema = ensure_schema
        self._metadata_engines = {}
        self._metadata_engines_lock = threading.Lock()

    def tenant(self, tenant_id=None):
        return self.tenant_registry.get(tenant_id)

    def metadata_engine_for(self, tenant):
        engine = self._metadata_engines.get(tenant.metadata_db_url)
        if engine is not None:
            return engine
        with self._metadata_engines_lock:
            engine = self._metadata_engines.get(tenant.metadata_db_url)
            if engine is None:
                engine = create_engine(tenant.metadata_db_url)
                if self.ensure_schema:
                    ensure_artifact_schema(engine)
                if self._register_tenant_on_engine_create:
                    self.tenant_registry.ensure_metadata(engine)
                self._metadata_engines[tenant.metadata_db_url] = engine
        return engine

    def _sync_graph_native_schema(self, tenant) -> None:
        """Approving a graph-native node/edge type (agents/graph_ontology_registry.py,
        source_agent="GraphNativeTypeRegistrar" or "DeepResearchOntologyExpansion")
        only flips its Postgres status -- the Nebula TAG/EDGE TYPE it needs
        may not exist yet if approval happens well after the run that
        proposed it (a "review_required" import already creates draft-status
        DDL too, see graph_schema_sync.sync_tenant_schema's include_draft,
        but that doesn't help a type approved by a LATER review session
        against an already-finished import/run). Re-running sync here is a
        plain CREATE ... IF NOT EXISTS, safe to call on every approval
        regardless of whether the DDL already exists. Best-effort: a
        transient Nebula outage must not make the approval itself fail -- the
        Postgres status change already committed by the caller; the next
        approval, or the tenant's own next import run, will retry this sync.
        Shared across ReviewRepository (OntologyArtifact review) and
        InstanceRepository (DeepResearchOntologyExpansion proposal review) --
        both need the exact same "approve -> make sure the DDL exists" step.

        ``propagation_sleep_seconds=0``: ``sync_tenant_schema``'s default
        11s sleep exists so a caller that immediately starts INSERTing
        vertices/edges right after (e.g. the import scripts) doesn't race
        Nebula's meta service propagating the new schema. Nothing here
        inserts data right after this call -- the review UI's HTTP request
        would otherwise block for 11+ seconds per newly-created TAG/EDGE
        TYPE for no benefit, measured directly at 26s wall-clock for a
        single approve action that created one new TAG (verified against a
        live Nebula cluster: the resulting TAG exists and is immediately
        queryable well within that default sleep window regardless)."""
        try:
            client = NebulaGraphClient(
                ip=tenant.graph_ip, port=tenant.graph_port, user=tenant.graph_user,
                password=tenant.graph_password, space=tenant.graph_database,
            )
            client.connect()
            try:
                session = sessionmaker(bind=self.metadata_engine_for(tenant))()
                try:
                    sync_tenant_schema(session, client, tenant.tenant_id, propagation_sleep_seconds=0)
                finally:
                    session.close()
            finally:
                client.close()
        except Exception as exc:
            print(f"[{type(self).__name__}] Nebula schema sync failed for tenant {tenant.tenant_id}: {_safe_error_message(exc)}")
