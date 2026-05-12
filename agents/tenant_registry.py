import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import text


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

    def public_dict(self) -> dict:
        data = asdict(self)
        data.pop("metadata_db_url", None)
        data.pop("source_db_url", None)
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
                )
                for item in config.get("tenants", [])
            ]
            return cls(tenants, config.get("default_tenant") or os.environ.get("ALETHEIA_TENANT"))

        default_id = os.environ.get("ALETHEIA_TENANT", "default")
        default_namespace = os.environ.get("ALETHEIA_NAMESPACE", "northwind")
        default_display = os.environ.get("ALETHEIA_TENANT_DISPLAY", "Northwind Demo")
        default_tenant = TenantConfig(
            tenant_id=default_id,
            namespace=default_namespace,
            display_name=default_display,
            graph_database=graph_database,
            metadata_db_url=metadata_url,
            source_db_url=source_url,
        )
        sandbox_tenant = TenantConfig(
            tenant_id=os.environ.get("ALETHEIA_SANDBOX_TENANT", "northwind-sandbox"),
            namespace=os.environ.get("ALETHEIA_SANDBOX_NAMESPACE", "northwind_sandbox"),
            display_name=os.environ.get("ALETHEIA_SANDBOX_DISPLAY", "Northwind Sandbox"),
            graph_database=os.environ.get("ALETHEIA_SANDBOX_GRAPH_SPACE", f"{graph_database}_sandbox"),
            metadata_db_url=metadata_url,
            source_db_url=source_url,
        )
        return cls([default_tenant, sandbox_tenant], default_id)

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
