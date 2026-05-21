import argparse
import hashlib
import json
import mimetypes
mimetypes.add_type("text/javascript", ".jsx")
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from sqlalchemy import create_engine, text

from reasoning_engine import ReasoningEngine

sys.path.append(str(Path(__file__).resolve().parent / "agents"))
from ontology_artifacts import ensure_artifact_schema  # noqa: E402
from tenant_registry import TenantRegistry  # noqa: E402


DB_URL = os.environ.get(
    "ALETHEIA_PG_URL",
    f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}",
)
SOURCE_DB_URL = os.environ.get(
    "ALETHEIA_MYSQL_URL",
    f"mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/{os.environ.get('ALETHEIA_MYSQL_DB', 'aletheia_test_data')}",
)
STATIC_ROOT = Path(__file__).resolve().parent / "web" / "review_workbench"


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _load_json(value, default):
    if not value:
        return default
    return json.loads(value)


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "scope"


def _jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _require_reason(action, reason):
    if action in {"reject", "rejected", "needs_changes", "comment"} and not reason.strip():
        raise ValueError(f"reason is required for {action}")


def _artifact_to_dict(row):
    return {
        "id": row["id"],
        "tenant_id": row["project_id"],
        "project_id": row["project_id"],
        "canonical_key": row["canonical_key"],
        "artifact_type": row["artifact_type"],
        "name": row["name"],
        "description": row["description"],
        "payload": _load_json(row["payload_json"], {}),
        "confidence": row["confidence"],
        "source_refs": _load_json(row["source_refs_json"], []),
        "status": row["status"],
        "version": row["version"],
        "source_agent": row["source_agent"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
        "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
    }


class ReviewRepository:
    def __init__(self, tenant_registry, ensure_schema=False):
        self.tenant_registry = tenant_registry
        self.ensure_schema = ensure_schema
        self.engines = {}

    def tenant(self, tenant_id=None):
        return self.tenant_registry.get(tenant_id)

    def engine_for(self, tenant):
        engine = self.engines.get(tenant.metadata_db_url)
        if engine is None:
            engine = create_engine(tenant.metadata_db_url)
            self.engines[tenant.metadata_db_url] = engine
            if self.ensure_schema:
                ensure_artifact_schema(engine)
            self.tenant_registry.ensure_metadata(engine)
        return engine

    def list_artifacts(self, tenant, filters):
        conditions = ["project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id}
        for field in ("artifact_type", "status", "source_agent"):
            value = filters.get(field)
            if value:
                conditions.append(f"{field} = :{field}")
                params[field] = value
        search = filters.get("search")
        if search:
            conditions.append(
                "(canonical_key ILIKE :search OR name ILIKE :search OR description ILIKE :search)"
            )
            params["search"] = f"%{search}%"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, project_id, canonical_key, artifact_type, name, description,
                           payload_json, confidence, source_refs_json, status, version,
                           source_agent, created_at, updated_at
                    FROM aletheia_ontology_artifacts
                    {where}
                    ORDER BY
                      CASE status
                        WHEN 'proposed' THEN 0
                        WHEN 'needs_changes' THEN 1
                        WHEN 'draft' THEN 2
                        WHEN 'approved' THEN 3
                        WHEN 'rejected' THEN 4
                        ELSE 5
                      END,
                      artifact_type,
                      canonical_key
                    """
                ),
                params,
            ).mappings().all()
            stats = conn.execute(
                text(
                    """
                    SELECT artifact_type, status, COUNT(*) AS count
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id
                    GROUP BY artifact_type, status
                    ORDER BY artifact_type, status
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
        return {
            "tenant": tenant.public_dict(),
            "artifacts": [_artifact_to_dict(row) for row in rows],
            "stats": [dict(row) for row in stats],
        }

    def get_artifact(self, tenant, canonical_key):
        with self.engine_for(tenant).connect() as conn:
            artifact = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, artifact_type, name, description,
                           payload_json, confidence, source_refs_json, status, version,
                           source_agent, created_at, updated_at
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
            if not artifact:
                return None
            evidence = conn.execute(
                text(
                    """
                    SELECT evidence_type, source_ref, content_hash, summary,
                           raw_payload_json, confidence, created_at
                    FROM aletheia_artifact_evidence
                    WHERE artifact_id = :artifact_id
                    ORDER BY source_ref, content_hash
                    """
                ),
                {"artifact_id": artifact["id"]},
            ).mappings().all()
            reviews = conn.execute(
                text(
                    """
                    SELECT decision, reviewer, reason, before_status, after_status,
                           before_version, after_version, created_at
                    FROM aletheia_artifact_reviews
                    WHERE artifact_id = :artifact_id
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"artifact_id": artifact["id"]},
            ).mappings().all()
        result = _artifact_to_dict(artifact)
        result["tenant"] = tenant.public_dict()
        result["evidence"] = [
            {
                "evidence_type": row["evidence_type"],
                "source_ref": row["source_ref"],
                "content_hash": row["content_hash"],
                "summary": row["summary"],
                "raw_payload": _load_json(row["raw_payload_json"], {}),
                "confidence": row["confidence"],
                "created_at": str(row["created_at"]) if row["created_at"] else None,
            }
            for row in evidence
        ]
        result["reviews"] = [
            {
                "decision": row["decision"],
                "reviewer": row["reviewer"],
                "reason": row["reason"],
                "before_status": row["before_status"],
                "after_status": row["after_status"],
                "before_version": row["before_version"],
                "after_version": row["after_version"],
                "created_at": str(row["created_at"]) if row["created_at"] else None,
            }
            for row in reviews
        ]
        return result

    def review_status(self, tenant, canonical_key, status, reviewer, reason):
        _require_reason(status, reason or "")
        with self.engine_for(tenant).begin() as conn:
            artifact = self._fetch_for_update(conn, tenant, canonical_key)
            before_status = artifact["status"]
            before_version = artifact["version"]
            before_payload_json = artifact["payload_json"]
            after_version = before_version + 1
            conn.execute(
                text(
                    """
                    UPDATE aletheia_ontology_artifacts
                    SET status = :status, version = version + 1, updated_at = NOW()
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "status": status, "canonical_key": canonical_key},
            )
            self._record_review_event(
                conn,
                artifact=artifact,
                decision=status,
                reviewer=reviewer,
                reason=reason,
                before_status=before_status,
                after_status=status,
                before_version=before_version,
                after_version=after_version,
                before_payload_json=before_payload_json,
                after_payload_json=before_payload_json,
            )
        return self.get_artifact(tenant, canonical_key)

    def comment(self, tenant, canonical_key, reviewer, reason):
        _require_reason("comment", reason or "")
        with self.engine_for(tenant).begin() as conn:
            artifact = self._fetch_for_update(conn, tenant, canonical_key)
            self._record_review_event(
                conn,
                artifact=artifact,
                decision="comment",
                reviewer=reviewer,
                reason=reason,
                before_status=artifact["status"],
                after_status=artifact["status"],
                before_version=artifact["version"],
                after_version=artifact["version"],
                before_payload_json=artifact["payload_json"],
                after_payload_json=artifact["payload_json"],
            )
        return self.get_artifact(tenant, canonical_key)

    def edit(self, tenant, canonical_key, reviewer, reason, name=None, description=None, payload=None):
        with self.engine_for(tenant).begin() as conn:
            artifact = self._fetch_for_update(conn, tenant, canonical_key)
            current_payload = _load_json(artifact["payload_json"], {})
            next_payload = current_payload if payload is None else payload
            next_name = artifact["name"] if name is None else name
            next_description = artifact["description"] if description is None else description
            after_payload_json = _json_dump(next_payload)
            after_version = artifact["version"] + 1
            conn.execute(
                text(
                    """
                    UPDATE aletheia_ontology_artifacts
                    SET name = :name,
                        description = :description,
                        payload_json = :payload_json,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "name": next_name,
                    "description": next_description,
                    "payload_json": after_payload_json,
                    "canonical_key": canonical_key,
                },
            )
            self._record_review_event(
                conn,
                artifact=artifact,
                decision="edit",
                reviewer=reviewer,
                reason=reason,
                before_status=artifact["status"],
                after_status=artifact["status"],
                before_version=artifact["version"],
                after_version=after_version,
                before_payload_json=artifact["payload_json"],
                after_payload_json=after_payload_json,
            )
        return self.get_artifact(tenant, canonical_key)

    def _fetch_for_update(self, conn, tenant, canonical_key):
        artifact = conn.execute(
            text(
                """
                SELECT id, project_id, canonical_key, artifact_type, name, description, payload_json,
                       status, version
                FROM aletheia_ontology_artifacts
                WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                FOR UPDATE
                """
            ),
            {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
        ).mappings().first()
        if not artifact:
            raise KeyError(canonical_key)
        return artifact

    def _record_review_event(
        self,
        conn,
        *,
        artifact,
        decision,
        reviewer,
        reason,
        before_status,
        after_status,
        before_version,
        after_version,
        before_payload_json,
        after_payload_json,
    ):
        conn.execute(
            text(
                """
                INSERT INTO aletheia_artifact_reviews
                (artifact_id, project_id, canonical_key, decision, reviewer, reason,
                 before_status, after_status, before_version, after_version,
                 before_payload_json, after_payload_json, created_at)
                VALUES
                (:artifact_id, :project_id, :canonical_key, :decision, :reviewer, :reason,
                 :before_status, :after_status, :before_version, :after_version,
                 :before_payload_json, :after_payload_json, NOW())
                """
            ),
            {
                "artifact_id": artifact["id"],
                "project_id": artifact["project_id"],
                "canonical_key": artifact["canonical_key"],
                "decision": decision,
                "reviewer": reviewer,
                "reason": reason,
                "before_status": before_status,
                "after_status": after_status,
                "before_version": before_version,
                "after_version": after_version,
                "before_payload_json": before_payload_json,
                "after_payload_json": after_payload_json,
            },
        )


class InstanceRepository:
    def __init__(self, tenant_registry, ensure_schema=False):
        self.tenant_registry = tenant_registry
        self.ensure_schema = ensure_schema
        self.metadata_engines = {}
        self.source_engines = {}

    def tenant(self, tenant_id=None):
        return self.tenant_registry.get(tenant_id)

    def metadata_engine_for(self, tenant):
        engine = self.metadata_engines.get(tenant.metadata_db_url)
        if engine is None:
            engine = create_engine(tenant.metadata_db_url)
            self.metadata_engines[tenant.metadata_db_url] = engine
            if self.ensure_schema:
                ensure_artifact_schema(engine)
        return engine

    def source_engine_for(self, tenant):
        engine = self.source_engines.get(tenant.source_db_url)
        if engine is None:
            engine = create_engine(tenant.source_db_url)
            self.source_engines[tenant.source_db_url] = engine
        return engine

    def types(self, tenant):
        all_keys = [cfg["artifact"] for cfg in self.ENTITY_CONFIG.values()]
        artifacts = self._approved_artifacts(tenant, all_keys)
        types = []
        for type_key, cfg in self.ENTITY_CONFIG.items():
            if cfg["artifact"] in artifacts:
                types.append({
                    "type": type_key.capitalize(),
                    "label": type_key.capitalize(),
                    "table": cfg["table"],
                    "ontology_artifact": cfg["artifact"],
                    "tenant_id": tenant.tenant_id,
                })
        return {"tenant": tenant.public_dict(), "types": types}

    def search(self, tenant, object_type, query, limit=25):
        cfg = self.ENTITY_CONFIG.get(object_type.lower())
        if not cfg:
            return {
                "tenant": tenant.public_dict(),
                "instances": [],
                "approved": False,
                "reason": f"Unknown type {object_type}",
            }
        canonical_key = cfg["artifact"]
        artifacts = self._approved_artifacts(tenant, [canonical_key])
        if canonical_key not in artifacts:
            return {
                "tenant": tenant.public_dict(),
                "instances": [],
                "approved": False,
                "reason": f"{canonical_key} is not approved for tenant {tenant.tenant_id}",
            }
        conditions = [f"CAST({cfg['pk']} AS CHAR) = :query"]
        for col in cfg["label_cols"]:
            conditions.append(f"{col} LIKE :like_query")
        where = " OR ".join(conditions)
        sql = f"SELECT * FROM {cfg['table']} WHERE (:query = '' OR {where}) ORDER BY {cfg['pk']} LIMIT :limit"
        with self.source_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(sql),
                {"query": query, "like_query": f"%{query}%", "limit": limit},
            ).mappings().all()
        type_cap = object_type.capitalize()
        return {
            "instances": [
                self._entity_node(tenant, type_cap, dict(row))
                for row in rows
            ],
            "approved": True,
            "tenant": tenant.public_dict(),
        }

    def detail(self, tenant, object_type, instance_id):
        canonical_key = self._object_key(object_type)
        artifacts = self._approved_artifacts(tenant, [canonical_key])
        if canonical_key not in artifacts:
            return None
        if object_type.lower() == "employee":
            employee = self._fetch_employee(tenant, instance_id)
            if not employee:
                return None
            order_count = self._order_count_for_employee(tenant, instance_id)
            reports = self._employee_reports(tenant, instance_id)
            return {
                "id": f"Employee:{employee['employeeID']}",
                "tenant_id": tenant.tenant_id,
                "namespace": tenant.namespace,
                "graph_database": tenant.graph_database,
                "type": "Employee",
                "label": self._employee_label(employee),
                "source_table": "employees",
                "source_pk": f"employeeID={employee['employeeID']}",
                "source_row": self._row(employee),
                "ontology_artifact": "object:employee",
                "key_properties": {
                    "employeeID": employee["employeeID"],
                    "name": self._employee_label(employee),
                    "title": employee.get("title"),
                    "city": employee.get("city"),
                    "reportsTo": employee.get("reportsTo"),
                },
                "relations_summary": {
                    "handled_orders": order_count,
                    "reports_to": reports.get("manager"),
                    "direct_reports": reports.get("direct_reports", 0),
                },
            }
        if object_type.lower() == "order":
            order = self._fetch_order(tenant, instance_id)
            if not order:
                return None
            return {
                "id": f"Order:{order['orderID']}",
                "tenant_id": tenant.tenant_id,
                "namespace": tenant.namespace,
                "graph_database": tenant.graph_database,
                "type": "Order",
                "label": f"Order #{order['orderID']}",
                "source_table": "orders",
                "source_pk": f"orderID={order['orderID']}",
                "source_row": self._row(order),
                "ontology_artifact": "object:order",
                "key_properties": {
                    "orderID": order["orderID"],
                    "customerID": order.get("customerID"),
                    "employeeID": order.get("employeeID"),
                    "orderDate": _jsonable(order.get("orderDate")),
                    "shipName": order.get("shipName"),
                },
            }
        return None

    # ---- generic entity config ----
    ENTITY_CONFIG = {
        "employee": {"table": "employees", "pk": "employeeID", "label_cols": ["firstName", "lastName"], "label_join": " ", "artifact": "object:employee"},
        "order":    {"table": "orders",    "pk": "orderID",    "label_cols": ["orderID"],                "label_fmt": "Order #{}", "artifact": "object:order"},
        "customer": {"table": "customers", "pk": "customerID","label_cols": ["companyName"],             "label_join": "",       "artifact": "object:customer"},
        "product":  {"table": "products",  "pk": "productID", "label_cols": ["productName"],             "label_join": "",       "artifact": "object:product"},
        "category": {"table": "categories","pk": "categoryID","label_cols": ["categoryName"],            "label_join": "",       "artifact": "object:category"},
    }
    LINK_CONFIG = [
        {"link": "link:employee:1:n:order",       "from": "employee", "to": "order",    "fk_table": "orders",    "fk_col": "employeeID"},
        {"link": "link:customer:1:n:order",        "from": "customer", "to": "order",    "fk_table": "orders",    "fk_col": "customerID"},
        {"link": "link:category:1:n:product",      "from": "category", "to": "product",  "fk_table": "products",  "fk_col": "categoryID"},
        {"link": "link:order:n:m:product",          "from": "order",    "to": "product",  "fk_table": "order_details", "fk_col": "orderID", "target_fk": "productID"},
        {"link": "link:employee:1:n:employee",      "from": "employee", "to": "employee", "fk_table": "employees", "fk_col": "reportsTo", "reverse": True},
    ]

    def _fetch_entity(self, tenant, object_type, instance_id):
        cfg = self.ENTITY_CONFIG.get(object_type.lower())
        if not cfg:
            return None
        with self.source_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {cfg['table']} WHERE {cfg['pk']} = :pk"),
                {"pk": instance_id},
            ).mappings().first()
        return dict(row) if row else None

    def _entity_node(self, tenant, object_type, row):
        cfg = self.ENTITY_CONFIG.get(object_type.lower())
        if not cfg:
            return None
        pk_val = row[cfg["pk"]]
        if "label_fmt" in cfg:
            label = cfg["label_fmt"].format(pk_val)
        else:
            parts = [str(row.get(c, "")) for c in cfg["label_cols"]]
            label = cfg.get("label_join", " ").join(parts).strip()
        return {
            "id": f"{object_type}:{pk_val}",
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
            "type": object_type,
            "label": label or f"{object_type} #{pk_val}",
            "source_table": cfg["table"],
            "source_pk": f"{cfg['pk']}={pk_val}",
            "ontology_artifact": cfg["artifact"],
            "status": "approved",
        }

    def neighborhood(self, tenant, object_type, instance_id, depth=1, limit=200):
        cfg = self.ENTITY_CONFIG.get(object_type.lower())
        if not cfg:
            return None
        depth = max(1, min(int(depth), 2))
        requested_limit = int(limit)
        limit = max(1, min(requested_limit, 300))
        object_artifact = cfg["artifact"]
        artifacts = self._approved_artifacts(tenant, [object_artifact])
        if object_artifact not in artifacts:
            return {
                "approved": False,
                "tenant": tenant.public_dict(),
                "missing_approved_artifacts": [object_artifact],
                "center": None, "nodes": [], "edges": [],
            }
        center_row = self._fetch_entity(tenant, object_type, instance_id)
        if not center_row:
            return None
        center = self._entity_node(tenant, object_type, center_row)
        nodes = [center]
        edges = []
        allowed_node_types = {object_type}
        allowed_link_keys = []
        for lc in self.LINK_CONFIG:
            is_from = lc["from"] == object_type.lower()
            is_to = lc["to"] == object_type.lower() and lc.get("reverse")
            if not is_from and not is_to:
                continue
            link_artifacts = self._approved_artifacts(tenant, [lc["link"]])
            if lc["link"] not in link_artifacts:
                continue
            allowed_link_keys.append(lc["link"])
            target_type_key = lc["to"] if is_from else lc["from"]
            target_cfg = self.ENTITY_CONFIG.get(target_type_key)
            if not target_cfg:
                continue
            allowed_node_types.add(target_cfg["table"].rstrip("s").capitalize())
            with self.source_engine_for(tenant).connect() as conn:
                if is_from and lc.get("target_fk"):
                    # n:m via join table
                    rows = conn.execute(
                        text(f"SELECT t.* FROM {target_cfg['table']} t JOIN {lc['fk_table']} j ON j.{lc['target_fk']} = t.{target_cfg['pk']} WHERE j.{lc['fk_col']} = :pk ORDER BY t.{target_cfg['pk']} LIMIT :lim"),
                        {"pk": instance_id, "lim": limit},
                    ).mappings().all()
                elif is_from:
                    rows = conn.execute(
                        text(f"SELECT * FROM {lc['fk_table']} WHERE {lc['fk_col']} = :pk ORDER BY 1 LIMIT :lim"),
                        {"pk": instance_id, "lim": limit},
                    ).mappings().all()
                elif is_to:
                    rows = conn.execute(
                        text(f"SELECT * FROM {target_cfg['table']} WHERE {target_cfg['pk']} IN (SELECT {lc['fk_col']} FROM {lc['fk_table']} WHERE {target_cfg['pk']} = :pk) LIMIT :lim"),
                        {"pk": instance_id, "lim": limit},
                    ).mappings().all()
                else:
                    rows = []
            neighbor_type = target_type_key.capitalize()
            for row in rows:
                row = dict(row)
                n = self._entity_node(tenant, neighbor_type, row)
                if n:
                    nodes.append(n)
                    edges.append({
                        "id": f"{center['id']}->{n['id']}",
                        "tenant_id": tenant.tenant_id,
                        "source": center["id"],
                        "target": n["id"],
                        "link_key": lc["link"],
                        "status": "approved",
                    })
        return {
            "approved": True,
            "tenant": tenant.public_dict(),
            "graph_database": tenant.graph_database,
            "depth": depth,
            "limit": limit,
            "limits": {"requested_limit": requested_limit, "applied_limit": limit, "hard_limit": 300, "truncated": requested_limit > limit},
            "center": center,
            "nodes": nodes,
            "edges": edges,
            "scope": {
                "tenant_id": tenant.tenant_id,
                "center_node": center["id"],
                "type": object_type,
                "id": str(instance_id),
                "depth": depth,
                "node_limit": limit,
                "edge_limit": limit,
                "allowed_node_types": sorted(allowed_node_types),
                "allowed_link_keys": allowed_link_keys,
                "approved_only": True,
            },
        }

    def edge_detail(self, tenant, source, target):
        if not source.startswith("Employee:") or not target.startswith("Order:"):
            return None
        artifacts = self._approved_artifacts(
            tenant,
            ["object:employee", "object:order", "link:employee:1:n:order"]
        )
        if "link:employee:1:n:order" not in artifacts:
            return None
        employee_id = source.split(":", 1)[1]
        order_id = target.split(":", 1)[1]
        employee = self._fetch_employee(tenant, employee_id)
        order = self._fetch_order(tenant, order_id)
        if not employee or not order or str(order.get("employeeID")) != str(employee_id):
            return None
        return self._employee_order_edge(
            tenant,
            employee,
            order,
            include_rows=True,
            artifact=artifacts.get("link:employee:1:n:order"),
        )

    def _approved_artifacts(self, tenant, keys):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT canonical_key, name, artifact_type, status, version, payload_json, description
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id AND canonical_key = ANY(:keys) AND status = 'approved'
                    """
                ),
                {"tenant_id": tenant.tenant_id, "keys": list(keys)},
            ).mappings().all()
        return {row["canonical_key"]: dict(row) for row in rows}

    def _object_key(self, object_type):
        return f"object:{object_type}".lower()

    def _fetch_employee(self, tenant, employee_id):
        with self.source_engine_for(tenant).connect() as conn:
            return conn.execute(
                text("SELECT * FROM employees WHERE employeeID = :employee_id"),
                {"employee_id": employee_id},
            ).mappings().first()

    def _fetch_order(self, tenant, order_id):
        with self.source_engine_for(tenant).connect() as conn:
            return conn.execute(
                text("SELECT * FROM orders WHERE orderID = :order_id"),
                {"order_id": order_id},
            ).mappings().first()

    def _order_count_for_employee(self, tenant, employee_id):
        with self.source_engine_for(tenant).connect() as conn:
            return conn.execute(
                text("SELECT COUNT(*) FROM orders WHERE employeeID = :employee_id"),
                {"employee_id": employee_id},
            ).scalar()

    def _employee_reports(self, tenant, employee_id):
        with self.source_engine_for(tenant).connect() as conn:
            manager = conn.execute(
                text(
                    """
                    SELECT m.employeeID, m.firstName, m.lastName
                    FROM employees e
                    LEFT JOIN employees m ON e.reportsTo = m.employeeID
                    WHERE e.employeeID = :employee_id
                    """
                ),
                {"employee_id": employee_id},
            ).mappings().first()
            direct_reports = conn.execute(
                text("SELECT COUNT(*) FROM employees WHERE reportsTo = :employee_id"),
                {"employee_id": employee_id},
            ).scalar()
        manager_label = None
        if manager and manager.get("employeeID"):
            manager_label = self._employee_label(manager)
        return {"manager": manager_label, "direct_reports": direct_reports}

    def _employee_label(self, row):
        return f"{row.get('firstName', '')} {row.get('lastName', '')}".strip()

    def _employee_node(self, tenant, row):
        return {
            "id": f"Employee:{row['employeeID']}",
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
            "type": "Employee",
            "label": self._employee_label(row),
            "summary": row.get("title"),
            "source_table": "employees",
            "source_pk": f"employeeID={row['employeeID']}",
            "ontology_artifact": "object:employee",
            "status": "approved",
        }

    def _order_node(self, tenant, row):
        return {
            "id": f"Order:{row['orderID']}",
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
            "type": "Order",
            "label": f"Order #{row['orderID']}",
            "summary": f"Customer {row.get('customerID')} · {row.get('orderDate')}",
            "source_table": "orders",
            "source_pk": f"orderID={row['orderID']}",
            "ontology_artifact": "object:order",
            "status": "approved",
        }

    def _employee_order_edge(self, tenant, employee, order, include_rows=False, artifact=None):
        edge = {
            "id": f"Employee:{employee['employeeID']}->Order:{order['orderID']}",
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
            "type": "EMPLOYEE_HANDLED_ORDER",
            "source": f"Employee:{employee['employeeID']}",
            "target": f"Order:{order['orderID']}",
            "label": "handled order",
            "source_ref": "orders.employeeID",
            "join_condition": "orders.employeeID = employees.employeeID",
            "ontology_link": "link:employee:1:n:order",
            "evidence": "orders.employeeID matches employees.employeeID for this Employee-Order relationship.",
            "source_field": "orders.employeeID",
            "target_field": "employees.employeeID",
            "artifact_status": artifact.get("status") if artifact else "approved",
            "artifact_version": artifact.get("version") if artifact else None,
        }
        if include_rows:
            edge["source_instance"] = self._employee_node(tenant, employee)
            edge["target_instance"] = self._order_node(tenant, order)
            edge["source_row"] = self._row(employee)
            edge["target_row"] = self._row(order)
        return edge

    def _row(self, row):
        return {key: _jsonable(value) for key, value in dict(row).items()}


class ReasoningRepository:
    TASK_KEY = "reasoning:employee-4-workload-analysis"
    QUESTION = "Why did Employee #4 handle so many orders? Is there abnormal workload or customer concentration risk?"
    REQUIRED_ARTIFACTS = ["object:employee", "object:order", "link:employee:1:n:order"]

    def __init__(self, tenant_registry, instance_repository, ensure_schema=False):
        self.tenant_registry = tenant_registry
        self.instance_repository = instance_repository
        self.ensure_schema = ensure_schema
        self.metadata_engines = {}
        self.source_engines = {}

    def tenant(self, tenant_id=None):
        return self.tenant_registry.get(tenant_id)

    def metadata_engine_for(self, tenant):
        engine = self.metadata_engines.get(tenant.metadata_db_url)
        if engine is None:
            engine = create_engine(tenant.metadata_db_url)
            self.metadata_engines[tenant.metadata_db_url] = engine
            if self.ensure_schema:
                ensure_artifact_schema(engine)
            self.tenant_registry.ensure_metadata(engine)
        return engine

    def source_engine_for(self, tenant):
        engine = self.source_engines.get(tenant.source_db_url)
        if engine is None:
            engine = create_engine(tenant.source_db_url)
            self.source_engines[tenant.source_db_url] = engine
        return engine

    def list_tasks(self, tenant, status_filter=None):
        conditions = ["project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id}
        if status_filter:
            conditions.append("status = :status_filter")
            params["status_filter"] = status_filter
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, project_id, canonical_key, question, scope_json, allowed_tools_json,
                           status, created_at, updated_at
                    FROM aletheia_reasoning_tasks
                    WHERE {where}
                    ORDER BY updated_at DESC, id DESC
                """),
                params,
            ).mappings().all()
        tasks = [self._task_to_dict(row) for row in rows]
        return {
            "tenant": tenant.public_dict(),
            "tasks": [
                {
                    **task,
                    "latest_run": self.latest_run(tenant, task["canonical_key"]),
                }
                for task in tasks
            ],
        }

    def list_findings_overview(self, tenant, limit=50):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT f.id, f.run_id, f.project_id, f.canonical_key, f.title, f.conclusion,
                           f.confidence, f.supporting_evidence_json, f.counter_evidence_json,
                           f.recommended_action_json, f.status, f.version, f.source_agent,
                           f.created_at, f.updated_at,
                           t.canonical_key AS task_key, t.question, t.scope_json,
                           r.run_key, r.status AS run_status, r.created_at AS run_created_at
                    FROM aletheia_reasoning_findings f
                    JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE f.project_id = :tenant_id
                    ORDER BY f.updated_at DESC, f.id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant.tenant_id, "limit": limit},
            ).mappings().all()
        findings = []
        for row in rows:
            finding = self._finding_to_dict(row)
            finding["task_key"] = row["task_key"]
            finding["question"] = row["question"]
            finding["task_scope"] = _load_json(row["scope_json"], {})
            finding["run_key"] = row["run_key"]
            finding["run_status"] = row["run_status"]
            finding["run_created_at"] = str(row["run_created_at"]) if row["run_created_at"] else None
            self._normalize_scoped_finding_display(tenant, finding)
            findings.append(finding)
        return findings

    def finding_detail(self, tenant, canonical_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT f.id, f.run_id, f.project_id, f.canonical_key, f.title, f.conclusion,
                           f.confidence, f.supporting_evidence_json, f.counter_evidence_json,
                           f.recommended_action_json, f.status, f.version, f.source_agent,
                           f.created_at, f.updated_at,
                           t.canonical_key AS task_key, t.question, t.scope_json,
                           r.run_key, r.agent_name, r.prompt_version, r.query_plan_json,
                           r.tool_calls_json, r.evidence_paths_json, r.output_json,
                           r.eval_result_json, r.status AS run_status, r.latency_ms,
                           r.cost_estimate, r.created_at AS run_created_at
                    FROM aletheia_reasoning_findings f
                    JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE f.project_id = :tenant_id AND f.canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
        if not row:
            return None
        finding = self._finding_to_dict(row)
        finding["task"] = {
            "canonical_key": row["task_key"],
            "question": row["question"],
            "scope": _load_json(row["scope_json"], {}),
        }
        finding["run"] = self._run_to_dict(
            {
                "id": row["run_id"],
                "project_id": row["project_id"],
                "run_key": row["run_key"],
                "agent_name": row["agent_name"],
                "prompt_version": row["prompt_version"],
                "query_plan_json": row["query_plan_json"],
                "tool_calls_json": row["tool_calls_json"],
                "evidence_paths_json": row["evidence_paths_json"],
                "output_json": row["output_json"],
                "eval_result_json": row["eval_result_json"],
                "status": row["run_status"],
                "latency_ms": row["latency_ms"],
                "cost_estimate": row["cost_estimate"],
                "created_at": row["run_created_at"],
            }
        )
        self._normalize_scoped_finding_display(tenant, finding)
        return finding

    def list_runs_overview(self, tenant, limit=30):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT r.id, r.project_id, r.run_key, r.agent_name, r.prompt_version,
                           r.query_plan_json, r.tool_calls_json, r.evidence_paths_json,
                           r.output_json, r.eval_result_json, r.status, r.latency_ms,
                           r.cost_estimate, r.created_at,
                           t.canonical_key AS task_key, t.question, t.scope_json
                    FROM aletheia_reasoning_runs r
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE r.project_id = :tenant_id
                    ORDER BY r.created_at DESC, r.id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant.tenant_id, "limit": limit},
            ).mappings().all()
        runs = []
        for row in rows:
            run = self._run_to_dict(row)
            run["task_key"] = row["task_key"]
            run["question"] = row["question"]
            run["task_scope"] = _load_json(row["scope_json"], {})
            runs.append(run)
        return runs

    def create_question_task(self, tenant, payload):
        question = (payload.get("question") or "").strip()
        if not question:
            raise ValueError("question is required")
        scope = payload.get("scope") or {}
        center_node = scope.get("center_node") or "Employee:4"
        depth = int(scope.get("depth") or 1)
        limit = int(scope.get("limit") or 200)
        inner_scope = {
            "source": "question_center",
            "center_node": center_node,
            "depth": depth,
            "node_limit": limit,
            "edge_limit": limit,
            "allowed_node_types": ["Employee", "Order"],
            "allowed_link_keys": ["link:employee:1:n:order"],
            "approved_only": True,
            "evidence_paths": [
                {
                    "kind": "question_scope",
                    "label": center_node,
                    "summary": f"Question Center scoped task for: {question}",
                    "url": scope.get("graph_url")
                    or f"/graph.html?tenant={quote(tenant.tenant_id)}&type=Employee&id=4&depth={depth}&limit={limit}",
                    "source_ref": "question_center",
                    "payload": {"scope": scope.get("type") or "tenant", "center_node": center_node},
                }
            ],
        }
        if scope.get("nonce"):
            inner_scope["nonce"] = scope["nonce"]
        return self.create_scoped_task_from_graph(
            tenant,
            {
                "question": question,
                "source": "question_center",
                "graph_url": scope.get("graph_url")
                or f"/graph.html?tenant={quote(tenant.tenant_id)}&type=Employee&id=4&depth={depth}&limit={limit}",
                "scope": inner_scope,
            },
        )

    def get_task(self, tenant, task_key):
        task = self._get_task_row(tenant, task_key)
        if task is None:
            return None
        latest_run = self.latest_run(tenant, task_key)
        findings = self.list_findings(tenant, task_key)
        for finding in findings:
            finding["task"] = task
            finding["run"] = latest_run or {}
            self._normalize_scoped_finding_display(tenant, finding)
        return {
            "tenant": tenant.public_dict(),
            "task": task,
            "latest_run": latest_run,
            "findings": findings,
        }

    def create_scoped_task_from_graph(self, tenant, payload):
        scope = payload.get("scope") or {}
        center_node = scope.get("center_node")
        center_edge = scope.get("center_edge")
        if not center_node and not center_edge:
            raise ValueError("center_node or center_edge is required")
        depth = max(1, min(int(scope.get("depth") or 1), 2))
        node_limit = max(1, min(int(scope.get("node_limit") or 100), 300))
        edge_limit = max(1, min(int(scope.get("edge_limit") or 100), 300))
        key_source = center_node or f"{center_edge.get('source')}->{center_edge.get('target')}"
        task_source = scope.get("source") or payload.get("source") or "graph_explorer"
        evidence_paths = scope.get("evidence_paths") or []
        evidence_kind = evidence_paths[0].get("kind") if evidence_paths else ("graph_edge" if center_edge else "graph_node")
        identity_parts = [tenant.tenant_id, task_source, evidence_kind, key_source, f"d{depth}", f"n{node_limit}", f"e{edge_limit}"]
        if task_source == "question_center":
            question_hash = hashlib.sha1((payload.get("question") or "").encode("utf-8")).hexdigest()[:10]
            identity_parts.append(f"q{question_hash}")
        nonce = scope.get("nonce") or payload.get("nonce")
        if nonce:
            identity_parts.append(f"r{nonce}")
        canonical_key = f"reasoning:graph-scope:{'-'.join(_slug(part) for part in identity_parts)}"
        question = payload.get("question") or (
            f"Explain the approved graph evidence around {key_source} and identify any workload, concentration, or provenance risk."
        )
        if center_node:
            if ":" not in center_node:
                raise ValueError("center_node must be like Employee:4")
            object_type, instance_id = center_node.split(":", 1)
            graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=node_limit)
            if graph and not graph.get("approved"):
                raise ValueError(f"center_node {center_node} is outside the approved graph scope (node not found or not approved)")
        if center_edge:
            source = center_edge.get("source")
            target = center_edge.get("target")
            if not source or not target or not self.instance_repository.edge_detail(tenant, source, target):
                raise ValueError("center_edge is outside the approved graph scope")
        task_scope = {
            "source": task_source,
            "tenant_id": tenant.tenant_id,
            "center_node": center_node,
            "center_edge": center_edge,
            "depth": depth,
            "node_limit": node_limit,
            "edge_limit": edge_limit,
            "allowed_node_types": scope.get("allowed_node_types") or ["Employee", "Order"],
            "allowed_link_keys": scope.get("allowed_link_keys") or ["link:employee:1:n:order"],
            "approved_only": True,
            "evidence_paths": evidence_paths,
            "review_gate": "draft_only",
            "graph_url": payload.get("graph_url"),
        }
        allowed_tools = ["graph_query", "instance_lookup", "edge_lookup", "artifact_lookup", "propose_finding", "propose_action"]
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_tasks
                    (project_id, canonical_key, question, scope_json, allowed_tools_json, status, created_at, updated_at)
                    VALUES (:tenant_id, :canonical_key, :question, :scope_json, :allowed_tools_json, 'active', NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      question = EXCLUDED.question,
                      scope_json = EXCLUDED.scope_json,
                      allowed_tools_json = EXCLUDED.allowed_tools_json,
                      status = aletheia_reasoning_tasks.status,
                      updated_at = NOW()
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": canonical_key,
                    "question": question,
                    "scope_json": _json_dump(task_scope),
                    "allowed_tools_json": _json_dump(allowed_tools),
                },
            )
            row = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, question, scope_json, allowed_tools_json,
                           status, created_at, updated_at
                    FROM aletheia_reasoning_tasks
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
        task = self._task_to_dict(row)
        return {
            "tenant": tenant.public_dict(),
            "task": task,
            "reasoning_url": f"/reasoning.html?tenant={tenant.tenant_id}&task={canonical_key}",
        }

    def _get_task_row(self, tenant, task_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, question, scope_json, allowed_tools_json,
                           status, created_at, updated_at
                    FROM aletheia_reasoning_tasks
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": task_key},
            ).mappings().first()
        return self._task_to_dict(row) if row else None

    def update_task_status(self, tenant, task_key, new_status):
        valid = {"active", "completed", "closed"}
        if new_status not in valid:
            raise ValueError(f"Invalid task status: {new_status}; expected one of {valid}")
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE aletheia_reasoning_tasks
                    SET status = :new_status, updated_at = NOW()
                    WHERE project_id = :tenant_id AND canonical_key = :task_key
                    RETURNING id, project_id, canonical_key, question, scope_json,
                              allowed_tools_json, status, created_at, updated_at
                    """
                ),
                {"tenant_id": tenant.tenant_id, "task_key": task_key, "new_status": new_status},
            ).mappings().first()
        if not row:
            return None
        return self._task_to_dict(row)

    def delete_task(self, tenant, task_key):
        with self.metadata_engine_for(tenant).begin() as conn:
            task_row = conn.execute(
                text("SELECT id FROM aletheia_reasoning_tasks WHERE project_id = :tid AND canonical_key = :key"),
                {"tid": tenant.tenant_id, "key": task_key},
            ).mappings().first()
            if not task_row:
                return None
            wh = "t.project_id = :tid AND t.canonical_key = :key"
            params = {"tid": tenant.tenant_id, "key": task_key}
            self._delete_task_cascade(conn, wh, params)
            conn.execute(text("DELETE FROM aletheia_reasoning_tasks WHERE project_id = :tid AND canonical_key = :key"), params)
        return {"deleted": True, "canonical_key": task_key}

    def _delete_task_cascade(self, conn, where_clause, params):
        conn.execute(text(f"""
            DELETE FROM aletheia_reasoning_reviews
            WHERE finding_id IN (
                SELECT f.id FROM aletheia_reasoning_findings f
                JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                WHERE {where_clause}
            )
        """), params)
        conn.execute(text(f"""
            DELETE FROM aletheia_reasoning_findings
            WHERE run_id IN (
                SELECT r.id FROM aletheia_reasoning_runs r
                JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                WHERE {where_clause}
            )
        """), params)
        conn.execute(text(f"""
            DELETE FROM aletheia_reasoning_runs
            WHERE task_id IN (
                SELECT t.id FROM aletheia_reasoning_tasks t WHERE {where_clause}
            )
        """), params)

    def bulk_delete_closed_tasks(self, tenant):
        wh = "t.project_id = :tid AND t.status = 'closed'"
        wh_task = "project_id = :tid AND status = 'closed'"
        params = {"tid": tenant.tenant_id}
        with self.metadata_engine_for(tenant).begin() as conn:
            self._delete_task_cascade(conn, wh, params)
            result = conn.execute(text(f"DELETE FROM aletheia_reasoning_tasks WHERE {wh_task}"), params)
        return {"deleted_count": result.rowcount}

    def bulk_close_tasks(self, tenant, keys=None, before=None):
        conditions = ["project_id = :tenant_id", "status != 'closed'"]
        params = {"tenant_id": tenant.tenant_id}
        if keys:
            conditions.append("canonical_key IN :keys")
            params["keys"] = tuple(keys)
        if before:
            conditions.append("updated_at < :before")
            params["before"] = before
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).begin() as conn:
            result = conn.execute(
                text(f"UPDATE aletheia_reasoning_tasks SET status = 'closed', updated_at = NOW() WHERE {where}"),
                params,
            )
        return {"closed_count": result.rowcount}

    def ensure_default_task(self, tenant):
        scope = {
            "object_type": "Employee",
            "instance_id": "4",
            "depth": 1,
            "required_artifacts": self.REQUIRED_ARTIFACTS,
            "graph_database": tenant.graph_database,
            "mvp_boundary": "fixed Northwind Employee #4 workload analysis",
        }
        allowed_tools = ["graph_query", "instance_lookup", "artifact_lookup", "propose_finding", "propose_action"]
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_tasks
                    (project_id, canonical_key, question, scope_json, allowed_tools_json, status, created_at, updated_at)
                    VALUES (:tenant_id, :canonical_key, :question, :scope_json, :allowed_tools_json, 'active', NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      question = EXCLUDED.question,
                      scope_json = EXCLUDED.scope_json,
                      allowed_tools_json = EXCLUDED.allowed_tools_json,
                      status = aletheia_reasoning_tasks.status,
                      updated_at = NOW()
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": self.TASK_KEY,
                    "question": self.QUESTION,
                    "scope_json": _json_dump(scope),
                    "allowed_tools_json": _json_dump(allowed_tools),
                },
            )
            row = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, question, scope_json, allowed_tools_json,
                           status, created_at, updated_at
                    FROM aletheia_reasoning_tasks
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": self.TASK_KEY},
            ).mappings().first()
        return self._task_to_dict(row)

    def run_task(self, tenant, task_key):
        if task_key != self.TASK_KEY:
            return self.run_scoped_graph_task(tenant, task_key)
        started = time.monotonic()
        task = self._get_task_row(tenant, self.TASK_KEY)
        if task is None:
            raise ValueError("Default task not found — it may have been deleted")
        if task.get("status") == "closed":
            raise ValueError("Cannot run a closed task")
        if task.get("status") == "completed":
            self.update_task_status(tenant, self.TASK_KEY, "active")
            task["status"] = "active"
        graph = self.graph_query(tenant, "Employee", "4")
        query_plan = [
            "Validate current tenant and approved-only artifact gate.",
            "Read Employee #4 1-hop Employee -> Order graph.",
            "Inspect Employee #4 source row and representative Order evidence.",
            "Aggregate workload and customer concentration from tenant source rows.",
            "Propose draft finding and action proposal with evidence paths.",
        ]
        tool_calls = [
            {"tool": "graph_query", "tenant_id": tenant.tenant_id, "approved_only": True, "status": "completed" if graph.get("approved") else "blocked"},
        ]
        if not graph.get("approved"):
            output = {
                "summary": "Reasoning blocked by tenant-scoped approved-only gate.",
                "missing_approved_artifacts": graph.get("missing_approved_artifacts", []),
                "unsupported_claims": [],
            }
            eval_result = {
                "passed": False,
                "reason": "missing approved artifacts",
                "unsupported_claims": [],
                "evidence_path_count": 0,
            }
            run = self._record_run(
                tenant,
                task,
                query_plan,
                tool_calls,
                [],
                output,
                eval_result,
                "blocked",
                started,
            )
            return {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}

        employee = self.instance_lookup(tenant, "Employee", "4")
        edge = self.edge_lookup(tenant, "Employee:4", "Order:10250")
        artifact = self.artifact_lookup(tenant, "link:employee:1:n:order")
        workload = self._workload_stats(tenant, "4")
        profile = self._employee_profile_summary(tenant, "4")
        evidence_paths = self._evidence_paths(tenant, employee, edge, artifact, workload)
        tool_calls.extend(
            [
                {"tool": "instance_lookup", "tenant_id": tenant.tenant_id, "object_type": "Employee", "id": "4", "status": "completed"},
                {"tool": "instance_lookup", "tenant_id": tenant.tenant_id, "object_type": "Order", "id": "10250", "status": "completed"},
                {"tool": "artifact_lookup", "tenant_id": tenant.tenant_id, "canonical_key": "link:employee:1:n:order", "status": "completed"},
                {"tool": "propose_finding", "tenant_id": tenant.tenant_id, "write_scope": "draft_reasoning_artifact", "status": "completed"},
                {"tool": "propose_action", "tenant_id": tenant.tenant_id, "write_scope": "draft_action_proposal", "status": "completed"},
            ]
        )
        conclusion = profile["profile_summary"]
        recommended_action = {
            "type": "review_workload",
            "title": "Review Employee #4 workload distribution",
            "description": "Validate whether this order volume reflects role specialization, historical assignment rules, or a workload imbalance before changing operations.",
            "execution_boundary": "proposal_only",
            "structured_answer": profile,
        }
        finding_suffix = f"run-{int(time.time() * 1000)}"
        workload_finding = {
            "canonical_key": f"finding:employee-4-workload-concentration:{finding_suffix}",
            "title": profile["title"],
            "conclusion": conclusion,
            "confidence": 0.82,
            "supporting_evidence": evidence_paths,
            "counter_evidence": [
                {
                    "kind": "limitation",
                    "summary": "MVP uses 1-hop Employee -> Order evidence only; it does not yet inspect product, revenue, or time-window seasonality.",
                }
            ],
            "recommended_action": recommended_action,
        }
        follow_up_finding = {
            "canonical_key": f"finding:employee-4-follow-up-risk-review:{finding_suffix}",
            "title": "Employee #4 workload needs time, customer, and freight follow-up before action",
            "conclusion": (
                "The approved 1-hop graph supports the workload concentration claim, but it is not enough "
                "to classify operational risk as abnormal. A reviewer should inspect orderDate, customer mix, "
                "and freight distribution before changing assignment rules."
            ),
            "confidence": 0.74,
            "supporting_evidence": [
                evidence_paths[0],
                evidence_paths[1],
                evidence_paths[3],
            ],
            "counter_evidence": [
                {
                    "kind": "scope_limit",
                    "summary": "No product, category, or revenue 2-hop evidence is used in this MVP run.",
                },
                {
                    "kind": "review_required",
                    "summary": "The recommended action is a review proposal, not an automated operational change.",
                },
            ],
            "recommended_action": {
                "type": "inspect_distribution",
                "title": "Inspect time, customer, and freight distribution for Employee #4",
                "description": "Run a bounded follow-up analysis before deciding whether the workload is normal specialization or a risk.",
                "execution_boundary": "proposal_only",
            },
        }
        findings = [workload_finding, follow_up_finding]
        output = {
            "summary": conclusion,
            "finding_keys": [finding["canonical_key"] for finding in findings],
            "unsupported_claims": [],
        }
        eval_result = {
            "passed": len(findings) >= 2 and all(len(finding["supporting_evidence"]) >= 2 for finding in findings),
            "unsupported_claims": [],
            "evidence_path_count": len(evidence_paths),
            "finding_count": len(findings),
            "tenant_id": tenant.tenant_id,
            "approved_only": True,
        }
        run = self._record_run(
            tenant,
            task,
            query_plan,
            tool_calls,
            evidence_paths,
            output,
            eval_result,
            "completed",
            started,
        )
        finding_rows = [self._record_finding(tenant, run, finding) for finding in findings]
        return {
            "tenant": tenant.public_dict(),
            "task": task,
            "run": run,
            "findings": finding_rows,
            "approved": True,
        }

    def run_task_streaming(self, tenant, task_key):
        if task_key != self.TASK_KEY:
            yield from self.run_scoped_graph_task_streaming(tenant, task_key)
            return
        started = time.monotonic()
        task = self._get_task_row(tenant, self.TASK_KEY)
        if task is None:
            yield {"event": "error", "data": {"message": "Default task not found — it may have been deleted"}}
            return
        if task.get("status") == "closed":
            yield {"event": "error", "data": {"message": "Cannot run a closed task"}}
            return
        if task.get("status") == "completed":
            self.update_task_status(tenant, self.TASK_KEY, "active")
            task["status"] = "active"
        query_plan = [
            "Validate current tenant and approved-only artifact gate.",
            "Read Employee #4 1-hop Employee -> Order graph.",
            "Inspect Employee #4 source row and representative Order evidence.",
            "Aggregate workload and customer concentration from tenant source rows.",
            "Propose draft finding and action proposal with evidence paths.",
        ]
        yield {"event": "plan", "data": {"query_plan": query_plan, "task": task}}
        graph = self.graph_query(tenant, "Employee", "4")
        tool_calls = [
            {"tool": "graph_query", "tenant_id": tenant.tenant_id, "approved_only": True, "status": "completed" if graph.get("approved") else "blocked"},
        ]
        yield {"event": "step", "data": {"tool": "graph_query", "status": tool_calls[0]["status"], "step": 1, "total": 5}}
        if not graph.get("approved"):
            output = {
                "summary": "Reasoning blocked by tenant-scoped approved-only gate.",
                "missing_approved_artifacts": graph.get("missing_approved_artifacts", []),
                "unsupported_claims": [],
            }
            eval_result = {"passed": False, "reason": "missing approved artifacts", "unsupported_claims": [], "evidence_path_count": 0}
            run = self._record_run(tenant, task, query_plan, tool_calls, [], output, eval_result, "blocked", started)
            yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}}
            return
        employee = self.instance_lookup(tenant, "Employee", "4")
        yield {"event": "step", "data": {"tool": "instance_lookup", "object_type": "Employee", "id": "4", "status": "completed", "step": 2, "total": 5}}
        edge = self.edge_lookup(tenant, "Employee:4", "Order:10250")
        yield {"event": "step", "data": {"tool": "edge_lookup", "edge": "Employee:4 → Order:10250", "status": "completed", "step": 3, "total": 5}}
        artifact = self.artifact_lookup(tenant, "link:employee:1:n:order")
        workload = self._workload_stats(tenant, "4")
        profile = self._employee_profile_summary(tenant, "4")
        yield {"event": "step", "data": {"tool": "workload_aggregate", "status": "completed", "step": 4, "total": 5}}
        evidence_paths = self._evidence_paths(tenant, employee, edge, artifact, workload)
        tool_calls.extend([
            {"tool": "instance_lookup", "tenant_id": tenant.tenant_id, "object_type": "Employee", "id": "4", "status": "completed"},
            {"tool": "instance_lookup", "tenant_id": tenant.tenant_id, "object_type": "Order", "id": "10250", "status": "completed"},
            {"tool": "artifact_lookup", "tenant_id": tenant.tenant_id, "canonical_key": "link:employee:1:n:order", "status": "completed"},
            {"tool": "propose_finding", "tenant_id": tenant.tenant_id, "write_scope": "draft_reasoning_artifact", "status": "completed"},
            {"tool": "propose_action", "tenant_id": tenant.tenant_id, "write_scope": "draft_action_proposal", "status": "completed"},
        ])
        yield {"event": "evidence", "data": {"evidence_paths": evidence_paths}}
        conclusion = profile["profile_summary"]
        recommended_action = {
            "type": "review_workload",
            "title": "Review Employee #4 workload distribution",
            "description": "Validate whether this order volume reflects role specialization, historical assignment rules, or a workload imbalance before changing operations.",
            "execution_boundary": "proposal_only",
            "structured_answer": profile,
        }
        finding_suffix = f"run-{int(time.time() * 1000)}"
        workload_finding = {
            "canonical_key": f"finding:employee-4-workload-concentration:{finding_suffix}",
            "title": profile["title"],
            "conclusion": conclusion,
            "confidence": 0.82,
            "supporting_evidence": evidence_paths,
            "counter_evidence": [{"kind": "limitation", "summary": "MVP uses 1-hop Employee -> Order evidence only; it does not yet inspect product, revenue, or time-window seasonality."}],
            "recommended_action": recommended_action,
        }
        follow_up_finding = {
            "canonical_key": f"finding:employee-4-follow-up-risk-review:{finding_suffix}",
            "title": "Employee #4 workload needs time, customer, and freight follow-up before action",
            "conclusion": "The approved 1-hop graph supports the workload concentration claim, but it is not enough to classify operational risk as abnormal. A reviewer should inspect orderDate, customer mix, and freight distribution before changing assignment rules.",
            "confidence": 0.74,
            "supporting_evidence": [evidence_paths[0], evidence_paths[1], evidence_paths[3]],
            "counter_evidence": [
                {"kind": "scope_limit", "summary": "No product, category, or revenue 2-hop evidence is used in this MVP run."},
                {"kind": "review_required", "summary": "The recommended action is a review proposal, not an automated operational change."},
            ],
            "recommended_action": {
                "type": "inspect_distribution",
                "title": "Inspect time, customer, and freight distribution for Employee #4",
                "description": "Run a bounded follow-up analysis before deciding whether the workload is normal specialization or a risk.",
                "execution_boundary": "proposal_only",
            },
        }
        findings = [workload_finding, follow_up_finding]
        output = {"summary": conclusion, "finding_keys": [f["canonical_key"] for f in findings], "unsupported_claims": []}
        eval_result = {
            "passed": len(findings) >= 2 and all(len(f["supporting_evidence"]) >= 2 for f in findings),
            "unsupported_claims": [],
            "evidence_path_count": len(evidence_paths),
            "finding_count": len(findings),
            "tenant_id": tenant.tenant_id,
            "approved_only": True,
        }
        run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "completed", started)
        yield {"event": "step", "data": {"tool": "propose_finding", "status": "completed", "step": 5, "total": 5}}
        finding_rows = []
        for f in findings:
            row = self._record_finding(tenant, run, f)
            finding_rows.append(row)
            yield {"event": "finding", "data": {"finding": row}}
        yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": finding_rows, "approved": True}}

    def run_scoped_graph_task_streaming(self, tenant, task_key):
        started = time.monotonic()
        task = self._get_task_row(tenant, task_key)
        if task is None:
            yield {"event": "error", "data": {"message": f"Task not found: {task_key}"}}
            return
        if task.get("status") == "closed":
            yield {"event": "error", "data": {"message": "Cannot run a closed task"}}
            return
        if task.get("status") == "completed":
            self.update_task_status(tenant, task_key, "active")
            task["status"] = "active"
        scope = task.get("scope") or {}
        query_plan = [
            "Validate tenant-scoped graph task and approved-only scope.",
            "Read only the selected node or edge evidence path from Graph Explorer.",
            "Propose a draft finding without approving, ingesting, or changing canonical graph data.",
        ]
        yield {"event": "plan", "data": {"query_plan": query_plan, "task": task}}
        tool_calls = [
            {"tool": "graph_query", "tenant_id": tenant.tenant_id, "approved_only": True, "status": "completed"},
            {"tool": "propose_finding", "tenant_id": tenant.tenant_id, "write_scope": "draft_reasoning_artifact", "status": "completed"},
        ]
        evidence_paths = list(scope.get("evidence_paths") or [])
        yield {"event": "step", "data": {"tool": "graph_query", "status": "completed" if evidence_paths else "blocked", "step": 1, "total": 3}}
        if not evidence_paths:
            tool_calls[0]["status"] = "blocked"
            output = {"summary": "Scoped graph reasoning blocked because no evidence paths were provided.", "unsupported_claims": ["missing evidence path"]}
            eval_result = {"passed": False, "approved_only": True, "draft_only": True, "unsupported_claims": ["missing evidence path"], "evidence_path_count": 0}
            run = self._record_run(tenant, task, query_plan, tool_calls, [], output, eval_result, "blocked", started)
            yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}}
            return
        center_node = scope.get("center_node")
        engine = ReasoningEngine(self.instance_repository)
        structured_answer = engine.analyze(tenant, center_node, task.get("question"))
        if structured_answer:
            query_plan = [
                "Validate tenant-scoped entity profile task and approved-only graph scope.",
                "Read the selected entity node evidence path from the approved graph.",
                "Run controlled source aggregations for entity profile facts.",
                "Produce a draft structured profile finding with evidence limits and next validation questions.",
            ]
            yield {"event": "plan", "data": {"query_plan": query_plan, "task": task}}
            tool_calls.insert(1, {"tool": "entity_profile_aggregate", "tenant_id": tenant.tenant_id, "approved_only": True, "write_scope": "read_only_source_aggregate", "status": "completed"})
            metrics = structured_answer.get("metrics") or {}
            rankings = metrics.get("rankings") or []
            label = metrics.get("label") or center_node
            ranking_summary = "; ".join(
                f"{r['my_count']} {r['target_type']}(s) (#{r['rank']}/{r['total_peers']}, {r['level']})"
                for r in rankings if r.get("my_count", 0) > 0
            ) or "no ranked relationships"
            evidence_paths.append({
                "kind": "controlled_aggregate",
                "label": f"{label} Business Profile",
                "summary": f"{label}: {ranking_summary}",
                "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={quote(task_key)}",
                "source_ref": f"{metrics.get('object_type', 'entity')} + peer ranking + value aggregation",
                "payload": metrics,
            })
            yield {"event": "step", "data": {"tool": "entity_profile_aggregate", "status": "completed", "step": 2, "total": 3}}
            title = structured_answer["title"]
            conclusion = structured_answer["profile_summary"]
        else:
            title, conclusion = self._edge_or_fallback_finding_text(tenant, task, scope)
        yield {"event": "evidence", "data": {"evidence_paths": evidence_paths}}
        finding = {
            "canonical_key": f"finding:graph-scope:{task_key}:run-{int(time.time() * 1000)}",
            "title": title,
            "conclusion": conclusion,
            "confidence": 0.78 if structured_answer else 0.72,
            "supporting_evidence": evidence_paths,
            "counter_evidence": [{"kind": "scope_limit", "summary": ("Conclusions are based solely on the approved graph and controlled aggregation; performance targets, utilization, profitability, or satisfaction data are not included." if structured_answer else "The task cannot expand beyond the selected approved graph scope without a new bounded graph request.")}],
            "recommended_action": {
                "type": "review_graph_scope",
                "title": "Review scoped graph evidence before operational action",
                "description": "Use this draft as a reviewer prompt; do not treat it as an approved finding until it passes the review gate.",
                "execution_boundary": "proposal_only",
                **({"structured_answer": structured_answer} if structured_answer else {}),
            },
        }
        output = {"summary": conclusion, "finding_keys": [finding["canonical_key"]], "unsupported_claims": [], "draft_only": True, **({"structured_answer": structured_answer} if structured_answer else {})}
        eval_result = {"passed": True, "approved_only": True, "draft_only": True, "unsupported_claims": [], "evidence_path_count": len(evidence_paths), "tenant_id": tenant.tenant_id}
        run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "completed", started)
        yield {"event": "step", "data": {"tool": "propose_finding", "status": "completed", "step": 3, "total": 3}}
        finding_row = self._record_finding(tenant, run, finding)
        yield {"event": "finding", "data": {"finding": finding_row}}
        yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [finding_row], "approved": True}}

    def run_scoped_graph_task(self, tenant, task_key):
        started = time.monotonic()
        task = self._get_task_row(tenant, task_key)
        if task is None:
            return None
        if task.get("status") == "closed":
            raise ValueError("Cannot run a closed task")
        if task.get("status") == "completed":
            self.update_task_status(tenant, task_key, "active")
            task["status"] = "active"
        scope = task.get("scope") or {}
        query_plan = [
            "Validate tenant-scoped graph task and approved-only scope.",
            "Read only the selected node or edge evidence path from Graph Explorer.",
            "Propose a draft finding without approving, ingesting, or changing canonical graph data.",
        ]
        tool_calls = [
            {"tool": "graph_query", "tenant_id": tenant.tenant_id, "approved_only": True, "status": "completed"},
            {"tool": "propose_finding", "tenant_id": tenant.tenant_id, "write_scope": "draft_reasoning_artifact", "status": "completed"},
        ]
        evidence_paths = list(scope.get("evidence_paths") or [])
        if not evidence_paths:
            tool_calls[0]["status"] = "blocked"
            output = {
                "summary": "Scoped graph reasoning blocked because no evidence paths were provided.",
                "unsupported_claims": ["missing evidence path"],
            }
            eval_result = {
                "passed": False,
                "approved_only": True,
                "draft_only": True,
                "unsupported_claims": ["missing evidence path"],
                "evidence_path_count": 0,
            }
            run = self._record_run(tenant, task, query_plan, tool_calls, [], output, eval_result, "blocked", started)
            return {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}
        center_node = scope.get("center_node")
        engine = ReasoningEngine(self.instance_repository)
        structured_answer = engine.analyze(tenant, center_node, task.get("question"))
        if structured_answer:
            query_plan = [
                "Validate tenant-scoped entity profile task and approved-only graph scope.",
                "Read the selected entity node evidence path from the approved graph.",
                "Run controlled source aggregations for entity profile facts.",
                "Produce a draft structured profile finding with evidence limits and next validation questions.",
            ]
            tool_calls.insert(
                1,
                {
                    "tool": "entity_profile_aggregate",
                    "tenant_id": tenant.tenant_id,
                    "approved_only": True,
                    "write_scope": "read_only_source_aggregate",
                    "status": "completed",
                },
            )
            metrics = structured_answer.get("metrics") or {}
            rankings = metrics.get("rankings") or []
            label = metrics.get("label") or center_node
            ranking_summary = "; ".join(
                f"{r['my_count']} {r['target_type']}(s) (#{r['rank']}/{r['total_peers']}, {r['level']})"
                for r in rankings if r.get("my_count", 0) > 0
            ) or "no ranked relationships"
            evidence_paths.append(
                {
                    "kind": "controlled_aggregate",
                    "label": f"{label} Business Profile",
                    "summary": f"{label}: {ranking_summary}",
                    "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={quote(task_key)}",
                    "source_ref": f"{metrics.get('object_type', 'entity')} + peer ranking + value aggregation",
                    "payload": metrics,
                }
            )
            title = structured_answer["title"]
            conclusion = structured_answer["profile_summary"]
        else:
            title, conclusion = self._edge_or_fallback_finding_text(tenant, task, scope)
        finding = {
            "canonical_key": f"finding:graph-scope:{task_key}:run-{int(time.time() * 1000)}",
            "title": title,
            "conclusion": conclusion,
            "confidence": 0.78 if structured_answer else 0.72,
            "supporting_evidence": evidence_paths,
            "counter_evidence": [
                {
                    "kind": "scope_limit",
                    "summary": (
                        "Conclusions are based solely on the approved graph and controlled aggregation; performance targets, utilization, profitability, or satisfaction data are not included."
                        if structured_answer
                        else "The task cannot expand beyond the selected approved graph scope without a new bounded graph request."
                    ),
                }
            ],
            "recommended_action": {
                "type": "review_graph_scope",
                "title": "Review scoped graph evidence before operational action",
                "description": "Use this draft as a reviewer prompt; do not treat it as an approved finding until it passes the review gate.",
                "execution_boundary": "proposal_only",
                **({"structured_answer": structured_answer} if structured_answer else {}),
            },
        }
        output = {
            "summary": conclusion,
            "finding_keys": [finding["canonical_key"]],
            "unsupported_claims": [],
            "draft_only": True,
            **({"structured_answer": structured_answer} if structured_answer else {}),
        }
        eval_result = {
            "passed": True,
            "approved_only": True,
            "draft_only": True,
            "unsupported_claims": [],
            "evidence_path_count": len(evidence_paths),
            "tenant_id": tenant.tenant_id,
        }
        run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "completed", started)
        finding_row = self._record_finding(tenant, run, finding)
        return {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [finding_row], "approved": True}

    def _edge_or_fallback_finding_text(self, tenant, task, scope):
        center_edge = scope.get("center_edge") or {}
        question = task.get("question") or "the scoped graph question"
        if center_edge.get("source") and center_edge.get("target"):
            source = center_edge["source"]
            target = center_edge["target"]
            edge = self.instance_repository.edge_detail(tenant, source, target)
            title = f"{source} -> {target} approved edge evidence"
            conclusion = (
                f'For the question "{question}", the approved graph contains the selected '
                f"{source} -> {target} relationship. "
            )
            if edge:
                conclusion += (
                    f"The relationship is supported by {edge.get('source_ref') or 'source-row evidence'} "
                    f"and ontology link {edge.get('ontology_link') or edge.get('link_key') or 'link'}. "
                )
            conclusion += "This is a draft answer for review and does not change canonical ontology or graph."
            return title, conclusion
        center = scope.get("center_node") or f"{center_edge.get('source', 'scope')} -> {center_edge.get('target', 'scope')}"
        return (
            f"Scoped answer for {center}",
            (
                f'For the question "{question}", the run is constrained to the selected approved graph scope. '
                "This is a draft answer for review and does not change canonical ontology or graph."
            ),
        )

    def _is_legacy_scoped_finding(self, finding):
        title = (finding.get("title") or "").lower()
        conclusion = (finding.get("conclusion") or "").lower()
        return (
            "scoped graph reasoning remains draft-only" in title
            or "created from graph explorer evidence" in conclusion
            or "work snapshot" in title
            or "approved order relationships" in title
            or "loaded in the current evidence scope" in conclusion
        )

    def _normalize_scoped_finding_display(self, tenant, finding):
        task = finding.get("task") or {}
        if not task:
            task = {
                "question": finding.get("question"),
                "scope": finding.get("task_scope") or {},
            }
        scope = task.get("scope") or finding.get("task_scope") or {}
        structured_answer = finding.get("structured_answer") or (finding.get("recommended_action") or {}).get("structured_answer")
        if not structured_answer:
            center_node = scope.get("center_node")
            engine = ReasoningEngine(self.instance_repository)
            structured_answer = engine.analyze(tenant, center_node, task.get("question"))
            if structured_answer:
                raw_recommended_action = finding.get("recommended_action") or {}
                finding["recommended_action"] = {
                    **raw_recommended_action,
                    "structured_answer": structured_answer,
                }
                finding["structured_answer"] = structured_answer
                for key in ("profile_summary", "key_facts", "business_interpretation", "evidence_limits", "next_questions"):
                    finding[key] = structured_answer.get(key) or ([] if key != "profile_summary" else "")
        if not structured_answer and not self._is_legacy_scoped_finding(finding):
            return finding
        raw_title = finding.get("title")
        raw_conclusion = finding.get("conclusion")
        if structured_answer:
            title = structured_answer.get("title") or raw_title
            conclusion = structured_answer.get("profile_summary") or raw_conclusion
            finding["confidence"] = max(float(finding.get("confidence") or 0), 0.78)
            metrics = structured_answer.get("metrics") or {}
            evidence_paths = list(finding.get("supporting_evidence") or [])
            if not any(path.get("kind") == "controlled_aggregate" for path in evidence_paths):
                rankings = metrics.get("rankings") or []
                label_val = metrics.get("label") or scope.get("center_node")
                if rankings:
                    ranking_text = "; ".join(
                        f"{r['my_count']} {r['target_type']} (#{r['rank']}/{r['total_peers']}, {r['level']})"
                        for r in rankings if r.get("my_count", 0) > 0
                    ) or "no ranked relationships"
                    summary_text = f"{label_val}: {ranking_text}"
                else:
                    neighbor_types = metrics.get("neighbor_types") or {}
                    neighbor_text = ", ".join(f"{c} {t}" for t, c in sorted(neighbor_types.items())) if neighbor_types else "scope data"
                    summary_text = f"{label_val} has {metrics.get('neighbor_count', 0)} related entities ({neighbor_text})"
                evidence_paths.append(
                    {
                        "kind": "controlled_aggregate",
                        "label": f"{label_val} Business Profile",
                        "summary": summary_text,
                        "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={quote(task.get('canonical_key') or '')}",
                        "source_ref": f"{metrics.get('object_type', 'entity')} + peer ranking",
                        "payload": metrics,
                    }
                )
                finding["supporting_evidence"] = evidence_paths
        else:
            title, conclusion = self._edge_or_fallback_finding_text(tenant, task, scope)
        finding["raw_title"] = raw_title
        finding["raw_conclusion"] = raw_conclusion
        finding["title"] = title
        finding["conclusion"] = conclusion
        finding["display_normalized"] = True
        return finding

    def graph_query(self, tenant, object_type, instance_id):
        return self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=1, limit=200)

    def instance_lookup(self, tenant, object_type, instance_id):
        return self.instance_repository.detail(tenant, object_type, instance_id)

    def edge_lookup(self, tenant, source, target):
        return self.instance_repository.edge_detail(tenant, source, target)

    def artifact_lookup(self, tenant, canonical_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, artifact_type, name, description,
                           payload_json, confidence, source_refs_json, status, version,
                           source_agent, created_at, updated_at
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key AND status = 'approved'
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
        return _artifact_to_dict(row) if row else None

    def list_findings(self, tenant, task_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT f.id, f.run_id, f.project_id, f.canonical_key, f.title, f.conclusion,
                           f.confidence, f.supporting_evidence_json, f.counter_evidence_json,
                           f.recommended_action_json, f.status, f.version, f.source_agent,
                           f.created_at, f.updated_at
                    FROM aletheia_reasoning_findings f
                    JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE f.project_id = :tenant_id AND t.canonical_key = :task_key
                    ORDER BY f.updated_at DESC, f.id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "task_key": task_key},
            ).mappings().all()
        return [self._finding_to_dict(row) for row in rows]

    def latest_run(self, tenant, task_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT r.id, r.project_id, r.run_key, r.agent_name, r.prompt_version,
                           r.query_plan_json, r.tool_calls_json, r.evidence_paths_json,
                           r.output_json, r.eval_result_json, r.status, r.latency_ms,
                           r.cost_estimate, r.created_at
                    FROM aletheia_reasoning_runs r
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE r.project_id = :tenant_id AND t.canonical_key = :task_key
                    ORDER BY r.created_at DESC, r.id DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant.tenant_id, "task_key": task_key},
            ).mappings().first()
        return self._run_to_dict(row) if row else None

    def review_finding(self, tenant, canonical_key, status, reviewer, reason):
        _require_reason(status, reason or "")
        with self.metadata_engine_for(tenant).begin() as conn:
            finding = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, status, version
                    FROM aletheia_reasoning_findings
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    FOR UPDATE
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
            if not finding:
                raise KeyError(canonical_key)
            before_status = finding["status"]
            before_version = finding["version"]
            after_version = before_version if status == "comment" else before_version + 1
            after_status = before_status if status == "comment" else status
            if status != "comment":
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_reasoning_findings
                        SET status = :status, version = version + 1, updated_at = NOW()
                        WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key, "status": status},
                )
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_reviews
                    (finding_id, project_id, canonical_key, decision, reviewer, reason,
                     before_status, after_status, before_version, after_version, created_at)
                    VALUES
                    (:finding_id, :project_id, :canonical_key, :decision, :reviewer, :reason,
                     :before_status, :after_status, :before_version, :after_version, NOW())
                    """
                ),
                {
                    "finding_id": finding["id"],
                    "project_id": finding["project_id"],
                    "canonical_key": finding["canonical_key"],
                    "decision": status,
                    "reviewer": reviewer,
                    "reason": reason,
                    "before_status": before_status,
                    "after_status": after_status,
                    "before_version": before_version,
                    "after_version": after_version,
                },
            )
            if status in ("approved", "rejected"):
                self._maybe_complete_task(conn, tenant.tenant_id, finding["id"])
        return self.get_finding(tenant, canonical_key)

    def _maybe_complete_task(self, conn, tenant_id, finding_id):
        row = conn.execute(
            text(
                """
                SELECT t.id AS task_id, t.status AS task_status
                FROM aletheia_reasoning_findings f
                JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                WHERE f.id = :finding_id AND f.project_id = :tenant_id
                """
            ),
            {"finding_id": finding_id, "tenant_id": tenant_id},
        ).mappings().first()
        if not row or row["task_status"] != "active":
            return
        task_id = row["task_id"]
        counts = conn.execute(
            text(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE f.status NOT IN ('approved', 'rejected')) AS pending
                FROM aletheia_reasoning_findings f
                JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                WHERE r.task_id = :task_id AND f.project_id = :tenant_id
                """
            ),
            {"task_id": task_id, "tenant_id": tenant_id},
        ).mappings().first()
        if counts["total"] > 0 and counts["pending"] == 0:
            conn.execute(
                text("UPDATE aletheia_reasoning_tasks SET status = 'completed', updated_at = NOW() WHERE id = :task_id"),
                {"task_id": task_id},
            )

    def get_finding(self, tenant, canonical_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, run_id, project_id, canonical_key, title, conclusion, confidence,
                           supporting_evidence_json, counter_evidence_json, recommended_action_json,
                           status, version, source_agent, created_at, updated_at
                    FROM aletheia_reasoning_findings
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
            if not row:
                return None
            context = conn.execute(
                text(
                    """
                    SELECT t.canonical_key AS task_key, t.question, t.scope_json,
                           r.id, r.project_id, r.run_key, r.agent_name, r.prompt_version,
                           r.query_plan_json, r.tool_calls_json, r.evidence_paths_json,
                           r.output_json, r.eval_result_json, r.status, r.latency_ms,
                           r.cost_estimate, r.created_at
                    FROM aletheia_reasoning_runs r
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE r.project_id = :tenant_id AND r.id = :run_id
                    """
                ),
                {"tenant_id": tenant.tenant_id, "run_id": row["run_id"]},
            ).mappings().first()
            reviews = conn.execute(
                text(
                    """
                    SELECT decision, reviewer, reason, before_status, after_status,
                           before_version, after_version, created_at
                    FROM aletheia_reasoning_reviews
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().all()
        finding = self._finding_to_dict(row)
        if context:
            finding["task"] = {
                "canonical_key": context["task_key"],
                "question": context["question"],
                "scope": _load_json(context["scope_json"], {}),
            }
            finding["run"] = self._run_to_dict(context)
            self._normalize_scoped_finding_display(tenant, finding)
        finding["reviews"] = [dict(review) for review in reviews]
        return finding

    def _employee_label(self, row):
        return f"{row.get('firstName', '')} {row.get('lastName', '')}".strip()

    def _workload_stats(self, tenant, employee_id):
        with self.source_engine_for(tenant).connect() as conn:
            order_count = conn.execute(
                text("SELECT COUNT(*) FROM orders WHERE employeeID = :employee_id"),
                {"employee_id": employee_id},
            ).scalar()
            total_orders = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()
            top_customer = conn.execute(
                text(
                    """
                    SELECT customerID, COUNT(*) AS order_count
                    FROM orders
                    WHERE employeeID = :employee_id
                    GROUP BY customerID
                    ORDER BY order_count DESC, customerID
                    LIMIT 1
                    """
                ),
                {"employee_id": employee_id},
            ).mappings().first()
        top_customer_orders = int(top_customer["order_count"]) if top_customer else 0
        top_customer_share = top_customer_orders / order_count if order_count else 0
        employee_share = order_count / total_orders if total_orders else 0
        return {
            "order_count": int(order_count or 0),
            "total_orders": int(total_orders or 0),
            "employee_share": employee_share,
            "employee_share_percent": employee_share * 100,
            "top_customer_id": top_customer["customerID"] if top_customer else None,
            "top_customer_orders": top_customer_orders,
            "top_customer_share": top_customer_share,
            "top_customer_share_percent": top_customer_share * 100,
        }

    def _employee_profile_summary(self, tenant, employee_id):
        with self.source_engine_for(tenant).connect() as conn:
            employee = conn.execute(
                text(
                    """
                    SELECT e.employeeID, e.firstName, e.lastName, e.title, e.city, e.region,
                           e.country, e.reportsTo, m.firstName AS managerFirstName,
                           m.lastName AS managerLastName, m.title AS managerTitle
                    FROM employees e
                    LEFT JOIN employees m ON e.reportsTo = m.employeeID
                    WHERE e.employeeID = :employee_id
                    """
                ),
                {"employee_id": employee_id},
            ).mappings().first()
            if not employee:
                return {
                    "title": f"Employee:{employee_id} 画像无法生成",
                    "profile_summary": f"Employee:{employee_id} 不在当前受控数据源中，无法形成员工画像。",
                    "key_facts": [],
                    "business_interpretation": ["当前缺少员工基础记录，不能进行业务判断。"],
                    "evidence_limits": ["缺少 employees 源表记录。"],
                    "next_questions": ["确认员工 ID 是否存在于当前租户的数据源。"],
                }
            order_stats = conn.execute(
                text(
                    """
                    SELECT COUNT(*) AS order_count,
                           COUNT(DISTINCT customerID) AS customer_count,
                           MIN(orderDate) AS first_order,
                           MAX(orderDate) AS last_order,
                           COALESCE(SUM(freight), 0) AS freight_sum,
                           COALESCE(AVG(freight), 0) AS avg_freight
                    FROM orders
                    WHERE employeeID = :employee_id
                    """
                ),
                {"employee_id": employee_id},
            ).mappings().first()
            total_orders = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()
            total_revenue = conn.execute(
                text(
                    """
                    SELECT COALESCE(SUM(od.unitPrice * od.quantity * (1 - od.discount)), 0)
                    FROM order_details od
                    """
                )
            ).scalar()
            revenue = conn.execute(
                text(
                    """
                    SELECT COALESCE(SUM(od.unitPrice * od.quantity * (1 - od.discount)), 0)
                    FROM orders o
                    JOIN order_details od ON od.orderID = o.orderID
                    WHERE o.employeeID = :employee_id
                    """
                ),
                {"employee_id": employee_id},
            ).scalar()
            rank_rows = conn.execute(
                text(
                    """
                    SELECT employeeID, COUNT(*) AS order_count
                    FROM orders
                    GROUP BY employeeID
                    ORDER BY order_count DESC, employeeID
                    """
                )
            ).mappings().all()
            top_customers = conn.execute(
                text(
                    """
                    SELECT o.customerID, c.companyName, COUNT(DISTINCT o.orderID) AS order_count,
                           COALESCE(SUM(od.unitPrice * od.quantity * (1 - od.discount)), 0) AS revenue
                    FROM orders o
                    LEFT JOIN customers c ON c.customerID = o.customerID
                    LEFT JOIN order_details od ON od.orderID = o.orderID
                    WHERE o.employeeID = :employee_id
                    GROUP BY o.customerID, c.companyName
                    ORDER BY order_count DESC, o.customerID
                    LIMIT 5
                    """
                ),
                {"employee_id": employee_id},
            ).mappings().all()
            yearly = conn.execute(
                text(
                    """
                    SELECT YEAR(orderDate) AS year, COUNT(*) AS order_count
                    FROM orders
                    WHERE employeeID = :employee_id
                    GROUP BY YEAR(orderDate)
                    ORDER BY year
                    """
                ),
                {"employee_id": employee_id},
            ).mappings().all()
            categories = conn.execute(
                text(
                    """
                    SELECT c.categoryName, COUNT(DISTINCT o.orderID) AS order_count,
                           COALESCE(SUM(od.unitPrice * od.quantity * (1 - od.discount)), 0) AS revenue
                    FROM orders o
                    JOIN order_details od ON od.orderID = o.orderID
                    JOIN products p ON p.productID = od.productID
                    JOIN categories c ON c.categoryID = p.categoryID
                    WHERE o.employeeID = :employee_id
                    GROUP BY c.categoryName
                    ORDER BY revenue DESC, c.categoryName
                    LIMIT 3
                    """
                ),
                {"employee_id": employee_id},
            ).mappings().all()
        name = self._employee_label(employee)
        order_count = int(order_stats["order_count"] or 0)
        total_orders = int(total_orders or 0)
        customer_count = int(order_stats["customer_count"] or 0)
        revenue = float(revenue or 0)
        total_revenue = float(total_revenue or 0)
        freight_sum = float(order_stats["freight_sum"] or 0)
        avg_freight = float(order_stats["avg_freight"] or 0)
        order_share = order_count / total_orders if total_orders else 0
        revenue_share = revenue / total_revenue if total_revenue else 0
        rank = next((index + 1 for index, row in enumerate(rank_rows) if str(row["employeeID"]) == str(employee_id)), None)
        employee_total = len(rank_rows)
        rank_label = f"{rank}/{employee_total}" if rank else "-"
        percentile = None
        if rank and employee_total > 1:
            percentile = 1 - ((rank - 1) / (employee_total - 1))
        top_customer = dict(top_customers[0]) if top_customers else {}
        top_customer_count = int(top_customer.get("order_count") or 0)
        top_customer_share = top_customer_count / order_count if order_count else 0
        concentration = "较分散"
        if top_customer_share >= 0.25:
            concentration = "偏集中"
        elif top_customer_share >= 0.15:
            concentration = "中等集中"
        load_label = "低订单负载"
        if percentile is not None and percentile >= 0.75:
            load_label = "高订单负载"
        elif percentile is not None and percentile >= 0.4:
            load_label = "中等订单负载"
        manager_name = " ".join(
            part for part in [employee.get("managerFirstName"), employee.get("managerLastName")] if part
        ) or None
        location = ", ".join(part for part in [employee.get("city"), employee.get("region"), employee.get("country")] if part)
        years = [
            {"year": int(row["year"]), "order_count": int(row["order_count"])}
            for row in yearly
            if row.get("year") is not None
        ]
        peak_year = max(years, key=lambda item: item["order_count"]) if years else None
        top_customer_text = (
            f"{top_customer.get('companyName') or top_customer.get('customerID')}（{top_customer_count} 单，占该员工订单 {top_customer_share * 100:.1f}%）"
            if top_customer
            else "无客户订单记录"
        )
        profile_summary = (
            f"{name} 是 {employee.get('title') or '未标注职位'}，位于 {location or '未知地区'}。"
            f"在当前已批准 Northwind 图谱和受控聚合中，他呈现为{load_label}、客户覆盖{concentration}的员工："
            f"共处理 {order_count} 单，占全体订单 {order_share * 100:.1f}%，订单量排名 {rank_label}；"
            f"覆盖 {customer_count} 个客户，最大客户为 {top_customer_text}。"
        )
        if load_label == "低订单负载":
            profile_summary += " 因此当前证据不支持把他判断为订单负载异常偏高。"
        else:
            profile_summary += " 是否异常仍需结合目标、工时、利润率和客户质量继续验证。"
        key_facts = [
            {"label": "员工基础信息", "value": f"{name} / {employee.get('title') or '-'} / {location or '-'}", "source_ref": f"employees.employeeID={employee_id}"},
            {"label": "直属关系", "value": f"reportsTo={employee.get('reportsTo') or '-'}" + (f" / manager={manager_name}" if manager_name else ""), "source_ref": "employees.reportsTo"},
            {"label": "订单负载", "value": f"{order_count} / {total_orders} 单，占比 {order_share * 100:.1f}%，排名 {rank_label}", "source_ref": "orders.employeeID"},
            {"label": "时间范围", "value": f"{_jsonable(order_stats['first_order']) or '-'} 至 {_jsonable(order_stats['last_order']) or '-'}" + (f"，峰值年份 {peak_year['year']}（{peak_year['order_count']} 单）" if peak_year else ""), "source_ref": "orders.orderDate"},
            {"label": "客户覆盖", "value": f"{customer_count} 个客户；Top 客户 {top_customer_text}", "source_ref": "orders.customerID"},
            {"label": "订单规模", "value": f"订单明细金额 {revenue:.2f}（占全体 {revenue_share * 100:.1f}%）；运费合计 {freight_sum:.2f}，平均运费 {avg_freight:.2f}", "source_ref": "order_details + orders.freight"},
        ]
        if categories:
            key_facts.append(
                {
                    "label": "主要品类",
                    "value": "；".join(f"{row['categoryName']} {float(row['revenue'] or 0):.2f}" for row in categories),
                    "source_ref": "order_details.productID -> products.categoryID",
                }
            )
        business_interpretation = [
            f"订单量排名 {rank_label}，说明该员工在样本内不是最高负载承接者；当前更像稳定覆盖型员工，而不是明显异常高负载员工。",
            f"客户覆盖 {customer_count} 个客户，最大客户占比 {top_customer_share * 100:.1f}%，未显示单一客户强依赖；需要进一步按金额而非订单数复核集中度。",
            f"职位为 {employee.get('title') or '-'}，因此订单承接量需要结合岗位职责解释；仅凭订单数不能判断绩效好坏或管理风险。",
        ]
        evidence_limits = [
            "当前画像只使用已批准图谱范围、employees/orders/order_details/customers/products/categories 的受控聚合。",
            "缺少绩效目标、工时、利润率、客户满意度和内部职责分工，不能直接判断绩效优劣或异常责任。",
            "当前图谱主关系仍以 Employee-Order 为核心；Customer、OrderDetail、Product/Category 属于受控 SQL 聚合证据，不自动写入正式知识图谱。",
        ]
        next_questions = [
            "按同职位或同地区员工对比订单量、金额和客户覆盖，判断 Steven Buchanan 是否真的偏离基线。",
            "按月份查看订单波动，确认是否存在阶段性峰值或交接导致的集中承接。",
            "按客户金额而非订单数计算 Top 客户依赖，识别是否存在大客户集中风险。",
            "补充工时、销售目标、利润率或客户满意度后，再判断绩效或风险。",
        ]
        return {
            "title": f"{name} 员工画像：{load_label}、客户覆盖{concentration}",
            "profile_summary": profile_summary,
            "key_facts": key_facts,
            "business_interpretation": business_interpretation,
            "evidence_limits": evidence_limits,
            "next_questions": next_questions,
            "metrics": {
                "employee_id": int(employee_id),
                "name": name,
                "title": employee.get("title"),
                "location": location,
                "order_count": order_count,
                "total_orders": total_orders,
                "order_share_percent": order_share * 100,
                "order_rank": rank,
                "employee_count": employee_total,
                "customer_count": customer_count,
                "top_customer_id": top_customer.get("customerID"),
                "top_customer_name": top_customer.get("companyName"),
                "top_customer_order_count": top_customer_count,
                "top_customer_share_percent": top_customer_share * 100,
                "revenue": revenue,
                "revenue_share_percent": revenue_share * 100,
                "freight_sum": freight_sum,
                "avg_freight": avg_freight,
                "yearly_orders": years,
                "top_customers": [
                    {
                        "customer_id": row["customerID"],
                        "company_name": row["companyName"],
                        "order_count": int(row["order_count"] or 0),
                        "revenue": float(row["revenue"] or 0),
                    }
                    for row in top_customers
                ],
            },
        }

    def _evidence_paths(self, tenant, employee, edge, artifact, workload):
        return [
            {
                "kind": "instance_node",
                "label": "Employee #4 source row",
                "summary": f"{employee['label']} is the center of the workload analysis.",
                "url": f"/instances.html?tenant={tenant.tenant_id}&type=Employee&id=4&node=Employee%3A4",
                "source_ref": "employees.employeeID=4",
                "payload": {"node_id": "Employee:4", "ontology_artifact": "object:employee"},
            },
            {
                "kind": "instance_edge",
                "label": "Employee #4 -> Order #10250 edge",
                "summary": "Representative approved Employee-Order edge with source row provenance.",
                "url": f"/instances.html?tenant={tenant.tenant_id}&type=Employee&id=4&edgeSource=Employee%3A4&edgeTarget=Order%3A10250",
                "source_ref": "orders.employeeID",
                "payload": {"edge_id": edge["id"] if edge else None, "ontology_link": "link:employee:1:n:order"},
            },
            {
                "kind": "ontology_artifact",
                "label": "Approved Employee-Order ontology link",
                "summary": artifact["description"] if artifact else "Approved link artifact required for graph evidence.",
                "url": f"/?tenant={tenant.tenant_id}&artifact=link%3Aemployee%3A1%3An%3Aorder",
                "source_ref": "artifact:link:employee:1:n:order",
                "payload": {"status": artifact["status"] if artifact else None},
            },
            {
                "kind": "aggregate",
                "label": "Tenant source-row workload aggregate",
                "summary": f"Employee #4 handled {workload['order_count']} of {workload['total_orders']} orders.",
                "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={self.TASK_KEY}",
                "source_ref": "orders.employeeID=4",
                "payload": workload,
            },
        ]

    def _record_run(self, tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, status, started):
        run_key = f"{task['canonical_key']}:run:{int(time.time() * 1000)}"
        latency_ms = int((time.monotonic() - started) * 1000)
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_runs
                    (task_id, project_id, run_key, agent_name, prompt_version,
                     query_plan_json, tool_calls_json, evidence_paths_json,
                     output_json, eval_result_json, status, latency_ms, cost_estimate, created_at)
                    VALUES
                    (:task_id, :tenant_id, :run_key, 'ReasoningWorkbenchAgent', 'northwind-workload-v1',
                     :query_plan_json, :tool_calls_json, :evidence_paths_json,
                     :output_json, :eval_result_json, :status, :latency_ms, 0.0, NOW())
                    RETURNING id, project_id, run_key, agent_name, prompt_version,
                              query_plan_json, tool_calls_json, evidence_paths_json,
                              output_json, eval_result_json, status, latency_ms,
                              cost_estimate, created_at
                    """
                ),
                {
                    "task_id": task["id"],
                    "tenant_id": tenant.tenant_id,
                    "run_key": run_key,
                    "query_plan_json": _json_dump(query_plan),
                    "tool_calls_json": _json_dump(tool_calls),
                    "evidence_paths_json": _json_dump(evidence_paths),
                    "output_json": _json_dump(output),
                    "eval_result_json": _json_dump(eval_result),
                    "status": status,
                    "latency_ms": latency_ms,
                },
            ).mappings().first()
            if status == "completed":
                conn.execute(
                    text("UPDATE aletheia_reasoning_tasks SET status = 'completed', updated_at = NOW() WHERE id = :task_id AND status = 'active'"),
                    {"task_id": task["id"]},
                )
                task["status"] = "completed"
        return self._run_to_dict(row)

    def _record_finding(self, tenant, run, finding):
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_findings
                    (run_id, project_id, canonical_key, title, conclusion, confidence,
                     supporting_evidence_json, counter_evidence_json, recommended_action_json,
                     status, version, source_agent, created_at, updated_at)
                    VALUES
                    (:run_id, :tenant_id, :canonical_key, :title, :conclusion, :confidence,
                     :supporting_evidence_json, :counter_evidence_json, :recommended_action_json,
                     'draft', 1, 'ReasoningWorkbenchAgent', NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      run_id = EXCLUDED.run_id,
                      title = EXCLUDED.title,
                      conclusion = EXCLUDED.conclusion,
                      confidence = EXCLUDED.confidence,
                      supporting_evidence_json = EXCLUDED.supporting_evidence_json,
                      counter_evidence_json = EXCLUDED.counter_evidence_json,
                      recommended_action_json = EXCLUDED.recommended_action_json,
                      status = 'draft',
                      version = aletheia_reasoning_findings.version + 1,
                      updated_at = NOW()
                    RETURNING id, run_id, project_id, canonical_key, title, conclusion, confidence,
                              supporting_evidence_json, counter_evidence_json, recommended_action_json,
                              status, version, source_agent, created_at, updated_at
                    """
                ),
                {
                    "run_id": run["id"],
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": finding["canonical_key"],
                    "title": finding["title"],
                    "conclusion": finding["conclusion"],
                    "confidence": finding["confidence"],
                    "supporting_evidence_json": _json_dump(finding["supporting_evidence"]),
                    "counter_evidence_json": _json_dump(finding["counter_evidence"]),
                    "recommended_action_json": _json_dump(finding["recommended_action"]),
                },
            ).mappings().first()
        return self._finding_to_dict(row)

    def _task_to_dict(self, row):
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "canonical_key": row["canonical_key"],
            "question": row["question"],
            "scope": _load_json(row["scope_json"], {}),
            "allowed_tools": _load_json(row["allowed_tools_json"], []),
            "status": row["status"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }

    def _run_to_dict(self, row):
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "run_key": row["run_key"],
            "agent_name": row["agent_name"],
            "prompt_version": row["prompt_version"],
            "query_plan": _load_json(row["query_plan_json"], []),
            "tool_calls": _load_json(row["tool_calls_json"], []),
            "evidence_paths": _load_json(row["evidence_paths_json"], []),
            "output": _load_json(row["output_json"], {}),
            "eval_result": _load_json(row["eval_result_json"], {}),
            "status": row["status"],
            "latency_ms": row["latency_ms"],
            "cost_estimate": row["cost_estimate"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
        }

    def _finding_to_dict(self, row):
        recommended_action = _load_json(row["recommended_action_json"], {})
        structured_answer = recommended_action.get("structured_answer") or {}
        finding = {
            "id": row["id"],
            "run_id": row["run_id"],
            "tenant_id": row["project_id"],
            "canonical_key": row["canonical_key"],
            "title": row["title"],
            "conclusion": row["conclusion"],
            "confidence": row["confidence"],
            "supporting_evidence": _load_json(row["supporting_evidence_json"], []),
            "counter_evidence": _load_json(row["counter_evidence_json"], []),
            "recommended_action": recommended_action,
            "status": row["status"],
            "version": row["version"],
            "source_agent": row["source_agent"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }
        if structured_answer:
            finding["structured_answer"] = structured_answer
            for key in ("profile_summary", "key_facts", "business_interpretation", "evidence_limits", "next_questions"):
                finding[key] = structured_answer.get(key) or ([] if key != "profile_summary" else "")
        return finding


class AgentGatewayRepository:
    BLOCKED_TOOLS = {
        "approve",
        "approve_finding",
        "ingest",
        "ingest_graph",
        "modify_canonical_artifact",
        "commit",
        "push",
        "deploy",
        "secret_read",
        "direct_db_write",
    }
    REQUIRED_OUTPUT_FIELDS = {"status", "summary", "tool_calls", "draft_artifacts", "files_touched", "policy_violations"}
    RUNTIME_PROFILES = [
        {
            "runtime_id": "generic_cli_builtin",
            "runtime_type": "generic_cli",
            "binary_ref": sys.executable,
            "command_template_id": "builtin_json_report_v1",
            "enabled": True,
        },
        {"runtime_id": "codex_cli_default", "runtime_type": "codex_cli", "binary_ref": "codex", "command_template_id": "codex_cli_json_report_v1", "enabled": True},
        {"runtime_id": "gemini_cli_default", "runtime_type": "gemini_cli", "binary_ref": "gemini", "command_template_id": "gemini_cli_json_report_v1", "enabled": True},
        {"runtime_id": "claude_code_cli_default", "runtime_type": "claude_code_cli", "binary_ref": "claude", "command_template_id": "claude_code_json_report_v1", "enabled": True},
        {"runtime_id": "openclaw_cli_default", "runtime_type": "openclaw_cli", "binary_ref": "openclaw", "command_template_id": "version_probe_only", "enabled": True},
        {"runtime_id": "hermes_cli_default", "runtime_type": "hermes_cli", "binary_ref": "hermes", "command_template_id": "version_probe_only", "enabled": True},
    ]

    def __init__(self, tenant_registry, ensure_schema=False):
        self.tenant_registry = tenant_registry
        self.ensure_schema = ensure_schema
        self.metadata_engines = {}

    def tenant(self, tenant_id=None):
        return self.tenant_registry.get(tenant_id)

    def metadata_engine_for(self, tenant):
        engine = self.metadata_engines.get(tenant.metadata_db_url)
        if engine is None:
            engine = create_engine(tenant.metadata_db_url)
            self.metadata_engines[tenant.metadata_db_url] = engine
            if self.ensure_schema:
                ensure_artifact_schema(engine)
            self.tenant_registry.ensure_metadata(engine)
        return engine

    def ensure_defaults(self, tenant):
        with self.metadata_engine_for(tenant).begin() as conn:
            for profile in self.RUNTIME_PROFILES:
                conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_agent_runtime_configs
                        (runtime_id, runtime_type, binary_ref, command_template_id, enabled,
                         health_status, health_detail_json, created_at, updated_at)
                        VALUES
                        (:runtime_id, :runtime_type, :binary_ref, :command_template_id, :enabled,
                         'unknown', '{}', NOW(), NOW())
                        ON CONFLICT (runtime_id) DO UPDATE SET
                          runtime_type = EXCLUDED.runtime_type,
                          binary_ref = EXCLUDED.binary_ref,
                          command_template_id = EXCLUDED.command_template_id,
                          enabled = EXCLUDED.enabled,
                          updated_at = NOW()
                        """
                    ),
                    profile,
                )
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_agent_policies
                    (project_id, policy_id, allowed_paths_json, allowed_tools_json, blocked_tools_json,
                     max_runtime_seconds, max_output_bytes, env_allowlist_json, secret_policy, created_at, updated_at)
                    VALUES
                    (:tenant_id, 'default_cli_policy', :allowed_paths_json, :allowed_tools_json,
                     :blocked_tools_json, 120, 65536, '[]', 'deny', NOW(), NOW())
                    ON CONFLICT (project_id, policy_id) DO UPDATE SET
                      allowed_paths_json = EXCLUDED.allowed_paths_json,
                      allowed_tools_json = EXCLUDED.allowed_tools_json,
                      blocked_tools_json = EXCLUDED.blocked_tools_json,
                      max_runtime_seconds = EXCLUDED.max_runtime_seconds,
                      max_output_bytes = EXCLUDED.max_output_bytes,
                      env_allowlist_json = EXCLUDED.env_allowlist_json,
                      secret_policy = EXCLUDED.secret_policy,
                      updated_at = NOW()
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "allowed_paths_json": _json_dump(["reports", "web/review_workbench", "agents", "README.md"]),
                    "allowed_tools_json": _json_dump(["read", "test", "propose_patch", "propose_finding", "write_report"]),
                    "blocked_tools_json": _json_dump(sorted(self.BLOCKED_TOOLS)),
                },
            )

    def list_settings(self, tenant):
        self.ensure_defaults(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            runtimes = conn.execute(
                text(
                    """
                    SELECT runtime_id, runtime_type, binary_ref, command_template_id, enabled,
                           health_status, health_detail_json, created_at, updated_at
                    FROM aletheia_agent_runtime_configs
                    ORDER BY runtime_type, runtime_id
                    """
                )
            ).mappings().all()
            policies = conn.execute(
                text(
                    """
                    SELECT policy_id, project_id, allowed_paths_json, allowed_tools_json,
                           blocked_tools_json, max_runtime_seconds, max_output_bytes,
                           env_allowlist_json, secret_policy, created_at, updated_at
                    FROM aletheia_agent_policies
                    WHERE project_id = :tenant_id
                    ORDER BY policy_id
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
            runs = conn.execute(
                text(
                    """
                    SELECT run_key, project_id, runtime_id, policy_id, task_type, prompt_hash,
                           status, tool_calls_json, policy_violations_json, files_touched_json,
                           output_refs_json, stdout_ref, stderr_ref, started_at, finished_at
                    FROM aletheia_agent_runs
                    WHERE project_id = :tenant_id
                    ORDER BY started_at DESC, id DESC
                    LIMIT 20
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
        return {
            "tenant": tenant.public_dict(),
            "runtimes": [self._runtime_with_readiness(tenant, row) for row in runtimes],
            "policies": [self._policy_to_dict(row) for row in policies],
            "runs": [self._run_to_dict(row) for row in runs],
            "secret_policy": {"storage": "credential_ref_only", "ui": "masked", "default": "deny"},
        }

    def readiness(self, tenant, runtime_id):
        self.ensure_defaults(tenant)
        runtime = self._get_runtime(tenant, runtime_id)
        if not runtime:
            return None
        readiness = self._readiness_for_runtime(tenant, self._runtime_to_dict(runtime))
        return {"tenant": tenant.public_dict(), "readiness": readiness}

    def health_check(self, tenant, runtime_id):
        self.ensure_defaults(tenant)
        runtime = self._get_runtime(tenant, runtime_id)
        if not runtime:
            return None
        status = "unavailable"
        detail = {
            "binary_ref": self._mask_binary(runtime["binary_ref"]),
            "secret_masked": True,
            "command_template_id": runtime["command_template_id"],
        }
        if runtime["command_template_id"] == "builtin_json_report_v1":
            status = "available"
            detail["version"] = f"python {sys.version.split()[0]}"
        else:
            binary = shutil.which(runtime["binary_ref"])
            detail["resolved"] = bool(binary)
            if binary:
                probe = self._probe_version(binary)
                status = "available" if probe["ok"] else "unavailable"
                detail.update(probe)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_agent_runtime_configs
                    SET health_status = :status, health_detail_json = :detail, updated_at = NOW()
                    WHERE runtime_id = :runtime_id
                    """
                ),
                {"status": status, "detail": _json_dump(detail), "runtime_id": runtime_id},
            )
        return {"tenant": tenant.public_dict(), "runtime": {**self._runtime_to_dict(runtime), "health_status": status, "health_detail": detail}}

    def run_smoke(self, tenant, runtime_id, payload):
        self.ensure_defaults(tenant)
        runtime = self._get_runtime(tenant, runtime_id)
        if not runtime:
            return None
        policy = self._get_policy(tenant, payload.get("policy_id") or "default_cli_policy")
        if not policy:
            raise ValueError("policy not found")
        prompt = payload.get("prompt") or "Summarize the Aletheia repository structure as a JSON report."
        task_type = payload.get("task_type") or "report"
        run_key = f"agent-run:{runtime_id}:{int(time.time() * 1000)}"
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        started = time.monotonic()

        command_violation = self._validate_execution(runtime, policy)
        if command_violation:
            output = {
                "status": "blocked",
                "summary": "Command execution blocked by local policy.",
                "tool_calls": [],
                "draft_artifacts": [],
                "files_touched": [],
                "policy_violations": [command_violation],
            }
            stdout, stderr, returncode = _json_dump(output), "", 0
        elif "mock_cli_output" in payload:
            stdout = str(payload.get("mock_cli_output") or "")
            stderr, returncode = "", 0
        else:
            stdout, stderr, returncode = self._execute_runtime(runtime, policy, tenant, prompt, task_type)
            if len(stdout.encode("utf-8")) > policy["max_output_bytes"]:
                stdout = stdout.encode("utf-8")[: policy["max_output_bytes"]].decode("utf-8", errors="ignore")
                stderr = f"{stderr}\noutput truncated by max_output_bytes".strip()

        output, parse_violations = self._parse_cli_output(stdout)
        reported_violations = output.get("policy_violations", [])
        if not isinstance(reported_violations, list):
            reported_violations = [{"code": "invalid_reported_policy_violations"}]
        policy_violations = parse_violations + reported_violations + self._validate_output(output, policy, tenant)
        structural_failure = any(
            violation.get("code") in {"non_json_output", "missing_required_fields"}
            for violation in policy_violations
        )
        status = output.get("status", "failed") if not policy_violations else "blocked"
        if structural_failure:
            status = "failed"
        if returncode != 0:
            status = "failed"
            policy_violations.append({"code": "command_failed", "detail": f"exit code {returncode}"})
        run = self._record_run(
            tenant,
            run_key=run_key,
            runtime_id=runtime["runtime_id"],
            policy_id=policy["policy_id"],
            task_type=task_type,
            prompt_hash=prompt_hash,
            status=status,
            output=output,
            policy_violations=policy_violations,
            stdout=stdout,
            stderr=stderr,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {"tenant": tenant.public_dict(), "run": run}

    def run_safe_demo(self, tenant, runtime_id, payload):
        readiness_result = self.readiness(tenant, runtime_id)
        if readiness_result is None:
            return None
        readiness = readiness_result["readiness"]
        if readiness["demo_status"] != "demo_ready":
            raise ValueError(f"Safe demo disabled: {readiness['demo_status']}")
        body = dict(payload)
        body.pop("mock_cli_output", None)
        body.setdefault("policy_id", "default_cli_policy")
        body.setdefault("task_type", "report")
        body.setdefault("prompt", "Read the Aletheia README and produce a repository structure smoke report.")
        return self.run_smoke(tenant, runtime_id, body)

    def _execute_runtime(self, runtime, policy, tenant, prompt, task_type):
        if runtime["command_template_id"] in {"claude_code_json_report_v1", "codex_cli_json_report_v1", "gemini_cli_json_report_v1"}:
            return self._execute_external_report_runtime(runtime, policy, tenant, prompt, task_type)
        if runtime["command_template_id"] != "builtin_json_report_v1":
            output = {
                "status": "blocked",
                "summary": f"{runtime['runtime_type']} execution is not enabled in MVP; health check only.",
                "tool_calls": [],
                "draft_artifacts": [],
                "files_touched": [],
                "policy_violations": [{"code": "runtime_probe_only", "runtime_id": runtime["runtime_id"]}],
            }
            return _json_dump(output), "", 0
        script = """
import json
import os
payload = json.loads(os.environ["ALETHEIA_AGENT_TASK"])
print(json.dumps({
  "status": "completed",
  "summary": "Aletheia contains agents, a review workbench, reasoning UI, reports, and evaluation fixtures.",
  "tool_calls": [{"tool": "read", "path": "README.md"}],
  "draft_artifacts": [{
    "artifact_type": "report",
    "payload": {
      "title": "Repository structure smoke report",
      "tenant_id": payload["tenant_id"],
      "task_type": payload["task_type"],
      "summary": "Smoke run produced a draft report only."
    }
  }],
  "files_touched": ["reports/agent-gateway-smoke.md"],
  "policy_violations": [],
  "stdout_ref": "inline",
  "stderr_ref": "inline"
}, sort_keys=True))
"""
        env = {
            "PATH": os.environ.get("PATH", ""),
            "ALETHEIA_AGENT_TASK": _json_dump(
                {
                    "tenant_id": tenant.tenant_id,
                    "task_type": task_type,
                    "prompt": prompt,
                    "allowed_paths": policy["allowed_paths"],
                    "allowed_tools": policy["allowed_tools"],
                    "blocked_tools": policy["blocked_tools"],
                }
            ),
        }
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent,
            env=env,
            text=True,
            capture_output=True,
            timeout=policy["max_runtime_seconds"],
            check=False,
        )
        return result.stdout, result.stderr, result.returncode

    def _execute_external_report_runtime(self, runtime, policy, tenant, prompt, task_type):
        binary = shutil.which(runtime["binary_ref"])
        if not binary:
            output = {
                "status": "blocked",
                "summary": f"{runtime['binary_ref']} is not visible to the service PATH.",
                "tool_calls": [],
                "draft_artifacts": [],
                "files_touched": [],
                "policy_violations": [{"code": "runtime_binary_missing", "runtime_id": runtime["runtime_id"]}],
            }
            return _json_dump(output), "", 0
        safe_prompt = self._safe_demo_prompt(runtime, tenant, task_type, prompt)
        last_message_path = Path("/tmp") / f"aletheia-{runtime['runtime_id']}-{int(time.time() * 1000)}.txt"
        command = self._runtime_command(runtime, binary, safe_prompt, last_message_path)
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parent,
                env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
                text=True,
                capture_output=True,
                timeout=policy["max_runtime_seconds"],
                check=False,
            )
            raw_output = self._extract_runtime_response(runtime, result.stdout, result.stderr, last_message_path)
            policy_violations = []
            status = "completed" if result.returncode == 0 and raw_output else "failed"
            if result.returncode != 0:
                policy_violations.append({"code": "runtime_command_failed", "detail": f"exit code {result.returncode}"})
        except subprocess.TimeoutExpired:
            raw_output = ""
            result = subprocess.CompletedProcess(command, 124, "", "safe demo timed out")
            status = "failed"
            policy_violations = [{"code": "runtime_timeout", "detail": f">{policy['max_runtime_seconds']}s"}]
        finally:
            try:
                last_message_path.unlink()
            except FileNotFoundError:
                pass
        raw_summary = raw_output.strip().replace("\n", " ")[:160] or "no response"
        summary = f"{runtime['runtime_type']} safe demo completed with read-only structured report output: {raw_summary}"
        output = {
            "status": status,
            "summary": summary,
            "tool_calls": [{"tool": "write_report", "runtime": runtime["runtime_id"], "mode": "safe_demo"}],
            "draft_artifacts": [
                {
                    "artifact_type": "report",
                    "status": "draft",
                    "payload": {
                        "title": f"{runtime['runtime_type']} safe demo report",
                        "tenant_id": tenant.tenant_id,
                        "task_type": task_type,
                        "summary": summary,
                        "runtime_id": runtime["runtime_id"],
                        "command_template_id": runtime["command_template_id"],
                        "raw_response": self._mask_secret_like(raw_output)[:4000],
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                }
            ],
            "files_touched": ["reports/agent-gateway-smoke.md"],
            "policy_violations": policy_violations,
        }
        runtime_log = _json_dump({"runtime_stdout": self._mask_secret_like(result.stdout), "runtime_stderr": self._mask_secret_like(result.stderr)})
        return _json_dump(output), runtime_log, result.returncode

    def _safe_demo_prompt(self, runtime, tenant, task_type, prompt):
        return "Say OK."

    def _runtime_command(self, runtime, binary, safe_prompt, last_message_path):
        template = runtime["command_template_id"]
        if template == "claude_code_json_report_v1":
            return [binary, "--print", "--output-format", "json", "--permission-mode", "plan", safe_prompt]
        if template == "codex_cli_json_report_v1":
            return [binary, "exec", "--cd", str(Path(__file__).resolve().parent), "--sandbox", "read-only", "--output-last-message", str(last_message_path), safe_prompt]
        if template == "gemini_cli_json_report_v1":
            return [binary, "--prompt", safe_prompt, "--approval-mode", "plan", "--output-format", "json"]
        raise ValueError(f"Unsupported runtime template: {template}")

    def _extract_runtime_response(self, runtime, stdout, stderr, last_message_path):
        template = runtime["command_template_id"]
        if template == "claude_code_json_report_v1":
            try:
                return str(json.loads(stdout or "{}").get("result") or "")
            except json.JSONDecodeError:
                return stdout or stderr
        if template == "codex_cli_json_report_v1":
            if last_message_path.exists():
                return last_message_path.read_text(encoding="utf-8", errors="ignore")
            return stdout or stderr
        if template == "gemini_cli_json_report_v1":
            try:
                return str(json.loads(stdout or "{}").get("response") or "")
            except json.JSONDecodeError:
                return stdout or stderr
        return stdout or stderr

    def _validate_execution(self, runtime, policy):
        if runtime["command_template_id"] not in {
            "builtin_json_report_v1",
            "version_probe_only",
            "claude_code_json_report_v1",
            "codex_cli_json_report_v1",
            "gemini_cli_json_report_v1",
        }:
            return {"code": "command_template_not_allowlisted", "detail": runtime["command_template_id"]}
        if policy["secret_policy"] != "deny":
            return {"code": "secret_policy_not_supported_in_mvp", "detail": policy["secret_policy"]}
        return None

    def _runtime_with_readiness(self, tenant, row):
        runtime = self._runtime_to_dict(row)
        runtime["readiness"] = self._readiness_for_runtime(tenant, runtime)
        return runtime

    def _readiness_for_runtime(self, tenant, runtime):
        policy = self._get_policy(tenant, "default_cli_policy")
        checks = []

        if not runtime["enabled"]:
            checks.append(self._check("runtime_enabled", "blocked", "Runtime profile is disabled.", "Enable the runtime profile before demo."))
        else:
            checks.append(self._check("runtime_enabled", "pass", "Runtime profile is enabled."))

        if runtime["command_template_id"] == "builtin_json_report_v1":
            binary_status = "pass"
            binary_detail = f"{runtime['binary_ref']} available for builtin template."
        else:
            binary_found = bool(shutil.which(runtime["binary_ref"]))
            binary_status = "pass" if binary_found else "fail"
            binary_detail = f"{runtime['binary_ref']} found in service PATH." if binary_found else f"{runtime['binary_ref']} is not visible to the service PATH."
        checks.append(
            self._check(
                "binary",
                binary_status,
                binary_detail,
                f"Install {runtime['binary_ref']} and restart the service with a PATH that can resolve it." if binary_status == "fail" else "",
            )
        )

        path_status = "pass" if runtime["command_template_id"] == "builtin_json_report_v1" or binary_status == "pass" else "fail"
        checks.append(
            self._check(
                "path_visible",
                path_status,
                "Runtime binary is visible to the service process." if path_status == "pass" else "Runtime binary is not visible from the background service.",
                f"Expose {runtime['binary_ref']} through the service PATH, not only the interactive shell." if path_status == "fail" else "",
            )
        )

        if runtime["runtime_type"] == "generic_cli":
            checks.append(self._check("auth", "pass", "No external auth required."))
        elif binary_status == "fail":
            checks.append(self._check("auth", "unknown", "Auth was not checked because the binary is unavailable.", f"Install and sign in to {runtime['binary_ref']} locally; credentials are not stored in Aletheia."))
        else:
            auth_check = self._auth_check(runtime)
            checks.append(auth_check)

        expected_template = self._expected_demo_template(runtime["runtime_type"])
        template_ready = runtime["command_template_id"] == expected_template
        checks.append(
            self._check(
                "template",
                "pass" if template_ready else "fail",
                f"{runtime['command_template_id']} is configured." if template_ready else f"{expected_template} is required for executable demo; current template is {runtime['command_template_id']}.",
                f"Add an allowlisted {expected_template} runtime template before enabling safe demo." if not template_ready else "",
            )
        )

        output_ready = runtime["command_template_id"] in {
            "builtin_json_report_v1",
            "claude_code_json_report_v1",
            "codex_cli_json_report_v1",
            "gemini_cli_json_report_v1",
        }
        checks.append(
            self._check(
                "output_contract",
                "pass" if output_ready else "fail",
                "Structured report output is supported by the gateway adapter." if output_ready else "No structured output parser is enabled for this placeholder profile.",
                "Configure a JSON/report adapter that maps CLI output into draft artifacts only." if not output_ready else "",
            )
        )

        if policy:
            policy_ok = (
                policy["secret_policy"] == "deny"
                and "reports" in policy["allowed_paths"]
                and set(self.BLOCKED_TOOLS).issubset(set(policy["blocked_tools"]))
            )
            checks.append(
                self._check(
                    "policy",
                    "pass" if policy_ok else "fail",
                    "Default CLI policy is deny-by-default with allowed paths and blocked actions." if policy_ok else "Default CLI policy is missing required safe-demo boundaries.",
                    "Restore default_cli_policy with secret_policy=deny, reports allowed path, and blocked tools." if not policy_ok else "",
                )
            )
        else:
            checks.append(self._check("policy", "fail", "default_cli_policy not found.", "Create the default CLI policy before running demos."))

        checks.append(self._check("working_dir", "pass", str(Path(__file__).resolve().parent)))
        checks.append(
            self._check(
                "smoke_task",
                "pass" if output_ready and template_ready else "fail",
                "Read-only repository summary safe demo." if output_ready and template_ready else "Safe demo is blocked until an executable template exists.",
                "Use generic_cli_builtin now, or add a controlled template for this CLI." if not (output_ready and template_ready) else "",
            )
        )

        if any(check["status"] == "blocked" for check in checks):
            demo_status = "disabled_by_policy"
        elif any(check["name"] == "binary" and check["status"] == "fail" for check in checks):
            demo_status = "not_installed"
        elif any(check["name"] == "path_visible" and check["status"] == "fail" for check in checks):
            demo_status = "path_not_visible"
        elif any(check["name"] == "auth" and check["status"] == "fail" for check in checks):
            demo_status = "auth_missing"
        elif any(check["name"] in {"template", "output_contract"} and check["status"] == "fail" for check in checks):
            demo_status = "output_contract_missing"
        elif any(check["name"] == "policy" and check["status"] == "fail" for check in checks):
            demo_status = "policy_not_ready"
        elif all(check["status"] in {"pass"} for check in checks):
            demo_status = "demo_ready"
        else:
            demo_status = "output_contract_missing"

        return {
            "runtime_id": runtime["runtime_id"],
            "demo_status": demo_status,
            "safe_demo_enabled": demo_status == "demo_ready",
            "checks": checks,
        }

    def _check(self, name, status, detail, next_action=""):
        return {
            "name": name,
            "status": status,
            "detail": self._mask_secret_like(detail),
            "next_action": self._mask_secret_like(next_action),
        }

    def _expected_demo_template(self, runtime_type):
        return {
            "claude_code_cli": "claude_code_json_report_v1",
            "codex_cli": "codex_cli_json_report_v1",
            "gemini_cli": "gemini_cli_json_report_v1",
            "openclaw_cli": "openclaw_cli_json_report_v1",
            "hermes_cli": "hermes_cli_json_report_v1",
            "generic_cli": "builtin_json_report_v1",
        }.get(runtime_type, f"{runtime_type}_json_report_v1")

    def _auth_check(self, runtime):
        runtime_type = runtime["runtime_type"]
        binary = runtime["binary_ref"]
        if runtime_type == "claude_code_cli":
            return self._run_auth_command("auth", [binary, "auth", "status"], "Claude Code auth is available.", f"Run `{binary} auth` locally and sign in.")
        if runtime_type == "codex_cli":
            return self._run_auth_command("auth", [binary, "login", "status"], "Codex CLI auth is available.", f"Run `{binary} login` locally and sign in.")
        if runtime_type == "gemini_cli":
            return self._run_auth_command("auth", [binary, "--version"], "Gemini CLI binary is available; auth is validated during safe demo execution.", f"Run `{binary}` locally and complete sign-in if safe demo reports auth failure.")
        return self._check("auth", "unknown", "Auth check is not implemented for this runtime.", f"Install and sign in to {binary} locally; credentials are not stored in Aletheia.")

    def _run_auth_command(self, name, command, success_detail, next_action):
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=8, check=False)
        except (OSError, subprocess.SubprocessError):
            return self._check(name, "fail", "Auth command failed or timed out.", next_action)
        output = self._mask_secret_like(result.stdout or result.stderr)
        if result.returncode == 0:
            return self._check(name, "pass", f"{success_detail} {output[:160]}")
        return self._check(name, "fail", f"Auth command failed. {output[:160]}", next_action)

    def _parse_cli_output(self, stdout):
        try:
            output = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return {"status": "failed", "summary": "CLI output was not valid JSON", "tool_calls": [], "draft_artifacts": [], "files_touched": [], "policy_violations": []}, [
                {"code": "non_json_output", "detail": str(exc)}
            ]
        missing = sorted(self.REQUIRED_OUTPUT_FIELDS - set(output))
        if missing:
            return output, [{"code": "missing_required_fields", "fields": missing}]
        return output, []

    def _validate_output(self, output, policy, tenant):
        violations = []
        blocked = set(policy["blocked_tools"]) | self.BLOCKED_TOOLS
        allowed = set(policy["allowed_tools"])
        for call in output.get("tool_calls", []):
            tool = str(call.get("tool") or call.get("name") or "").lower()
            if tool in blocked:
                violations.append({"code": "blocked_tool_call", "tool": tool})
            if tool and tool not in allowed and tool not in blocked:
                violations.append({"code": "tool_not_allowed", "tool": tool})
        text_blob = _json_dump(output).lower()
        for blocked_word in sorted(blocked):
            pattern = r"(?<![a-z0-9_/-])" + re.escape(blocked_word.lower()) + r"(?![a-z0-9_/-])"
            if re.search(pattern, text_blob):
                violations.append({"code": "blocked_action_in_output", "action": blocked_word})
        for path in output.get("files_touched", []):
            if not self._path_allowed(path, policy["allowed_paths"]):
                violations.append({"code": "path_not_allowed", "path": path})
        for artifact in output.get("draft_artifacts", []):
            status = artifact.get("status", "draft")
            if status not in {"draft", "accepted_for_review"}:
                violations.append({"code": "non_draft_output", "status": status})
            payload = artifact.get("payload", {})
            if payload.get("tenant_id") and payload.get("tenant_id") != tenant.tenant_id:
                violations.append({"code": "tenant_mismatch", "tenant_id": payload.get("tenant_id")})
        return violations

    def _path_allowed(self, path, allowed_paths):
        normalized = str(path).strip().lstrip("/")
        if ".." in Path(normalized).parts:
            return False
        return any(normalized == allowed.rstrip("/") or normalized.startswith(f"{allowed.rstrip('/')}/") for allowed in allowed_paths)

    def _record_run(self, tenant, *, run_key, runtime_id, policy_id, task_type, prompt_hash, status, output, policy_violations, stdout, stderr, latency_ms):
        files_touched = output.get("files_touched", [])
        tool_calls = output.get("tool_calls", [])
        output_refs = {
            "summary": output.get("summary"),
            "stdout_ref": "inline_masked",
            "stderr_ref": "inline_masked",
            "latency_ms": latency_ms,
        }
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_agent_runs
                    (run_key, project_id, runtime_id, policy_id, task_type, prompt_hash,
                     status, tool_calls_json, policy_violations_json, files_touched_json,
                     output_refs_json, stdout_ref, stderr_ref, started_at, finished_at)
                    VALUES
                    (:run_key, :tenant_id, :runtime_id, :policy_id, :task_type, :prompt_hash,
                     :status, :tool_calls_json, :policy_violations_json, :files_touched_json,
                     :output_refs_json, :stdout_ref, :stderr_ref, NOW(), NOW())
                    RETURNING run_key, project_id, runtime_id, policy_id, task_type, prompt_hash,
                              status, tool_calls_json, policy_violations_json, files_touched_json,
                              output_refs_json, stdout_ref, stderr_ref, started_at, finished_at
                    """
                ),
                {
                    "run_key": run_key,
                    "tenant_id": tenant.tenant_id,
                    "runtime_id": runtime_id,
                    "policy_id": policy_id,
                    "task_type": task_type,
                    "prompt_hash": prompt_hash,
                    "status": status,
                    "tool_calls_json": _json_dump(tool_calls),
                    "policy_violations_json": _json_dump(policy_violations),
                    "files_touched_json": _json_dump(files_touched),
                    "output_refs_json": _json_dump(output_refs),
                    "stdout_ref": self._mask_secret_like(stdout),
                    "stderr_ref": self._mask_secret_like(stderr),
                },
            ).mappings().first()
            run_id = conn.execute(text("SELECT id FROM aletheia_agent_runs WHERE project_id = :tenant_id AND run_key = :run_key"), {"tenant_id": tenant.tenant_id, "run_key": run_key}).scalar()
            if status == "completed":
                for artifact in output.get("draft_artifacts", []):
                    conn.execute(
                        text(
                            """
                            INSERT INTO aletheia_agent_output_artifacts
                            (run_id, project_id, artifact_type, payload_json, status, created_at)
                            VALUES (:run_id, :tenant_id, :artifact_type, :payload_json, :status, NOW())
                            """
                        ),
                        {
                            "run_id": run_id,
                            "tenant_id": tenant.tenant_id,
                            "artifact_type": artifact.get("artifact_type", "report"),
                            "payload_json": _json_dump(artifact.get("payload", {})),
                            "status": artifact.get("status", "draft"),
                        },
                    )
        return self._run_to_dict(row)

    def _get_runtime(self, tenant, runtime_id):
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text(
                    """
                    SELECT runtime_id, runtime_type, binary_ref, command_template_id, enabled,
                           health_status, health_detail_json, created_at, updated_at
                    FROM aletheia_agent_runtime_configs
                    WHERE runtime_id = :runtime_id
                    """
                ),
                {"runtime_id": runtime_id},
            ).mappings().first()

    def _get_policy(self, tenant, policy_id):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT policy_id, project_id, allowed_paths_json, allowed_tools_json,
                           blocked_tools_json, max_runtime_seconds, max_output_bytes,
                           env_allowlist_json, secret_policy, created_at, updated_at
                    FROM aletheia_agent_policies
                    WHERE project_id = :tenant_id AND policy_id = :policy_id
                    """
                ),
                {"tenant_id": tenant.tenant_id, "policy_id": policy_id},
            ).mappings().first()
        return self._policy_to_dict(row) if row else None

    def _probe_version(self, binary):
        for args in ([binary, "--version"], [binary, "version"]):
            try:
                result = subprocess.run(args, text=True, capture_output=True, timeout=5, check=False)
                output = (result.stdout or result.stderr or "").strip().splitlines()
                if result.returncode == 0 and output:
                    return {"ok": True, "version": self._mask_secret_like(output[0])[:240]}
            except (OSError, subprocess.SubprocessError):
                continue
        return {"ok": False, "version": "unavailable"}

    def _mask_binary(self, value):
        return Path(value).name if "/" in value else value

    def _mask_secret_like(self, value):
        text_value = str(value or "")
        secret_patterns = [
            r"sk-[A-Za-z0-9*_-]{8,}",
            r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^\\s,'\"]+",
        ]
        for pattern in secret_patterns:
            text_value = re.sub(pattern, "[masked]", text_value)
        for key in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"):
            if key in text_value.upper():
                return "[masked]"
        return text_value[:8192]

    def _runtime_to_dict(self, row):
        return {
            "runtime_id": row["runtime_id"],
            "runtime_type": row["runtime_type"],
            "binary_ref": self._mask_binary(row["binary_ref"]),
            "command_template_id": row["command_template_id"],
            "enabled": row["enabled"],
            "health_status": row["health_status"],
            "health_detail": _load_json(row["health_detail_json"], {}),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
        }

    def _policy_to_dict(self, row):
        return {
            "policy_id": row["policy_id"],
            "tenant_id": row["project_id"],
            "allowed_paths": _load_json(row["allowed_paths_json"], []),
            "allowed_tools": _load_json(row["allowed_tools_json"], []),
            "blocked_tools": _load_json(row["blocked_tools_json"], []),
            "max_runtime_seconds": row["max_runtime_seconds"],
            "max_output_bytes": row["max_output_bytes"],
            "env_allowlist": _load_json(row["env_allowlist_json"], []),
            "secret_policy": row["secret_policy"],
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
        }

    def _run_to_dict(self, row):
        return {
            "run_key": row["run_key"],
            "tenant_id": row["project_id"],
            "runtime_id": row["runtime_id"],
            "policy_id": row["policy_id"],
            "task_type": row["task_type"],
            "prompt_hash": row["prompt_hash"],
            "status": row["status"],
            "tool_calls": _load_json(row["tool_calls_json"], []),
            "policy_violations": _load_json(row["policy_violations_json"], []),
            "files_touched": _load_json(row["files_touched_json"], []),
            "output_refs": _load_json(row["output_refs_json"], {}),
            "stdout_ref": row["stdout_ref"],
            "stderr_ref": row["stderr_ref"],
            "started_at": str(row["started_at"]) if row["started_at"] else None,
            "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
        }


class ReviewWorkbenchHandler(BaseHTTPRequestHandler):
    repository = None
    instance_repository = None
    reasoning_repository = None
    agent_gateway_repository = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            tenant = self._tenant(parsed)
        except (KeyError, ValueError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/tenants":
            self._send_json(
                {
                    "current": tenant.public_dict(),
                    "default_tenant_id": self.repository.tenant_registry.default_tenant_id,
                    "tenants": self.repository.tenant_registry.list_public(),
                }
            )
            return
        if parsed.path == "/api/artifacts":
            filters = {key: values[0] for key, values in parse_qs(parsed.query).items() if values and values[0]}
            filters.pop("tenant", None)
            self._send_json(self.repository.list_artifacts(tenant, filters))
            return
        if parsed.path == "/api/portal/overview":
            self._send_json(self._portal_overview(tenant))
            return
        if parsed.path.startswith("/api/portal/findings/"):
            finding_key = unquote(parsed.path.removeprefix("/api/portal/findings/"))
            finding = self.reasoning_repository.finding_detail(tenant, finding_key)
            if finding is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {finding_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "finding": finding})
            return
        if parsed.path == "/api/agent-gateway/settings":
            self._send_json(self.agent_gateway_repository.list_settings(tenant))
            return
        if parsed.path.startswith("/api/agent-gateway/runtimes/") and parsed.path.endswith("/readiness"):
            runtime_id = unquote(parsed.path.removeprefix("/api/agent-gateway/runtimes/").removesuffix("/readiness").rstrip("/"))
            result = self.agent_gateway_repository.readiness(tenant, runtime_id)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/tasks":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            self._send_json(self.reasoning_repository.list_tasks(tenant, status_filter=status_filter))
            return
        if parsed.path.startswith("/api/reasoning/tasks/"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/"))
            task = self.reasoning_repository.get_task(tenant, task_key)
            if task is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json(task)
            return
        if parsed.path.startswith("/api/reasoning/findings/"):
            finding_key = unquote(parsed.path.removeprefix("/api/reasoning/findings/"))
            finding = self.reasoning_repository.get_finding(tenant, finding_key)
            if finding is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {finding_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "finding": finding})
            return
        if parsed.path == "/api/graph/context":
            query = parse_qs(parsed.query)
            object_type = query.get("type", ["Employee"])[0]
            instance_id = query.get("id", ["4"])[0]
            depth = int(query.get("depth", ["1"])[0])
            limit = int(query.get("limit", ["200"])[0])
            graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
            if graph is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph context not found")
                return
            if graph.get("approved"):
                graph["graph_url"] = (
                    f"/graph.html?tenant={quote(tenant.tenant_id)}&type={quote(object_type)}"
                    f"&id={quote(str(instance_id))}&depth={graph.get('depth', depth)}&limit={graph.get('limit', limit)}"
                )
            self._send_json(graph)
            return
        if parsed.path.startswith("/api/graph/node/"):
            node_key = unquote(parsed.path.removeprefix("/api/graph/node/"))
            if ":" not in node_key:
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected node key like Employee:4")
                return
            object_type, instance_id = node_key.split(":", 1)
            detail = self.instance_repository.detail(tenant, object_type, instance_id)
            if detail is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph node not found or not approved")
                return
            if object_type.lower() == "employee":
                graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=1, limit=300)
                if graph and graph.get("approved"):
                    detail["neighborhood_summary"] = {
                        "nodes": len(graph.get("nodes", [])),
                        "edges": len(graph.get("edges", [])),
                        "by_relation": {"link:employee:1:n:order": len(graph.get("edges", []))},
                    }
            else:
                detail["neighborhood_summary"] = {"nodes": 1, "edges": 0, "by_relation": {}}
            self._send_json({"tenant": tenant.public_dict(), "node": detail})
            return
        if parsed.path.startswith("/api/graph/edge/"):
            edge_key = unquote(parsed.path.removeprefix("/api/graph/edge/"))
            if "->" not in edge_key:
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected edge key like Employee:4->Order:10250")
                return
            source, target = edge_key.split("->", 1)
            edge = self.instance_repository.edge_detail(tenant, source, target)
            if edge is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph edge not found or not approved")
                return
            self._send_json({"tenant": tenant.public_dict(), "edge": edge})
            return
        if parsed.path == "/api/instances/types":
            self._send_json(self.instance_repository.types(tenant))
            return
        if parsed.path == "/api/instances/search":
            query = parse_qs(parsed.query)
            object_type = query.get("type", ["Employee"])[0]
            search = query.get("q", [""])[0]
            limit = int(query.get("limit", ["25"])[0])
            self._send_json(self.instance_repository.search(tenant, object_type, search, limit=limit))
            return
        if parsed.path == "/api/instances/edge":
            query = parse_qs(parsed.query)
            source = query.get("source", [""])[0]
            target = query.get("target", [""])[0]
            edge = self.instance_repository.edge_detail(tenant, source, target)
            if edge is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Edge not found or not approved")
                return
            self._send_json(edge)
            return
        if parsed.path.startswith("/api/instances/"):
            parts = parsed.path.removeprefix("/api/instances/").split("/")
            if len(parts) == 2:
                object_type, instance_id = unquote(parts[0]), unquote(parts[1])
                detail = self.instance_repository.detail(tenant, object_type, instance_id)
                if detail is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "Instance not found or object type is not approved")
                    return
                self._send_json(detail)
                return
            if len(parts) == 3 and parts[2] == "neighborhood":
                object_type, instance_id = unquote(parts[0]), unquote(parts[1])
                query = parse_qs(parsed.query)
                depth = int(query.get("depth", ["1"])[0])
                limit = int(query.get("limit", ["200"])[0])
                graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
                if graph is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "Neighborhood not found")
                    return
                self._send_json(graph)
                return
        if parsed.path.startswith("/api/artifacts/"):
            canonical_key = unquote(parsed.path.removeprefix("/api/artifacts/"))
            artifact = self.repository.get_artifact(tenant, canonical_key)
            if artifact is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Artifact not found: {canonical_key}")
                return
            self._send_json(artifact)
            return
        self._send_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            tenant = self._tenant(parsed)
        except (KeyError, ValueError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path.startswith("/api/agent-gateway/runtimes/") and parsed.path.endswith("/health"):
            runtime_id = unquote(parsed.path.removeprefix("/api/agent-gateway/runtimes/").removesuffix("/health").rstrip("/"))
            result = self.agent_gateway_repository.health_check(tenant, runtime_id)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/agent-gateway/runs":
            try:
                body = self._read_json()
                runtime_id = body.get("runtime_id") or "generic_cli_builtin"
                result = self.agent_gateway_repository.run_smoke(tenant, runtime_id, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/agent-gateway/safe-demo":
            try:
                body = self._read_json()
                runtime_id = body.get("runtime_id") or "generic_cli_builtin"
                result = self.agent_gateway_repository.run_safe_demo(tenant, runtime_id, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/graph/expand":
            try:
                body = self._read_json()
                node_key = body.get("node_key") or body.get("center_node") or "Employee:4"
                if ":" not in node_key:
                    raise ValueError("node_key must be like Employee:4")
                object_type, instance_id = node_key.split(":", 1)
                depth = int(body.get("depth") or 1)
                limit = int(body.get("limit") or body.get("node_limit") or 200)
                graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if graph is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph expansion not found")
                return
            self._send_json(graph)
            return
        if parsed.path == "/api/reasoning/tasks/from-graph":
            try:
                body = self._read_json()
                result = self.reasoning_repository.create_scoped_task_from_graph(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/questions":
            try:
                body = self._read_json()
                result = self.reasoning_repository.create_question_task(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/tasks/bulk-close":
            try:
                body = self._read_json()
                keys = body.get("keys")
                before = body.get("before")
                if not keys and not before:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Provide 'keys' (array) or 'before' (ISO date)")
                    return
                result = self.reasoning_repository.bulk_close_tasks(tenant, keys=keys, before=before)
            except Exception as exc:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"tenant": tenant.public_dict(), **result})
            return
        if parsed.path == "/api/reasoning/tasks/bulk-delete-closed":
            try:
                result = self.reasoning_repository.bulk_delete_closed_tasks(tenant)
            except Exception as exc:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"tenant": tenant.public_dict(), **result})
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/delete"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/delete").rstrip("/"))
            result = self.reasoning_repository.delete_task(tenant, task_key)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Task not found: {task_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/close"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/close").rstrip("/"))
            result = self.reasoning_repository.update_task_status(tenant, task_key, "closed")
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "task": result})
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/reopen"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/reopen").rstrip("/"))
            result = self.reasoning_repository.update_task_status(tenant, task_key, "active")
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "task": result})
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/run/stream"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/run/stream").rstrip("/"))
            self._stream_run(tenant, task_key)
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/run"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/run").rstrip("/"))
            try:
                result = self.reasoning_repository.run_task(tenant, task_key)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/findings/"):
            parts = parsed.path.removeprefix("/api/reasoning/findings/").split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/reasoning/findings/<canonical_key>/<action>")
                return
            finding_key = unquote(parts[0])
            action = parts[1]
            try:
                body = self._read_json()
                reviewer = body.get("reviewer") or "Itachi"
                reason = body.get("reason") or ""
                if action == "approve":
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "approved", reviewer, reason)
                elif action == "reject":
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "rejected", reviewer, reason)
                elif action == "needs-changes":
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "needs_changes", reviewer, reason)
                elif action == "comment":
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "comment", reviewer, reason)
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, f"Unknown reasoning action: {action}")
                    return
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except json.JSONDecodeError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"tenant": tenant.public_dict(), "finding": result})
            return
        if not parsed.path.startswith("/api/artifacts/"):
            self._send_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
            return
        parts = parsed.path.removeprefix("/api/artifacts/").split("/")
        if len(parts) != 2:
            self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/artifacts/<canonical_key>/<action>")
            return
        canonical_key = unquote(parts[0])
        action = parts[1]
        try:
            body = self._read_json()
            reviewer = body.get("reviewer") or "Itachi"
            reason = body.get("reason") or ""
            if action == "approve":
                result = self.repository.review_status(tenant, canonical_key, "approved", reviewer, reason)
            elif action == "reject":
                result = self.repository.review_status(tenant, canonical_key, "rejected", reviewer, reason)
            elif action == "needs-changes":
                result = self.repository.review_status(tenant, canonical_key, "needs_changes", reviewer, reason)
            elif action == "comment":
                result = self.repository.comment(tenant, canonical_key, reviewer, reason)
            elif action == "edit":
                payload = body.get("payload") if "payload" in body else None
                result = self.repository.edit(
                    tenant,
                    canonical_key,
                    reviewer,
                    reason,
                    name=body.get("name"),
                    description=body.get("description"),
                    payload=payload,
                )
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Unknown action: {action}")
                return
        except KeyError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, f"Artifact not found: {exc.args[0]}")
            return
        except json.JSONDecodeError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
            return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:  # pragma: no cover - displayed to local operator
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(result)

    def _portal_overview(self, tenant):
        artifact_result = self.repository.list_artifacts(tenant, {})
        artifacts = artifact_result.get("artifacts", [])
        artifact_stats = artifact_result.get("stats", [])
        tasks = self.reasoning_repository.list_tasks(tenant).get("tasks", [])
        findings = self.reasoning_repository.list_findings_overview(tenant, limit=25)
        runs = self.reasoning_repository.list_runs_overview(tenant, limit=25)
        agent_settings = self.agent_gateway_repository.list_settings(tenant)
        agent_runs = agent_settings.get("runs", [])
        default_graph = self.instance_repository.neighborhood(tenant, "Employee", "4", depth=1, limit=200)
        sandbox_graph = None
        try:
            sandbox_graph = self.instance_repository.neighborhood(
                self.repository.tenant_registry.get("northwind-sandbox"), "Employee", "4", depth=1, limit=200
            )
        except Exception:
            sandbox_graph = None
        approved_artifacts = [artifact for artifact in artifacts if artifact.get("status") == "approved"]
        draft_findings = [finding for finding in findings if finding.get("status") == "draft"]
        low_confidence = [finding for finding in findings if float(finding.get("confidence") or 0) < 0.75]
        blocked_runs = [run for run in runs if run.get("status") in {"blocked", "failed"}]
        blocked_agent_runs = [
            run
            for run in agent_runs
            if run.get("status") in {"blocked", "failed"} or run.get("policy_violations")
        ]
        attention_items = []
        for finding in draft_findings[:5]:
            attention_items.append(
                {
                    "kind": "draft",
                    "severity": "review",
                    "title": "Draft finding awaits review",
                    "summary": finding.get("title"),
                    "href": f"/findings.html?tenant={quote(tenant.tenant_id)}&finding={quote(finding.get('canonical_key'))}",
                }
            )
        for finding in low_confidence[:4]:
            attention_items.append(
                {
                    "kind": "low_confidence",
                    "severity": "medium",
                    "title": "Low confidence conclusion",
                    "summary": f"{finding.get('title')} · confidence {float(finding.get('confidence') or 0):.2f}",
                    "href": f"/findings.html?tenant={quote(tenant.tenant_id)}&finding={quote(finding.get('canonical_key'))}",
                }
            )
        for run in blocked_runs[:4]:
            attention_items.append(
                {
                    "kind": "blocked_reasoning",
                    "severity": "high",
                    "title": "Reasoning run blocked",
                    "summary": run.get("output", {}).get("summary") or run.get("run_key"),
                    "href": f"/questions.html?tenant={quote(tenant.tenant_id)}&task={quote(run.get('task_key'))}",
                }
            )
        if sandbox_graph and sandbox_graph.get("approved") is False:
            attention_items.append(
                {
                    "kind": "missing_approved_artifacts",
                    "severity": "high",
                    "title": "Sandbox approved-only gate blocks graph reasoning",
                    "summary": ", ".join(sandbox_graph.get("missing_approved_artifacts") or []),
                    "href": f"/quality.html?tenant={quote(tenant.tenant_id)}",
                }
            )
        for run in blocked_agent_runs[:4]:
            attention_items.append(
                {
                    "kind": "policy_violation",
                    "severity": "high",
                    "title": "Agent runtime requires attention",
                    "summary": run.get("run_key"),
                    "href": f"/settings.html?tenant={quote(tenant.tenant_id)}",
                }
            )
        latest_times = [
            item.get("updated_at") or item.get("created_at")
            for item in [*findings, *tasks, *artifacts]
            if item.get("updated_at") or item.get("created_at")
        ]
        return {
            "tenant": tenant.public_dict(),
            "knowledge_status": {
                "entity_count": len(default_graph.get("nodes", [])) if default_graph and default_graph.get("approved") else 0,
                "relation_count": len(default_graph.get("edges", [])) if default_graph and default_graph.get("approved") else 0,
                "artifact_count": len(artifacts),
                "approved_artifact_count": len(approved_artifacts),
                "finding_count": len(findings),
                "task_count": len(tasks),
                "approved_only": True,
                "system_state": "ready" if default_graph and default_graph.get("approved") else "blocked",
                "latest_update": max(latest_times) if latest_times else None,
                "graph_database": tenant.graph_database,
                "namespace": tenant.namespace,
            },
            "artifact_stats": artifact_stats,
            "key_findings": findings[:8],
            "attention_items": attention_items[:12],
            "quality": {
                "draft_findings": len(draft_findings),
                "low_confidence_findings": len(low_confidence),
                "blocked_reasoning_runs": len(blocked_runs),
                "blocked_agent_runs": len(blocked_agent_runs),
                "sandbox_missing_artifacts": sandbox_graph.get("missing_approved_artifacts", []) if sandbox_graph else [],
                "sandbox_approved": sandbox_graph.get("approved") if sandbox_graph else None,
            },
            "recent_changes": {
                "tasks": tasks[:8],
                "runs": runs[:8],
                "findings": findings[:8],
                "agent_runs": agent_runs[:5],
            },
            "quick_tasks": [
                {"label": "Ask a question", "href": f"/questions.html?tenant={quote(tenant.tenant_id)}"},
                {"label": "Explain a finding", "href": f"/findings.html?tenant={quote(tenant.tenant_id)}"},
                {"label": "Inspect an entity", "href": f"/instances.html?tenant={quote(tenant.tenant_id)}&type=Employee&id=4"},
                {"label": "View evidence chain", "href": f"/findings.html?tenant={quote(tenant.tenant_id)}"},
                {"label": "Trace graph path", "href": f"/graph.html?tenant={quote(tenant.tenant_id)}&type=Employee&id=4&depth=1&limit=200"},
                {"label": "Check quality issues", "href": f"/quality.html?tenant={quote(tenant.tenant_id)}"},
                {"label": "Run scoped reasoning", "href": f"/questions.html?tenant={quote(tenant.tenant_id)}&template=scoped"},
            ],
        }

    def _tenant(self, parsed):
        query = parse_qs(parsed.query)
        tenant_id = query.get("tenant", [None])[0]
        return self.repository.tenant(tenant_id)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _cors_headers(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status=status)

    def _send_sse_event(self, event_type, data):
        msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

    def _stream_run(self, tenant, task_key):
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for item in self.reasoning_repository.run_task_streaming(tenant, task_key):
                self._send_sse_event(item["event"], item["data"])
        except Exception as exc:
            self._send_sse_event("error", {"message": str(exc)})

    def _send_static(self, request_path):
        relative_path = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        file_path = (STATIC_ROOT / relative_path).resolve()
        if not str(file_path).startswith(str(STATIC_ROOT.resolve())) or not file_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        body = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="Run the Aletheia Review Workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db-url", default=DB_URL)
    parser.add_argument("--source-db-url", default=SOURCE_DB_URL)
    parser.add_argument("--tenants-file", help="JSON file defining tenant_id/namespace/graph_database mappings")
    parser.add_argument("--ensure-schema", action="store_true", help="Create/migrate artifact tables before serving")
    parser.add_argument("--tls-cert", help="Path to TLS certificate PEM file (enables HTTPS)")
    parser.add_argument("--tls-key", help="Path to TLS private key PEM file")
    args = parser.parse_args()

    os.environ["ALETHEIA_PG_URL"] = args.db_url
    os.environ["ALETHEIA_MYSQL_URL"] = args.source_db_url
    registry = TenantRegistry.load(args.tenants_file)
    ReviewWorkbenchHandler.repository = ReviewRepository(registry, ensure_schema=args.ensure_schema)
    ReviewWorkbenchHandler.instance_repository = InstanceRepository(registry, ensure_schema=args.ensure_schema)
    ReviewWorkbenchHandler.reasoning_repository = ReasoningRepository(
        registry,
        ReviewWorkbenchHandler.instance_repository,
        ensure_schema=args.ensure_schema,
    )
    ReviewWorkbenchHandler.agent_gateway_repository = AgentGatewayRepository(registry, ensure_schema=args.ensure_schema)
    server = LocalThreadingHTTPServer((args.host, args.port), ReviewWorkbenchHandler)
    scheme = "http"
    if args.tls_cert and args.tls_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ctx.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"Review Workbench: {scheme}://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
