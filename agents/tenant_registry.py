import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, text


def default_graph_ip() -> str:
    return os.environ.get("ALETHEIA_GRAPH_IP", "127.0.0.1")


def default_graph_port() -> int:
    return int(os.environ.get("ALETHEIA_GRAPH_PORT", "9669"))


def default_graph_user() -> str:
    return os.environ.get("ALETHEIA_GRAPH_USER", "root")


def default_graph_password() -> str:
    return os.environ.get("ALETHEIA_GRAPH_PASSWORD", "nebula")


def default_metadata_db_url() -> str:
    return os.environ.get(
        "ALETHEIA_PG_URL",
        "postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/"
        f"{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}",
    )


def default_source_db_url() -> str:
    return os.environ.get(
        "ALETHEIA_MYSQL_URL",
        "mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/"
        f"{os.environ.get('ALETHEIA_MYSQL_DB', 'aletheia_test_data')}",
    )


def default_graph_database() -> str:
    return os.environ.get("ALETHEIA_GRAPH_SPACE", "aletheia")


@dataclass(frozen=True)
class TenantConfig:
    tenant_id: str
    namespace: str
    display_name: str
    graph_database: str
    metadata_db_url: str
    source_db_url: str
    status: str = "active"
    # graph_database doubles as the Nebula space name -- every tenant is
    # graph-native (the SQL retrieval engine was retired), so there's no
    # backend distinction left to encode here. relation_catalog_scope
    # defaults to tenant_id when unset. Node/edge TYPES are no longer fixed
    # per tenant (graph_tag_name/graph_edge_type/graph_object_type) -- they
    # come from the tenant's approved ontology registry
    # (agents/graph_ontology_registry.py) and each vertex/edge's real Nebula
    # tag/edge-type name, not a single hardcoded name pair.
    graph_ip: str = field(default_factory=default_graph_ip)
    graph_port: int = field(default_factory=default_graph_port)
    graph_user: str = field(default_factory=default_graph_user)
    graph_password: str = field(default_factory=default_graph_password)
    relation_catalog_scope: str = ""

    def public_dict(self) -> dict:
        data = asdict(self)
        data.pop("metadata_db_url", None)
        data.pop("source_db_url", None)
        data.pop("graph_password", None)
        return data


class TenantRegistry:
    def __init__(self, tenants: list[TenantConfig], default_tenant_id: str | None = None):
        if not tenants:
            raise ValueError("at least one tenant is required")
        self.tenants = {tenant.tenant_id: tenant for tenant in tenants}
        self.default_tenant_id = default_tenant_id or tenants[0].tenant_id
        if self.default_tenant_id not in self.tenants:
            raise ValueError(f"default tenant not found: {self.default_tenant_id}")

    @classmethod
    def load(cls, config_path: str | None = None) -> "TenantRegistry":
        config = cls._load_raw_config(config_path)
        metadata_url = default_metadata_db_url()
        source_url = default_source_db_url()
        graph_database = default_graph_database()
        if config:
            tenants = [
                TenantConfig(
                    tenant_id=item["tenant_id"],
                    namespace=item.get("namespace") or item["tenant_id"],
                    display_name=item.get("display_name") or item.get("namespace") or item["tenant_id"],
                    graph_database=item.get("graph_database") or graph_database,
                    metadata_db_url=item.get("metadata_db_url") or metadata_url,
                    source_db_url=item.get("source_db_url") or source_url,
                    status=item.get("status", "active"),
                    graph_ip=item.get("graph_ip") or default_graph_ip(),
                    graph_port=item.get("graph_port") or default_graph_port(),
                    graph_user=item.get("graph_user") or default_graph_user(),
                    graph_password=item.get("graph_password") or default_graph_password(),
                    relation_catalog_scope=item.get("relation_catalog_scope") or item["tenant_id"],
                )
                for item in config.get("tenants", [])
            ]
            tenants = cls._merge_metadata_tenants(tenants, metadata_url, source_url, graph_database)
            return cls(tenants, config.get("default_tenant") or os.environ.get("ALETHEIA_TENANT"))

        # No config/tenants.json and no ALETHEIA_TENANTS_FILE/_JSON override --
        # fall back to the two graph-native tenants built this session
        # (scripts/import_hotpotqa_nebula_tenant.py, scripts/import_webqsp_graph_tenant.py)
        # rather than the retired SQL-backed Northwind demo tenants.
        hotpotqa_tenant = TenantConfig(
            tenant_id=os.environ.get("ALETHEIA_TENANT", "hotpotqa-graph-v1"),
            namespace="hotpotqa_graph_v1",
            display_name="HotpotQA (Nebula graph-native)",
            graph_database=os.environ.get("ALETHEIA_GRAPH_SPACE", "hotpotqa_kg"),
            metadata_db_url=metadata_url,
            source_db_url=source_url,
            relation_catalog_scope="hotpotqa",
        )
        webqsp_tenant = TenantConfig(
            tenant_id="webqsp-graph-v1",
            namespace="webqsp_graph_v1",
            display_name="WebQSP (Nebula graph-native)",
            graph_database="webqsp_kg",
            metadata_db_url=metadata_url,
            source_db_url=source_url,
            relation_catalog_scope="webqsp",
        )
        tenants = cls._merge_metadata_tenants([hotpotqa_tenant, webqsp_tenant], metadata_url, source_url, graph_database)
        return cls(tenants, hotpotqa_tenant.tenant_id)

    @staticmethod
    def _load_raw_config(config_path: str | None) -> dict | None:
        raw = os.environ.get("ALETHEIA_TENANTS_JSON")
        if raw:
            return json.loads(raw)
        path = config_path or os.environ.get("ALETHEIA_TENANTS_FILE")
        if path and Path(path).is_file():
            return json.loads(Path(path).read_text(encoding="utf-8"))
        default_path = Path(__file__).resolve().parents[1] / "config" / "tenants.json"
        if default_path.is_file():
            return json.loads(default_path.read_text(encoding="utf-8"))
        return None

    @staticmethod
    def _merge_metadata_tenants(
        tenants: list[TenantConfig],
        metadata_url: str,
        source_url: str,
        graph_database: str,
    ) -> list[TenantConfig]:
        merged = {tenant.tenant_id: tenant for tenant in tenants}
        try:
            engine = create_engine(metadata_url)
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT tenant_id, namespace, display_name, graph_database, status
                        FROM aletheia_tenants
                        WHERE status = 'active'
                        ORDER BY tenant_id
                        """
                    )
                ).mappings().all()
        except Exception:
            return tenants
        for row in rows:
            tenant_id = row["tenant_id"]
            if tenant_id in merged:
                continue
            merged[tenant_id] = TenantConfig(
                tenant_id=tenant_id,
                namespace=row.get("namespace") or tenant_id,
                display_name=row.get("display_name") or tenant_id,
                graph_database=row.get("graph_database") or graph_database,
                metadata_db_url=metadata_url,
                source_db_url=source_url,
                status=row.get("status") or "active",
            )
        return list(merged.values())

    def get(self, tenant_id: str | None) -> TenantConfig:
        resolved = tenant_id or self.default_tenant_id
        tenant = self.tenants.get(resolved)
        if not tenant:
            raise KeyError(resolved)
        if tenant.status != "active":
            raise ValueError(f"tenant is not active: {resolved}")
        return tenant

    def list_public(self) -> list[dict]:
        return [tenant.public_dict() for tenant in self.tenants.values()]

    def ensure_metadata(self, engine) -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS aletheia_tenants (
                        tenant_id VARCHAR(255) PRIMARY KEY,
                        namespace VARCHAR(255) NOT NULL,
                        display_name VARCHAR(255) NOT NULL,
                        graph_database VARCHAR(255) NOT NULL,
                        status VARCHAR(50) NOT NULL DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
            )
            for tenant in self.tenants.values():
                conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_tenants
                        (tenant_id, namespace, display_name, graph_database, status, created_at, updated_at)
                        VALUES (:tenant_id, :namespace, :display_name, :graph_database, :status, NOW(), NOW())
                        ON CONFLICT (tenant_id) DO UPDATE SET
                          namespace = EXCLUDED.namespace,
                          display_name = EXCLUDED.display_name,
                          graph_database = EXCLUDED.graph_database,
                          status = EXCLUDED.status,
                          updated_at = NOW()
                        """
                    ),
                    tenant.public_dict(),
                )
