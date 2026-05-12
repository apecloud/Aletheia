import argparse
import json
import mimetypes
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import create_engine, text

sys.path.append(str(Path(__file__).resolve().parent / "agents"))
from ontology_artifacts import ensure_artifact_schema  # noqa: E402


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
    def __init__(self, db_url, ensure_schema=False):
        self.engine = create_engine(db_url)
        if ensure_schema:
            ensure_artifact_schema(self.engine)

    def list_artifacts(self, filters):
        conditions = []
        params = {}
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
        with self.engine.connect() as conn:
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
                    GROUP BY artifact_type, status
                    ORDER BY artifact_type, status
                    """
                )
            ).mappings().all()
        return {
            "artifacts": [_artifact_to_dict(row) for row in rows],
            "stats": [dict(row) for row in stats],
        }

    def get_artifact(self, canonical_key):
        with self.engine.connect() as conn:
            artifact = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, artifact_type, name, description,
                           payload_json, confidence, source_refs_json, status, version,
                           source_agent, created_at, updated_at
                    FROM aletheia_ontology_artifacts
                    WHERE canonical_key = :canonical_key
                    """
                ),
                {"canonical_key": canonical_key},
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

    def review_status(self, canonical_key, status, reviewer, reason):
        _require_reason(status, reason or "")
        with self.engine.begin() as conn:
            artifact = self._fetch_for_update(conn, canonical_key)
            before_status = artifact["status"]
            before_version = artifact["version"]
            before_payload_json = artifact["payload_json"]
            after_version = before_version + 1
            conn.execute(
                text(
                    """
                    UPDATE aletheia_ontology_artifacts
                    SET status = :status, version = version + 1, updated_at = NOW()
                    WHERE canonical_key = :canonical_key
                    """
                ),
                {"status": status, "canonical_key": canonical_key},
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
        return self.get_artifact(canonical_key)

    def comment(self, canonical_key, reviewer, reason):
        _require_reason("comment", reason or "")
        with self.engine.begin() as conn:
            artifact = self._fetch_for_update(conn, canonical_key)
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
        return self.get_artifact(canonical_key)

    def edit(self, canonical_key, reviewer, reason, name=None, description=None, payload=None):
        with self.engine.begin() as conn:
            artifact = self._fetch_for_update(conn, canonical_key)
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
                    WHERE canonical_key = :canonical_key
                    """
                ),
                {
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
        return self.get_artifact(canonical_key)

    def _fetch_for_update(self, conn, canonical_key):
        artifact = conn.execute(
            text(
                """
                SELECT id, canonical_key, artifact_type, name, description, payload_json,
                       status, version
                FROM aletheia_ontology_artifacts
                WHERE canonical_key = :canonical_key
                FOR UPDATE
                """
            ),
            {"canonical_key": canonical_key},
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
                (artifact_id, canonical_key, decision, reviewer, reason,
                 before_status, after_status, before_version, after_version,
                 before_payload_json, after_payload_json, created_at)
                VALUES
                (:artifact_id, :canonical_key, :decision, :reviewer, :reason,
                 :before_status, :after_status, :before_version, :after_version,
                 :before_payload_json, :after_payload_json, NOW())
                """
            ),
            {
                "artifact_id": artifact["id"],
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
    def __init__(self, metadata_db_url, source_db_url):
        self.metadata_engine = create_engine(metadata_db_url)
        self.source_engine = create_engine(source_db_url)

    def types(self):
        artifacts = self._approved_artifacts(["object:employee", "object:order"])
        types = []
        if "object:employee" in artifacts:
            types.append(
                {
                    "type": "Employee",
                    "label": "Employee",
                    "ontology_artifact": "object:employee",
                }
            )
        if "object:order" in artifacts:
            types.append(
                {
                    "type": "Order",
                    "label": "Order",
                    "ontology_artifact": "object:order",
                }
            )
        return {"types": types}

    def search(self, object_type, query, limit=25):
        canonical_key = self._object_key(object_type)
        artifacts = self._approved_artifacts([canonical_key])
        if canonical_key not in artifacts:
            return {"instances": [], "approved": False, "reason": f"{canonical_key} is not approved"}
        if object_type.lower() != "employee":
            return {"instances": [], "approved": True, "reason": "MVP search supports Employee only"}
        sql = """
            SELECT employeeID, firstName, lastName, title, city, reportsTo
            FROM employees
            WHERE (:query = ''
               OR CAST(employeeID AS CHAR) = :query
               OR firstName LIKE :like_query
               OR lastName LIKE :like_query
               OR CONCAT(firstName, ' ', lastName) LIKE :like_query)
            ORDER BY employeeID
            LIMIT :limit
        """
        with self.source_engine.connect() as conn:
            rows = conn.execute(
                text(sql),
                {
                    "query": query,
                    "like_query": f"%{query}%",
                    "limit": limit,
                },
            ).mappings().all()
        return {
            "instances": [
                {
                    "id": f"Employee:{row['employeeID']}",
                    "type": "Employee",
                    "label": self._employee_label(row),
                    "summary": row["title"],
                    "source_table": "employees",
                    "source_pk": f"employeeID={row['employeeID']}",
                    "ontology_artifact": "object:employee",
                }
                for row in rows
            ],
            "approved": True,
        }

    def detail(self, object_type, instance_id):
        canonical_key = self._object_key(object_type)
        artifacts = self._approved_artifacts([canonical_key])
        if canonical_key not in artifacts:
            return None
        if object_type.lower() == "employee":
            employee = self._fetch_employee(instance_id)
            if not employee:
                return None
            order_count = self._order_count_for_employee(instance_id)
            reports = self._employee_reports(instance_id)
            return {
                "id": f"Employee:{employee['employeeID']}",
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
            order = self._fetch_order(instance_id)
            if not order:
                return None
            return {
                "id": f"Order:{order['orderID']}",
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

    def neighborhood(self, object_type, instance_id, depth=1, limit=200):
        if object_type.lower() != "employee":
            return None
        artifacts = self._approved_artifacts(
            ["object:employee", "object:order", "link:employee:1:n:order"]
        )
        missing = [
            key
            for key in ["object:employee", "object:order", "link:employee:1:n:order"]
            if key not in artifacts
        ]
        if missing:
            return {
                "approved": False,
                "missing_approved_artifacts": missing,
                "center": None,
                "nodes": [],
                "edges": [],
            }
        employee = self._fetch_employee(instance_id)
        if not employee:
            return None
        with self.source_engine.connect() as conn:
            orders = conn.execute(
                text(
                    """
                    SELECT orderID, customerID, employeeID, orderDate, requiredDate,
                           shippedDate, shipName, freight
                    FROM orders
                    WHERE employeeID = :employee_id
                    ORDER BY orderID
                    LIMIT :limit
                    """
                ),
                {"employee_id": instance_id, "limit": limit},
            ).mappings().all()
        center = self._employee_node(employee)
        order_nodes = [self._order_node(row) for row in orders]
        edges = [self._employee_order_edge(employee, row) for row in orders]
        return {
            "approved": True,
            "depth": min(int(depth), 1),
            "limit": limit,
            "center": center,
            "nodes": [center] + order_nodes,
            "edges": edges,
            "relations_summary": {
                "handled_orders": self._order_count_for_employee(instance_id),
                "returned_orders": len(orders),
            },
        }

    def edge_detail(self, source, target):
        if not source.startswith("Employee:") or not target.startswith("Order:"):
            return None
        artifacts = self._approved_artifacts(
            ["object:employee", "object:order", "link:employee:1:n:order"]
        )
        if "link:employee:1:n:order" not in artifacts:
            return None
        employee_id = source.split(":", 1)[1]
        order_id = target.split(":", 1)[1]
        employee = self._fetch_employee(employee_id)
        order = self._fetch_order(order_id)
        if not employee or not order or str(order.get("employeeID")) != str(employee_id):
            return None
        return self._employee_order_edge(employee, order, include_rows=True)

    def _approved_artifacts(self, keys):
        with self.metadata_engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT canonical_key, name, artifact_type, status, payload_json
                    FROM aletheia_ontology_artifacts
                    WHERE canonical_key = ANY(:keys) AND status = 'approved'
                    """
                ),
                {"keys": list(keys)},
            ).mappings().all()
        return {row["canonical_key"]: dict(row) for row in rows}

    def _object_key(self, object_type):
        return f"object:{object_type}".lower()

    def _fetch_employee(self, employee_id):
        with self.source_engine.connect() as conn:
            return conn.execute(
                text("SELECT * FROM employees WHERE employeeID = :employee_id"),
                {"employee_id": employee_id},
            ).mappings().first()

    def _fetch_order(self, order_id):
        with self.source_engine.connect() as conn:
            return conn.execute(
                text("SELECT * FROM orders WHERE orderID = :order_id"),
                {"order_id": order_id},
            ).mappings().first()

    def _order_count_for_employee(self, employee_id):
        with self.source_engine.connect() as conn:
            return conn.execute(
                text("SELECT COUNT(*) FROM orders WHERE employeeID = :employee_id"),
                {"employee_id": employee_id},
            ).scalar()

    def _employee_reports(self, employee_id):
        with self.source_engine.connect() as conn:
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

    def _employee_node(self, row):
        return {
            "id": f"Employee:{row['employeeID']}",
            "type": "Employee",
            "label": self._employee_label(row),
            "summary": row.get("title"),
            "source_table": "employees",
            "source_pk": f"employeeID={row['employeeID']}",
            "ontology_artifact": "object:employee",
            "status": "approved",
        }

    def _order_node(self, row):
        return {
            "id": f"Order:{row['orderID']}",
            "type": "Order",
            "label": f"Order #{row['orderID']}",
            "summary": f"Customer {row.get('customerID')} · {row.get('orderDate')}",
            "source_table": "orders",
            "source_pk": f"orderID={row['orderID']}",
            "ontology_artifact": "object:order",
            "status": "approved",
        }

    def _employee_order_edge(self, employee, order, include_rows=False):
        edge = {
            "id": f"Employee:{employee['employeeID']}->Order:{order['orderID']}",
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
        }
        if include_rows:
            edge["source_instance"] = self._employee_node(employee)
            edge["target_instance"] = self._order_node(order)
            edge["source_row"] = self._row(employee)
            edge["target_row"] = self._row(order)
        return edge

    def _row(self, row):
        return {key: _jsonable(value) for key, value in dict(row).items()}


class ReviewWorkbenchHandler(BaseHTTPRequestHandler):
    repository = None
    instance_repository = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/artifacts":
            filters = {key: values[0] for key, values in parse_qs(parsed.query).items() if values and values[0]}
            self._send_json(self.repository.list_artifacts(filters))
            return
        if parsed.path == "/api/instances/types":
            self._send_json(self.instance_repository.types())
            return
        if parsed.path == "/api/instances/search":
            query = parse_qs(parsed.query)
            object_type = query.get("type", ["Employee"])[0]
            search = query.get("q", [""])[0]
            limit = int(query.get("limit", ["25"])[0])
            self._send_json(self.instance_repository.search(object_type, search, limit=limit))
            return
        if parsed.path == "/api/instances/edge":
            query = parse_qs(parsed.query)
            source = query.get("source", [""])[0]
            target = query.get("target", [""])[0]
            edge = self.instance_repository.edge_detail(source, target)
            if edge is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Edge not found or not approved")
                return
            self._send_json(edge)
            return
        if parsed.path.startswith("/api/instances/"):
            parts = parsed.path.removeprefix("/api/instances/").split("/")
            if len(parts) == 2:
                object_type, instance_id = unquote(parts[0]), unquote(parts[1])
                detail = self.instance_repository.detail(object_type, instance_id)
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
                graph = self.instance_repository.neighborhood(object_type, instance_id, depth=depth, limit=limit)
                if graph is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "Neighborhood not found")
                    return
                self._send_json(graph)
                return
        if parsed.path.startswith("/api/artifacts/"):
            canonical_key = unquote(parsed.path.removeprefix("/api/artifacts/"))
            artifact = self.repository.get_artifact(canonical_key)
            if artifact is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Artifact not found: {canonical_key}")
                return
            self._send_json(artifact)
            return
        self._send_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
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
                result = self.repository.review_status(canonical_key, "approved", reviewer, reason)
            elif action == "reject":
                result = self.repository.review_status(canonical_key, "rejected", reviewer, reason)
            elif action == "needs-changes":
                result = self.repository.review_status(canonical_key, "needs_changes", reviewer, reason)
            elif action == "comment":
                result = self.repository.comment(canonical_key, reviewer, reason)
            elif action == "edit":
                payload = body.get("payload") if "payload" in body else None
                result = self.repository.edit(
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

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status=status)

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
    parser.add_argument("--ensure-schema", action="store_true", help="Create/migrate artifact tables before serving")
    args = parser.parse_args()

    ReviewWorkbenchHandler.repository = ReviewRepository(args.db_url, ensure_schema=args.ensure_schema)
    ReviewWorkbenchHandler.instance_repository = InstanceRepository(args.db_url, args.source_db_url)
    server = LocalThreadingHTTPServer((args.host, args.port), ReviewWorkbenchHandler)
    print(f"Review Workbench: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
