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
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "agents"))

from sqlalchemy import create_engine, inspect, text

from reasoning_engine import ReasoningEngine
from server.graph_projection_fixtures import GRAPH_ENTITY_CONFIG, GRAPH_LINK_CONFIG, SOURCE_LINK_SCHEMAS

from iterative_graph_enrichment_agent import IterativeGraphEnrichmentAgent  # noqa: E402
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
STATIC_ROOT = ROOT / "web" / "review_workbench"


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


def _web_enrichment_query(raw_payload, target_artifact_key=None):
    raw_payload = raw_payload or {}
    candidates = []

    top_level_search = raw_payload.get("search_query")
    if isinstance(top_level_search, dict):
        candidates.extend(
            [
                top_level_search.get("query"),
                top_level_search.get("q"),
                top_level_search.get("text"),
            ]
        )
    elif isinstance(top_level_search, str):
        candidates.append(top_level_search)

    source = raw_payload.get("source") or {}
    if isinstance(source, dict):
        nested_search = source.get("search_query")
        if isinstance(nested_search, dict):
            candidates.extend(
                [
                    nested_search.get("query"),
                    nested_search.get("q"),
                    nested_search.get("text"),
                ]
            )
        elif isinstance(nested_search, str):
            candidates.append(nested_search)
        candidates.extend([source.get("query"), source.get("q"), source.get("search_text")])

    candidates.extend([raw_payload.get("query"), raw_payload.get("q"), raw_payload.get("search_text")])
    for candidate in candidates:
        if candidate:
            return str(candidate)
    if target_artifact_key:
        return f"{target_artifact_key} web enrichment evidence"
    return None


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _fmt_number(value):
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "scope"


def _jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _field_property_from_row(row, table_name):
    key = row.get("COLUMN_KEY") or ""
    primary_key = True if key == "PRI" else None if not key else False
    foreign_key = True if key == "MUL" else None if not key else False
    return {
        "name": row.get("COLUMN_NAME"),
        "source_table": table_name,
        "qualified_name": f"{table_name}.{row.get('COLUMN_NAME')}",
        "data_type": row.get("DATA_TYPE"),
        "column_type": row.get("COLUMN_TYPE") or row.get("DATA_TYPE"),
        "nullable": row.get("IS_NULLABLE") == "YES",
        "primary_key": primary_key,
        "foreign_key": foreign_key,
        "key_role": "primary_key" if key == "PRI" else "foreign_key" if key == "MUL" else "unknown",
        "default": row.get("COLUMN_DEFAULT"),
        "extra": row.get("EXTRA") or "",
        "comment": row.get("COLUMN_COMMENT") or "",
        "max_length": row.get("CHARACTER_MAXIMUM_LENGTH"),
        "numeric_precision": row.get("NUMERIC_PRECISION"),
        "numeric_scale": row.get("NUMERIC_SCALE"),
        "ordinal_position": row.get("ORDINAL_POSITION"),
        "maps_to_property": row.get("COLUMN_NAME"),
    }


def _require_reason(action, reason):
    if action in {
        "approve",
        "approved",
        "reject",
        "rejected",
        "needs_changes",
        "needs_more_evidence",
        "stale",
        "superseded",
        "reaffirmed",
        "comment",
    } and not reason.strip():
        raise ValueError(f"reason is required for {action}")


def _safe_error_message(exc):
    message = str(exc)
    for secret in ("aletheia_password", "aletheia_root"):
        message = message.replace(secret, "***")
    return message[:500]


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


SOURCE_TABLE_SCHEMAS = {
    "employee": {
        "table": "employees",
        "primary_key": "employeeID",
        "columns": [
            "employeeID", "lastName", "firstName", "title", "titleOfCourtesy",
            "birthDate", "hireDate", "address", "city", "region", "postalCode",
            "country", "homePhone", "extension", "photo", "notes", "reportsTo", "photoPath",
        ],
    },
    "order": {
        "table": "orders",
        "primary_key": "orderID",
        "columns": [
            "orderID", "customerID", "employeeID", "orderDate", "requiredDate",
            "shippedDate", "shipVia", "freight", "shipName", "shipAddress",
            "shipCity", "shipRegion", "shipPostalCode", "shipCountry",
        ],
    },
    "customer": {
        "table": "customers",
        "primary_key": "customerID",
        "columns": [
            "customerID", "companyName", "contactName", "contactTitle", "address",
            "city", "region", "postalCode", "country", "phone", "fax",
        ],
    },
    "product": {
        "table": "products",
        "primary_key": "productID",
        "columns": [
            "productID", "productName", "supplierID", "categoryID", "quantityPerUnit",
            "unitPrice", "unitsInStock", "unitsOnOrder", "reorderLevel", "discontinued",
        ],
    },
    "category": {
        "table": "categories",
        "primary_key": "categoryID",
        "columns": ["categoryID", "categoryName", "description", "picture"],
    },
    "credit_card_transaction": {
        "table": "credit_card_transactions_safe",
        "primary_key": "transaction_id",
        "columns": [
            "transaction_id", "accountNumber", "customerId", "creditLimit",
            "availableMoney", "transactionDateTime", "transactionAmount",
            "merchantName", "merchantCategoryCode", "cardPresent", "cvvMatch",
            "countryMismatch", "isFraud",
        ],
    },
    "account": {
        "table": "credit_card_transactions_safe",
        "primary_key": "accountNumber",
        "columns": ["accountNumber", "customerId", "creditLimit", "availableMoney", "currentBalance"],
    },
    "card": {
        "table": "credit_card_transactions_safe",
        "primary_key": "cardLast4Digits",
        "columns": ["cardLast4Digits", "cvvMatch", "expirationDateKeyInMatch", "cardPresent"],
    },
    "merchant": {
        "table": "credit_card_transactions_safe",
        "primary_key": "merchantName",
        "columns": ["merchantName", "merchantCategoryCode", "merchantCountryCode", "acqCountry"],
    },
    "chokepoint": {
        "table": "maritime_chokepoint_risk_indicators",
        "primary_key": "risk_indicator_id",
        "columns": ["risk_indicator_id", "canal", "drought", "TC1", "TC3", "severity_conflict", "severity_piracy", "severity_geopolitical"],
    },
    "country": {
        "table": "maritime_chokepoint_country_dependencies",
        "primary_key": "iso3",
        "columns": ["iso3", "q", "v", "q_sea_predict", "v_sea_predict", "revenue_USD"],
    },
    "tradedependency": {
        "table": "maritime_chokepoint_country_dependencies",
        "primary_key": "dependency_id",
        "columns": ["dependency_id", "iso3", "canal", "q_canal", "v_canal", "q", "v", "revenue_USD"],
    },
    "trade_dependency": {
        "table": "maritime_chokepoint_country_dependencies",
        "primary_key": "dependency_id",
        "columns": ["dependency_id", "iso3", "canal", "q_canal", "v_canal", "q", "v", "revenue_USD"],
    },
    "hazard": {
        "table": "maritime_chokepoint_risk_indicators",
        "primary_key": "risk_indicator_id",
        "columns": [
            "risk_indicator_id", "canal",
            "likelihood_conflict", "timescale_conflict", "severity_conflict",
            "likelihood_piracy", "timescale_piracy", "severity_piracy",
            "likelihood_blockage", "timescale_blockage", "severity_blockage",
            "likelihood_geopolitical", "timescale_geopolitical", "severity_geopolitical",
        ],
    },
    "riskindicator": {
        "table": "maritime_chokepoint_risk_indicators",
        "primary_key": "risk_indicator_id",
        "columns": ["risk_indicator_id", "canal", "piracy", "geopolitical", "drought", "TC1", "TC3", "likelihood_conflict", "severity_conflict", "likelihood_blockage", "severity_blockage"],
    },
    "risk_indicator": {
        "table": "maritime_chokepoint_risk_indicators",
        "primary_key": "risk_indicator_id",
        "columns": ["risk_indicator_id", "canal", "piracy", "geopolitical", "drought", "TC1", "TC3", "likelihood_conflict", "severity_conflict", "likelihood_blockage", "severity_blockage"],
    },
    "systemicriskresult": {
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "v_share", "v_share_mar", "trade_at_risk_v", "trade_at_risk_q", "revenue_at_risk", "trade_impacted"],
    },
    "systemic_risk_result": {
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "v_share", "v_share_mar", "trade_at_risk_v", "trade_at_risk_q", "revenue_at_risk", "trade_impacted"],
    },
    "riskfinding": {
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "trade_at_risk_v", "trade_impacted"],
    },
    "risk_finding": {
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "trade_at_risk_v", "trade_impacted"],
    },
    "mitigationaction": {
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "trade_at_risk_v", "trade_impacted"],
    },
    "mitigation_action": {
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "trade_at_risk_v", "trade_impacted"],
    },
    "conflictevent": {
        "table": "us_iran_war_conflict_events",
        "primary_key": "event_id",
        "columns": ["event_id", "event_date", "event_type", "actors", "location", "summary", "source_id", "confidence"],
    },
    "conflict_event": {
        "table": "us_iran_war_conflict_events",
        "primary_key": "event_id",
        "columns": ["event_id", "event_date", "event_type", "actors", "location", "summary", "source_id", "confidence"],
    },
    "economicchannel": {
        "table": "us_iran_war_economic_channels",
        "primary_key": "channel_id",
        "columns": ["channel_id", "channel_name", "mechanism", "primary_metric", "source_id", "confidence"],
    },
    "economic_channel": {
        "table": "us_iran_war_economic_channels",
        "primary_key": "channel_id",
        "columns": ["channel_id", "channel_name", "mechanism", "primary_metric", "source_id", "confidence"],
    },
    "countryexposure": {
        "table": "us_iran_war_country_exposures",
        "primary_key": "country_id",
        "columns": ["country_id", "iso3", "country_name", "exposure_type", "exposure_level", "impact_summary", "key_metric", "source_id", "confidence"],
    },
    "country_exposure": {
        "table": "us_iran_war_country_exposures",
        "primary_key": "country_id",
        "columns": ["country_id", "iso3", "country_name", "exposure_type", "exposure_level", "impact_summary", "key_metric", "source_id", "confidence"],
    },
    "recommendedaction": {
        "table": "us_iran_war_recommended_actions",
        "primary_key": "action_id",
        "columns": ["action_id", "action_name", "owner_role", "trigger", "recommended_action", "source_id", "confidence"],
    },
    "recommended_action": {
        "table": "us_iran_war_recommended_actions",
        "primary_key": "action_id",
        "columns": ["action_id", "action_name", "owner_role", "trigger", "recommended_action", "source_id", "confidence"],
    },
    "sourcedocument": {
        "table": "us_iran_war_web_sources",
        "primary_key": "source_id",
        "columns": ["source_id", "title", "publisher", "url", "query", "retrieved_at", "license_risk", "robots_risk", "summary", "confidence"],
    },
    "source_document": {
        "table": "us_iran_war_web_sources",
        "primary_key": "source_id",
        "columns": ["source_id", "title", "publisher", "url", "query", "retrieved_at", "license_risk", "robots_risk", "summary", "confidence"],
    },
    "graphedge": {
        "table": "us_iran_war_graph_edges",
        "primary_key": "edge_id",
        "columns": ["edge_id", "source_node", "relation", "target_node", "source_id", "confidence"],
    },
    "graph_edge": {
        "table": "us_iran_war_graph_edges",
        "primary_key": "edge_id",
        "columns": ["edge_id", "source_node", "relation", "target_node", "source_id", "confidence"],
    },
}


# SOURCE_LINK_SCHEMAS lives in server.graph_projection_fixtures


SOURCE_TABLE_HINTS_BY_TABLE = {schema["table"]: schema for schema in SOURCE_TABLE_SCHEMAS.values()}


def _fallback_fields(table_schema):
    fields = []
    table = table_schema.get("table")
    primary_key = table_schema.get("primary_key")
    for idx, column in enumerate(table_schema.get("columns") or [], start=1):
        fields.append(
            {
                "name": column,
                "source_table": table,
                "qualified_name": f"{table}.{column}",
                "data_type": None,
                "column_type": None,
                "nullable": None,
                "primary_key": column == primary_key,
                "foreign_key": False,
                "key_role": "primary_key" if column == primary_key else "attribute",
                "default": None,
                "extra": "",
                "comment": "",
                "max_length": None,
                "numeric_precision": None,
                "numeric_scale": None,
                "ordinal_position": idx,
                "maps_to_property": column,
            }
        )
    return fields


def _field_by_qualified_name(fields):
    return {f"{field.get('source_table')}.{field.get('name')}": field for field in fields}


def _apply_table_hints(table_schema, fields):
    primary_key = table_schema.get("primary_key") if table_schema else None
    for field in fields:
        if field.get("name") == primary_key:
            field["declared_primary_key_hint"] = True
            if field.get("key_role") == "unknown":
                field["key_role_hint"] = "declared_primary_key"
        elif field.get("key_role") == "unknown" and field.get("foreign_key") is None:
            field["key_role"] = "unknown"
    return fields


def _ontology_source_schema(artifact, table_fields=None):
    canonical_key = artifact.get("canonical_key") or ""
    payload = artifact.get("payload") or {}
    artifact_type = artifact.get("artifact_type")
    if canonical_key in SOURCE_LINK_SCHEMAS:
        schema = dict(SOURCE_LINK_SCHEMAS[canonical_key])
        table_fields = table_fields or {}
        field_map = _field_by_qualified_name(
            table_fields.get(schema["source_table"], {}).get("fields", [])
            + table_fields.get(schema["target_table"], {}).get("fields", [])
        )
        schema["kind"] = "relationship_source_schema"
        source_tables = [table_fields.get(t, {}) for t in (schema["source_table"], schema["target_table"])]
        schema["schema_source"] = (
            "live"
            if all(t.get("schema_source") == "live" for t in source_tables)
            else "degraded"
            if any(t.get("schema_source") == "degraded" for t in source_tables)
            else "fallback"
        )
        if schema["schema_source"] != "live":
            schema["degraded"] = True
            schema["source_statuses"] = {
                table: table_fields.get(table, {}).get("schema_source", "missing")
                for table in (schema["source_table"], schema["target_table"])
            }
            errors = {
                table: table_fields.get(table, {}).get("connection_error")
                for table in (schema["source_table"], schema["target_table"])
                if table_fields.get(table, {}).get("connection_error")
            }
            if errors:
                schema["connection_errors"] = errors
        schema["source_object"] = payload.get("source_object_name")
        schema["target_object"] = payload.get("target_object_name")
        schema["link_type"] = payload.get("link_type") or schema.get("cardinality")
        source_prop = dict(field_map.get(schema["source_field"], {}))
        target_prop = dict(field_map.get(schema["target_field"], {}))
        if source_prop:
            source_prop["relationship_role"] = "source_identity_field"
        if target_prop:
            target_prop["relationship_role"] = "target_reference_field"
            if target_prop.get("foreign_key") is None:
                target_prop["foreign_key"] = "unknown"
            if target_prop.get("key_role") == "unknown":
                target_prop["key_role"] = "relationship_reference"
        schema["source_field_property"] = source_prop
        schema["target_field_property"] = target_prop
        schema["field_properties"] = [
            f for f in [schema["source_field_property"], schema["target_field_property"]] if f
        ]
        return schema
    if artifact_type == "object":
        object_name = str(payload.get("object_name") or artifact.get("name") or canonical_key.removeprefix("object:")).lower().replace(" ", "_")
        table_schema = SOURCE_TABLE_SCHEMAS.get(object_name)
        if table_schema:
            schema = dict(table_schema)
            live = (table_fields or {}).get(schema["table"])
            schema["columns"] = live.get("columns") if live else schema.get("columns", [])
            schema["fields"] = _apply_table_hints(schema, live.get("fields")) if live else _fallback_fields(schema)
            schema["schema_source"] = live.get("schema_source") if live else "fallback"
            if live and live.get("schema_source") != "live":
                schema["degraded"] = True
                schema["degraded_reason"] = live.get("degraded_reason")
                schema["connection_error"] = live.get("connection_error")
            schema["kind"] = "object_source_schema"
            schema["object_name"] = artifact.get("name")
            return schema
    return {"kind": "unmapped", "source_refs": artifact.get("source_refs", [])}


class ReviewRepository:
    def __init__(self, tenant_registry, ensure_schema=False):
        self.tenant_registry = tenant_registry
        self.ensure_schema = ensure_schema
        self.engines = {}
        self.source_engines = {}

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

    def source_engine_for(self, tenant):
        engine = self.source_engines.get(tenant.source_db_url)
        if engine is None:
            engine = create_engine(tenant.source_db_url)
            self.source_engines[tenant.source_db_url] = engine
        return engine

    def source_table_schema(self, tenant, table_name):
        try:
            with self.source_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY,
                               COLUMN_DEFAULT, EXTRA, CHARACTER_MAXIMUM_LENGTH,
                               NUMERIC_PRECISION, NUMERIC_SCALE, ORDINAL_POSITION, COLUMN_COMMENT
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
                        ORDER BY ORDINAL_POSITION
                        """
                    ),
                    {"table_name": table_name},
                ).mappings().all()
            fields = [_field_property_from_row(row, table_name) for row in rows]
            return {
                "table": table_name,
                "schema_source": "live" if fields else "degraded",
                "columns": [field["name"] for field in fields],
                "fields": fields,
                **({} if fields else {"degraded": True, "degraded_reason": "source table was reachable but no columns were found"}),
            }
        except Exception as exc:
            return {
                "table": table_name,
                "schema_source": "degraded",
                "columns": [],
                "fields": [],
                "degraded": True,
                "degraded_reason": "source database connection failed",
                "connection_error": _safe_error_message(exc),
            }

    def source_schemas_for_artifact(self, tenant, artifact):
        canonical_key = artifact.get("canonical_key") or ""
        payload = artifact.get("payload") or {}
        artifact_type = artifact.get("artifact_type")
        table_names = set()
        if canonical_key in SOURCE_LINK_SCHEMAS:
            link_schema = SOURCE_LINK_SCHEMAS[canonical_key]
            table_names.add(link_schema["source_table"])
            table_names.add(link_schema["target_table"])
        elif artifact_type == "object":
            object_name = str(payload.get("object_name") or artifact.get("name") or canonical_key.removeprefix("object:")).lower().replace(" ", "_")
            if object_name in SOURCE_TABLE_SCHEMAS:
                table_names.add(SOURCE_TABLE_SCHEMAS[object_name]["table"])
        schemas = {}
        object_hint = None
        if artifact_type == "object":
            object_name = str(payload.get("object_name") or artifact.get("name") or canonical_key.removeprefix("object:")).lower().replace(" ", "_")
            object_hint = SOURCE_TABLE_SCHEMAS.get(object_name)
        for table in table_names:
            schema = self.source_table_schema(tenant, table)
            hint = object_hint if object_hint and object_hint.get("table") == table else SOURCE_TABLE_HINTS_BY_TABLE.get(table)
            if hint and schema.get("fields"):
                schema["fields"] = _apply_table_hints(hint, schema["fields"])
            schemas[table] = schema
        return schemas

    def list_artifacts(self, tenant, filters):
        conditions = ["project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id}
        for field in ("artifact_type", "status", "source_agent"):
            value = filters.get(field)
            if value:
                if field == "status" and value == "proposed":
                    conditions.append("status IN (:status, :draft_status)")
                    params["status"] = value
                    params["draft_status"] = "draft"
                else:
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

    def list_web_enrichment(self, tenant, target_artifact_key=None, limit=50):
        params = {"tenant_id": tenant.tenant_id, "limit": limit}
        conditions = ["p.project_id = :tenant_id"]
        if target_artifact_key:
            conditions.append("p.target_artifact_key = :target_artifact_key")
            params["target_artifact_key"] = target_artifact_key
        where = " AND ".join(conditions)
        try:
            with self.engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT p.proposal_key, p.target_artifact_key, p.source_url,
                               p.source_title, p.summary, p.raw_payload_json,
                               p.content_hash, p.confidence, p.status, p.created_at,
                               r.run_key, r.search_provider, r.safety_profile_json,
                               r.budget_json, r.skipped_sources_json
                        FROM aletheia_web_enrichment_proposals p
                        JOIN aletheia_web_enrichment_runs r ON r.id = p.run_id
                        WHERE {where}
                        ORDER BY p.created_at DESC, p.id DESC
                        LIMIT :limit
                        """
                    ),
                    params,
                ).mappings().all()
        except Exception as exc:
            return {
                "tenant": tenant.public_dict(),
                "proposals": [],
                "degraded": True,
                "degraded_reason": _safe_error_message(exc),
            }
        return {
            "tenant": tenant.public_dict(),
            "proposals": [
                {
                    "proposal_key": row["proposal_key"],
                    "target_artifact_key": row["target_artifact_key"],
                    "source_url": row["source_url"],
                    "source_title": row["source_title"],
                    "summary": row["summary"],
                    "raw_payload": _load_json(row["raw_payload_json"], {}),
                    "content_hash": row["content_hash"],
                    "confidence": row["confidence"],
                    "status": row["status"],
                    "created_at": str(row["created_at"]) if row["created_at"] else None,
                    "run_key": row["run_key"],
                    "search_provider": row["search_provider"],
                    "safety_profile": _load_json(row["safety_profile_json"], {}),
                    "budget": _load_json(row["budget_json"], {}),
                    "skipped_sources": _load_json(row["skipped_sources_json"], []),
                }
                for row in rows
            ],
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
        result["source_schema"] = _ontology_source_schema(result, self.source_schemas_for_artifact(tenant, result))
        result["canonical"] = {
            "status": result["status"],
            "version": result["version"],
            "graph_ingestion_eligible": result["status"] == "approved",
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
        }
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
        result["used_by"] = self.used_by(tenant, result)
        result["web_enrichment"] = self.list_web_enrichment(tenant, canonical_key, limit=20).get("proposals", [])
        return result

    def used_by(self, tenant, artifact):
        canonical_key = artifact.get("canonical_key")
        used_by = []
        if canonical_key == "link:employee:1:n:order":
            used_by.extend(
                [
                    {
                        "kind": "graph_path",
                        "label": "Employee -> Order approved graph paths",
                        "href": f"/?screen=graph&tenant={quote(tenant.tenant_id)}&type=Employee&id=4&depth=1&limit=200",
                        "summary": "Employee order neighborhoods use orders.employeeID = employees.employeeID.",
                    },
                    {
                        "kind": "reasoning",
                        "label": "Employee workload and profile reasoning",
                        "href": f"/?screen=reasoning&tenant={quote(tenant.tenant_id)}&ontology_basis=link%3Aemployee%3A1%3An%3Aorder",
                        "summary": "Scoped Employee reasoning cites this relationship as ontology basis.",
                    },
                    {
                        "kind": "instance",
                        "label": "Employee instance explorer",
                        "href": f"/?screen=graph&tenant={quote(tenant.tenant_id)}&type=Employee&id=4",
                        "summary": "Instance edge provenance is backed by this canonical link.",
                    },
                ]
            )
        elif canonical_key and canonical_key.startswith("object:"):
            object_type = canonical_key.removeprefix("object:").capitalize()
            used_by.append(
                {
                    "kind": "graph_scope",
                    "label": f"{object_type} graph scopes",
                    "href": f"/?screen=graph&tenant={quote(tenant.tenant_id)}&type={quote(object_type)}",
                    "summary": "Approved object types are eligible for graph and instance views.",
                }
            )
        return used_by

    def review_status(self, tenant, canonical_key, status, reviewer, reason):
        if status != "approved":
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
        self.reasoning_repository = None

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

    def _cfg_key(self, object_type):
        raw = str(object_type or "").strip()
        if not raw:
            return ""
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", raw).replace("-", "_").lower()
        compact = re.sub(r"[^a-z0-9]", "", raw.lower())
        for key in self.ENTITY_CONFIG:
            key_compact = re.sub(r"[^a-z0-9]", "", key.lower())
            if raw.lower() == key.lower() or snake == key.lower() or compact == key_compact:
                return key
        return raw.lower()

    def _cfg_type(self, cfg, fallback):
        return cfg.get("type") or str(fallback).capitalize()

    def _artifact_statuses(self, tenant, keys):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT canonical_key, name, artifact_type, status, version, payload_json, description
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id AND canonical_key = ANY(:keys)
                    """
                ),
                {"tenant_id": tenant.tenant_id, "keys": list(keys)},
            ).mappings().all()
        return {row["canonical_key"]: dict(row) for row in rows}

    def types(self, tenant, include_draft=False):
        all_keys = [cfg["artifact"] for cfg in self.ENTITY_CONFIG.values()]
        artifacts = self._artifact_statuses(tenant, all_keys) if include_draft else self._approved_artifacts(tenant, all_keys)
        types = []
        for type_key, cfg in self.ENTITY_CONFIG.items():
            if cfg["artifact"] in artifacts:
                artifact = artifacts[cfg["artifact"]]
                type_name = self._cfg_type(cfg, type_key)
                types.append({
                    "type": type_name,
                    "label": cfg.get("label") or type_name,
                    "table": cfg["table"],
                    "ontology_artifact": cfg["artifact"],
                    "artifact_status": artifact.get("status"),
                    "approved": artifact.get("status") == "approved",
                    "tenant_id": tenant.tenant_id,
                })
        return {"tenant": tenant.public_dict(), "types": types}

    def search(self, tenant, object_type, query, limit=25, include_draft=False):
        cfg_key = self._cfg_key(object_type)
        cfg = self.ENTITY_CONFIG.get(cfg_key)
        if not cfg:
            return {
                "tenant": tenant.public_dict(),
                "instances": [],
                "approved": False,
                "reason": f"Unknown type {object_type}",
            }
        canonical_key = cfg["artifact"]
        artifacts = self._artifact_statuses(tenant, [canonical_key]) if include_draft else self._approved_artifacts(tenant, [canonical_key])
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
        if cfg.get("distinct"):
            select_cols = list(dict.fromkeys([cfg["pk"], *cfg["label_cols"]]))
            sql = (
                f"SELECT DISTINCT {', '.join(select_cols)} FROM {cfg['table']} "
                f"WHERE (:query = '' OR {where}) ORDER BY {cfg['pk']} LIMIT :limit"
            )
        else:
            sql = f"SELECT * FROM {cfg['table']} WHERE (:query = '' OR {where}) ORDER BY {cfg['pk']} LIMIT :limit"
        with self.source_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(sql),
                {"query": query, "like_query": f"%{query}%", "limit": limit},
            ).mappings().all()
        type_cap = self._cfg_type(cfg, cfg_key)
        return {
            "instances": [
                self._entity_node(tenant, type_cap, dict(row))
                for row in rows
            ],
            "approved": artifacts[canonical_key].get("status") == "approved",
            "artifact_status": artifacts[canonical_key].get("status"),
            "tenant": tenant.public_dict(),
        }

    def detail(self, tenant, object_type, instance_id):
        canonical_key = self._object_key(object_type)
        artifacts = self._approved_artifacts(tenant, [canonical_key])
        if canonical_key not in artifacts:
            return None
        cfg_key = self._cfg_key(object_type)
        if cfg_key == "employee":
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
        if cfg_key == "order":
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
        cfg = self.ENTITY_CONFIG.get(cfg_key)
        row = self._fetch_entity(tenant, object_type, instance_id)
        if cfg and row:
            node = self._entity_node(tenant, self._cfg_type(cfg, cfg_key), row)
            if not node:
                return None
            return {
                **node,
                "source_row": self._row(row),
                "key_properties": {
                    key: _jsonable(row.get(key))
                    for key in dict.fromkeys([cfg["pk"], *cfg.get("label_cols", [])])
                    if key in row
                },
            }
        return None

    # ---- generic entity config ----
    ENTITY_CONFIG = GRAPH_ENTITY_CONFIG
    LINK_CONFIG = GRAPH_LINK_CONFIG

    def _fetch_entity(self, tenant, object_type, instance_id):
        cfg = self.ENTITY_CONFIG.get(self._cfg_key(object_type))
        if not cfg:
            return None
        with self.source_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {cfg['table']} WHERE {cfg['pk']} = :pk"),
                {"pk": instance_id},
            ).mappings().first()
        return dict(row) if row else None

    def _entity_node(self, tenant, object_type, row):
        cfg = self.ENTITY_CONFIG.get(self._cfg_key(object_type))
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

    def _schema_graph_artifacts(self, tenant):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT canonical_key, artifact_type, name, payload_json, confidence
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id
                      AND status = 'approved'
                      AND source_agent = 'SchemaGraphModelingAgent'
                    ORDER BY artifact_type, canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
        objects = {}
        links = []
        for row in rows:
            payload = _load_json(row["payload_json"], {})
            if payload.get("prompt_version") != "schema_graph_modeling_v1" or payload.get("llm_inferred") is not True:
                continue
            item = {**dict(row), "payload": payload}
            natural_key = row["canonical_key"].split(":", 1)[1] if ":" in row["canonical_key"] else row["canonical_key"]
            if row["artifact_type"] == "object":
                objects[natural_key] = item
            elif row["artifact_type"] == "link":
                links.append(item)
        return objects, links

    def _schema_graph_node_type(self, artifact):
        return re.sub(r"[^0-9A-Za-z]", "", artifact["name"]) or artifact["canonical_key"].split(":", 1)[-1]

    def _source_columns(self, tenant, table):
        inspector = inspect(self.source_engine_for(tenant))
        if table not in inspector.get_table_names():
            return set()
        return {column["name"] for column in inspector.get_columns(table)}

    def _schema_graph_safe_join_condition(self, tenant, join_condition):
        match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*", str(join_condition or ""))
        if not match:
            return None
        left_table, left_col, right_table, right_col = match.groups()
        source_tables = set(inspect(self.source_engine_for(tenant)).get_table_names())
        if left_table not in source_tables or right_table not in source_tables:
            return None
        if left_col not in self._source_columns(tenant, left_table) or right_col not in self._source_columns(tenant, right_table):
            return None
        return f"{left_table}.{left_col} = {right_table}.{right_col}"

    def _schema_graph_node(self, tenant, artifact, table, pk_value):
        node_type = self._schema_graph_node_type(artifact)
        return {
            "id": f"{node_type}:{pk_value}",
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
            "type": node_type,
            "label": str(pk_value),
            "source_table": table,
            "source_pk": f"{artifact['payload'].get('primary_key')}={pk_value}",
            "ontology_artifact": artifact["canonical_key"],
            "status": "approved",
            "projection_source": "SchemaGraphModelingAgent",
        }

    def _schema_graph_full_graph(self, tenant, object_type=None, instance_id=None, limit=200):
        objects, links = self._schema_graph_artifacts(tenant)
        if not objects:
            return None
        requested_limit = int(limit)
        limit = max(1, min(requested_limit, 300))
        nodes = []
        edges = []
        seen_nodes = set()
        seen_edges = set()

        def remember_node(node):
            if node and node["id"] not in seen_nodes:
                nodes.append(node)
                seen_nodes.add(node["id"])
            return node

        source_engine = self.source_engine_for(tenant)
        inspector = inspect(source_engine)
        source_tables = set(inspector.get_table_names())
        per_type_limit = max(1, min(40, limit // max(len(objects), 1) + 1))

        with source_engine.connect() as conn:
            for artifact in objects.values():
                payload = artifact["payload"]
                pk = payload.get("primary_key")
                table = next((item for item in payload.get("mapped_table_names") or [] if item in source_tables), None)
                if not table or not pk or pk not in self._source_columns(tenant, table):
                    continue
                rows = conn.execute(
                    text(f"SELECT DISTINCT {table}.{pk} AS node_pk FROM {table} WHERE {table}.{pk} IS NOT NULL ORDER BY {table}.{pk} LIMIT :limit"),
                    {"limit": per_type_limit},
                ).mappings().all()
                for row in rows:
                    remember_node(self._schema_graph_node(tenant, artifact, table, row["node_pk"]))
                    if len(nodes) >= limit:
                        break
                if len(nodes) >= limit:
                    break

            for link in links:
                payload = link["payload"]
                source_artifact = objects.get(payload.get("source_object_key"))
                target_artifact = objects.get(payload.get("target_object_key"))
                source_table = payload.get("source_table")
                target_table = payload.get("target_table")
                join_condition = self._schema_graph_safe_join_condition(tenant, payload.get("join_condition"))
                if not source_artifact or not target_artifact or not source_table or not target_table or not join_condition:
                    continue
                if source_table not in source_tables or target_table not in source_tables:
                    continue
                source_pk = source_artifact["payload"].get("primary_key")
                target_pk = target_artifact["payload"].get("primary_key")
                if source_pk not in self._source_columns(tenant, source_table) or target_pk not in self._source_columns(tenant, target_table):
                    continue
                try:
                    rows = conn.execute(
                        text(
                            f"SELECT DISTINCT {source_table}.{source_pk} AS source_pk, "
                            f"{target_table}.{target_pk} AS target_pk "
                            f"FROM {source_table} JOIN {target_table} ON {join_condition} "
                            f"WHERE {source_table}.{source_pk} IS NOT NULL AND {target_table}.{target_pk} IS NOT NULL "
                            "LIMIT :limit"
                        ),
                        {"limit": limit * 3},
                    ).mappings().all()
                except Exception:
                    continue
                for row in rows:
                    source_node = remember_node(self._schema_graph_node(tenant, source_artifact, source_table, row["source_pk"]))
                    target_node = remember_node(self._schema_graph_node(tenant, target_artifact, target_table, row["target_pk"]))
                    edge_id = f"{source_node['id']}->{target_node['id']}:{link['canonical_key']}"
                    if edge_id in seen_edges:
                        continue
                    seen_edges.add(edge_id)
                    edges.append({
                        "id": edge_id,
                        "tenant_id": tenant.tenant_id,
                        "source": source_node["id"],
                        "target": target_node["id"],
                        "link_key": link["canonical_key"],
                        "label": link["name"],
                        "status": "approved",
                        "projection_source": "SchemaGraphModelingAgent",
                    })
                    if len(edges) >= limit * 3:
                        break
        center = None
        if object_type and instance_id:
            compact_type = re.sub(r"[^0-9A-Za-z]", "", str(object_type)).lower()
            for artifact in objects.values():
                if re.sub(r"[^0-9A-Za-z]", "", artifact["name"]).lower() == compact_type:
                    table = next((item for item in artifact["payload"].get("mapped_table_names") or [] if item in source_tables), None)
                    if table:
                        center = remember_node(self._schema_graph_node(tenant, artifact, table, instance_id))
                    break
        return {
            "approved": True,
            "tenant": tenant.public_dict(),
            "graph_database": tenant.graph_database,
            "depth": 0,
            "limit": limit,
            "limits": {"requested_limit": requested_limit, "applied_limit": limit, "hard_limit": 300, "truncated": len(nodes) >= limit or len(edges) >= limit * 3},
            "center": center,
            "nodes": nodes[:limit],
            "edges": edges[: limit * 3],
            "scope": {
                "tenant_id": tenant.tenant_id,
                "view": "all",
                "node_limit": limit,
                "edge_limit": limit * 3,
                "approved_only": True,
                "projection_source": "SchemaGraphModelingAgent",
            },
        }

    def neighborhood(self, tenant, object_type, instance_id, depth=1, limit=200):
        cfg_key = self._cfg_key(object_type)
        cfg = self.ENTITY_CONFIG.get(cfg_key)
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
        center_type = self._cfg_type(cfg, cfg_key)
        center = self._entity_node(tenant, center_type, center_row)
        nodes = [center]
        edges = []
        allowed_node_types = {center_type}
        allowed_link_keys = []
        for lc in self.LINK_CONFIG:
            is_from = lc["from"] == cfg_key
            is_to = lc["to"] == cfg_key and lc.get("reverse")
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
            neighbor_type = self._cfg_type(target_cfg, target_type_key)
            allowed_node_types.add(neighbor_type)
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
                "type": center_type,
                "id": str(instance_id),
                "depth": depth,
                "node_limit": limit,
                "edge_limit": limit,
                "allowed_node_types": sorted(allowed_node_types),
                "allowed_link_keys": allowed_link_keys,
                "approved_only": True,
            },
        }

    def full_graph(self, tenant, object_type=None, instance_id=None, limit=200):
        schema_graph = self._schema_graph_full_graph(tenant, object_type=object_type, instance_id=instance_id, limit=limit)
        if schema_graph is not None:
            return schema_graph
        requested_limit = int(limit)
        limit = max(1, min(requested_limit, 300))
        cfg_items = list(self.ENTITY_CONFIG.items())
        artifact_keys = [cfg["artifact"] for _, cfg in cfg_items]
        artifacts = self._approved_artifacts(tenant, artifact_keys)
        approved_cfgs = [(key, cfg) for key, cfg in cfg_items if cfg["artifact"] in artifacts]
        if not approved_cfgs:
            return {
                "approved": False,
                "tenant": tenant.public_dict(),
                "graph_database": tenant.graph_database,
                "center": None,
                "nodes": [],
                "edges": [],
                "missing_approved_artifacts": artifact_keys,
            }
        per_type_limit = max(1, min(40, limit // max(len(approved_cfgs), 1) + 1))
        nodes = []
        seen_nodes = set()

        def remember_node(node):
            if node and node["id"] not in seen_nodes:
                nodes.append(node)
                seen_nodes.add(node["id"])
                return True
            return False

        with self.source_engine_for(tenant).connect() as conn:
            for cfg_key, cfg in approved_cfgs:
                type_name = self._cfg_type(cfg, cfg_key)
                if cfg.get("distinct"):
                    select_cols = list(dict.fromkeys([cfg["pk"], *cfg["label_cols"]]))
                    sql = f"SELECT DISTINCT {', '.join(select_cols)} FROM {cfg['table']} ORDER BY {cfg['pk']} LIMIT :lim"
                else:
                    sql = f"SELECT * FROM {cfg['table']} ORDER BY {cfg['pk']} LIMIT :lim"
                rows = conn.execute(text(sql), {"lim": per_type_limit}).mappings().all()
                for row in rows:
                    node = self._entity_node(tenant, type_name, dict(row))
                    if node:
                        remember_node(node)
                    if len(nodes) >= limit:
                        break
                if len(nodes) >= limit:
                    break

        center = None
        if object_type and instance_id:
            center_row = self._fetch_entity(tenant, object_type, instance_id)
            cfg = self.ENTITY_CONFIG.get(self._cfg_key(object_type))
            if center_row and cfg:
                center_type = self._cfg_type(cfg, self._cfg_key(object_type))
                center = self._entity_node(tenant, center_type, center_row)
                if center:
                    center["center"] = True
                    if not remember_node(center):
                        for idx, node in enumerate(nodes):
                            if node["id"] == center["id"]:
                                nodes[idx] = {**node, "center": True}
                                break

        def remember_maritime_chokepoint(canal):
            if not canal:
                return
            remember_node({
                "id": f"Chokepoint:{canal}",
                "tenant_id": tenant.tenant_id,
                "namespace": tenant.namespace,
                "graph_database": tenant.graph_database,
                "type": "Chokepoint",
                "label": str(canal),
                "source_table": "maritime_chokepoint_risk_indicators",
                "source_pk": f"canal={canal}",
                "ontology_artifact": "object:chokepoint",
                "status": "approved",
            })

        def remember_entity_node(type_name, row):
            node = self._entity_node(tenant, type_name, dict(row))
            if node:
                remember_node(node)

        center_maritime_dep_rows = []
        center_maritime_result_rows = []
        center_maritime_risk_rows = []
        if tenant.tenant_id == "maritime-risk" and center and object_type and instance_id:
            center_type_key = self._cfg_key(object_type)
            related_limit = max(8, min(48, limit))
            with self.source_engine_for(tenant).connect() as conn:
                dep_rows = []
                result_rows = []
                risk_rows = []
                if center_type_key == "country":
                    dep_rows = conn.execute(
                        text(
                            "SELECT * FROM maritime_chokepoint_country_dependencies "
                            "WHERE iso3 = :iso3 ORDER BY v_canal DESC LIMIT :lim"
                        ),
                        {"iso3": instance_id, "lim": related_limit},
                    ).mappings().all()
                    result_rows = conn.execute(
                        text(
                            "SELECT * FROM maritime_chokepoint_systemic_risk_results "
                            "WHERE iso3 = :iso3 ORDER BY trade_at_risk_v DESC LIMIT :lim"
                        ),
                        {"iso3": instance_id, "lim": related_limit},
                    ).mappings().all()
                elif center_type_key == "chokepoint":
                    dep_rows = conn.execute(
                        text(
                            "SELECT * FROM maritime_chokepoint_country_dependencies "
                            "WHERE canal = :canal ORDER BY v_canal DESC LIMIT :lim"
                        ),
                        {"canal": instance_id, "lim": related_limit},
                    ).mappings().all()
                    result_rows = conn.execute(
                        text(
                            "SELECT * FROM maritime_chokepoint_systemic_risk_results "
                            "WHERE canal = :canal ORDER BY trade_at_risk_v DESC LIMIT :lim"
                        ),
                        {"canal": instance_id, "lim": related_limit},
                    ).mappings().all()
                    risk_rows = conn.execute(
                        text(
                            "SELECT * FROM maritime_chokepoint_risk_indicators "
                            "WHERE canal = :canal OR risk_indicator_id = :risk_id "
                            "ORDER BY risk_indicator_id LIMIT :lim"
                        ),
                        {"canal": instance_id, "risk_id": instance_id, "lim": related_limit},
                    ).mappings().all()

                canals = sorted({str(row["canal"]) for row in [*dep_rows, *result_rows] if row.get("canal")})
                if center_type_key == "country":
                    for canal in canals[:related_limit]:
                        risk_rows.extend(conn.execute(
                            text(
                                "SELECT * FROM maritime_chokepoint_risk_indicators "
                                "WHERE canal = :canal ORDER BY risk_indicator_id LIMIT 1"
                            ),
                            {"canal": canal},
                        ).mappings().all())

            for row in dep_rows:
                remember_entity_node("TradeDependency", row)
                remember_maritime_chokepoint(row.get("canal"))
            for row in result_rows:
                remember_entity_node("SystemicRiskResult", row)
                remember_entity_node("RiskFinding", row)
                remember_entity_node("MitigationAction", row)
                remember_maritime_chokepoint(row.get("canal"))
            for row in risk_rows:
                remember_entity_node("Hazard", row)
                remember_entity_node("RiskIndicator", row)
                remember_maritime_chokepoint(row.get("canal"))
            center_maritime_dep_rows = [dict(row) for row in dep_rows]
            center_maritime_result_rows = [dict(row) for row in result_rows]
            center_maritime_risk_rows = [dict(row) for row in risk_rows]
        node_ids = {node["id"] for node in nodes}
        edges = []
        seen_edges = set()

        def add_edge(source, target, link_key, label=None, row_id=None):
            if source not in node_ids or target not in node_ids or source == target:
                return
            edge_id = f"{source}->{target}:{link_key}"
            if edge_id in seen_edges:
                return
            seen_edges.add(edge_id)
            edges.append({
                "id": edge_id,
                "tenant_id": tenant.tenant_id,
                "source": source,
                "target": target,
                "link_key": link_key,
                "label": label or link_key,
                "source_row": row_id,
                "status": "approved",
            })

        link_artifacts = self._approved_artifacts(tenant, [lc["link"] for lc in self.LINK_CONFIG])
        with self.source_engine_for(tenant).connect() as conn:
            for lc in self.LINK_CONFIG:
                if lc["link"] not in link_artifacts:
                    continue
                from_cfg = self.ENTITY_CONFIG.get(lc["from"])
                to_cfg = self.ENTITY_CONFIG.get(lc["to"])
                if not from_cfg or not to_cfg:
                    continue
                from_type = self._cfg_type(from_cfg, lc["from"])
                to_type = self._cfg_type(to_cfg, lc["to"])
                try:
                    if lc.get("target_fk"):
                        rows = conn.execute(
                            text(f"SELECT {lc['fk_col']} AS source_pk, {lc['target_fk']} AS target_pk FROM {lc['fk_table']} LIMIT :lim"),
                            {"lim": limit * 2},
                        ).mappings().all()
                    elif lc.get("reverse"):
                        rows = conn.execute(
                            text(f"SELECT {lc['fk_col']} AS source_pk, {from_cfg['pk']} AS target_pk FROM {lc['fk_table']} WHERE {lc['fk_col']} IS NOT NULL LIMIT :lim"),
                            {"lim": limit * 2},
                        ).mappings().all()
                    else:
                        rows = conn.execute(
                            text(f"SELECT {lc['fk_col']} AS source_pk, {to_cfg['pk']} AS target_pk FROM {lc['fk_table']} WHERE {lc['fk_col']} IS NOT NULL LIMIT :lim"),
                            {"lim": limit * 2},
                        ).mappings().all()
                except Exception:
                    rows = []
                for row in rows:
                    add_edge(f"{from_type}:{row['source_pk']}", f"{to_type}:{row['target_pk']}", lc["link"])

        if tenant.tenant_id == "maritime-risk":
            with self.source_engine_for(tenant).connect() as conn:
                risk_rows = conn.execute(text("SELECT risk_indicator_id, canal FROM maritime_chokepoint_risk_indicators LIMIT :lim"), {"lim": limit}).mappings().all()
                dep_rows = conn.execute(text("SELECT dependency_id, iso3, canal FROM maritime_chokepoint_country_dependencies LIMIT :lim"), {"lim": limit}).mappings().all()
                result_rows = conn.execute(text("SELECT risk_result_id, iso3, canal FROM maritime_chokepoint_systemic_risk_results LIMIT :lim"), {"lim": limit}).mappings().all()
            for row in risk_rows:
                chokepoint = f"Chokepoint:{row['canal']}"
                add_edge(f"Hazard:{row['risk_indicator_id']}", chokepoint, "hazard_at_chokepoint", "hazard at")
                add_edge(f"RiskIndicator:{row['risk_indicator_id']}", chokepoint, "risk_indicator_for_chokepoint", "risk indicator")
            for row in dep_rows:
                dep = f"TradeDependency:{row['dependency_id']}"
                add_edge(dep, f"Country:{row['iso3']}", "dependency_country", "country")
                add_edge(dep, f"Chokepoint:{row['canal']}", "dependency_chokepoint", "chokepoint")
            for row in result_rows:
                for source_type in ("SystemicRiskResult", "RiskFinding", "MitigationAction"):
                    source = f"{source_type}:{row['risk_result_id']}"
                    add_edge(source, f"Country:{row['iso3']}", "risk_country", "country")
                    add_edge(source, f"Chokepoint:{row['canal']}", "risk_chokepoint", "chokepoint")
            for row in center_maritime_risk_rows:
                chokepoint = f"Chokepoint:{row['canal']}"
                add_edge(f"Hazard:{row['risk_indicator_id']}", chokepoint, "hazard_at_chokepoint", "hazard at")
                add_edge(f"RiskIndicator:{row['risk_indicator_id']}", chokepoint, "risk_indicator_for_chokepoint", "risk indicator")
            for row in center_maritime_dep_rows:
                dep = f"TradeDependency:{row['dependency_id']}"
                add_edge(dep, f"Country:{row['iso3']}", "dependency_country", "country")
                add_edge(dep, f"Chokepoint:{row['canal']}", "dependency_chokepoint", "chokepoint")
            for row in center_maritime_result_rows:
                for source_type in ("SystemicRiskResult", "RiskFinding", "MitigationAction"):
                    source = f"{source_type}:{row['risk_result_id']}"
                    add_edge(source, f"Country:{row['iso3']}", "risk_country", "country")
                    add_edge(source, f"Chokepoint:{row['canal']}", "risk_chokepoint", "chokepoint")

        return {
            "approved": True,
            "tenant": tenant.public_dict(),
            "graph_database": tenant.graph_database,
            "depth": 0,
            "limit": limit,
            "limits": {"requested_limit": requested_limit, "applied_limit": limit, "hard_limit": 300, "truncated": requested_limit > limit or len(nodes) >= limit},
            "center": center,
            "nodes": nodes,
            "edges": edges[: limit * 3],
            "scope": {
                "tenant_id": tenant.tenant_id,
                "view": "all",
                "node_limit": limit,
                "edge_limit": limit * 3,
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
        cfg = self.ENTITY_CONFIG.get(self._cfg_key(object_type))
        if cfg:
            return cfg["artifact"]
        return f"object:{object_type}".lower()

    def _ensure_continuous_enrichment_schema(self, tenant):
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS aletheia_continuous_enrichment_sessions (
                        id SERIAL PRIMARY KEY,
                        project_id VARCHAR(255) NOT NULL DEFAULT 'default',
                        session_key VARCHAR(255) NOT NULL,
                        objective TEXT NOT NULL,
                        status VARCHAR(50) NOT NULL DEFAULT 'idle',
                        config_json TEXT NOT NULL DEFAULT '{}',
                        frontier_json TEXT NOT NULL DEFAULT '[]',
                        last_run_key VARCHAR(255),
                        cycle_count INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_continuous_enrichment_session_project_key
                    ON aletheia_continuous_enrichment_sessions (project_id, session_key)
                    """
                )
            )

    def _default_continuous_session(self, tenant):
        session_key = f"continuous:{tenant.tenant_id}:us-iran-impact:mvp"
        objective = (
            "Analyze US-Iran escalation impacts across military events, Hormuz and maritime chokepoints, "
            "energy flows, importing countries, shipping/insurance, financial markets, supply chains, "
            "and candidate reviewer actions"
        )
        config = {
            "mode": "bounded_autonomous",
            "run_mode": "scheduled_or_manual",
            "cadence": "manual",
            "custom_interval_minutes": 60,
            "rate_limit_per_cycle": 4,
            "stop_condition": "pause, stop, budget exhausted, or no new frontier",
            "allowed_domains": ["zenodo.org"],
            "max_iterations": 1,
            "max_frontier": 4,
            "max_results_per_query": 4,
            "visited_frontier_keys": [],
            "frontier_cooldown_minutes": 360,
            "frontier_priority_policy": [
                "new_graph_node_or_edge",
                "user_question_scope",
                "reasoning_finding_seed",
                "graph_coverage",
            ],
            "frontier_state": {"last_enriched_at": {}, "selected_count": {}, "coverage_cursor": 0},
            "latest_events": [],
            "canonical_writes": "disabled",
            "formal_graph_writes": "disabled",
            "ontology_review_required": True,
            "fact_graph_target": "proposed_graph_space",
            "finding_target": "candidate_findings",
        }
        frontier = [
            {"kind": "event", "target_key": "Event:US-Iran escalation", "priority": 1.0, "depth": 0},
            {"kind": "chokepoint", "target_key": "Chokepoint:Hormuz Strait", "priority": 0.95, "depth": 0},
            {"kind": "commodity", "target_key": "Commodity:Crude Oil", "priority": 0.85, "depth": 0},
            {"kind": "country_cluster", "target_key": "Country:JPN/KOR/CHN/USA", "priority": 0.8, "depth": 0},
        ]
        self._ensure_continuous_enrichment_schema(tenant)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_continuous_enrichment_sessions
                        (project_id, session_key, objective, status, config_json, frontier_json, updated_at)
                    VALUES
                        (:tenant_id, :session_key, :objective, 'idle', :config_json, :frontier_json, CURRENT_TIMESTAMP)
                    ON CONFLICT (project_id, session_key) DO NOTHING
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "objective": objective,
                    "config_json": _json_dump(config),
                    "frontier_json": _json_dump(frontier),
                },
            )
        return session_key

    def _continuous_source_fixture(self, session_key):
        suffix = _slug(f"{session_key}-{int(time.time())}")
        return [
            {
                "title": "US-Iran escalation raises Hormuz shipping disruption risk for JPN KOR energy imports",
                "url": f"https://zenodo.org/records/13841882/{suffix}-hormuz-energy",
                "snippet": (
                    "shipping disruption and sanctions around Hormuz Strait create import exposure for JPN KOR "
                    "with trade_at_risk_v and dependency_share metrics; analyst review action required."
                ),
            },
            {
                "title": "US-Iran conflict risk affects Bab el-Mandeb trade routes for CHN IND USA",
                "url": f"https://zenodo.org/records/13841882/{suffix}-bab-el-mandeb",
                "snippet": (
                    "likelihood_conflict and severity_conflict around Bab el-Mandeb Strait expose CHN IND USA "
                    "to trade_at_risk_v and trade_impacted; analyst review action required."
                ),
            },
            {
                "title": "Malacca Strait geopolitical spillover affects CHN JPN KOR trade flows",
                "url": f"https://zenodo.org/records/13841882/{suffix}-malacca",
                "snippet": (
                    "likelihood_geopolitical hazard at Malacca Strait affects CHN JPN KOR dependency_share "
                    "and trade_impacted; analyst review action required."
                ),
            },
            {
                "title": "Untrusted US-Iran impact claim",
                "url": f"https://example.org/{suffix}-untrusted",
                "snippet": "conflict at Suez Canal affects USA trade_at_risk_v",
            },
        ]

    def _continuous_update_config(self, config, body):
        config = dict(config or {})
        if "cadence" in body:
            cadence = (body.get("cadence") or "manual").strip()
            if cadence not in {"manual", "hourly", "daily", "custom"}:
                raise ValueError("cadence must be manual, hourly, daily, or custom")
            config["cadence"] = cadence
        if "custom_interval_minutes" in body:
            interval = max(1, min(int(body.get("custom_interval_minutes") or 60), 10080))
            config["custom_interval_minutes"] = interval
        if "allowlist" in body or "allowed_domains" in body:
            raw_domains = body.get("allowed_domains") or body.get("allowlist") or []
            if isinstance(raw_domains, str):
                domains = [part.strip().lower() for part in re.split(r"[,\\s]+", raw_domains) if part.strip()]
            else:
                domains = [str(part).strip().lower() for part in raw_domains if str(part).strip()]
            if not domains:
                raise ValueError("allowlist must include at least one public domain")
            config["allowed_domains"] = list(dict.fromkeys(domains))
        if "budget" in body:
            budget = int(body.get("budget") or config.get("max_frontier") or 4)
            config["max_frontier"] = max(1, min(budget, 25))
            config["rate_limit_per_cycle"] = max(1, min(budget, 25))
        for key in ("max_iterations", "max_frontier", "max_results_per_query", "rate_limit_per_cycle"):
            if key in body:
                config[key] = max(1, min(int(body.get(key) or config.get(key) or 1), 50))
        if "frontier_cooldown_minutes" in body:
            config["frontier_cooldown_minutes"] = max(0, min(int(body.get("frontier_cooldown_minutes") or 0), 10080))
        if "stop_condition" in body:
            config["stop_condition"] = (body.get("stop_condition") or "").strip() or config.get("stop_condition")
        return config

    def _continuous_cadence_seconds(self, config):
        cadence = (config or {}).get("cadence") or "manual"
        if cadence == "hourly":
            return 3600
        if cadence == "daily":
            return 86400
        if cadence == "custom":
            return max(1, int((config or {}).get("custom_interval_minutes") or 60)) * 60
        return None

    def _continuous_next_run_at(self, config):
        seconds = self._continuous_cadence_seconds(config)
        if not seconds:
            return None
        return datetime.utcfromtimestamp(time.time() + seconds).isoformat()

    def _continuous_frontier_state(self, config):
        state = config.get("frontier_state") if isinstance(config.get("frontier_state"), dict) else {}
        state.setdefault("last_enriched_at", {})
        state.setdefault("selected_count", {})
        state.setdefault("coverage_cursor", 0)
        return state

    def _continuous_frontier_cooldown_seconds(self, config):
        minutes = int((config or {}).get("frontier_cooldown_minutes") or 360)
        return max(0, min(minutes, 10080)) * 60

    def _continuous_parse_iso_ts(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
            return (parsed - datetime(1970, 1, 1)).total_seconds()
        except Exception:
            return None

    def _continuous_frontier_key(self, item):
        return str(item.get("key") or item.get("target_key") or item.get("name") or "").strip()

    def _continuous_frontier_name(self, item):
        return str(item.get("name") or item.get("target_key") or item.get("key") or "frontier").strip()

    def _continuous_source_priority(self, source_kind):
        return {
            "new_graph_node": 100,
            "new_graph_edge": 100,
            "user_question_scope": 80,
            "reasoning_finding_seed": 60,
            "graph_coverage": 20,
        }.get(source_kind or "", 10)

    def _continuous_source_reason(self, source_kind):
        return {
            "new_graph_node": "new proposed graph node has not been enriched yet",
            "new_graph_edge": "new proposed graph edge/path has not been enriched yet",
            "user_question_scope": "user scoped reasoning question is an active research focus",
            "reasoning_finding_seed": "reasoning finding suggests a path that needs more evidence or expansion",
            "graph_coverage": "coverage fallback is rotating through graph items after higher-priority seeds",
        }.get(source_kind or "", "continuous enrichment frontier")

    def _continuous_normalize_frontier_item(self, item, *, source_kind=None, priority=None, reason=None):
        item = dict(item or {})
        if source_kind is None:
            source_kind = item.get("source_kind")
        if source_kind is None:
            kind = str(item.get("kind") or item.get("artifact_type") or "").lower()
            if kind == "proposed_node":
                source_kind = "new_graph_node"
            elif kind == "proposed_edge":
                source_kind = "new_graph_edge"
            else:
                source_kind = "graph_coverage"
        item["source_kind"] = source_kind
        if priority is not None:
            normalized_priority = float(priority)
        else:
            normalized_priority = max(float(item.get("priority") or 0), float(self._continuous_source_priority(source_kind)))
        item["priority"] = normalized_priority
        item["reason"] = reason or item.get("reason") or self._continuous_source_reason(source_kind)
        return item

    def _hydrate_continuous_frontier_item(self, tenant, item):
        key = self._continuous_frontier_key(item)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
        if payload or not key:
            return self._continuous_normalize_frontier_item(item)
        if not key.startswith("proposed-graph:"):
            return self._continuous_normalize_frontier_item(item)
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT e.element_key, e.element_type, e.name, e.payload_json,
                               e.evidence_refs_json, e.source_url, e.confidence,
                               e.status, e.iteration, r.run_key
                        FROM aletheia_proposed_graph_elements e
                        LEFT JOIN aletheia_iterative_graph_enrichment_runs r
                          ON e.run_id = r.id AND e.project_id = r.project_id
                        WHERE e.project_id = :tenant_id AND e.element_key = :element_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "element_key": key},
                ).mappings().first()
        except Exception:
            return self._continuous_normalize_frontier_item(item)
        if not row:
            return self._continuous_normalize_frontier_item(item)
        hydrated = dict(item)
        payload = _load_json(row["payload_json"], {})
        deep_profile = payload.get("deep_graph_profile") if isinstance(payload.get("deep_graph_profile"), dict) else {}
        hydrated.update(
            {
                "key": row["element_key"],
                "name": row["name"] or hydrated.get("name") or row["element_key"],
                "artifact_type": f"proposed_{row['element_type']}",
                "kind": f"proposed_{row['element_type']}",
                "source": hydrated.get("source") or "proposed_graph",
                "source_run_key": hydrated.get("source_run_key") or row["run_key"],
                "confidence": hydrated.get("confidence") if hydrated.get("confidence") is not None else row["confidence"],
                "depth": int(hydrated.get("depth") or row["iteration"] or 0),
                "evidence_refs": hydrated.get("evidence_refs") or _load_json(row["evidence_refs_json"], []),
                "source_url": hydrated.get("source_url") or row["source_url"],
                "ontology_type": hydrated.get("ontology_type") or payload.get("ontology_type") or payload.get("source_type") or payload.get("relation"),
                "payload": payload,
                "path": hydrated.get("path")
                or deep_profile.get("path_label")
                or payload.get("path_label")
                or (
                    f"{payload.get('source_label')} -> {payload.get('relation') or 'related_to'} -> {payload.get('target_label')}"
                    if payload.get("source_label") and payload.get("target_label")
                    else None
                ),
                "relation": hydrated.get("relation") or payload.get("relation"),
            }
        )
        source_kind = hydrated.get("source_kind")
        if not source_kind:
            source_kind = "new_graph_edge" if row["element_type"] == "edge" else "new_graph_node"
        return self._continuous_normalize_frontier_item(hydrated, source_kind=source_kind)

    def _continuous_row_to_frontier_item(self, row, source_kind):
        payload = _load_json(row["payload_json"], {})
        deep_profile = payload.get("deep_graph_profile") if isinstance(payload.get("deep_graph_profile"), dict) else {}
        item = {
            "key": row["element_key"],
            "name": row["name"] or row["element_key"],
            "artifact_type": f"proposed_{row['element_type']}",
            "kind": f"proposed_{row['element_type']}",
            "source": "proposed_graph",
            "source_kind": source_kind,
            "source_run_key": row.get("run_key"),
            "confidence": row.get("confidence"),
            "depth": int(row.get("iteration") or 0),
            "evidence_refs": _load_json(row.get("evidence_refs_json"), []),
            "source_url": row.get("source_url"),
            "ontology_type": payload.get("ontology_type") or payload.get("source_type") or payload.get("relation"),
            "payload": payload,
            "path": deep_profile.get("path_label")
            or payload.get("path_label")
            or (
                f"{payload.get('source_label')} -> {payload.get('relation') or 'related_to'} -> {payload.get('target_label')}"
                if payload.get("source_label") and payload.get("target_label")
                else None
            ),
            "relation": payload.get("relation"),
        }
        return self._continuous_normalize_frontier_item(item, source_kind=source_kind)

    def _continuous_proposed_graph_frontier(self, tenant, config, limit=50):
        state = self._continuous_frontier_state(config)
        enriched = state.get("last_enriched_at") or {}
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT e.element_key, e.element_type, e.name, e.payload_json,
                               e.evidence_refs_json, e.source_url, e.confidence,
                               e.status, e.iteration, r.run_key
                        FROM aletheia_proposed_graph_elements e
                        LEFT JOIN aletheia_iterative_graph_enrichment_runs r
                          ON e.run_id = r.id AND e.project_id = r.project_id
                        WHERE e.project_id = :tenant_id
                          AND e.element_type IN ('node', 'edge')
                          AND e.status IN ('draft', 'needs_more_evidence', 'approved')
                        ORDER BY e.created_at DESC, e.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            return []
        items = []
        for row in rows:
            is_new = row["element_key"] not in enriched
            if is_new:
                source_kind = "new_graph_edge" if row["element_type"] == "edge" else "new_graph_node"
            else:
                source_kind = "graph_coverage"
            items.append(self._continuous_row_to_frontier_item(row, source_kind))
        return items

    def _continuous_question_scope_frontier(self, tenant, limit=10):
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT canonical_key, question, scope_json, status, updated_at
                        FROM aletheia_reasoning_tasks
                        WHERE project_id = :tenant_id
                          AND status NOT IN ('closed', 'deleted')
                        ORDER BY updated_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            return []
        items = []
        for row in rows:
            scope = _load_json(row["scope_json"], {})
            graph_node = scope.get("graph_node") if isinstance(scope.get("graph_node"), dict) else {}
            graph_edge = scope.get("graph_edge") if isinstance(scope.get("graph_edge"), dict) else {}
            center_node = scope.get("center_node")
            center_type = scope.get("center_type") or scope.get("type") or graph_node.get("type")
            center_id = scope.get("center_id") or scope.get("id") or graph_node.get("id")
            if center_node and ":" in str(center_node) and not (center_type and center_id):
                center_type, center_id = str(center_node).split(":", 1)
            source_label = graph_edge.get("source_label") or graph_edge.get("source")
            target_label = graph_edge.get("target_label") or graph_edge.get("target")
            relation = graph_edge.get("relation")
            name = graph_edge.get("name") or graph_node.get("label") or (f"{center_type}:{center_id}" if center_type and center_id else row["question"])
            payload = {
                "label": graph_node.get("label") or name,
                "source_label": source_label,
                "target_label": target_label,
                "relation": relation,
                "ontology_type": center_type,
                "question": row["question"],
            }
            item = {
                "key": f"user-question:{row['canonical_key']}",
                "name": name,
                "artifact_type": "user_question_scope",
                "kind": "question_scope",
                "source": "reasoning_task",
                "source_kind": "user_question_scope",
                "related_question_key": row["canonical_key"],
                "payload": {key: value for key, value in payload.items() if value},
                "path": graph_edge.get("path_label") or scope.get("path_label"),
                "ontology_type": center_type,
            }
            items.append(self._continuous_normalize_frontier_item(item, source_kind="user_question_scope"))
        return items

    def _continuous_reasoning_finding_frontier(self, tenant, limit=20):
        items = []
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT f.canonical_key, f.title, f.conclusion, f.confidence,
                               f.supporting_evidence_json, f.recommended_action_json,
                               f.status, t.canonical_key AS task_key
                        FROM aletheia_reasoning_findings f
                        JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                        JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                        WHERE f.project_id = :tenant_id
                          AND f.status IN ('draft', 'approved', 'needs_more_evidence', 'reaffirmed')
                        ORDER BY f.confidence DESC, f.updated_at DESC, f.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            rows = []
        for row in rows:
            evidence = _load_json(row["supporting_evidence_json"], [])
            action = _load_json(row["recommended_action_json"], {})
            first_path = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
            item = {
                "key": f"reasoning-finding:{row['canonical_key']}",
                "name": row["title"],
                "artifact_type": "reasoning_finding_seed",
                "kind": "finding_seed",
                "source": "reasoning_finding",
                "source_kind": "reasoning_finding_seed",
                "related_finding_key": row["canonical_key"],
                "related_question_key": row.get("task_key"),
                "confidence": row.get("confidence"),
                "payload": {
                    "label": row["title"],
                    "summary": row["conclusion"],
                    "metrics": first_path.get("metrics") or first_path.get("metric"),
                    "source_label": first_path.get("source_label"),
                    "target_label": first_path.get("target_label"),
                    "relation": first_path.get("relation"),
                    "recommended_action": action,
                },
                "path": first_path.get("path_label") or first_path.get("path"),
                "evidence_refs": evidence,
            }
            items.append(self._continuous_normalize_frontier_item(item, source_kind="reasoning_finding_seed"))
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT c.canonical_key, c.title, c.summary, c.value_score,
                               c.evidence_chain_json, c.status, s.session_key
                        FROM aletheia_autopilot_candidate_findings c
                        JOIN aletheia_autopilot_sessions s ON c.session_id = s.id
                        WHERE c.project_id = :tenant_id
                          AND c.status IN ('draft', 'needs_more_evidence')
                        ORDER BY c.value_score DESC, c.updated_at DESC, c.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            rows = []
        for row in rows:
            evidence = _load_json(row["evidence_chain_json"], [])
            first_path = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
            item = {
                "key": f"reasoning-finding:{row['canonical_key']}",
                "name": row["title"],
                "artifact_type": "reasoning_finding_seed",
                "kind": "candidate_finding_seed",
                "source": "autopilot_candidate_finding",
                "source_kind": "reasoning_finding_seed",
                "related_finding_key": row["canonical_key"],
                "related_run": row.get("session_key"),
                "confidence": row.get("value_score"),
                "payload": {
                    "label": row["title"],
                    "summary": row["summary"],
                    "metrics": first_path.get("metrics") or first_path.get("metric"),
                    "source_label": first_path.get("source_label"),
                    "target_label": first_path.get("target_label"),
                    "relation": first_path.get("relation"),
                },
                "path": first_path.get("path_label") or first_path.get("path"),
                "evidence_refs": evidence,
            }
            items.append(self._continuous_normalize_frontier_item(item, source_kind="reasoning_finding_seed"))
        return items

    def _continuous_frontier_available(self, item, config, now_ts):
        key = self._continuous_frontier_key(item)
        if not key:
            return False
        state = self._continuous_frontier_state(config)
        last_ts = self._continuous_parse_iso_ts((state.get("last_enriched_at") or {}).get(key))
        if last_ts is None:
            return True
        return now_ts - last_ts >= self._continuous_frontier_cooldown_seconds(config)

    def _continuous_frontier_candidates(self, tenant, stored_frontier, config):
        candidates = []
        for item in stored_frontier or []:
            candidates.append(self._hydrate_continuous_frontier_item(tenant, item))
        candidates.extend(self._continuous_proposed_graph_frontier(tenant, config, limit=75))
        candidates.extend(self._continuous_question_scope_frontier(tenant, limit=10))
        candidates.extend(self._continuous_reasoning_finding_frontier(tenant, limit=20))
        deduped = {}
        for item in candidates:
            normalized = self._continuous_normalize_frontier_item(item)
            key = self._continuous_frontier_key(normalized)
            if not key:
                continue
            existing = deduped.get(key)
            if existing is None or normalized.get("priority", 0) > existing.get("priority", 0):
                deduped[key] = normalized
        return list(deduped.values())

    def _continuous_frontier_for_cycle(self, tenant, stored_frontier, config, max_frontier):
        now_ts = time.time()
        selected = []
        candidates = [self._continuous_normalize_frontier_item(item) for item in self._continuous_frontier_candidates(tenant, stored_frontier, config)]
        available_candidates = [item for item in candidates if self._continuous_frontier_available(item, config, now_ts)]
        if not available_candidates:
            available_candidates = [item for item in candidates if item.get("source_kind") == "graph_coverage"]
        available_candidates.sort(key=lambda item: (-float(item.get("priority") or 0), int(item.get("depth") or 0), self._continuous_frontier_key(item)))
        for hydrated in available_candidates:
            key = self._continuous_frontier_key(hydrated)
            if not key:
                continue
            selected_item = {
                "key": key,
                "name": self._continuous_frontier_name(hydrated),
                "artifact_type": hydrated.get("artifact_type") or hydrated.get("kind") or "frontier_item",
                "source": hydrated.get("source") or "continuous_frontier",
                "source_kind": hydrated.get("source_kind") or "graph_coverage",
                "priority": float(hydrated.get("priority") or self._continuous_source_priority(hydrated.get("source_kind"))),
                "reason": hydrated.get("reason") or self._continuous_source_reason(hydrated.get("source_kind")),
                "depth": int(hydrated.get("depth") or 0),
            }
            for field in (
                "kind",
                "payload",
                "path",
                "relation",
                "ontology_type",
                "evidence_refs",
                "source_run_key",
                "source_url",
                "confidence",
                "related_finding_key",
                "related_question_key",
                "related_run",
            ):
                if hydrated.get(field) is not None:
                    selected_item[field] = hydrated.get(field)
            selected.append(selected_item)
            if len(selected) >= max_frontier:
                break
        return selected

    def _continuous_next_frontier(self, previous_frontier, result, config):
        visited = set(config.get("visited_frontier_keys") or [])
        existing = {self._continuous_frontier_key(item) for item in (previous_frontier or [])}
        next_frontier = []
        additions = []
        for element in result.get("proposed_graph") or []:
            if element.get("element_type") not in {"node", "edge"}:
                continue
            key = element.get("element_key")
            if not key or key in existing or key in visited:
                continue
            payload = element.get("payload") or {}
            deep_profile = payload.get("deep_graph_profile") if isinstance(payload.get("deep_graph_profile"), dict) else {}
            item = {
                "kind": f"proposed_{element.get('element_type')}",
                "key": key,
                "name": element.get("name") or key,
                "artifact_type": f"proposed_{element.get('element_type')}",
                "source": "proposed_graph",
                "source_kind": "new_graph_edge" if element.get("element_type") == "edge" else "new_graph_node",
                "source_run_key": result.get("run", {}).get("run_key"),
                "confidence": element.get("confidence"),
                "depth": int(element.get("iteration") or 1),
                "evidence_refs": element.get("evidence_refs") or [],
                "source_url": element.get("source_url"),
                "ontology_type": payload.get("ontology_type") or payload.get("source_type") or payload.get("relation"),
                "payload": payload,
                "path": deep_profile.get("path_label") or payload.get("path_label"),
                "relation": payload.get("relation"),
            }
            item = self._continuous_normalize_frontier_item(item, source_kind=item["source_kind"])
            next_frontier.append(item)
            additions.append(item)
            existing.add(key)
        # Keep untouched frontier items so paused or budget-limited sessions
        # can continue after the newly discovered nodes/edges.
        for item in previous_frontier or []:
            key = self._continuous_frontier_key(item)
            if key and key not in visited and key not in existing:
                next_frontier.append(item)
                existing.add(key)
        return next_frontier[:100], additions

    def _continuous_append_events(self, config, events):
        merged = list(config.get("latest_events") or [])
        merged.extend(events)
        config["latest_events"] = merged[-50:]
        return config

    def configure_continuous_enrichment_session(self, tenant, session_key, body=None):
        body = body or {}
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        config = self._continuous_update_config(_load_json(row["config_json"], {}), body)
        config["next_run_at"] = self._continuous_next_run_at(config)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET config_json = :config_json, updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "session_key": session_key, "config_json": _json_dump(config)},
            )
        return self.continuous_enrichment_session(tenant, session_key)

    def _continuous_session_row(self, tenant, session_key):
        self._ensure_continuous_enrichment_schema(tenant)
        self._default_continuous_session(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text(
                    """
                    SELECT id, project_id, session_key, objective, status, config_json, frontier_json,
                           last_run_key, cycle_count, created_at, updated_at
                    FROM aletheia_continuous_enrichment_sessions
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "session_key": session_key},
            ).mappings().first()

    def _continuous_session_to_dict(self, tenant, row):
        if row is None:
            return None
        latest = None
        if row["last_run_key"]:
            latest_data = self.proposed_graph_elements(tenant, run_key=row["last_run_key"], limit=120)
            latest = {
                "run": (latest_data.get("runs") or [None])[0],
                "element_count": len(latest_data.get("elements") or []),
                "finding_count": len([e for e in latest_data.get("elements") or [] if e.get("element_type") == "finding"]),
                "findings": [
                    {
                        "name": e.get("name"),
                        "element_key": e.get("element_key"),
                        "status": e.get("status"),
                        "confidence": e.get("confidence"),
                        "path": ((e.get("payload") or {}).get("deep_graph_profile") or {}).get("path_label"),
                        "source_url": e.get("source_url"),
                    }
                    for e in latest_data.get("elements") or []
                    if e.get("element_type") == "finding"
                ],
            }
        return {
            "session_key": row["session_key"],
            "tenant_id": row["project_id"],
            "objective": row["objective"],
            "status": row["status"],
            "config": _load_json(row["config_json"], {}),
            "frontier": _load_json(row["frontier_json"], []),
            "last_run_key": row["last_run_key"],
            "cycle_count": row["cycle_count"],
            "created_at": _jsonable(row["created_at"]),
            "updated_at": _jsonable(row["updated_at"]),
            "latest": latest,
            "write_boundary": {
                "ontology_candidates_require_review": True,
                "graph_fact_target": "proposed_graph_space",
                "candidate_findings_only": True,
                "canonical_write": False,
                "formal_graph_write": False,
            },
        }

    def continuous_enrichment_sessions(self, tenant):
        self._ensure_continuous_enrichment_schema(tenant)
        self._default_continuous_session(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_id, session_key, objective, status, config_json, frontier_json,
                           last_run_key, cycle_count, created_at, updated_at
                    FROM aletheia_continuous_enrichment_sessions
                    WHERE project_id = :tenant_id
                    ORDER BY updated_at DESC, session_key ASC
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
        return {"tenant": tenant.public_dict(), "sessions": [self._continuous_session_to_dict(tenant, row) for row in rows]}

    def continuous_enrichment_session(self, tenant, session_key):
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        return {"tenant": tenant.public_dict(), "session": self._continuous_session_to_dict(tenant, row)}

    def update_continuous_enrichment_session_status(self, tenant, session_key, status):
        if status not in {"idle", "paused", "stopped"}:
            raise ValueError("Unsupported continuous enrichment session status")
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET status = :status, updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "session_key": session_key, "status": status},
            )
        return self.continuous_enrichment_session(tenant, session_key)

    def run_continuous_enrichment_cycle(self, tenant, session_key, body=None):
        body = body or {}
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        if row["status"] == "stopped":
            raise ValueError("Continuous enrichment session is stopped")
        if row["status"] == "paused" and not body.get("force"):
            raise ValueError("Continuous enrichment session is paused")
        config = self._continuous_update_config(_load_json(row["config_json"], {}), body)
        allowed_domains = config.get("allowed_domains") or ["zenodo.org"]
        max_frontier = int(config.get("max_frontier") or config.get("rate_limit_per_cycle") or 4)
        stored_frontier = _load_json(row["frontier_json"], [])
        frontier_items = self._continuous_frontier_for_cycle(tenant, stored_frontier, config, max_frontier)
        fixture_path = Path("/tmp") / f"aletheia-continuous-{_slug(session_key)}-{int(time.time())}.json"
        fixture_path.write_text(_json_dump(body.get("search_results") or self._continuous_source_fixture(session_key)), encoding="utf-8")
        objective = body.get("objective") or row["objective"]
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                        """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET status = 'running',
                        config_json = :config_json,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "session_key": session_key, "config_json": _json_dump(config)},
            )
        try:
            result = IterativeGraphEnrichmentAgent(
                tenant.metadata_db_url,
                tenant=tenant.tenant_id,
                search_results_json=str(fixture_path),
                allowed_domains=allowed_domains,
                max_iterations=int(config.get("max_iterations") or 1),
                max_frontier=max_frontier,
                max_results_per_query=int(config.get("max_results_per_query") or 4),
            ).run(objective, artifact_keys=body.get("artifact_keys") or None, frontier_items=frontier_items or None)
        finally:
            try:
                fixture_path.unlink()
            except OSError:
                pass
        run_key = result["run"]["run_key"]
        proposed_graph = result.get("proposed_graph") or []
        next_frontier, frontier_additions = self._continuous_next_frontier(stored_frontier, result, config)
        visited = list(dict.fromkeys([*(config.get("visited_frontier_keys") or []), *[self._continuous_frontier_key(item) for item in frontier_items if self._continuous_frontier_key(item)]]))
        config["visited_frontier_keys"] = visited[-500:]
        frontier_state = self._continuous_frontier_state(config)
        now_iso = datetime.utcnow().isoformat()
        for item in frontier_items:
            key = self._continuous_frontier_key(item)
            if not key:
                continue
            frontier_state["last_enriched_at"][key] = now_iso
            frontier_state["selected_count"][key] = int(frontier_state["selected_count"].get(key) or 0) + 1
        frontier_state["coverage_cursor"] = int(frontier_state.get("coverage_cursor") or 0) + len(frontier_items)
        config["frontier_state"] = frontier_state
        events = []
        graph_changed = bool(proposed_graph)
        if graph_changed:
            config["last_graph_changed_at"] = datetime.utcnow().isoformat()
            events.append(
                {
                    "type": "graph_changed",
                    "run_key": run_key,
                    "proposed_count": result["run"].get("proposed_count"),
                    "returned_element_count": len(proposed_graph),
                    "new_frontier_count": len(frontier_additions),
                    "created_at": config["last_graph_changed_at"],
                    "canonical_write": False,
                    "formal_graph_write": False,
                    "target": "proposed_graph_space",
                }
            )
            events.append(
                {
                    "type": "new_evidence_available",
                    "run_key": run_key,
                    "frontier_keys": [item.get("key") for item in frontier_additions[:10]],
                    "created_at": datetime.utcnow().isoformat(),
                    "review_boundary": "proposed_graph_review_gate",
                }
            )
        autopilot_result = None
        if graph_changed and body.get("trigger_autopilot", True) and self.reasoning_repository is not None:
            autopilot_payload = {
                "session_key": f"autopilot:{tenant.tenant_id}:continuous-enrichment:{_slug(run_key)}",
                "objective": (
                    body.get("autopilot_objective")
                    or f"Re-run deep reasoning after new graph evidence from {run_key}; generate candidate findings only."
                ),
                "budget": body.get("autopilot_budget") or {
                    "max_hypotheses": 6,
                    "max_reasoning_tasks": 4,
                    "max_tool_calls": 20,
                    "max_runtime_seconds": 120,
                },
                "created_by": "Continuous Enrichment Agent",
                "scope": {
                    "tenant": tenant.tenant_id,
                    "source_run_key": run_key,
                    "event": "new_evidence_available",
                    "candidate_findings_only": True,
                    "canonical_writes": "disabled",
                    "formal_graph_writes": "disabled",
                },
            }
            try:
                if tenant.tenant_id == "maritime-risk":
                    autopilot_result = self.reasoning_repository.run_maritime_risk_autopilot_playbook(tenant, autopilot_payload)
                elif tenant.tenant_id == "creditcardfraud":
                    autopilot_result = self.reasoning_repository.run_creditcardfraud_autopilot_playbook(tenant, autopilot_payload)
                else:
                    autopilot_result = self.reasoning_repository.create_autopilot_session(tenant, autopilot_payload)
                config["last_autopilot_session_key"] = (autopilot_result.get("session") or {}).get("session_key") or autopilot_payload["session_key"]
                events.append(
                    {
                        "type": "autopilot_triggered",
                        "source_event": "new_evidence_available",
                        "source_run_key": run_key,
                        "autopilot_session_key": config["last_autopilot_session_key"],
                        "candidate_findings": len(autopilot_result.get("candidate_findings") or []),
                        "auto_approve_findings": False,
                        "created_at": datetime.utcnow().isoformat(),
                    }
                )
            except Exception as exc:
                events.append(
                    {
                        "type": "autopilot_trigger_failed",
                        "source_event": "new_evidence_available",
                        "source_run_key": run_key,
                        "reason": _safe_error_message(exc),
                        "created_at": datetime.utcnow().isoformat(),
                    }
                )
        config["next_run_at"] = self._continuous_next_run_at(config)
        self._continuous_append_events(config, events)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET status = 'idle',
                        last_run_key = :run_key,
                        cycle_count = cycle_count + 1,
                        config_json = :config_json,
                        frontier_json = :frontier_json,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "run_key": run_key,
                    "config_json": _json_dump(config),
                    "frontier_json": _json_dump(next_frontier),
                },
            )
        return {
            "tenant": tenant.public_dict(),
            "session": self.continuous_enrichment_session(tenant, session_key)["session"],
            "cycle": {
                "run_key": run_key,
                "status": result["run"]["status"],
                "proposed_count": result["run"]["proposed_count"],
                "returned_element_count": len(result.get("proposed_graph") or []),
                "finding_count": len([e for e in result.get("proposed_graph") or [] if e.get("element_type") == "finding"]),
                "skipped_sources": result["run"].get("skipped_sources") or [],
                "frontier_used": frontier_items,
                "next_frontier_count": len(next_frontier),
                "new_frontier": frontier_additions,
                "events": events,
                "autopilot_session_key": config.get("last_autopilot_session_key"),
                "frontier_priority_summary": {
                    "selected": [
                        {
                            "key": item.get("key"),
                            "name": item.get("name"),
                            "source_kind": item.get("source_kind"),
                            "priority": item.get("priority"),
                            "reason": item.get("reason"),
                            "related_finding_key": item.get("related_finding_key"),
                            "related_question_key": item.get("related_question_key"),
                        }
                        for item in frontier_items
                    ],
                    "coverage_cursor": frontier_state.get("coverage_cursor"),
                    "cooldown_minutes": config.get("frontier_cooldown_minutes"),
                },
                "findings": [
                    {
                        "name": e.get("name"),
                        "confidence": e.get("confidence"),
                        "source_url": e.get("source_url"),
                        "path": ((e.get("payload") or {}).get("deep_graph_profile") or {}).get("path_label"),
                    }
                    for e in result.get("proposed_graph") or []
                    if e.get("element_type") == "finding"
                ],
            },
            "write_boundary": {
                "canonical_write": False,
                "formal_graph_write": False,
                "target": "proposed_graph_space",
                "findings": "candidate_only",
                "autopilot_auto_approve": False,
            },
        }

    def agent_runs_console(self, tenant, limit=20):
        limit = max(1, min(int(limit or 20), 100))

        sessions = self.continuous_enrichment_sessions(tenant).get("sessions", [])
        runs = []
        degraded = []

        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, run_key, source_agent, status, objective, frontier_json,
                               expansion_trace_json, safety_profile_json, budget_json,
                               skipped_sources_json, proposed_count, pruned_count,
                               finding_count, error, started_at, finished_at
                        FROM aletheia_iterative_graph_enrichment_runs
                        WHERE project_id = :tenant_id
                        ORDER BY started_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": limit},
                ).mappings().all()
                for row in rows:
                    elements = conn.execute(
                        text(
                            """
                            SELECT element_key, element_type, name, payload_json,
                                   evidence_refs_json, source_url, confidence,
                                   status, iteration, created_at
                            FROM aletheia_proposed_graph_elements
                            WHERE project_id = :tenant_id AND run_id = :run_id
                            ORDER BY iteration ASC, element_type ASC, name ASC
                            LIMIT 200
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "run_id": row["id"]},
                    ).mappings().all()
                    runs.append(
                        {
                            "kind": "iterative_graph_enrichment",
                            "run_key": row["run_key"],
                            "agent": row["source_agent"],
                            "status": row["status"],
                            "objective": row["objective"],
                            "frontier": _load_json(row["frontier_json"], []),
                            "trace": _load_json(row["expansion_trace_json"], []),
                            "safety_profile": _load_json(row["safety_profile_json"], {}),
                            "budget": _load_json(row["budget_json"], {}),
                            "skipped_sources": _load_json(row["skipped_sources_json"], []),
                            "counts": {
                                "proposed": row["proposed_count"],
                                "pruned": row["pruned_count"],
                                "findings": row["finding_count"],
                                "returned": len(elements),
                            },
                            "elements": [
                                {
                                    "element_key": e["element_key"],
                                    "element_type": e["element_type"],
                                    "name": e["name"],
                                    "payload": _load_json(e["payload_json"], {}),
                                    "evidence_refs": _load_json(e["evidence_refs_json"], []),
                                    "source_url": e["source_url"],
                                    "confidence": e["confidence"],
                                    "status": e["status"],
                                    "iteration": e["iteration"],
                                    "created_at": _jsonable(e["created_at"]),
                                }
                                for e in elements
                            ],
                            "started_at": _jsonable(row["started_at"]),
                            "finished_at": _jsonable(row["finished_at"]),
                            "error": row["error"],
                            "write_boundary": {
                                "target": "proposed_graph_space",
                                "canonical_write": False,
                                "formal_graph_write": False,
                                "findings": "candidate_only",
                            },
                        }
                    )
        except Exception as exc:
            degraded.append({"kind": "iterative_graph_enrichment", "reason": _safe_error_message(exc)})

        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, run_key, source_agent, search_provider, status,
                               target_artifacts_json, safety_profile_json, budget_json,
                               skipped_sources_json, query_count, result_count,
                               proposal_count, error, started_at, finished_at
                        FROM aletheia_web_enrichment_runs
                        WHERE project_id = :tenant_id
                        ORDER BY started_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": limit},
                ).mappings().all()
                for row in rows:
                    proposals = conn.execute(
                        text(
                            """
                            SELECT proposal_key, target_artifact_key, source_url,
                                   source_title, summary, raw_payload_json,
                                   confidence, status, created_at
                            FROM aletheia_web_enrichment_proposals
                            WHERE project_id = :tenant_id AND run_id = :run_id
                            ORDER BY created_at DESC, id DESC
                            LIMIT 100
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "run_id": row["id"]},
                    ).mappings().all()
                    runs.append(
                        {
                            "kind": "web_enrichment_crawl",
                            "run_key": row["run_key"],
                            "agent": row["source_agent"],
                            "status": row["status"],
                            "objective": f"Enrich ontology artifacts via {row['search_provider']} search/crawl",
                            "frontier": [
                                {"kind": "target_artifact", "target_key": key}
                                for key in _load_json(row["target_artifacts_json"], [])
                            ],
                            "trace": [
                                {
                                    "query": _web_enrichment_query(
                                        _load_json(p["raw_payload_json"], {}),
                                        p["target_artifact_key"],
                                    ),
                                    "result_count": 1,
                                    "source_url": p["source_url"],
                                    "target": p["target_artifact_key"],
                                    "extracted_candidates": [p["proposal_key"]],
                                }
                                for p in proposals
                            ],
                            "safety_profile": _load_json(row["safety_profile_json"], {}),
                            "budget": _load_json(row["budget_json"], {}),
                            "skipped_sources": _load_json(row["skipped_sources_json"], []),
                            "counts": {
                                "queries": row["query_count"],
                                "results": row["result_count"],
                                "proposals": row["proposal_count"],
                                "returned": len(proposals),
                            },
                            "elements": [
                                {
                                    "element_key": p["proposal_key"],
                                    "element_type": "ontology_enrichment_proposal",
                                    "name": p["source_title"] or p["target_artifact_key"],
                                    "target_artifact_key": p["target_artifact_key"],
                                    "payload": _load_json(p["raw_payload_json"], {}),
                                    "evidence_refs": [p["source_url"]] if p["source_url"] else [],
                                    "source_url": p["source_url"],
                                    "confidence": p["confidence"],
                                    "status": p["status"],
                                    "summary": p["summary"],
                                    "created_at": _jsonable(p["created_at"]),
                                }
                                for p in proposals
                            ],
                            "started_at": _jsonable(row["started_at"]),
                            "finished_at": _jsonable(row["finished_at"]),
                            "error": row["error"],
                            "write_boundary": {
                                "target": "ontology_review_queue",
                                "canonical_write": False,
                                "formal_graph_write": False,
                                "ontology_review_required": True,
                            },
                        }
                    )
        except Exception as exc:
            degraded.append({"kind": "web_enrichment_crawl", "reason": _safe_error_message(exc)})

        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, session_key, objective, scope_json, budget_json,
                               safety_profile_json, status, created_by,
                               created_at, updated_at
                        FROM aletheia_autopilot_sessions
                        WHERE project_id = :tenant_id
                        ORDER BY updated_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": limit},
                ).mappings().all()
                for row in rows:
                    hypotheses = conn.execute(
                        text(
                            """
                            SELECT hypothesis_key, title, rationale, status, priority,
                                   evidence_plan_json, reasoning_task_keys_json, pruned_reason,
                                   created_at, updated_at
                            FROM aletheia_autopilot_hypotheses
                            WHERE project_id = :tenant_id AND session_id = :session_id
                            ORDER BY priority ASC, id ASC
                            LIMIT 100
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "session_id": row["id"]},
                    ).mappings().all()
                    candidates = conn.execute(
                        text(
                            """
                            SELECT canonical_key, title, conclusion, value_score,
                                   confidence, novelty_score, impact_score,
                                   evidence_chain_json, evidence_limits_json,
                                   suggested_action_json, status, created_at, updated_at
                            FROM aletheia_autopilot_candidate_findings
                            WHERE project_id = :tenant_id AND session_id = :session_id
                            ORDER BY value_score DESC, confidence DESC, id ASC
                            LIMIT 100
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "session_id": row["id"]},
                    ).mappings().all()
                    runs.append(
                        {
                            "kind": "autopilot_deep_reasoning",
                            "run_key": row["session_key"],
                            "agent": row["created_by"],
                            "status": row["status"],
                            "objective": row["objective"],
                            "frontier": [_load_json(row["scope_json"], {})],
                            "trace": [
                                {
                                    "hypothesis_key": h["hypothesis_key"],
                                    "title": h["title"],
                                    "status": h["status"],
                                    "priority": h["priority"],
                                    "evidence_plan": _load_json(h["evidence_plan_json"], []),
                                    "reasoning_task_keys": _load_json(h["reasoning_task_keys_json"], []),
                                    "pruned_reason": h["pruned_reason"],
                                }
                                for h in hypotheses
                            ],
                            "safety_profile": _load_json(row["safety_profile_json"], {}),
                            "budget": _load_json(row["budget_json"], {}),
                            "skipped_sources": [
                                {"reason": h["pruned_reason"], "hypothesis": h["hypothesis_key"]}
                                for h in hypotheses
                                if h["pruned_reason"]
                            ],
                            "counts": {
                                "hypotheses": len(hypotheses),
                                "candidate_findings": len(candidates),
                                "pruned": sum(1 for h in hypotheses if h["status"] == "pruned"),
                            },
                            "elements": [
                                {
                                    "element_key": c["canonical_key"],
                                    "element_type": "candidate_finding",
                                    "name": c["title"],
                                    "payload": {
                                        "conclusion": c["conclusion"],
                                        "value_score": c["value_score"],
                                        "novelty_score": c["novelty_score"],
                                        "impact_score": c["impact_score"],
                                        "suggested_action": _load_json(c["suggested_action_json"], {}),
                                    },
                                    "evidence_refs": [
                                        item.get("source_ref") or item.get("source") or item.get("metric") or item.get("kind")
                                        for item in _load_json(c["evidence_chain_json"], [])
                                    ],
                                    "evidence_chain": _load_json(c["evidence_chain_json"], []),
                                    "confidence": c["confidence"],
                                    "status": c["status"],
                                    "created_at": _jsonable(c["created_at"]),
                                }
                                for c in candidates
                            ],
                            "started_at": _jsonable(row["created_at"]),
                            "finished_at": _jsonable(row["updated_at"]),
                            "error": None,
                            "write_boundary": {
                                "target": "candidate_findings",
                                "canonical_write": False,
                                "formal_graph_write": False,
                                "auto_approve_findings": False,
                            },
                        }
                    )
        except Exception as exc:
            degraded.append({"kind": "autopilot_deep_reasoning", "reason": _safe_error_message(exc)})

        runs.sort(key=lambda item: item.get("started_at") or item.get("finished_at") or "", reverse=True)
        return {
            "tenant": tenant.public_dict(),
            "sessions": sessions,
            "runs": runs[: limit * 3],
            "degraded": degraded,
            "write_boundary": {
                "ontology_candidates_require_review": True,
                "graph_fact_target": "proposed_graph_space",
                "candidate_findings_only": True,
                "canonical_write": False,
                "formal_graph_write": False,
            },
        }

    def proposed_graph_elements(self, tenant, run_key=None, limit=50, status_filter="pending"):
        limit = max(1, min(int(limit), 200))
        where = "e.project_id = :tenant_id"
        params = {"tenant_id": tenant.tenant_id, "limit": limit}
        if run_key:
            where += " AND r.run_key = :run_key"
            params["run_key"] = run_key
        status_filter = (status_filter or "pending").replace("-", "_").lower()
        if status_filter in {"pending", "active", "draft"}:
            where += " AND e.status IN ('draft', 'needs_more_evidence')"
        elif status_filter in {"reviewed", "closed"}:
            where += " AND e.status IN ('approved', 'rejected')"
        elif status_filter in {"approved", "rejected", "needs_more_evidence"}:
            where += " AND e.status = :status_filter"
            params["status_filter"] = status_filter
        elif status_filter in {"all", "*"}:
            pass
        else:
            raise ValueError("Unsupported proposed graph status filter")
        with self.metadata_engine_for(tenant).connect() as conn:
            summary = conn.execute(
                text(
                    f"""
                    SELECT COUNT(*) AS total_count
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {where}
                    """
                ),
                params,
            ).mappings().first()
            type_rows = conn.execute(
                text(
                    f"""
                    SELECT e.element_type, COUNT(*) AS count
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {where}
                    GROUP BY e.element_type
                    """
                ),
                params,
            ).mappings().all()
            rows = conn.execute(
                text(
                    f"""
                    SELECT e.element_key, e.element_type, e.name, e.payload_json,
                           e.evidence_refs_json, e.source_url, e.confidence, e.status,
                           e.iteration, e.created_at, r.run_key, r.objective,
                           r.status AS run_status, r.proposed_count, r.finding_count,
                           r.pruned_count, r.expansion_trace_json, r.safety_profile_json,
                           r.skipped_sources_json, r.started_at, r.finished_at
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {where}
                    ORDER BY r.started_at DESC, e.iteration ASC, e.element_type ASC, e.name ASC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
        elements = []
        runs = {}
        for row in rows:
            run = runs.setdefault(
                row["run_key"],
                {
                    "run_key": row["run_key"],
                    "objective": row["objective"],
                    "status": row["run_status"],
                    "proposed_count": row["proposed_count"],
                    "finding_count": row["finding_count"],
                    "pruned_count": row["pruned_count"],
                    "expansion_trace": _load_json(row["expansion_trace_json"], []),
                    "safety_profile": _load_json(row["safety_profile_json"], {}),
                    "skipped_sources": _load_json(row["skipped_sources_json"], []),
                    "started_at": _jsonable(row["started_at"]),
                    "finished_at": _jsonable(row["finished_at"]),
                },
            )
            elements.append(
                {
                    "element_key": row["element_key"],
                    "element_type": row["element_type"],
                    "name": row["name"],
                    "payload": _load_json(row["payload_json"], {}),
                    "evidence_refs": _load_json(row["evidence_refs_json"], []),
                    "source_url": row["source_url"],
                    "confidence": row["confidence"],
                    "status": row["status"],
                    "iteration": row["iteration"],
                    "created_at": _jsonable(row["created_at"]),
                    "run_key": run["run_key"],
                }
            )
        return {
            "tenant": tenant.public_dict(),
            "runs": list(runs.values()),
            "elements": elements,
            "total_count": int(summary["total_count"] or 0) if summary else len(elements),
            "element_type_counts": {row["element_type"]: int(row["count"] or 0) for row in type_rows},
            "status_filter": status_filter,
        }

    def review_proposed_graph_element(self, tenant, element_key, action, body=None):
        action = (action or "").replace("_", "-").lower()
        body = body or {}
        status_by_action = {
            "approve": "approved",
            "reject": "rejected",
            "needs-evidence": "needs_more_evidence",
            "comment": None,
        }
        if action not in status_by_action:
            raise ValueError("Unsupported graph proposal review action")
        reason = (body.get("reason") or body.get("note") or "").strip()
        if action in {"reject", "needs-evidence"} and not reason:
            raise ValueError("Review reason is required for reject or needs evidence")
        reviewer = (body.get("reviewer") or "Saskue").strip() or "Saskue"
        reviewed_at = datetime.utcnow().isoformat()
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT element_key, element_type, name, payload_json, evidence_refs_json,
                           source_url, confidence, status, iteration, created_at
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "element_key": element_key},
            ).mappings().first()
            if row is None:
                return None
            payload = _load_json(row["payload_json"], {})
            before_status = row["status"]
            after_status = status_by_action[action] or before_status
            review_event = {
                "decision": action,
                "reviewer": reviewer,
                "reason": reason,
                "before_status": before_status,
                "after_status": after_status,
                "created_at": reviewed_at,
                "canonical_write": False,
                "formal_graph_write": False,
            }
            payload.setdefault("review_events", []).append(review_event)
            payload["review_boundary"] = {
                "writes_canonical": False,
                "writes_formal_graph": False,
                "status_scope": "proposed_graph_element_only",
            }
            conn.execute(
                text(
                    """
                    UPDATE aletheia_proposed_graph_elements
                    SET status = :status, payload_json = :payload_json
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "element_key": element_key,
                    "status": after_status,
                    "payload_json": _json_dump(payload),
                },
            )
        element = {
            "element_key": row["element_key"],
            "element_type": row["element_type"],
            "name": row["name"],
            "payload": payload,
            "evidence_refs": _load_json(row["evidence_refs_json"], []),
            "source_url": row["source_url"],
            "confidence": row["confidence"],
            "status": after_status,
            "iteration": row["iteration"],
            "created_at": _jsonable(row["created_at"]),
        }
        return {
            "tenant": tenant.public_dict(),
            "element": element,
            "review": review_event,
            "write_boundary": {
                "canonical_write": False,
                "formal_graph_write": False,
                "target": "proposed_graph_space",
            },
        }

    def _proposed_graph_element_requires_ontology_review(self, payload):
        payload = payload or {}
        boundary = payload.get("review_boundary") or payload.get("write_boundary") or payload.get("governance") or {}
        return any(
            bool(value)
            for value in (
                payload.get("requires_ontology_proposal"),
                payload.get("ontology_proposal_required"),
                payload.get("requires_ontology_review"),
                boundary.get("requires_ontology_proposal"),
                boundary.get("ontology_proposal_required"),
                boundary.get("requires_ontology_review"),
            )
        )

    def review_proposed_graph_elements_batch(self, tenant, element_keys, action, body=None):
        action = (action or "").replace("_", "-").lower()
        body = body or {}
        if action not in {"approve", "reject", "needs-evidence", "comment"}:
            raise ValueError("Unsupported graph proposal review action")
        if not isinstance(element_keys, list) or not element_keys:
            raise ValueError("Batch review requires element_keys")
        unique_keys = []
        seen = set()
        for key in element_keys:
            key = str(key or "").strip()
            if key and key not in seen:
                seen.add(key)
                unique_keys.append(key)
        if not unique_keys:
            raise ValueError("Batch review requires element_keys")
        if len(unique_keys) > 200:
            raise ValueError("Batch review is limited to 200 proposed graph elements")

        reason = (body.get("reason") or body.get("note") or "").strip()
        reviewer = (body.get("reviewer") or "Itachi").strip() or "Itachi"
        results = []
        for element_key in unique_keys:
            with self.metadata_engine_for(tenant).connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT element_key, element_type, payload_json
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id AND element_key = :element_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "element_key": element_key},
                ).mappings().first()
            if row is None:
                results.append({"element_key": element_key, "ok": False, "error": "Proposed graph element not found"})
                continue
            payload = _load_json(row["payload_json"], {})
            if action == "approve" and self._proposed_graph_element_requires_ontology_review(payload):
                results.append(
                    {
                        "element_key": element_key,
                        "element_type": row["element_type"],
                        "ok": False,
                        "error": "Requires ontology proposal review before graph approval",
                    }
                )
                continue
            try:
                result = self.review_proposed_graph_element(
                    tenant,
                    element_key,
                    action,
                    {"reason": reason, "reviewer": reviewer},
                )
            except ValueError as exc:
                results.append(
                    {
                        "element_key": element_key,
                        "element_type": row["element_type"],
                        "ok": False,
                        "error": str(exc),
                    }
                )
                continue
            if result is None:
                results.append({"element_key": element_key, "ok": False, "error": "Proposed graph element not found"})
                continue
            results.append(
                {
                    "element_key": element_key,
                    "element_type": result["element"]["element_type"],
                    "ok": True,
                    "status": result["element"]["status"],
                    "element": result["element"],
                    "review": result["review"],
                }
            )
        ok_count = sum(1 for item in results if item.get("ok"))
        return {
            "tenant": tenant.public_dict(),
            "action": action,
            "requested_count": len(unique_keys),
            "ok_count": ok_count,
            "failed_count": len(results) - ok_count,
            "results": results,
            "write_boundary": {
                "canonical_write": False,
                "formal_graph_write": False,
                "target": "proposed_graph_space",
                "scope": "selected_proposed_graph_elements",
            },
        }

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
        self._autopilot_schema_ready = set()
        self._finding_experience_schema_ready = set()

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

    def ensure_finding_experience_schema(self, tenant):
        key = tenant.metadata_db_url
        if key in self._finding_experience_schema_ready:
            return
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_finding_actions (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(255) NOT NULL DEFAULT 'default',
                    action_key VARCHAR(500) NOT NULL,
                    finding_key VARCHAR(500) NOT NULL,
                    title TEXT NOT NULL,
                    action_type VARCHAR(100) NOT NULL DEFAULT 'investigate',
                    owner VARCHAR(255),
                    due_at TIMESTAMP,
                    priority VARCHAR(50) NOT NULL DEFAULT 'medium',
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
                    result VARCHAR(100),
                    result_detail TEXT,
                    created_from VARCHAR(100) NOT NULL DEFAULT 'approved_finding',
                    canonical_write BOOLEAN NOT NULL DEFAULT FALSE,
                    graph_write BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    closed_at TIMESTAMP
                )
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_finding_actions_project_key ON aletheia_finding_actions (project_id, action_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_finding_actions_project_finding ON aletheia_finding_actions (project_id, finding_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_finding_actions_project_status_due ON aletheia_finding_actions (project_id, status, due_at)"))
        self._finding_experience_schema_ready.add(key)

    def ensure_autopilot_schema(self, tenant):
        key = tenant.metadata_db_url
        if key in self._autopilot_schema_ready:
            return
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_autopilot_sessions (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(255) NOT NULL,
                    session_key VARCHAR(255) NOT NULL,
                    objective TEXT NOT NULL,
                    scope_json TEXT NOT NULL DEFAULT '{}',
                    budget_json TEXT NOT NULL DEFAULT '{}',
                    safety_profile_json TEXT NOT NULL DEFAULT '{}',
                    status VARCHAR(50) NOT NULL DEFAULT 'draft',
                    created_by VARCHAR(255) NOT NULL DEFAULT 'Autopilot',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_autopilot_hypotheses (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES aletheia_autopilot_sessions(id) ON DELETE CASCADE,
                    project_id VARCHAR(255) NOT NULL,
                    hypothesis_key VARCHAR(255) NOT NULL,
                    title TEXT NOT NULL,
                    rationale TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'queued',
                    priority INTEGER NOT NULL DEFAULT 100,
                    evidence_plan_json TEXT NOT NULL DEFAULT '[]',
                    reasoning_task_keys_json TEXT NOT NULL DEFAULT '[]',
                    pruned_reason TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_autopilot_candidate_findings (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES aletheia_autopilot_sessions(id) ON DELETE CASCADE,
                    hypothesis_id INTEGER REFERENCES aletheia_autopilot_hypotheses(id) ON DELETE SET NULL,
                    project_id VARCHAR(255) NOT NULL,
                    canonical_key VARCHAR(255) NOT NULL,
                    title TEXT NOT NULL,
                    conclusion TEXT NOT NULL,
                    value_score FLOAT NOT NULL DEFAULT 0,
                    confidence FLOAT NOT NULL DEFAULT 0,
                    novelty_score FLOAT NOT NULL DEFAULT 0,
                    impact_score FLOAT NOT NULL DEFAULT 0,
                    evidence_chain_json TEXT NOT NULL DEFAULT '[]',
                    evidence_limits_json TEXT NOT NULL DEFAULT '[]',
                    suggested_action_json TEXT NOT NULL DEFAULT '{}',
                    status VARCHAR(50) NOT NULL DEFAULT 'draft',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_autopilot_sessions_project_key ON aletheia_autopilot_sessions (project_id, session_key)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_autopilot_hypotheses_project_key ON aletheia_autopilot_hypotheses (project_id, hypothesis_key)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_autopilot_candidate_findings_project_key ON aletheia_autopilot_candidate_findings (project_id, canonical_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_autopilot_hypotheses_session ON aletheia_autopilot_hypotheses (session_id, priority, id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_autopilot_candidate_findings_session ON aletheia_autopilot_candidate_findings (session_id, value_score DESC, id)"))
        self._autopilot_schema_ready.add(key)

    def _autopilot_budget(self, raw):
        raw = raw or {}
        return {
            "max_hypotheses": max(1, min(int(raw.get("max_hypotheses") or 8), 25)),
            "max_reasoning_tasks": max(1, min(int(raw.get("max_reasoning_tasks") or raw.get("max_runs") or 5), 20)),
            "max_tool_calls": max(1, min(int(raw.get("max_tool_calls") or raw.get("max_queries") or 20), 80)),
            "max_runtime_seconds": max(5, min(int(raw.get("max_runtime_seconds") or 120), 600)),
            "sample_strategy": raw.get("sample_strategy") or "deterministic_full_table_aggregates",
        }

    def _autopilot_safety_profile(self, raw):
        raw = raw or {}
        profile = {
            "approved_only": raw.get("approved_only", True) is not False,
            "safe_views_only": raw.get("safe_views_only", True) is not False,
            "allow_sensitive_fields": False,
            "masked_fields_only": True,
            "write_scope": "draft_only",
            "canonical_writes": "disabled",
            "auto_approve_findings": False,
        }
        blocked = raw.get("blocked_fields") if "blocked_fields" in raw else ["card_verification_code_fields"]
        normalized_blocked = []
        for field in blocked:
            if field in {"cardCVV", "enteredCVV"}:
                field = "card_verification_code_fields"
            normalized_blocked.append(field)
        profile["blocked_fields"] = list(dict.fromkeys(normalized_blocked))
        return profile

    def create_autopilot_session(self, tenant, payload):
        objective = (payload.get("objective") or "").strip()
        if not objective:
            raise ValueError("objective is required")
        self.ensure_autopilot_schema(tenant)
        scope = payload.get("scope") or {}
        budget = self._autopilot_budget(payload.get("budget") or {})
        safety = self._autopilot_safety_profile(payload.get("safety_profile") or payload.get("safety") or {})
        nonce = payload.get("nonce") or int(time.time() * 1000)
        session_key = payload.get("session_key") or f"autopilot:{tenant.tenant_id}:{_slug(objective)}:{nonce}"
        created_by = payload.get("created_by") or "Autopilot"
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO aletheia_autopilot_sessions
                    (project_id, session_key, objective, scope_json, budget_json, safety_profile_json,
                     status, created_by, created_at, updated_at)
                    VALUES
                    (:tenant_id, :session_key, :objective, :scope_json, :budget_json, :safety_profile_json,
                     'draft', :created_by, NOW(), NOW())
                    ON CONFLICT (project_id, session_key) DO UPDATE SET
                      objective = EXCLUDED.objective,
                      scope_json = EXCLUDED.scope_json,
                      budget_json = EXCLUDED.budget_json,
                      safety_profile_json = EXCLUDED.safety_profile_json,
                      status = aletheia_autopilot_sessions.status,
                      updated_at = NOW()
                    RETURNING id, project_id, session_key, objective, scope_json, budget_json,
                              safety_profile_json, status, created_by, created_at, updated_at
                """),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "objective": objective,
                    "scope_json": _json_dump(scope),
                    "budget_json": _json_dump(budget),
                    "safety_profile_json": _json_dump(safety),
                    "created_by": created_by,
                },
            ).mappings().first()
        for item in payload.get("hypotheses") or []:
            self.add_autopilot_hypothesis(tenant, session_key, item)
        for item in payload.get("candidate_findings") or []:
            self.add_autopilot_candidate_finding(tenant, session_key, item)
        return self.get_autopilot_session(tenant, session_key)

    def list_autopilot_sessions(self, tenant, status=None, limit=50):
        self.ensure_autopilot_schema(tenant)
        conditions = ["project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id, "limit": max(1, min(int(limit or 50), 100))}
        if status:
            conditions.append("status = :status")
            params["status"] = status
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, project_id, session_key, objective, scope_json, budget_json,
                           safety_profile_json, status, created_by, created_at, updated_at
                    FROM aletheia_autopilot_sessions
                    WHERE {where}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT :limit
                """),
                params,
            ).mappings().all()
        return {"tenant": tenant.public_dict(), "sessions": [self._autopilot_session_to_dict(row) for row in rows]}

    def get_autopilot_session(self, tenant, session_key):
        self.ensure_autopilot_schema(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            session = conn.execute(
                text("""
                    SELECT id, project_id, session_key, objective, scope_json, budget_json,
                           safety_profile_json, status, created_by, created_at, updated_at
                    FROM aletheia_autopilot_sessions
                    WHERE project_id = :tenant_id AND session_key = :session_key
                """),
                {"tenant_id": tenant.tenant_id, "session_key": session_key},
            ).mappings().first()
            if not session:
                return None
            hypotheses = conn.execute(
                text("""
                    SELECT id, session_id, project_id, hypothesis_key, title, rationale, status,
                           priority, evidence_plan_json, reasoning_task_keys_json, pruned_reason,
                           created_at, updated_at
                    FROM aletheia_autopilot_hypotheses
                    WHERE project_id = :tenant_id AND session_id = :session_id
                    ORDER BY priority ASC, id ASC
                """),
                {"tenant_id": tenant.tenant_id, "session_id": session["id"]},
            ).mappings().all()
            candidates = conn.execute(
                text("""
                    SELECT id, session_id, hypothesis_id, project_id, canonical_key, title, conclusion,
                           value_score, confidence, novelty_score, impact_score, evidence_chain_json,
                           evidence_limits_json, suggested_action_json, status, created_at, updated_at
                    FROM aletheia_autopilot_candidate_findings
                    WHERE project_id = :tenant_id AND session_id = :session_id
                    ORDER BY value_score DESC, confidence DESC, id ASC
                """),
                {"tenant_id": tenant.tenant_id, "session_id": session["id"]},
            ).mappings().all()
        return {
            "tenant": tenant.public_dict(),
            "session": self._autopilot_session_to_dict(session),
            "hypotheses": [self._autopilot_hypothesis_to_dict(row) for row in hypotheses],
            "candidate_findings": [self._autopilot_candidate_to_dict(row) for row in candidates],
        }

    def add_autopilot_hypothesis(self, tenant, session_key, payload):
        self.ensure_autopilot_schema(tenant)
        session = self._autopilot_session_row(tenant, session_key)
        if not session:
            raise KeyError(session_key)
        title = (payload.get("title") or "").strip()
        if not title:
            raise ValueError("hypothesis title is required")
        hypothesis_key = payload.get("hypothesis_key") or f"{session_key}:hypothesis:{_slug(title)}"
        status = payload.get("status") or "queued"
        if status not in {"queued", "running", "completed", "pruned"}:
            raise ValueError("hypothesis status must be queued, running, completed, or pruned")
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO aletheia_autopilot_hypotheses
                    (session_id, project_id, hypothesis_key, title, rationale, status, priority,
                     evidence_plan_json, reasoning_task_keys_json, pruned_reason, created_at, updated_at)
                    VALUES
                    (:session_id, :tenant_id, :hypothesis_key, :title, :rationale, :status, :priority,
                     :evidence_plan_json, :reasoning_task_keys_json, :pruned_reason, NOW(), NOW())
                    ON CONFLICT (project_id, hypothesis_key) DO UPDATE SET
                      title = EXCLUDED.title,
                      rationale = EXCLUDED.rationale,
                      status = EXCLUDED.status,
                      priority = EXCLUDED.priority,
                      evidence_plan_json = EXCLUDED.evidence_plan_json,
                      reasoning_task_keys_json = EXCLUDED.reasoning_task_keys_json,
                      pruned_reason = EXCLUDED.pruned_reason,
                      updated_at = NOW()
                    RETURNING id, session_id, project_id, hypothesis_key, title, rationale, status,
                              priority, evidence_plan_json, reasoning_task_keys_json, pruned_reason,
                              created_at, updated_at
                """),
                {
                    "session_id": session["id"],
                    "tenant_id": tenant.tenant_id,
                    "hypothesis_key": hypothesis_key,
                    "title": title,
                    "rationale": payload.get("rationale"),
                    "status": status,
                    "priority": int(payload.get("priority") or 100),
                    "evidence_plan_json": _json_dump(payload.get("evidence_plan") or []),
                    "reasoning_task_keys_json": _json_dump(payload.get("reasoning_task_keys") or []),
                    "pruned_reason": payload.get("pruned_reason"),
                },
            ).mappings().first()
        return {"tenant": tenant.public_dict(), "hypothesis": self._autopilot_hypothesis_to_dict(row)}

    def add_autopilot_candidate_finding(self, tenant, session_key, payload):
        self.ensure_autopilot_schema(tenant)
        session = self._autopilot_session_row(tenant, session_key)
        if not session:
            raise KeyError(session_key)
        title = (payload.get("title") or "").strip()
        conclusion = (payload.get("conclusion") or "").strip()
        if not title or not conclusion:
            raise ValueError("candidate finding title and conclusion are required")
        canonical_key = payload.get("canonical_key") or f"candidate:autopilot:{_slug(session_key)}:{_slug(title)}"
        status = payload.get("status") or "draft"
        if status not in {"draft", "needs_more_evidence", "rejected", "promoted"}:
            raise ValueError("candidate finding status must be draft, needs_more_evidence, rejected, or promoted")
        if status == "promoted":
            raise ValueError("candidate findings cannot be auto-promoted by the Autopilot API")
        hypothesis_id = payload.get("hypothesis_id")
        hypothesis_key = payload.get("hypothesis_key")
        if hypothesis_key and not hypothesis_id:
            hypothesis = self._autopilot_hypothesis_row(tenant, hypothesis_key)
            hypothesis_id = hypothesis["id"] if hypothesis else None
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO aletheia_autopilot_candidate_findings
                    (session_id, hypothesis_id, project_id, canonical_key, title, conclusion,
                     value_score, confidence, novelty_score, impact_score, evidence_chain_json,
                     evidence_limits_json, suggested_action_json, status, created_at, updated_at)
                    VALUES
                    (:session_id, :hypothesis_id, :tenant_id, :canonical_key, :title, :conclusion,
                     :value_score, :confidence, :novelty_score, :impact_score, :evidence_chain_json,
                     :evidence_limits_json, :suggested_action_json, :status, NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      hypothesis_id = EXCLUDED.hypothesis_id,
                      title = EXCLUDED.title,
                      conclusion = EXCLUDED.conclusion,
                      value_score = EXCLUDED.value_score,
                      confidence = EXCLUDED.confidence,
                      novelty_score = EXCLUDED.novelty_score,
                      impact_score = EXCLUDED.impact_score,
                      evidence_chain_json = EXCLUDED.evidence_chain_json,
                      evidence_limits_json = EXCLUDED.evidence_limits_json,
                      suggested_action_json = EXCLUDED.suggested_action_json,
                      status = EXCLUDED.status,
                      updated_at = NOW()
                    RETURNING id, session_id, hypothesis_id, project_id, canonical_key, title,
                              conclusion, value_score, confidence, novelty_score, impact_score,
                              evidence_chain_json, evidence_limits_json, suggested_action_json,
                              status, created_at, updated_at
                """),
                {
                    "session_id": session["id"],
                    "hypothesis_id": hypothesis_id,
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": canonical_key,
                    "title": title,
                    "conclusion": conclusion,
                    "value_score": float(payload.get("value_score") or 0),
                    "confidence": float(payload.get("confidence") or 0),
                    "novelty_score": float(payload.get("novelty_score") or 0),
                    "impact_score": float(payload.get("impact_score") or 0),
                    "evidence_chain_json": _json_dump(payload.get("evidence_chain") or []),
                    "evidence_limits_json": _json_dump(payload.get("evidence_limits") or []),
                    "suggested_action_json": _json_dump(payload.get("suggested_action") or {}),
                    "status": status,
                },
            ).mappings().first()
        return {"tenant": tenant.public_dict(), "candidate_finding": self._autopilot_candidate_to_dict(row)}

    def review_autopilot_candidate(self, tenant, candidate_key, action, reviewer, reason):
        self.ensure_autopilot_schema(tenant)
        decision_aliases = {
            "approve": "approved",
            "reject": "rejected",
            "needs-evidence": "needs_more_evidence",
            "needs-more-evidence": "needs_more_evidence",
            "needs_more_evidence": "needs_more_evidence",
            "comment": "comment",
        }
        decision = decision_aliases.get(action, action)
        if decision not in {"approved", "rejected", "needs_more_evidence", "comment"}:
            raise ValueError(f"Unsupported candidate review action: {action}")
        if decision != "approved":
            _require_reason(decision, reason or "")
        with self.metadata_engine_for(tenant).begin() as conn:
            candidate = conn.execute(
                text(
                    """
                    SELECT c.*, s.session_key, s.objective, h.hypothesis_key
                    FROM aletheia_autopilot_candidate_findings c
                    JOIN aletheia_autopilot_sessions s ON c.session_id = s.id
                    LEFT JOIN aletheia_autopilot_hypotheses h ON c.hypothesis_id = h.id
                    WHERE c.project_id = :tenant_id AND c.canonical_key = :candidate_key
                    FOR UPDATE OF c
                    """
                ),
                {"tenant_id": tenant.tenant_id, "candidate_key": candidate_key},
            ).mappings().first()
            if not candidate:
                raise KeyError(candidate_key)
            candidate_dict = self._autopilot_candidate_to_dict(candidate)
            before_status = candidate_dict["status"]
            if decision == "comment":
                evidence_limits = list(candidate_dict.get("evidence_limits") or [])
                evidence_limits.append(f"Reviewer note by {reviewer}: {reason.strip()}")
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_autopilot_candidate_findings
                        SET evidence_limits_json = :evidence_limits_json, updated_at = NOW()
                        WHERE project_id = :tenant_id AND canonical_key = :candidate_key
                        """
                    ),
                    {
                        "tenant_id": tenant.tenant_id,
                        "candidate_key": candidate_key,
                        "evidence_limits_json": _json_dump(evidence_limits),
                    },
                )
                return {
                    "tenant": tenant.public_dict(),
                    "candidate_finding": self._autopilot_candidate_to_dict({
                        **candidate,
                        "evidence_limits_json": _json_dump(evidence_limits),
                    }),
                    "review": {"decision": decision, "reviewer": reviewer, "reason": reason},
                    "canonical_boundary": self._finding_canonical_boundary(),
                }
            conn.execute(
                text(
                    """
                    UPDATE aletheia_autopilot_candidate_findings
                    SET status = :status, updated_at = NOW()
                    WHERE project_id = :tenant_id AND canonical_key = :candidate_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "candidate_key": candidate_key, "status": decision},
            )
            if decision != "approved":
                return {
                    "tenant": tenant.public_dict(),
                    "candidate_finding": self._autopilot_candidate_to_dict({**candidate, "status": decision}),
                    "review": {
                        "decision": decision,
                        "reviewer": reviewer,
                        "reason": reason,
                        "before_status": before_status,
                        "after_status": decision,
                    },
                    "canonical_boundary": self._finding_canonical_boundary(),
                }
            evidence_chain = candidate_dict.get("evidence_chain") or []
            if not evidence_chain:
                raise ValueError("approved candidate requires evidence_chain")
            formal_key = f"finding:approved:{_slug(candidate_key)}"
            task_key = f"reasoning:approved-finding:{_slug(candidate_key)}"
            run_key = f"{task_key}:run:{int(time.time() * 1000)}"
            now_scope = {
                "source": "autopilot_candidate_review_gate",
                "tenant_id": tenant.tenant_id,
                "candidate_key": candidate_key,
                "autopilot_session_key": candidate["session_key"],
                "hypothesis_key": candidate.get("hypothesis_key"),
                "approved_only": True,
                "review_gate": "human_finding_approval",
                "canonical_writes": False,
                "graph_writes": False,
            }
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_tasks
                    (project_id, canonical_key, question, scope_json, allowed_tools_json, status, created_at, updated_at)
                    VALUES (:tenant_id, :task_key, :question, :scope_json, :allowed_tools_json, 'completed', NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      question = EXCLUDED.question,
                      scope_json = EXCLUDED.scope_json,
                      allowed_tools_json = EXCLUDED.allowed_tools_json,
                      status = 'completed',
                      updated_at = NOW()
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "task_key": task_key,
                    "question": f"Human-approved Autopilot finding: {candidate_dict['title']}",
                    "scope_json": _json_dump(now_scope),
                    "allowed_tools_json": _json_dump(["prior_finding_registry", "propose_action", "propose_change_proposal"]),
                },
            )
            task = conn.execute(
                text("SELECT id FROM aletheia_reasoning_tasks WHERE project_id = :tenant_id AND canonical_key = :task_key"),
                {"tenant_id": tenant.tenant_id, "task_key": task_key},
            ).mappings().first()
            run = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_runs
                    (task_id, project_id, run_key, agent_name, prompt_version,
                     query_plan_json, tool_calls_json, evidence_paths_json,
                     output_json, eval_result_json, status, latency_ms, cost_estimate, created_at)
                    VALUES
                    (:task_id, :tenant_id, :run_key, 'FindingApprovalReviewGate', 'finding-approval-v1',
                     :query_plan_json, :tool_calls_json, :evidence_paths_json,
                     :output_json, :eval_result_json, 'completed', 0, 0.0, NOW())
                    RETURNING id, project_id, run_key, agent_name, prompt_version,
                              query_plan_json, tool_calls_json, evidence_paths_json,
                              output_json, eval_result_json, status, latency_ms, cost_estimate, created_at
                    """
                ),
                {
                    "task_id": task["id"],
                    "tenant_id": tenant.tenant_id,
                    "run_key": run_key,
                    "query_plan_json": _json_dump([
                        {"step": "review_candidate", "boundary": "human review gate"},
                        {"step": "register_approved_finding", "writes_canonical": False},
                    ]),
                    "tool_calls_json": _json_dump([
                        {"tool": "autopilot_candidate_read", "source_ref": candidate_key, "safe_view_only": True},
                        {"tool": "finding_registry_write", "status": "approved", "canonical_write": False},
                    ]),
                    "evidence_paths_json": _json_dump([
                        *evidence_chain,
                        {
                            "kind": "autopilot_candidate",
                            "label": "Reviewed Autopilot candidate",
                            "source_ref": candidate_key,
                            "payload": {
                                "session_key": candidate["session_key"],
                                "hypothesis_key": candidate.get("hypothesis_key"),
                            },
                        },
                    ]),
                    "output_json": _json_dump({
                        "answer": candidate_dict["conclusion"],
                        "reviewed_inference": True,
                        "prior_finding": formal_key,
                    }),
                    "eval_result_json": _json_dump({"passed": True, "checks": ["evidence_chain_present", "human_review_present", "canonical_write_disabled"]}),
                },
            ).mappings().first()
            recommended_action = self._approved_finding_action(candidate_dict, candidate, reason)
            finding = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_findings
                    (run_id, project_id, canonical_key, title, conclusion, confidence,
                     supporting_evidence_json, counter_evidence_json, recommended_action_json,
                     status, version, source_agent, created_at, updated_at)
                    VALUES
                    (:run_id, :tenant_id, :canonical_key, :title, :conclusion, :confidence,
                     :supporting_evidence_json, :counter_evidence_json, :recommended_action_json,
                     'approved', 1, 'FindingApprovalReviewGate', NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      run_id = EXCLUDED.run_id,
                      title = EXCLUDED.title,
                      conclusion = EXCLUDED.conclusion,
                      confidence = EXCLUDED.confidence,
                      supporting_evidence_json = EXCLUDED.supporting_evidence_json,
                      counter_evidence_json = EXCLUDED.counter_evidence_json,
                      recommended_action_json = EXCLUDED.recommended_action_json,
                      status = 'approved',
                      version = aletheia_reasoning_findings.version + 1,
                      source_agent = 'FindingApprovalReviewGate',
                      updated_at = NOW()
                    RETURNING id, run_id, project_id, canonical_key, title, conclusion, confidence,
                              supporting_evidence_json, counter_evidence_json, recommended_action_json,
                              status, version, source_agent, created_at, updated_at
                    """
                ),
                {
                    "run_id": run["id"],
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": formal_key,
                    "title": candidate_dict["title"],
                    "conclusion": candidate_dict["conclusion"],
                    "confidence": candidate_dict["confidence"],
                    "supporting_evidence_json": _json_dump(evidence_chain),
                    "counter_evidence_json": _json_dump(candidate_dict.get("evidence_limits") or []),
                    "recommended_action_json": _json_dump(recommended_action),
                },
            ).mappings().first()
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_reviews
                    (finding_id, project_id, canonical_key, decision, reviewer, reason,
                     before_status, after_status, before_version, after_version, created_at)
                    VALUES
                    (:finding_id, :project_id, :canonical_key, 'approved', :reviewer, :reason,
                     :before_status, 'approved', 0, :after_version, NOW())
                    """
                ),
                {
                    "finding_id": finding["id"],
                    "project_id": tenant.tenant_id,
                    "canonical_key": formal_key,
                    "reviewer": reviewer,
                    "reason": reason,
                    "before_status": before_status,
                    "after_version": finding["version"],
                },
            )
        approved = self.get_finding(tenant, formal_key)
        return {
            "tenant": tenant.public_dict(),
            "candidate_finding": self.get_autopilot_candidate(tenant, candidate_key),
            "finding": self._decorate_approved_finding(approved),
            "registry_entry": {
                "finding_key": formal_key,
                "context_label": "prior_finding",
                "reasoning_label": "reviewed_inference",
                "active_context": True,
            },
            "workspace_next_action": approved.get("recommended_action", {}).get("workspace_next_action") if approved else None,
            "change_proposal_bridge": approved.get("recommended_action", {}).get("change_proposal_bridge") if approved else None,
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def get_autopilot_candidate(self, tenant, candidate_key):
        self.ensure_autopilot_schema(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT *
                    FROM aletheia_autopilot_candidate_findings
                    WHERE project_id = :tenant_id AND canonical_key = :candidate_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "candidate_key": candidate_key},
            ).mappings().first()
        return self._autopilot_candidate_to_dict(row) if row else None

    def _approved_finding_action(self, candidate_dict, candidate_row, reason):
        suggested = candidate_dict.get("suggested_action") or {}
        deep_graph_profile = candidate_dict.get("deep_graph_profile") or self._deep_graph_profile(candidate_dict.get("evidence_chain") or [])
        return {
            "type": "reviewed_inference",
            "prior_insight_label": "approved finding",
            "source_candidate_key": candidate_dict.get("canonical_key"),
            "autopilot_session_key": candidate_row.get("session_key"),
            "hypothesis_key": candidate_row.get("hypothesis_key"),
            "finding_emphasis": candidate_dict.get("finding_emphasis") or deep_graph_profile.get("finding_emphasis"),
            "deep_graph_profile": deep_graph_profile,
            "review_reason": reason,
            "next_action": suggested,
            "workspace_next_action": {
                "type": "case_next_action",
                "label": suggested.get("next") or suggested.get("label") or "Review approved finding and assign follow-up owner",
                "source": "approved_finding",
                "status": "ready_for_dispatch",
                "writes_canonical": False,
            },
            "change_proposal_bridge": {
                "available": True,
                "proposal_types": ["ontology_rule", "graph_edge", "review_playbook"],
                "writes_canonical": False,
                "requires_governance_review": True,
            },
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _finding_canonical_boundary(self):
        return {
            "finding_approval_writes": ["aletheia_reasoning_findings", "aletheia_reasoning_reviews"],
            "canonical_ontology_write": False,
            "graph_write": False,
            "auto_business_action": False,
            "promotion_requires": "separate ontology/graph/rule proposal review gate",
        }

    DEEP_GRAPH_REQUIRED_STEPS = ("hazard", "chokepoint", "dependent_country", "risk_metric", "recommended_action")

    def _deep_graph_profile(self, evidence_chain):
        def step_for(item):
            kind = str(item.get("kind") or "").lower()
            metric = str(item.get("metric") or "").lower()
            if "hazard" in kind or kind in {"risk_indicator"} or metric.startswith("likelihood_") or metric.startswith("severity_"):
                return "hazard"
            if "chokepoint" in kind or metric == "canal":
                return "chokepoint"
            if kind in {"dependent_country", "dependent_countries", "country", "countries"} or metric == "iso3":
                return "dependent_country"
            if kind in {"trade_metric", "risk_metric"} or metric in {"trade_at_risk_v", "trade_impacted", "v_canal", "v_canal / v", "top_trade_at_risk_v"}:
                return "risk_metric"
            if "action" in kind:
                return "recommended_action"
            return None

        def label_for(item):
            value = item.get("value")
            if isinstance(value, list):
                countries = [str(v.get("iso3")) for v in value if isinstance(v, dict) and v.get("iso3")]
                return ", ".join(countries[:5]) or item.get("metric") or item.get("kind")
            if isinstance(value, dict):
                return value.get("label") or value.get("name") or value.get("iso3") or value.get("canal") or item.get("metric") or item.get("kind")
            return str(value) if value not in (None, "") else item.get("metric") or item.get("kind")

        step_order = []
        nodes = []
        for item in evidence_chain or []:
            if not isinstance(item, dict):
                continue
            step = step_for(item)
            if not step:
                continue
            if step not in step_order:
                step_order.append(step)
            nodes.append(
                {
                    "step": step,
                    "kind": item.get("kind"),
                    "source_ref": item.get("source_ref"),
                    "metric": item.get("metric"),
                    "label": label_for(item),
                }
            )
        missing_steps = [step for step in self.DEEP_GRAPH_REQUIRED_STEPS if step not in step_order]
        hop_count = max(len(step_order) - 1, 0)
        multi_hop = hop_count >= 3 and not missing_steps
        return {
            "reasoning_type": "graph_multi_hop" if multi_hop else "evidence_chain",
            "finding_emphasis": "deep_graph_finding" if multi_hop else "candidate_finding",
            "required_steps": list(self.DEEP_GRAPH_REQUIRED_STEPS),
            "observed_steps": step_order,
            "missing_steps": missing_steps,
            "hop_count": hop_count,
            "multi_hop": multi_hop,
            "path": nodes,
            "path_label": " -> ".join(node["label"] for node in nodes if node.get("label")),
        }

    def _finding_action_to_dict(self, row):
        due_at = row["due_at"]
        closed_at = row["closed_at"]
        updated_at = row["updated_at"]
        created_at = row["created_at"]
        is_overdue = False
        if due_at and row["status"] not in {"closed"}:
            try:
                is_overdue = due_at < datetime.now(due_at.tzinfo)
            except Exception:
                is_overdue = str(due_at) < datetime.now().isoformat()
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "action_key": row["action_key"],
            "finding_key": row["finding_key"],
            "title": row["title"],
            "action_type": row["action_type"],
            "owner": row["owner"],
            "due_at": str(due_at) if due_at else None,
            "priority": row["priority"],
            "status": row["status"],
            "result": row["result"],
            "result_detail": row["result_detail"],
            "created_from": row["created_from"],
            "canonical_write": bool(row["canonical_write"]),
            "graph_write": bool(row["graph_write"]),
            "is_overdue": bool(is_overdue),
            "created_at": str(created_at) if created_at else None,
            "updated_at": str(updated_at) if updated_at else None,
            "closed_at": str(closed_at) if closed_at else None,
        }

    def _review_to_dict(self, row):
        return {
            "canonical_key": row.get("canonical_key"),
            "decision": row["decision"],
            "reviewer": row["reviewer"],
            "reason": row["reason"],
            "before_status": row["before_status"],
            "after_status": row["after_status"],
            "before_version": row["before_version"],
            "after_version": row["after_version"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
        }

    def run_creditcardfraud_autopilot_playbook(self, tenant, payload):
        if tenant.tenant_id != "creditcardfraud":
            raise ValueError("creditcardfraud playbook requires tenant=creditcardfraud")
        profile = self._creditcardfraud_profile(tenant)
        objective = payload.get("objective") or "Discover high-value credit card fraud risk findings"
        session_key = payload.get("session_key")
        session_payload = {
            "session_key": session_key,
            "objective": objective,
            "scope": {
                "tenant": tenant.tenant_id,
                "table": "credit_card_transactions_safe",
                "approved_only": True,
                "source_surface": "creditcardfraud_discovery_playbook",
                "source_mode": profile.get("source_mode"),
            },
            "budget": payload.get("budget") or {
                "max_hypotheses": 8,
                "max_reasoning_tasks": 5,
                "max_tool_calls": 20,
                "max_runtime_seconds": 120,
            },
            "safety_profile": {
                "approved_only": True,
                "safe_views_only": True,
                "allow_sensitive_fields": False,
                "blocked_fields": ["card_verification_code_fields"],
            },
            "created_by": payload.get("created_by") or "Creditcardfraud Discovery Playbook",
        }
        if isinstance(payload.get("scope"), dict):
            session_payload["scope"].update(payload["scope"])
        created = self.create_autopilot_session(tenant, session_payload)
        session_key = created["session"]["session_key"]

        hypothesis_specs = [
            {
                "title": "Card-not-present transactions concentrate fraud risk",
                "rationale": "Compare card-present and card-not-present fraud rates against the dataset baseline.",
                "status": "completed",
                "priority": 10,
                "evidence_plan": [{"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "fraud_rate_by_card_present"}],
                "reasoning_task_keys": ["reasoning:creditcardfraud:dataset-risk-profile:v1"],
            },
            {
                "title": "Verification mismatch transactions have elevated fraud rate",
                "rationale": "Use the safe derived verification-match flag instead of raw verification values.",
                "status": "completed",
                "priority": 20,
                "evidence_plan": [{"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "fraud_rate_by_verification_match"}],
                "reasoning_task_keys": ["reasoning:creditcardfraud:dataset-risk-profile:v1"],
            },
            {
                "title": "Missing POS entry mode may identify a weak-control channel",
                "rationale": "Missing POS entry mode showed the highest fraud-rate lift in the imported dataset profile.",
                "status": "completed",
                "priority": 30,
                "evidence_plan": [{"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "fraud_rate_missing_pos_entry"}],
                "reasoning_task_keys": ["reasoning:creditcardfraud:dataset-risk-profile:v1"],
            },
            {
                "title": "Merchant categories concentrate fraud exposure",
                "rationale": "Rank merchant categories by fraud rate and volume to separate noisy rates from high-value findings.",
                "status": "completed",
                "priority": 40,
                "evidence_plan": [{"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "fraud_rate_by_merchant_category"}],
                "reasoning_task_keys": ["reasoning:creditcardfraud:dataset-risk-profile:v1"],
            },
            {
                "title": "Same account/merchant/amount/day duplicate clusters indicate multi-swipe risk",
                "rationale": "Repeated same-day transaction clusters are useful triage candidates for duplicate authorization or multi-swipe review.",
                "status": "completed",
                "priority": 50,
                "evidence_plan": [{"kind": "cluster", "source_ref": "credit_card_transactions_safe", "metric": "same_account_merchant_amount_day_clusters"}],
                "reasoning_task_keys": ["reasoning:creditcardfraud:dataset-risk-profile:v1"],
            },
            {
                "title": "Expiration-key mismatch does not clear the value threshold",
                "rationale": "The imported profile did not show enough value lift to promote this into a candidate finding before stronger evidence exists.",
                "status": "pruned",
                "priority": 90,
                "evidence_plan": [{"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "expiration_key_in_match"}],
                "pruned_reason": "Pruned because expected fraud-rate lift is below candidate threshold and no strong operational action follows from the field alone.",
                "reasoning_task_keys": ["reasoning:creditcardfraud:dataset-risk-profile:v1"],
            },
        ]
        hypotheses = {}
        for spec in hypothesis_specs:
            result = self.add_autopilot_hypothesis(tenant, session_key, spec)
            hypotheses[spec["title"]] = result["hypothesis"]["hypothesis_key"]

        candidate_specs = self._creditcardfraud_candidate_specs(profile, hypotheses)
        for spec in candidate_specs:
            self.add_autopilot_candidate_finding(tenant, session_key, spec)

        return self.get_autopilot_session(tenant, session_key)

    def run_maritime_risk_autopilot_playbook(self, tenant, payload):
        if tenant.tenant_id != "maritime-risk":
            raise ValueError("maritime-risk playbook requires tenant=maritime-risk")
        profile = self._maritime_risk_profile(tenant)
        objective = payload.get("objective") or "Discover graph reasoning findings for maritime chokepoint risk"
        session_payload = {
            "session_key": payload.get("session_key"),
            "objective": objective,
            "scope": {
                "tenant": tenant.tenant_id,
                "tables": [
                    "maritime_chokepoint_country_dependencies",
                    "maritime_chokepoint_risk_indicators",
                    "maritime_chokepoint_systemic_risk_results",
                ],
                "approved_only": True,
                "source_surface": "maritime_risk_graph_reasoning_playbook",
                "source_mode": profile.get("source_mode"),
                "reasoning_mode": "graph_multi_hop",
                "finding_emphasis": "deep_graph_findings",
                "required_finding_path": [
                    "hazard",
                    "chokepoint",
                    "dependent_country",
                    "trade_or_risk_metric",
                    "recommended_action",
                ],
            },
            "budget": payload.get("budget") or {
                "max_hypotheses": 8,
                "max_reasoning_tasks": 5,
                "max_tool_calls": 20,
                "max_runtime_seconds": 120,
            },
            "safety_profile": {
                "approved_only": True,
                "safe_views_only": True,
                "allow_sensitive_fields": False,
                "blocked_fields": [],
                "canonical_writes": "disabled",
                "auto_approve_findings": False,
            },
            "created_by": payload.get("created_by") or "Maritime-risk Graph Reasoning Playbook",
        }
        if isinstance(payload.get("scope"), dict):
            session_payload["scope"].update(payload["scope"])
        created = self.create_autopilot_session(tenant, session_payload)
        session_key = created["session"]["session_key"]

        hypothesis_specs = [
            {
                "title": "Single-chokepoint dependency can create concentrated country exposure",
                "rationale": "Rank country/chokepoint pairs by value share and dependent trade value to find countries exposed to one chokepoint.",
                "status": "completed",
                "priority": 10,
                "evidence_plan": [
                    {
                        "kind": "graph_path",
                        "source_ref": "maritime_chokepoint_risk_indicators -> maritime_chokepoint_country_dependencies -> maritime_risk_playbook",
                        "metric": "hazard_to_country_dependency_share_to_action",
                        "required_graph_path": list(self.DEEP_GRAPH_REQUIRED_STEPS),
                    }
                ],
                "reasoning_task_keys": ["reasoning:maritime-risk:chokepoint-dependency:v1"],
            },
            {
                "title": "Hazard severity should be joined to dependent trade value before ranking chokepoints",
                "rationale": "Combine hazard likelihood/severity with systemic risk results so the finding explains risk propagation, not just trade volume.",
                "status": "completed",
                "priority": 20,
                "evidence_plan": [
                    {
                        "kind": "graph_path",
                        "source_ref": "maritime_chokepoint_risk_indicators -> maritime_chokepoint_systemic_risk_results -> maritime_risk_playbook",
                        "metric": "hazard_adjusted_trade_at_risk_to_action",
                        "required_graph_path": list(self.DEEP_GRAPH_REQUIRED_STEPS),
                    }
                ],
                "reasoning_task_keys": ["reasoning:maritime-risk:hazard-adjusted-risk:v1"],
            },
            {
                "title": "Red Sea / Bab el-Mandeb escalation should prioritize dependent countries by systemic risk",
                "rationale": "Use the chokepoint hazard row and downstream country risk rows to prioritize analyst review when upstream events increase.",
                "status": "completed",
                "priority": 30,
                "evidence_plan": [
                    {
                        "kind": "graph_path",
                        "source_ref": "maritime_chokepoint_risk_indicators -> maritime_chokepoint_systemic_risk_results -> maritime_risk_playbook",
                        "metric": "bab_el_mandeb_hazard_to_country_priority_to_action",
                        "required_graph_path": list(self.DEEP_GRAPH_REQUIRED_STEPS),
                    }
                ],
                "reasoning_task_keys": ["reasoning:maritime-risk:red-sea-priority:v1"],
            },
            {
                "title": "High throughput alone is not enough for a graph reasoning finding",
                "rationale": "A volume-only ranking does not explain hazard, dependency, country exposure, and action linkage.",
                "status": "pruned",
                "priority": 90,
                "evidence_plan": [{"kind": "aggregate", "source_ref": "maritime_chokepoint_country_dependencies", "metric": "sum_v_canal"}],
                "pruned_reason": "Pruned because it is a ranking/reporting hypothesis without a complete hazard -> chokepoint -> country -> risk metric -> action path.",
                "reasoning_task_keys": ["reasoning:maritime-risk:volume-only:v1"],
            },
        ]
        hypotheses = {}
        for spec in hypothesis_specs:
            result = self.add_autopilot_hypothesis(tenant, session_key, spec)
            hypotheses[spec["title"]] = result["hypothesis"]["hypothesis_key"]

        for spec in self._maritime_risk_candidate_specs(profile, hypotheses):
            self.add_autopilot_candidate_finding(tenant, session_key, spec)

        return self.get_autopilot_session(tenant, session_key)

    def _maritime_risk_profile(self, tenant):
        fallback = {
            "source_mode": "fallback_reported_profile",
            "tables": {
                "maritime_chokepoint_country_dependencies": 4950,
                "maritime_chokepoint_risk_indicators": 24,
                "maritime_chokepoint_systemic_risk_results": 4752,
            },
            "top_dependency": [
                {"iso3": "ERI", "canal": "Bab el-Mandeb Strait", "v_canal": 820217259.96, "v": 1122417684.78, "share": 0.7308},
                {"iso3": "QAT", "canal": "Strait of Hormuz", "v_canal": 96857752381.97, "v": 139422651416.03, "share": 0.6947},
                {"iso3": "DJI", "canal": "Bab el-Mandeb Strait", "v_canal": 5487893583.24, "v": 7982553963.16, "share": 0.6875},
            ],
            "top_systemic_risk": [
                {"iso3": "CHN", "canal": "Taiwan Strait", "trade_at_risk_v": 23559681578.78, "trade_impacted": 81768261948.46, "v_share": 0.2324},
                {"iso3": "CHN", "canal": "Bab el-Mandeb Strait", "trade_at_risk_v": 15110427387.67, "trade_impacted": 46556020850.45, "v_share": 0.0918},
                {"iso3": "USA", "canal": "Panama Canal", "trade_at_risk_v": 12192832212.34, "trade_impacted": 306107297223.91, "v_share": 0.1049},
            ],
            "bab_el_mandeb_priority": [
                {"iso3": "CHN", "canal": "Bab el-Mandeb Strait", "trade_at_risk_v": 15110427387.67, "trade_impacted": 46556020850.45, "v_share": 0.0918},
                {"iso3": "IND", "canal": "Bab el-Mandeb Strait", "trade_at_risk_v": 7067159777.33, "trade_impacted": 21774290660.73, "v_share": 0.2291},
                {"iso3": "USA", "canal": "Bab el-Mandeb Strait", "trade_at_risk_v": 6574347208.87, "trade_impacted": 20255909239.48, "v_share": 0.0479},
            ],
            "bab_el_mandeb_hazard": {
                "canal": "Bab el-Mandeb Strait",
                "likelihood_conflict": 0.6731,
                "severity_conflict": 0.5,
                "likelihood_geopolitical": 2.3529,
                "severity_geopolitical": 0.5,
                "likelihood_piracy": 0.2556,
                "severity_piracy": 0.005,
            },
            "top_dependency_hazard": {
                "canal": "Bab el-Mandeb Strait",
                "likelihood_conflict": 0.6731,
                "severity_conflict": 0.5,
                "likelihood_geopolitical": 2.3529,
                "severity_geopolitical": 0.5,
                "likelihood_piracy": 0.2556,
                "severity_piracy": 0.005,
            },
        }
        try:
            with self.source_engine_for(tenant).connect() as conn:
                tables = {
                    table: int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
                    for table in fallback["tables"]
                }
                top_dependency = [
                    dict(row)
                    for row in conn.execute(text("""
                        SELECT iso3, canal, v_canal, v, v_canal / NULLIF(v, 0) AS share
                        FROM maritime_chokepoint_country_dependencies
                        WHERE v > 0
                        ORDER BY share DESC, v_canal DESC
                        LIMIT 5
                    """)).mappings().all()
                ]
                dependency_canal = top_dependency[0]["canal"] if top_dependency else fallback["top_dependency"][0]["canal"]
                dependency_hazard = conn.execute(
                    text("""
                        SELECT canal, likelihood_conflict, severity_conflict,
                               likelihood_geopolitical, severity_geopolitical,
                               likelihood_piracy, severity_piracy,
                               likelihood_blockage, severity_blockage
                        FROM maritime_chokepoint_risk_indicators
                        WHERE canal = :canal
                    """),
                    {"canal": dependency_canal},
                ).mappings().first()
                top_systemic_risk = [
                    dict(row)
                    for row in conn.execute(text("""
                        SELECT iso3, canal, trade_at_risk_v, trade_impacted, revenue_at_risk, v_share
                        FROM maritime_chokepoint_systemic_risk_results
                        ORDER BY trade_at_risk_v DESC
                        LIMIT 5
                    """)).mappings().all()
                ]
                bab_priority = [
                    dict(row)
                    for row in conn.execute(text("""
                        SELECT iso3, canal, trade_at_risk_v, trade_impacted, revenue_at_risk, v_share
                        FROM maritime_chokepoint_systemic_risk_results
                        WHERE canal = 'Bab el-Mandeb Strait'
                        ORDER BY trade_at_risk_v DESC
                        LIMIT 5
                    """)).mappings().all()
                ]
                bab_hazard = conn.execute(text("""
                    SELECT canal, likelihood_conflict, severity_conflict,
                           likelihood_geopolitical, severity_geopolitical,
                           likelihood_piracy, severity_piracy,
                           likelihood_blockage, severity_blockage
                    FROM maritime_chokepoint_risk_indicators
                    WHERE canal = 'Bab el-Mandeb Strait'
                """)).mappings().first()
            return {
                **fallback,
                "source_mode": "live_source_tables",
                "tables": tables,
                "top_dependency": top_dependency or fallback["top_dependency"],
                "top_systemic_risk": top_systemic_risk or fallback["top_systemic_risk"],
                "bab_el_mandeb_priority": bab_priority or fallback["bab_el_mandeb_priority"],
                "bab_el_mandeb_hazard": dict(bab_hazard) if bab_hazard else fallback["bab_el_mandeb_hazard"],
                "top_dependency_hazard": dict(dependency_hazard) if dependency_hazard else fallback["top_dependency_hazard"],
            }
        except Exception:
            return fallback

    def _maritime_risk_candidate_specs(self, profile, hypotheses):
        dependency = profile["top_dependency"][0]
        dependency_hazard = profile["top_dependency_hazard"]
        systemic = profile["top_systemic_risk"][0]
        bab_priority = profile["bab_el_mandeb_priority"]
        bab_hazard = profile["bab_el_mandeb_hazard"]
        priority_labels = ", ".join(
            f"{row['iso3']} (${float(row['trade_at_risk_v']) / 1_000_000_000:.1f}B at risk)"
            for row in bab_priority[:3]
        )
        evidence_limit = "Draft candidate from maritime-risk graph playbook; requires human review before formal finding approval."
        return [
            {
                "hypothesis_key": hypotheses["Single-chokepoint dependency can create concentrated country exposure"],
                "title": "Single chokepoint dependency creates concentrated country exposure",
                "conclusion": (
                    f"{dependency['iso3']} depends heavily on {dependency['canal']}, where the hazard profile includes "
                    f"conflict likelihood {dependency_hazard.get('likelihood_conflict') or 'n/a'} and geopolitical likelihood "
                    f"{dependency_hazard.get('likelihood_geopolitical') or 'n/a'}: "
                    f"{float(dependency['share']):.1%} of modeled maritime trade value flows through that chokepoint "
                    f"(${float(dependency['v_canal']) / 1_000_000_000:.2f}B of dependent value)."
                ),
                "value_score": 0.82,
                "confidence": 0.78,
                "novelty_score": 0.62,
                "impact_score": 0.8,
                "evidence_chain": [
                    {"kind": "hazard", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "likelihood_conflict", "value": dependency_hazard.get("likelihood_conflict")},
                    {"kind": "hazard", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "likelihood_geopolitical", "value": dependency_hazard.get("likelihood_geopolitical")},
                    {"kind": "chokepoint", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "canal", "value": dependency["canal"]},
                    {"kind": "dependent_country", "source_ref": "maritime_chokepoint_country_dependencies", "metric": "iso3", "value": dependency["iso3"]},
                    {"kind": "trade_metric", "source_ref": "maritime_chokepoint_country_dependencies", "metric": "v_canal", "value": round(float(dependency["v_canal"]), 2)},
                    {"kind": "risk_metric", "source_ref": "maritime_chokepoint_country_dependencies", "metric": "v_canal / v", "value": f"{float(dependency['share']):.1%}"},
                    {"kind": "recommended_action", "source_ref": "maritime_risk_playbook", "metric": "portfolio_review", "value": "Prioritize dependency diversification review for the country/chokepoint pair."},
                ],
                "evidence_limits": [evidence_limit, "This phase uses structural 2022 dependency data and does not include live event updates."],
                "suggested_action": {"next": "Open a country/chokepoint dependency review and compare alternate maritime routes."},
            },
            {
                "hypothesis_key": hypotheses["Hazard severity should be joined to dependent trade value before ranking chokepoints"],
                "title": "Hazard-adjusted chokepoint risk should drive review priority",
                "conclusion": (
                    f"{systemic['canal']} has the highest modeled trade-at-risk row in the current dataset: "
                    f"{systemic['iso3']} shows ${float(systemic['trade_at_risk_v']) / 1_000_000_000:.1f}B expected trade value at risk "
                    f"and ${float(systemic['trade_impacted']) / 1_000_000_000:.1f}B trade impacted."
                ),
                "value_score": 0.88,
                "confidence": 0.8,
                "novelty_score": 0.66,
                "impact_score": 0.87,
                "evidence_chain": [
                    {"kind": "hazard", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "chokepoint", "value": systemic["canal"]},
                    {"kind": "chokepoint", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "canal", "value": systemic["canal"]},
                    {"kind": "dependent_country", "source_ref": "maritime_chokepoint_systemic_risk_results", "metric": "iso3", "value": systemic["iso3"]},
                    {"kind": "risk_metric", "source_ref": "maritime_chokepoint_systemic_risk_results", "metric": "trade_at_risk_v", "value": round(float(systemic["trade_at_risk_v"]), 2)},
                    {"kind": "risk_metric", "source_ref": "maritime_chokepoint_systemic_risk_results", "metric": "trade_impacted", "value": round(float(systemic["trade_impacted"]), 2)},
                    {"kind": "recommended_action", "source_ref": "maritime_risk_playbook", "metric": "risk_review_queue", "value": "Create a priority review queue for countries with high trade_at_risk_v on this chokepoint."},
                ],
                "evidence_limits": [evidence_limit, "Hazard indicators are joined at chokepoint level; country-level risk is modeled through dependency and systemic risk tables."],
                "suggested_action": {"next": "Rank affected countries by trade_at_risk_v and validate current operational exposure."},
            },
            {
                "hypothesis_key": hypotheses["Red Sea / Bab el-Mandeb escalation should prioritize dependent countries by systemic risk"],
                "title": "Bab el-Mandeb risk propagation identifies countries for immediate review",
                "conclusion": (
                    "If Red Sea / Bab el-Mandeb risk rises, the first review queue should include "
                    f"{priority_labels}. The graph path is hazard at Bab el-Mandeb -> chokepoint -> dependent country -> systemic risk metric -> analyst action."
                ),
                "value_score": 0.9,
                "confidence": 0.82,
                "novelty_score": 0.72,
                "impact_score": 0.9,
                "evidence_chain": [
                    {"kind": "hazard", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "likelihood_conflict", "value": bab_hazard.get("likelihood_conflict")},
                    {"kind": "hazard", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "severity_conflict", "value": bab_hazard.get("severity_conflict")},
                    {"kind": "chokepoint", "source_ref": "maritime_chokepoint_risk_indicators", "metric": "canal", "value": "Bab el-Mandeb Strait"},
                    {"kind": "dependent_countries", "source_ref": "maritime_chokepoint_systemic_risk_results", "metric": "top_trade_at_risk_v", "value": [
                        {"iso3": row["iso3"], "trade_at_risk_v": round(float(row["trade_at_risk_v"]), 2), "trade_impacted": round(float(row["trade_impacted"]), 2)}
                        for row in bab_priority[:5]
                    ]},
                    {"kind": "risk_metric", "source_ref": "maritime_chokepoint_systemic_risk_results", "metric": "top_trade_at_risk_v", "value": round(float(bab_priority[0]["trade_at_risk_v"]), 2) if bab_priority else None},
                    {"kind": "recommended_action", "source_ref": "maritime_risk_playbook", "metric": "country_priority_review", "value": "Assign analyst review to top exposed countries and request updated live event enrichment."},
                ],
                "evidence_limits": [evidence_limit, "The playbook uses structural chokepoint risk data; ACLED/GDELT live events are a planned enrichment, not yet imported."],
                "suggested_action": {"next": "Create a Bab el-Mandeb review case for the top exposed countries and attach live event enrichment when available."},
            },
        ]

    def _creditcardfraud_profile(self, tenant):
        fallback = {
            "source_mode": "fallback_reported_profile",
            "total_transactions": 786363,
            "fraud_transactions": 12417,
            "fraud_rate": 0.01579,
            "nonfraud_avg_amount": 135.57,
            "fraud_avg_amount": 225.22,
            "card_not_present_count": 433495,
            "card_not_present_fraud_rate": 0.0207,
            "verification_mismatch_count": 7015,
            "verification_mismatch_fraud_rate": 0.0289,
            "pos_missing_count": 4054,
            "pos_missing_fraud_rate": 0.0664,
            "duplicate_clusters": 12761,
            "high_risk_categories": [
                {"category": "airline", "fraud_rate": 0.0346},
                {"category": "rideshare", "fraud_rate": 0.0249},
                {"category": "online_retail", "fraud_rate": 0.0244},
                {"category": "online_gifts", "fraud_rate": 0.0242},
            ],
            "examples": [
                {"transaction_id": 571924, "risk_signal": "high amount online transaction with fraud label"},
                {"transaction_id": 149886, "risk_signal": "missing POS entry mode and fraud label"},
                {"transaction_id": 391987, "risk_signal": "verification mismatch and fraud label"},
            ],
        }
        try:
            with self.source_engine_for(tenant).connect() as conn:
                base = conn.execute(text("""
                    SELECT
                      COUNT(*) AS total_transactions,
                      SUM(CASE WHEN isFraud THEN 1 ELSE 0 END) AS fraud_transactions,
                      AVG(CASE WHEN isFraud THEN transactionAmount ELSE NULL END) AS fraud_avg_amount,
                      AVG(CASE WHEN NOT isFraud THEN transactionAmount ELSE NULL END) AS nonfraud_avg_amount
                    FROM credit_card_transactions_safe
                """)).mappings().first()
                cnp = conn.execute(text("""
                    SELECT COUNT(*) AS total, SUM(CASE WHEN isFraud THEN 1 ELSE 0 END) AS fraud
                    FROM credit_card_transactions_safe
                    WHERE cardPresent = false
                """)).mappings().first()
                mismatch = conn.execute(text("""
                    SELECT COUNT(*) AS total, SUM(CASE WHEN isFraud THEN 1 ELSE 0 END) AS fraud
                    FROM credit_card_transactions_safe
                    WHERE cvvMatch = false
                """)).mappings().first()
                pos_missing = conn.execute(text("""
                    SELECT COUNT(*) AS total, SUM(CASE WHEN isFraud THEN 1 ELSE 0 END) AS fraud
                    FROM credit_card_transactions_safe
                    WHERE posEntryMode IS NULL OR posEntryMode = ''
                """)).mappings().first()
                categories = conn.execute(text("""
                    SELECT merchantCategoryCode AS category,
                           COUNT(*) AS total,
                           SUM(CASE WHEN isFraud THEN 1 ELSE 0 END) AS fraud
                    FROM credit_card_transactions_safe
                    GROUP BY merchantCategoryCode
                    HAVING COUNT(*) >= 100
                    ORDER BY (SUM(CASE WHEN isFraud THEN 1 ELSE 0 END) / COUNT(*)) DESC
                    LIMIT 4
                """)).mappings().all()
                dup = conn.execute(text("""
                    SELECT COUNT(*) AS clusters FROM (
                      SELECT customerId, merchantName, transactionAmount, DATE(transactionDateTime) AS tx_day
                      FROM credit_card_transactions_safe
                      GROUP BY customerId, merchantName, transactionAmount, DATE(transactionDateTime)
                      HAVING COUNT(*) > 1
                    ) q
                """)).mappings().first()
            total = int(base["total_transactions"] or 0)
            fraud = int(base["fraud_transactions"] or 0)
            if total <= 0:
                return fallback
            return {
                **fallback,
                "source_mode": "live_safe_view",
                "total_transactions": total,
                "fraud_transactions": fraud,
                "fraud_rate": fraud / total,
                "fraud_avg_amount": float(base["fraud_avg_amount"] or fallback["fraud_avg_amount"]),
                "nonfraud_avg_amount": float(base["nonfraud_avg_amount"] or fallback["nonfraud_avg_amount"]),
                "card_not_present_count": int(cnp["total"] or 0),
                "card_not_present_fraud_rate": (int(cnp["fraud"] or 0) / int(cnp["total"] or 1)),
                "verification_mismatch_count": int(mismatch["total"] or 0),
                "verification_mismatch_fraud_rate": (int(mismatch["fraud"] or 0) / int(mismatch["total"] or 1)),
                "pos_missing_count": int(pos_missing["total"] or 0),
                "pos_missing_fraud_rate": (int(pos_missing["fraud"] or 0) / int(pos_missing["total"] or 1)),
                "duplicate_clusters": int(dup["clusters"] or 0),
                "high_risk_categories": [
                    {"category": row["category"], "fraud_rate": int(row["fraud"] or 0) / int(row["total"] or 1)}
                    for row in categories
                ] or fallback["high_risk_categories"],
            }
        except Exception:
            return fallback

    def _creditcardfraud_candidate_specs(self, profile, hypotheses):
        baseline = profile["fraud_rate"]
        categories = profile["high_risk_categories"]
        examples = profile["examples"]
        evidence_limit = "Draft candidate from Autopilot playbook; requires human review before formal finding approval."
        return [
            {
                "hypothesis_key": hypotheses["Card-not-present transactions concentrate fraud risk"],
                "title": "Card-not-present transactions carry elevated fraud risk",
                "conclusion": f"Card-not-present transactions show a fraud rate of {profile['card_not_present_fraud_rate']:.2%} versus the dataset baseline of {baseline:.2%}, making this a high-value triage segment.",
                "value_score": 0.84,
                "confidence": 0.78,
                "novelty_score": 0.58,
                "impact_score": 0.82,
                "evidence_chain": [
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "baseline_fraud_rate", "value": f"{baseline:.2%}"},
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "card_not_present_fraud_rate", "value": f"{profile['card_not_present_fraud_rate']:.2%}"},
                    {"kind": "volume", "source_ref": "credit_card_transactions_safe", "metric": "card_not_present_count", "value": profile["card_not_present_count"]},
                ],
                "evidence_limits": [evidence_limit],
                "suggested_action": {"next": "Break down by merchant category and transaction amount decile."},
            },
            {
                "hypothesis_key": hypotheses["Verification mismatch transactions have elevated fraud rate"],
                "title": "Verification mismatch is a compact fraud-risk signal",
                "conclusion": f"Transactions where the derived verification-match flag is false show a fraud rate of {profile['verification_mismatch_fraud_rate']:.2%}, above the baseline of {baseline:.2%}.",
                "value_score": 0.79,
                "confidence": 0.73,
                "novelty_score": 0.52,
                "impact_score": 0.77,
                "evidence_chain": [
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "verification_mismatch_count", "value": profile["verification_mismatch_count"]},
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "verification_mismatch_fraud_rate", "value": f"{profile['verification_mismatch_fraud_rate']:.2%}"},
                    {"kind": "privacy_boundary", "source_ref": "credit_card_transactions_safe", "metric": "derived_match_flag_only", "value": "raw verification values excluded"},
                ],
                "evidence_limits": [evidence_limit, "Uses a derived match flag only; raw verification values are not surfaced."],
                "suggested_action": {"next": "Prioritize mismatch transactions with high amount or card-not-present channel."},
            },
            {
                "hypothesis_key": hypotheses["Missing POS entry mode may identify a weak-control channel"],
                "title": "Missing POS entry mode should be reviewed as a weak-control pattern",
                "conclusion": f"Transactions with missing POS entry mode show a fraud rate of {profile['pos_missing_fraud_rate']:.2%}, materially above baseline.",
                "value_score": 0.88,
                "confidence": 0.8,
                "novelty_score": 0.66,
                "impact_score": 0.84,
                "evidence_chain": [
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "pos_missing_count", "value": profile["pos_missing_count"]},
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "pos_missing_fraud_rate", "value": f"{profile['pos_missing_fraud_rate']:.2%}"},
                    {"kind": "example", "source_ref": "credit_card_transactions_safe", "metric": "high_risk_transaction_id", "value": examples[1]["transaction_id"]},
                ],
                "evidence_limits": [evidence_limit],
                "suggested_action": {"next": "Review ingestion completeness and POS-mode normalization rules."},
            },
            {
                "hypothesis_key": hypotheses["Merchant categories concentrate fraud exposure"],
                "title": "Merchant category concentration reveals high-yield fraud review segments",
                "conclusion": "The highest-risk merchant categories include " + ", ".join(f"{c['category']} ({c['fraud_rate']:.2%})" for c in categories) + ".",
                "value_score": 0.81,
                "confidence": 0.76,
                "novelty_score": 0.61,
                "impact_score": 0.8,
                "evidence_chain": [
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "top_merchant_categories", "value": [{"category": c["category"], "fraud_rate": f"{c['fraud_rate']:.2%}"} for c in categories]},
                    {"kind": "aggregate", "source_ref": "credit_card_transactions_safe", "metric": "baseline_fraud_rate", "value": f"{baseline:.2%}"},
                ],
                "evidence_limits": [evidence_limit, "Category ranking should be paired with volume and amount thresholds before operational use."],
                "suggested_action": {"next": "Create category-specific review queues for high-rate and high-volume intersections."},
            },
            {
                "hypothesis_key": hypotheses["Same account/merchant/amount/day duplicate clusters indicate multi-swipe risk"],
                "title": "Same-day duplicate transaction clusters need multi-swipe review",
                "conclusion": f"The dataset contains {profile['duplicate_clusters']:,} same customer / same merchant / same amount / same-day duplicate clusters, a useful review entry point for duplicate authorization and multi-swipe behavior.",
                "value_score": 0.77,
                "confidence": 0.7,
                "novelty_score": 0.64,
                "impact_score": 0.72,
                "evidence_chain": [
                    {"kind": "cluster", "source_ref": "credit_card_transactions_safe", "metric": "duplicate_clusters", "value": profile["duplicate_clusters"]},
                    {"kind": "example", "source_ref": "credit_card_transactions_safe", "metric": "high_risk_transaction_id", "value": examples[0]["transaction_id"]},
                ],
                "evidence_limits": [evidence_limit, "Duplicate clusters include benign repeats; candidate requires case-level review."],
                "suggested_action": {"next": "Separate reversals, merchant retries, and high-confidence multi-swipe clusters."},
            },
        ]

    def _autopilot_session_row(self, tenant, session_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text("SELECT * FROM aletheia_autopilot_sessions WHERE project_id = :tenant_id AND session_key = :session_key"),
                {"tenant_id": tenant.tenant_id, "session_key": session_key},
            ).mappings().first()

    def _autopilot_hypothesis_row(self, tenant, hypothesis_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text("SELECT * FROM aletheia_autopilot_hypotheses WHERE project_id = :tenant_id AND hypothesis_key = :hypothesis_key"),
                {"tenant_id": tenant.tenant_id, "hypothesis_key": hypothesis_key},
            ).mappings().first()

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

    ACTIVE_FINDING_STATUSES = {"approved", "reaffirmed"}
    INACTIVE_FINDING_STATUSES = {"rejected", "needs_more_evidence", "needs_changes", "stale", "superseded"}

    def list_findings_overview(self, tenant, limit=50, status=None, context=None):
        conditions = ["f.project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id, "limit": limit}
        if status:
            conditions.append("f.status = :status")
            params["status"] = status
        elif context == "active":
            conditions.append("f.status IN ('approved', 'reaffirmed')")
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT f.id, f.run_id, f.project_id, f.canonical_key, f.title, f.conclusion,
                           f.confidence, f.supporting_evidence_json, f.counter_evidence_json,
                           f.recommended_action_json, f.status, f.version, f.source_agent,
                           f.created_at, f.updated_at,
                           t.canonical_key AS task_key, t.question, t.scope_json,
                           r.run_key, r.status AS run_status, r.created_at AS run_created_at
                    FROM aletheia_reasoning_findings f
                    JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE {where}
                    ORDER BY f.updated_at DESC, f.id DESC
                    LIMIT :limit
                    """
                ),
                params,
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

    def list_findings_registry(self, tenant, status=None, context=None, limit=50, filters=None):
        self.ensure_finding_experience_schema(tenant)
        filters = filters or {}
        findings = self.list_findings_overview(tenant, limit=limit, status=status, context=context)
        action_map = self._finding_action_map(tenant, [finding["canonical_key"] for finding in findings])
        review_map = self._finding_latest_review_map(tenant, [finding["canonical_key"] for finding in findings])
        enriched = []
        for finding in findings:
            decorated = self._decorate_approved_finding(finding)
            decorated["actions"] = action_map.get(finding["canonical_key"], [])
            decorated["action_summary"] = self._finding_action_summary(decorated["actions"])
            decorated["latest_review"] = review_map.get(finding["canonical_key"])
            decorated["finding_type"] = self._finding_type(decorated)
            decorated["source_label"] = self._finding_source_label(decorated)
            decorated["freshness"] = self._finding_freshness(decorated)
            decorated["value_score"] = self._finding_value_score(decorated)
            decorated["evidence_count"] = len(decorated.get("supporting_evidence") or [])
            enriched.append(decorated)
        enriched = self._filter_registry_findings(enriched, filters)
        enriched = self._sort_registry_findings(enriched, filters.get("sort"))
        groups = self._group_registry_findings(enriched, filters.get("group"))
        return {
            "tenant": tenant.public_dict(),
            "context": context or "all",
            "status": status,
            "filters": filters,
            "active_statuses": sorted(self.ACTIVE_FINDING_STATUSES),
            "excluded_from_active": sorted(self.INACTIVE_FINDING_STATUSES),
            "groups": groups,
            "findings": enriched,
        }

    def _finding_action_map(self, tenant, finding_keys):
        if not finding_keys:
            return {}
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                           priority, status, result, result_detail, created_from, canonical_write,
                           graph_write, created_at, updated_at, closed_at
                    FROM aletheia_finding_actions
                    WHERE project_id = :tenant_id AND finding_key = ANY(:finding_keys)
                    ORDER BY updated_at DESC, id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "finding_keys": finding_keys},
            ).mappings().all()
        result = {}
        for row in rows:
            action = self._finding_action_to_dict(row)
            result.setdefault(action["finding_key"], []).append(action)
        return result

    def _finding_latest_review_map(self, tenant, finding_keys):
        if not finding_keys:
            return {}
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (canonical_key)
                           canonical_key, decision, reviewer, reason, before_status, after_status,
                           before_version, after_version, created_at
                    FROM aletheia_reasoning_reviews
                    WHERE project_id = :tenant_id AND canonical_key = ANY(:finding_keys)
                    ORDER BY canonical_key, created_at DESC, id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "finding_keys": finding_keys},
            ).mappings().all()
        return {row["canonical_key"]: self._review_to_dict(row) for row in rows}

    def _finding_action_summary(self, actions):
        if not actions:
            return {"state": "no_action", "count": 0, "open_count": 0, "closed_count": 0}
        open_actions = [a for a in actions if a["status"] not in {"closed"}]
        closed_actions = [a for a in actions if a["status"] == "closed"]
        overdue = [a for a in open_actions if a.get("is_overdue")]
        primary = overdue[0] if overdue else open_actions[0] if open_actions else closed_actions[0]
        state = "overdue_action" if overdue else "open_action" if open_actions else "closed_action"
        return {
            "state": state,
            "count": len(actions),
            "open_count": len(open_actions),
            "closed_count": len(closed_actions),
            "primary": primary,
        }

    def _finding_type(self, finding):
        action = finding.get("recommended_action") or {}
        explicit = action.get("finding_type") or action.get("type")
        if explicit in {"risk_pattern", "operational_anomaly", "quality_issue", "ontology_conflict", "investigation_prompt"}:
            return explicit
        text_value = " ".join([finding.get("title") or "", finding.get("conclusion") or ""]).lower()
        if any(word in text_value for word in ("fraud", "risk", "mismatch", "card-not-present")):
            return "risk_pattern"
        if any(word in text_value for word in ("anomaly", "unusual", "abnormal", "duplicate")):
            return "operational_anomaly"
        if any(word in text_value for word in ("missing", "quality", "degraded", "weak-control")):
            return "quality_issue"
        if any(word in text_value for word in ("ontology", "schema", "canonical", "graph")):
            return "ontology_conflict"
        return "investigation_prompt"

    def _finding_source_label(self, finding):
        action = finding.get("recommended_action") or {}
        source_agent = finding.get("source_agent") or ""
        if action.get("source_candidate_key") or "Autopilot" in source_agent:
            return "Autopilot"
        if source_agent == "FindingApprovalReviewGate":
            return "Autopilot"
        if "Manual" in source_agent:
            return "Manual review"
        return "Reasoning"

    def _finding_freshness(self, finding):
        status_value = finding.get("status")
        latest = finding.get("latest_review") or {}
        decision = latest.get("decision")
        if status_value == "stale":
            return "stale"
        if status_value == "superseded":
            return "superseded"
        if decision == "reaffirmed":
            return "reaffirmed_recently"
        if status_value in self.ACTIVE_FINDING_STATUSES:
            return "due_for_revalidation"
        return "audit_only"

    def _finding_value_score(self, finding):
        action = finding.get("recommended_action") or {}
        for key in ("value_score", "impact_score", "confidence"):
            if action.get(key) is not None:
                try:
                    return float(action.get(key))
                except (TypeError, ValueError):
                    pass
        try:
            return float(finding.get("confidence") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _filter_registry_findings(self, findings, filters):
        def keep(finding):
            if filters.get("finding_type") and finding.get("finding_type") != filters["finding_type"]:
                return False
            if filters.get("source") and finding.get("source_label") != filters["source"]:
                return False
            if filters.get("action_state") and finding.get("action_summary", {}).get("state") != filters["action_state"]:
                return False
            if filters.get("freshness") and finding.get("freshness") != filters["freshness"]:
                return False
            min_conf = filters.get("min_confidence")
            max_conf = filters.get("max_confidence")
            confidence = float(finding.get("confidence") or 0)
            if min_conf is not None and confidence < float(min_conf):
                return False
            if max_conf is not None and confidence > float(max_conf):
                return False
            min_value = filters.get("min_value")
            max_value = filters.get("max_value")
            value = float(finding.get("value_score") or 0)
            if min_value is not None and value < float(min_value):
                return False
            if max_value is not None and value > float(max_value):
                return False
            return True
        return [finding for finding in findings if keep(finding)]

    def _sort_registry_findings(self, findings, sort_key):
        sort_key = sort_key or "newest_reviewed"
        if sort_key == "value_desc":
            return sorted(findings, key=lambda f: float(f.get("value_score") or 0), reverse=True)
        if sort_key == "oldest_unrevalidated":
            return sorted(findings, key=lambda f: f.get("latest_review", {}).get("created_at") or f.get("updated_at") or "")
        if sort_key == "action_due_asc":
            return sorted(findings, key=lambda f: (f.get("action_summary", {}).get("primary") or {}).get("due_at") or "9999-12-31")
        if sort_key == "confidence_desc":
            return sorted(findings, key=lambda f: float(f.get("confidence") or 0), reverse=True)
        return sorted(findings, key=lambda f: (f.get("latest_review", {}) or {}).get("created_at") or f.get("updated_at") or "", reverse=True)

    def _group_registry_findings(self, findings, group_key):
        if not group_key:
            return []
        key_map = {
            "tenant": lambda f: f.get("tenant_id"),
            "status": lambda f: f.get("status"),
            "finding_type": lambda f: f.get("finding_type"),
            "action_state": lambda f: f.get("action_summary", {}).get("state"),
            "source": lambda f: f.get("source_label"),
        }
        fn = key_map.get(group_key)
        if not fn:
            return []
        counts = {}
        for finding in findings:
            key = fn(finding) or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return [{"group": key, "count": value} for key, value in sorted(counts.items())]

    def _decorate_approved_finding(self, finding):
        if finding.get("status") in self.ACTIVE_FINDING_STATUSES:
            finding = dict(finding)
            action = finding.get("recommended_action") or {}
            finding["context_label"] = "prior_finding"
            finding["reasoning_use"] = {
                "kind": "prior_finding",
                "label": "reviewed_inference",
                "source_ref": finding.get("canonical_key"),
                "allowed_context": "active_reasoning_context",
                "canonical_write": False,
                "graph_write": False,
                "auto_business_action": False,
            }
            finding["workspace_next_action"] = action.get("workspace_next_action") or action.get("next_action") or action.get("next")
            finding["change_proposal_bridge"] = action.get("change_proposal_bridge") or {
                "available": True,
                "writes_canonical": False,
                "requires_review_gate": True,
            }
        return finding

    def active_prior_findings(self, tenant, limit=5):
        findings = self.list_findings_overview(tenant, limit=limit, context="active")
        prior = []
        for finding in findings:
            prior.append({
                "kind": "prior_finding",
                "label": "reviewed_inference",
                "summary": finding.get("conclusion") or finding.get("title"),
                "source_ref": finding.get("canonical_key"),
                "confidence": finding.get("confidence"),
                "payload": {
                    "finding_key": finding.get("canonical_key"),
                    "status": finding.get("status"),
                    "title": finding.get("title"),
                    "reviewed_inference": True,
                    "canonical_write": False,
                    "graph_write": False,
                },
            })
        return prior

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
        center_node = scope.get("center_node") or payload.get("center_node")
        depth = int(scope.get("depth") or payload.get("depth") or 1)
        limit = int(scope.get("limit") or payload.get("limit") or 200)
        if not center_node:
            types = self.instance_repository.types(tenant).get("types") or []
            if not types:
                raise ValueError(f"No approved object types are available for tenant {tenant.tenant_id}")
            first_type = types[0]["type"]
            instances = self.instance_repository.search(tenant, first_type, "", limit=1).get("instances") or []
            if not instances:
                raise ValueError(f"No source instances are available for tenant {tenant.tenant_id} type {first_type}")
            center_node = instances[0]["id"]
        if ":" not in center_node:
            raise ValueError("center_node must be like ObjectType:ID")
        object_type, instance_id = center_node.split(":", 1)
        tenant_types = self.instance_repository.types(tenant).get("types") or []
        allowed_types = {str(t.get("type") or "") for t in tenant_types}
        if allowed_types and object_type not in allowed_types:
            raise ValueError(f"center_node {center_node} is not an approved object type for tenant {tenant.tenant_id}")
        graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
        if not graph or not graph.get("approved"):
            raise ValueError(f"center_node {center_node} is outside the approved graph scope (node not found or not approved)")
        graph_type = graph.get("scope", {}).get("type") or object_type
        graph_url = (
            scope.get("graph_url")
            or graph.get("graph_url")
            or f"/graph.html?tenant={quote(tenant.tenant_id)}&type={quote(graph_type)}&id={quote(str(instance_id))}&depth={depth}&limit={limit}"
        )
        inner_scope = {
            "source": "question_center",
            "center_node": center_node,
            "depth": depth,
            "node_limit": limit,
            "edge_limit": limit,
            "allowed_node_types": graph.get("scope", {}).get("allowed_node_types") or [graph_type],
            "allowed_link_keys": graph.get("scope", {}).get("allowed_link_keys") or [],
            "approved_only": True,
            "evidence_paths": [
                {
                    "kind": "question_scope",
                    "label": center_node,
                    "summary": f"Question Center scoped task for: {question}",
                    "url": graph_url,
                    "source_ref": "question_center",
                    "payload": {"scope": scope.get("type") or "tenant", "center_node": center_node},
                }
            ],
        }
        if scope.get("nonce"):
            inner_scope["nonce"] = scope["nonce"]
        inner_scope["question"] = question
        return self.create_scoped_task_from_graph(
            tenant,
            {
                "question": question,
                "source": "question_center",
                "graph_url": graph_url,
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
        graph_scope = {}
        if center_node:
            if ":" not in center_node:
                raise ValueError("center_node must be like Employee:4")
            object_type, instance_id = center_node.split(":", 1)
            graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=node_limit)
            if not graph or not graph.get("approved"):
                raise ValueError(f"center_node {center_node} is outside the approved graph scope (node not found or not approved)")
            graph_scope = (graph or {}).get("scope") or {}
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
            "allowed_node_types": scope.get("allowed_node_types") if scope.get("allowed_node_types") is not None else (graph_scope.get("allowed_node_types") or ["Employee", "Order"]),
            "allowed_link_keys": scope.get("allowed_link_keys") if scope.get("allowed_link_keys") is not None else (graph_scope.get("allowed_link_keys") or ["link:employee:1:n:order"]),
            "approved_only": True,
            "evidence_paths": evidence_paths,
            "review_gate": "draft_only",
            "graph_url": payload.get("graph_url"),
            "question": question,
        }
        prior_findings = self.active_prior_findings(tenant, limit=5)
        if prior_findings:
            task_scope["prior_findings"] = prior_findings
            task_scope["evidence_paths"] = [*evidence_paths, *prior_findings]
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
        scope_depth = int(scope.get("depth") or 1)
        scope_limit = int(scope.get("node_limit") or 200)
        engine = ReasoningEngine(self.instance_repository)
        structured_answer = engine.analyze(tenant, center_node, task.get("question"), depth=scope_depth, limit=scope_limit)
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
            source_key_profile = metrics.get("source_key_profile") or {}
            label = metrics.get("label") or center_node
            if source_key_profile.get("related_tables"):
                top_paths = source_key_profile.get("top_paths") or []
                path_summary = ", ".join(f"{p['label']} ({p['metric']} {_fmt_number(p['metric_value'])})" for p in top_paths[:3]) or "no ranked paths"
                ranking_summary = f"{source_key_profile.get('total_key_rows', 0)} source rows; top paths: {path_summary}"
                second_hop_paths = source_key_profile.get("second_hop_paths") or []
                if second_hop_paths:
                    shared_summary = "; ".join(
                        f"{p['label']} -> {', '.join(peer['key'] for peer in p.get('top_peers', [])[:4])}"
                        for p in second_hop_paths[:3]
                    )
                    ranking_summary += f"; depth-{source_key_profile.get('scope_depth', scope_depth)} shared paths: {shared_summary}"
                source_tables = [str(t.get("table") or "") for t in source_key_profile.get("related_tables", [])]
                profile_label = "Maritime Exposure Profile" if any(table.startswith("maritime_") for table in source_tables) else "Source Evidence Profile"
                aggregate_label = f"{label} {profile_label}"
                aggregate_source_ref = f"{metrics.get('object_type', 'entity')} + degree + source-key metric aggregation"
            else:
                ranking_summary = "; ".join(
                    f"{r['my_count']} {r['target_type']}(s) (#{r['rank']}/{r['total_peers']}, {r['level']})"
                    for r in rankings if r.get("my_count", 0) > 0
                ) or "no ranked relationships"
                aggregate_label = f"{label} Business Profile"
                aggregate_source_ref = f"{metrics.get('object_type', 'entity')} + peer ranking + value aggregation"
            evidence_paths.append({
                "kind": "controlled_aggregate",
                "label": aggregate_label,
                "summary": f"{label}: {ranking_summary}",
                "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={quote(task_key)}",
                "source_ref": aggregate_source_ref,
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
            "counter_evidence": [{"kind": "scope_limit", "summary": ("Conclusions are based solely on the approved graph and controlled aggregation; external benchmarks, thresholds, and unapproved evidence are not included." if structured_answer else "The task cannot expand beyond the selected approved graph scope without a new bounded graph request.")}],
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
        scope_depth = int(scope.get("depth") or 1)
        scope_limit = int(scope.get("node_limit") or 200)
        engine = ReasoningEngine(self.instance_repository)
        structured_answer = engine.analyze(tenant, center_node, task.get("question"), depth=scope_depth, limit=scope_limit)
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
            source_key_profile = metrics.get("source_key_profile") or {}
            label = metrics.get("label") or center_node
            if source_key_profile.get("related_tables"):
                top_paths = source_key_profile.get("top_paths") or []
                path_summary = ", ".join(f"{p['label']} ({p['metric']} {_fmt_number(p['metric_value'])})" for p in top_paths[:3]) or "no ranked paths"
                ranking_summary = f"{source_key_profile.get('total_key_rows', 0)} source rows; top paths: {path_summary}"
                second_hop_paths = source_key_profile.get("second_hop_paths") or []
                if second_hop_paths:
                    shared_summary = "; ".join(
                        f"{p['label']} -> {', '.join(peer['key'] for peer in p.get('top_peers', [])[:4])}"
                        for p in second_hop_paths[:3]
                    )
                    ranking_summary += f"; depth-{source_key_profile.get('scope_depth', scope_depth)} shared paths: {shared_summary}"
                source_tables = [str(t.get("table") or "") for t in source_key_profile.get("related_tables", [])]
                profile_label = "Maritime Exposure Profile" if any(table.startswith("maritime_") for table in source_tables) else "Source Evidence Profile"
                aggregate_label = f"{label} {profile_label}"
                aggregate_source_ref = f"{metrics.get('object_type', 'entity')} + degree + source-key metric aggregation"
            else:
                ranking_summary = "; ".join(
                    f"{r['my_count']} {r['target_type']}(s) (#{r['rank']}/{r['total_peers']}, {r['level']})"
                    for r in rankings if r.get("my_count", 0) > 0
                ) or "no ranked relationships"
                aggregate_label = f"{label} Business Profile"
                aggregate_source_ref = f"{metrics.get('object_type', 'entity')} + peer ranking + value aggregation"
            evidence_paths.append(
                {
                    "kind": "controlled_aggregate",
                    "label": aggregate_label,
                    "summary": f"{label}: {ranking_summary}",
                    "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={quote(task_key)}",
                    "source_ref": aggregate_source_ref,
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
                        "Conclusions are based solely on the approved graph and controlled aggregation; external benchmarks, thresholds, and unapproved evidence are not included."
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
            structured_answer = engine.analyze(
                tenant,
                center_node,
                task.get("question"),
                depth=int(scope.get("depth") or 1),
                limit=int(scope.get("node_limit") or 200),
            )
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
        status_aliases = {
            "needs_changes": "needs_more_evidence",
            "needs-evidence": "needs_more_evidence",
            "needs-more-evidence": "needs_more_evidence",
            "reject": "rejected",
            "approve": "approved",
            "mark-stale": "stale",
            "supersede": "superseded",
            "reaffirm": "reaffirmed",
        }
        decision = status_aliases.get(status, status)
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
            after_version = before_version if decision == "comment" else before_version + 1
            after_status = before_status if decision == "comment" else "approved" if decision == "reaffirmed" else decision
            if decision != "comment":
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_reasoning_findings
                        SET status = :status, version = version + 1, updated_at = NOW()
                        WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key, "status": after_status},
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
                    "decision": decision,
                    "reviewer": reviewer,
                    "reason": reason,
                    "before_status": before_status,
                    "after_status": after_status,
                    "before_version": before_version,
                    "after_version": after_version,
                },
            )
            if after_status in ("approved", "rejected"):
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

    def finding_workspace_action(self, tenant, canonical_key, payload=None):
        self.ensure_finding_experience_schema(tenant)
        payload = payload or {}
        finding = self.get_finding(tenant, canonical_key)
        if not finding:
            raise KeyError(canonical_key)
        if finding.get("status") not in self.ACTIVE_FINDING_STATUSES:
            raise ValueError("workspace action can only be created from active approved/reaffirmed findings")
        recommended = finding.get("recommended_action") or {}
        action = recommended.get("workspace_next_action") or {
            "type": "case_next_action",
            "label": "Review approved finding and assign owner",
            "status": "ready_for_dispatch",
            "writes_canonical": False,
        }
        title = payload.get("title") or action.get("label") or action.get("title") or "Review approved finding"
        action_type = payload.get("action_type") or action.get("action_type") or "investigate"
        priority = payload.get("priority") or action.get("priority") or "medium"
        owner = payload.get("owner") or action.get("owner")
        due_at = payload.get("due_at") or action.get("due_at")
        action_key = payload.get("action_key") or f"action:{_slug(canonical_key)}:{_slug(title)}"
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_finding_actions
                    (project_id, action_key, finding_key, title, action_type, owner, due_at,
                     priority, status, created_from, canonical_write, graph_write, created_at, updated_at)
                    VALUES
                    (:tenant_id, :action_key, :finding_key, :title, :action_type, :owner,
                     CAST(:due_at AS TIMESTAMP), :priority, 'open', 'approved_finding', FALSE, FALSE, NOW(), NOW())
                    ON CONFLICT (project_id, action_key) DO UPDATE SET
                      title = EXCLUDED.title,
                      action_type = EXCLUDED.action_type,
                      owner = EXCLUDED.owner,
                      due_at = EXCLUDED.due_at,
                      priority = EXCLUDED.priority,
                      updated_at = NOW()
                    RETURNING id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                              priority, status, result, result_detail, created_from, canonical_write,
                              graph_write, created_at, updated_at, closed_at
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "action_key": action_key,
                    "finding_key": canonical_key,
                    "title": title,
                    "action_type": action_type,
                    "owner": owner,
                    "due_at": due_at,
                    "priority": priority,
                },
            ).mappings().first()
            self._append_finding_usage_review(
                conn,
                tenant,
                canonical_key,
                decision="action_created",
                reviewer=payload.get("reviewer") or "Itachi",
                reason=f"Workspace action created: {title}",
            )
        return {
            "tenant": tenant.public_dict(),
            "finding_key": canonical_key,
            "workspace_next_action": self._finding_action_to_dict(row),
        }

    def update_finding_action(self, tenant, action_key, action, payload=None):
        self.ensure_finding_experience_schema(tenant)
        payload = payload or {}
        valid_transitions = {
            "start": {"open": "in_progress", "reopened": "in_progress", "blocked": "in_progress"},
            "block": {"open": "blocked", "in_progress": "blocked"},
            "close": {"in_progress": "closed"},
            "reopen": {"closed": "reopened"},
            "update": {},
        }
        close_results = {"confirmed_risk", "false_positive", "evidence_added", "proposal_created", "no_action_needed", "rerun_scheduled"}
        with self.metadata_engine_for(tenant).begin() as conn:
            current = conn.execute(
                text(
                    """
                    SELECT id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                           priority, status, result, result_detail, created_from, canonical_write,
                           graph_write, created_at, updated_at, closed_at
                    FROM aletheia_finding_actions
                    WHERE project_id = :tenant_id AND action_key = :action_key
                    FOR UPDATE
                    """
                ),
                {"tenant_id": tenant.tenant_id, "action_key": action_key},
            ).mappings().first()
            if not current:
                raise KeyError(action_key)
            before_status = current["status"]
            new_status = before_status
            result = payload.get("result") if "result" in payload else current["result"]
            result_detail = payload.get("result_detail") if "result_detail" in payload else current["result_detail"]
            if action == "update":
                pass
            else:
                transition = valid_transitions.get(action)
                if transition is None or before_status not in transition:
                    raise ValueError(f"Invalid action transition: {before_status} -> {action}")
                new_status = transition[before_status]
            if new_status == "closed":
                if not result:
                    raise ValueError("closing an action requires result")
                if result not in close_results:
                    raise ValueError(f"invalid close result: {result}")
            closed_at_expr = "NOW()" if new_status == "closed" else "NULL" if action == "reopen" else "closed_at"
            row = conn.execute(
                text(
                    f"""
                    UPDATE aletheia_finding_actions
                    SET title = COALESCE(:title, title),
                        action_type = COALESCE(:action_type, action_type),
                        owner = COALESCE(:owner, owner),
                        due_at = COALESCE(CAST(:due_at AS TIMESTAMP), due_at),
                        priority = COALESCE(:priority, priority),
                        status = :status,
                        result = :result,
                        result_detail = :result_detail,
                        closed_at = {closed_at_expr},
                        updated_at = NOW()
                    WHERE project_id = :tenant_id AND action_key = :action_key
                    RETURNING id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                              priority, status, result, result_detail, created_from, canonical_write,
                              graph_write, created_at, updated_at, closed_at
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "action_key": action_key,
                    "title": payload.get("title"),
                    "action_type": payload.get("action_type"),
                    "owner": payload.get("owner"),
                    "due_at": payload.get("due_at"),
                    "priority": payload.get("priority"),
                    "status": new_status,
                    "result": result,
                    "result_detail": result_detail,
                },
            ).mappings().first()
            decision = f"action_{action}"
            reason = payload.get("reason") or f"Workspace action {action}: {action_key}"
            self._append_finding_usage_review(
                conn,
                tenant,
                current["finding_key"],
                decision=decision,
                reviewer=payload.get("reviewer") or "Itachi",
                reason=reason,
            )
        return {
            "tenant": tenant.public_dict(),
            "workspace_next_action": self._finding_action_to_dict(row),
            "finding_status_unchanged": True,
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _append_finding_usage_review(self, conn, tenant, canonical_key, decision, reviewer, reason):
        finding = conn.execute(
            text(
                """
                SELECT id, status, version
                FROM aletheia_reasoning_findings
                WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                """
            ),
            {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
        ).mappings().first()
        if not finding:
            raise KeyError(canonical_key)
        conn.execute(
            text(
                """
                INSERT INTO aletheia_reasoning_reviews
                (finding_id, project_id, canonical_key, decision, reviewer, reason,
                 before_status, after_status, before_version, after_version, created_at)
                VALUES
                (:finding_id, :project_id, :canonical_key, :decision, :reviewer, :reason,
                 :status, :status, :version, :version, NOW())
                """
            ),
            {
                "finding_id": finding["id"],
                "project_id": tenant.tenant_id,
                "canonical_key": canonical_key,
                "decision": decision,
                "reviewer": reviewer,
                "reason": reason,
                "status": finding["status"],
                "version": finding["version"],
            },
        )

    def finding_change_proposal(self, tenant, canonical_key, payload=None):
        finding = self.get_finding(tenant, canonical_key)
        if not finding:
            raise KeyError(canonical_key)
        if finding.get("status") not in self.ACTIVE_FINDING_STATUSES:
            raise ValueError("change proposal can only be drafted from active approved/reaffirmed findings")
        proposal_type = (payload or {}).get("proposal_type") or "ontology_rule"
        return {
            "tenant": tenant.public_dict(),
            "finding_key": canonical_key,
            "proposal": {
                "proposal_key": f"proposal:{proposal_type}:{_slug(canonical_key)}",
                "proposal_type": proposal_type,
                "status": "proposal_draft",
                "source_finding_key": canonical_key,
                "summary": finding.get("conclusion"),
                "writes_canonical": False,
                "requires_governance_review": True,
                "boundary": self._finding_canonical_boundary(),
            },
        }

    def finding_revalidation_queue(self, tenant, status=None, limit=50):
        self.ensure_finding_experience_schema(tenant)
        findings = self.list_findings_registry(tenant, context=None, limit=limit, filters={"sort": "oldest_unrevalidated"}).get("findings", [])
        queue = []
        for finding in findings:
            if status and finding.get("status") != status:
                continue
            if finding.get("status") not in {"approved", "reaffirmed", "stale", "superseded"}:
                continue
            latest = finding.get("latest_review") or {}
            actions = finding.get("actions") or []
            reason = "aging_threshold"
            if finding.get("status") == "stale":
                reason = "already_stale"
            elif finding.get("status") == "superseded":
                reason = "superseded_audit"
            elif any(action.get("is_overdue") for action in actions):
                reason = "action_overdue"
            elif latest.get("decision") == "reaffirmed":
                reason = "reaffirmed_recently"
            queue.append({
                "finding_key": finding["canonical_key"],
                "title": finding["title"],
                "status": finding["status"],
                "reason": reason,
                "last_review": latest,
                "last_reaffirmed_at": latest.get("created_at") if latest.get("decision") == "reaffirmed" else None,
                "action_summary": finding.get("action_summary"),
                "affected_downstream": {
                    "actions": len(actions),
                    "reasoning_context": finding.get("status") in self.ACTIVE_FINDING_STATUSES,
                },
                "suggested_batch_operation": "reaffirm" if finding.get("status") in self.ACTIVE_FINDING_STATUSES else "mark_stale",
                "canonical_write": False,
                "graph_write": False,
            })
        return {"tenant": tenant.public_dict(), "queue": queue[:limit], "canonical_boundary": self._finding_canonical_boundary()}

    def batch_revalidate_findings(self, tenant, payload):
        self.ensure_finding_experience_schema(tenant)
        keys = payload.get("finding_keys") or []
        action = payload.get("action") or "reaffirm"
        reviewer = payload.get("reviewer") or "Itachi"
        reason = payload.get("reason") or f"batch revalidation: {action}"
        owner = payload.get("owner")
        if not keys:
            raise ValueError("finding_keys is required")
        if action not in {"reaffirm", "mark_stale", "assign_owner"}:
            raise ValueError("batch action must be reaffirm, mark_stale, or assign_owner")
        results = []
        for key in keys:
            if action == "reaffirm":
                finding = self.review_finding(tenant, key, "reaffirmed", reviewer, reason)
                results.append({"finding_key": key, "status": finding.get("status"), "decision": "reaffirmed"})
            elif action == "mark_stale":
                finding = self.review_finding(tenant, key, "stale", reviewer, reason)
                results.append({"finding_key": key, "status": finding.get("status"), "decision": "stale"})
            else:
                if not owner:
                    raise ValueError("owner is required for assign_owner")
                action_result = self.finding_workspace_action(
                    tenant,
                    key,
                    {
                        "title": "Revalidate approved finding",
                        "action_type": "rerun_autopilot",
                        "owner": owner,
                        "priority": payload.get("priority") or "medium",
                        "due_at": payload.get("due_at"),
                        "reviewer": reviewer,
                    },
                )
                results.append({"finding_key": key, "decision": "assign_owner", "workspace_next_action": action_result["workspace_next_action"]})
        return {
            "tenant": tenant.public_dict(),
            "action": action,
            "results": results,
            "canonical_boundary": self._finding_canonical_boundary(),
        }

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
        prompt_version = (
            "graph-scope-reasoning-v1"
            if str(task.get("canonical_key") or "").startswith("reasoning:graph-scope:")
            else "northwind-workload-v1"
        )
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_runs
                    (task_id, project_id, run_key, agent_name, prompt_version,
                     query_plan_json, tool_calls_json, evidence_paths_json,
                    output_json, eval_result_json, status, latency_ms, cost_estimate, created_at)
                    VALUES
                    (:task_id, :tenant_id, :run_key, 'ReasoningWorkbenchAgent', :prompt_version,
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
                    "prompt_version": prompt_version,
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
        supporting_evidence = _load_json(row["supporting_evidence_json"], [])
        deep_graph_profile = recommended_action.get("deep_graph_profile") or self._deep_graph_profile(supporting_evidence)
        finding = {
            "id": row["id"],
            "run_id": row["run_id"],
            "tenant_id": row["project_id"],
            "canonical_key": row["canonical_key"],
            "title": row["title"],
            "conclusion": row["conclusion"],
            "confidence": row["confidence"],
            "supporting_evidence": supporting_evidence,
            "deep_graph_profile": deep_graph_profile,
            "finding_emphasis": recommended_action.get("finding_emphasis") or deep_graph_profile.get("finding_emphasis"),
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

    def _autopilot_session_to_dict(self, row):
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "session_key": row["session_key"],
            "objective": row["objective"],
            "scope": _load_json(row["scope_json"], {}),
            "budget": _load_json(row["budget_json"], {}),
            "safety_profile": _load_json(row["safety_profile_json"], {}),
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }

    def _autopilot_hypothesis_to_dict(self, row):
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "tenant_id": row["project_id"],
            "hypothesis_key": row["hypothesis_key"],
            "title": row["title"],
            "rationale": row["rationale"],
            "status": row["status"],
            "priority": row["priority"],
            "evidence_plan": _load_json(row["evidence_plan_json"], []),
            "reasoning_task_keys": _load_json(row["reasoning_task_keys_json"], []),
            "pruned_reason": row["pruned_reason"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }

    def _autopilot_candidate_to_dict(self, row):
        evidence_chain = _load_json(row["evidence_chain_json"], [])
        deep_graph_profile = self._deep_graph_profile(evidence_chain)
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "hypothesis_id": row["hypothesis_id"],
            "tenant_id": row["project_id"],
            "canonical_key": row["canonical_key"],
            "title": row["title"],
            "conclusion": row["conclusion"],
            "value_score": row["value_score"],
            "confidence": row["confidence"],
            "novelty_score": row["novelty_score"],
            "impact_score": row["impact_score"],
            "evidence_chain": evidence_chain,
            "deep_graph_profile": deep_graph_profile,
            "finding_emphasis": deep_graph_profile["finding_emphasis"],
            "evidence_limits": _load_json(row["evidence_limits_json"], []),
            "suggested_action": _load_json(row["suggested_action_json"], {}),
            "status": row["status"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }


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
        if parsed.path == "/api/ontology/catalog":
            filters = {key: values[0] for key, values in parse_qs(parsed.query).items() if values and values[0]}
            filters.pop("tenant", None)
            if "kind" in filters and "artifact_type" not in filters:
                filters["artifact_type"] = filters.pop("kind")
            if "q" in filters and "search" not in filters:
                filters["search"] = filters.pop("q")
            self._send_json(self.repository.list_artifacts(tenant, filters))
            return
        if parsed.path == "/api/web-enrichment/proposals":
            query = parse_qs(parsed.query)
            artifact_key = query.get("artifact", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                self._send_json(self.repository.list_web_enrichment(tenant, artifact_key, limit=limit))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path.startswith("/api/ontology/"):
            canonical_key = unquote(parsed.path.removeprefix("/api/ontology/"))
            artifact = self.repository.get_artifact(tenant, canonical_key)
            if artifact is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Ontology artifact not found: {canonical_key}")
                return
            self._send_json(
                {
                    "tenant": tenant.public_dict(),
                    "artifact": artifact,
                    "definition": artifact.get("payload", {}),
                    "source_schema": artifact.get("source_schema", {}),
                    "evidence": artifact.get("evidence", []),
                    "reviews": artifact.get("reviews", []),
                    "canonical": artifact.get("canonical", {}),
                    "used_by": artifact.get("used_by", []),
                    "issues": [],
                }
            )
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
        if parsed.path == "/api/reasoning/autopilot/sessions":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                result = self.reasoning_repository.list_autopilot_sessions(tenant, status=status_filter, limit=limit)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/sessions/"):
            session_key = unquote(parsed.path.removeprefix("/api/reasoning/autopilot/sessions/").rstrip("/"))
            result = self.reasoning_repository.get_autopilot_session(tenant, session_key)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/tasks":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            self._send_json(self.reasoning_repository.list_tasks(tenant, status_filter=status_filter))
            return
        if parsed.path == "/api/reasoning/findings":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            context = query.get("context", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                filters = {
                    "finding_type": query.get("finding_type", [None])[0],
                    "source": query.get("source", [None])[0],
                    "action_state": query.get("action_state", [None])[0],
                    "freshness": query.get("freshness", [None])[0],
                    "sort": query.get("sort", [None])[0],
                    "group": query.get("group", [None])[0],
                }
                for key in ("min_confidence", "max_confidence", "min_value", "max_value"):
                    if query.get(key, [None])[0] not in (None, ""):
                        filters[key] = float(query.get(key, [None])[0])
                filters = {key: value for key, value in filters.items() if value not in (None, "")}
                self._send_json(self.reasoning_repository.list_findings_registry(
                    tenant,
                    status=status_filter,
                    context=context,
                    limit=limit,
                    filters=filters,
                ))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/reasoning/findings/revalidation-queue":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                self._send_json(self.reasoning_repository.finding_revalidation_queue(tenant, status=status_filter, limit=limit))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
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
            depth = int(query.get("depth", ["1"])[0])
            limit = int(query.get("limit", ["200"])[0])
            view = query.get("view", ["scope"])[0]
            object_type = (query.get("type", [""])[0] or "").strip()
            instance_id = (query.get("id", [""])[0] or "").strip()
            if view != "all":
                object_type = object_type or "Employee"
                instance_id = instance_id or "4"
            graph = (
                self.instance_repository.full_graph(tenant, object_type, instance_id, limit=limit)
                if view == "all"
                else self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
            )
            if graph is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph context not found")
                return
            if graph.get("approved"):
                if view == "all":
                    graph["graph_url"] = f"/graph.html?tenant={quote(tenant.tenant_id)}&view=all&limit={graph.get('limit', limit)}"
                else:
                    graph["graph_url"] = (
                        f"/graph.html?tenant={quote(tenant.tenant_id)}&type={quote(object_type)}"
                        f"&id={quote(str(instance_id))}&depth={graph.get('depth', depth)}&limit={graph.get('limit', limit)}"
                    )
            self._send_json(graph)
            return
        if parsed.path == "/api/graph/proposed-elements":
            query = parse_qs(parsed.query)
            run_key = query.get("run_key", [""])[0] or None
            limit = int(query.get("limit", ["50"])[0])
            status_filter = query.get("status", ["pending"])[0]
            try:
                self._send_json(self.instance_repository.proposed_graph_elements(tenant, run_key=run_key, limit=limit, status_filter=status_filter))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/agent-runs/console":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["20"])[0])
            self._send_json(self.instance_repository.agent_runs_console(tenant, limit=limit))
            return
        if parsed.path == "/api/enrichment/sessions":
            self._send_json(self.instance_repository.continuous_enrichment_sessions(tenant))
            return
        if parsed.path.startswith("/api/enrichment/sessions/"):
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").rstrip("/"))
            if "/" in session_key:
                session_key = session_key.split("/", 1)[0]
            result = self.instance_repository.continuous_enrichment_session(tenant, session_key)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
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
            query = parse_qs(parsed.query)
            include_draft = query.get("include_draft", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self.instance_repository.types(tenant, include_draft=include_draft))
            return
        if parsed.path == "/api/instances/search":
            query = parse_qs(parsed.query)
            object_type = query.get("type", ["Employee"])[0]
            search = query.get("q", [""])[0]
            limit = int(query.get("limit", ["25"])[0])
            include_draft = query.get("include_draft", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self.instance_repository.search(tenant, object_type, search, limit=limit, include_draft=include_draft))
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
        if parsed.path == "/api/graph/proposed-elements/batch-review":
            try:
                body = self._read_json()
                result = self.instance_repository.review_proposed_graph_elements_batch(
                    tenant,
                    body.get("element_keys") or [],
                    body.get("action") or "",
                    body,
                )
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/graph/proposed-elements/"):
            parts = parsed.path.removeprefix("/api/graph/proposed-elements/").split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected /api/graph/proposed-elements/{element_key}/{action}")
                return
            element_key, action = unquote(parts[0]), unquote(parts[1])
            try:
                body = self._read_json()
                result = self.instance_repository.review_proposed_graph_element(tenant, element_key, action, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Proposed graph element not found")
                return
            self._send_json(result)
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
        if parsed.path.startswith("/api/enrichment/sessions/") and parsed.path.endswith("/run-cycle"):
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").removesuffix("/run-cycle").rstrip("/"))
            try:
                body = self._read_json()
                result = self.instance_repository.run_continuous_enrichment_cycle(tenant, session_key, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/enrichment/sessions/") and parsed.path.endswith("/configure"):
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").removesuffix("/configure").rstrip("/"))
            try:
                body = self._read_json()
                result = self.instance_repository.configure_continuous_enrichment_session(tenant, session_key, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/enrichment/sessions/") and parsed.path.endswith(("/pause", "/resume", "/stop")):
            action = parsed.path.rstrip("/").rsplit("/", 1)[1]
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").removesuffix(f"/{action}").rstrip("/"))
            status = {"pause": "paused", "resume": "idle", "stop": "stopped"}[action]
            try:
                result = self.instance_repository.update_continuous_enrichment_session_status(tenant, session_key, status)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/autopilot/sessions":
            try:
                body = self._read_json()
                result = self.reasoning_repository.create_autopilot_session(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/autopilot/playbooks/creditcardfraud/run":
            try:
                body = self._read_json()
                result = self.reasoning_repository.run_creditcardfraud_autopilot_playbook(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/autopilot/playbooks/maritime-risk/run":
            try:
                body = self._read_json()
                result = self.reasoning_repository.run_maritime_risk_autopilot_playbook(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/sessions/") and parsed.path.endswith("/hypotheses"):
            session_key = unquote(parsed.path.removeprefix("/api/reasoning/autopilot/sessions/").removesuffix("/hypotheses").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.add_autopilot_hypothesis(tenant, session_key, body)
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot session not found: {session_key}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/sessions/") and parsed.path.endswith("/candidate-findings"):
            session_key = unquote(parsed.path.removeprefix("/api/reasoning/autopilot/sessions/").removesuffix("/candidate-findings").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.add_autopilot_candidate_finding(tenant, session_key, body)
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot session not found: {session_key}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/candidate-findings/"):
            parts = parsed.path.removeprefix("/api/reasoning/autopilot/candidate-findings/").split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/reasoning/autopilot/candidate-findings/<canonical_key>/<action>")
                return
            candidate_key = unquote(parts[0])
            action = parts[1]
            try:
                body = self._read_json()
                result = self.reasoning_repository.review_autopilot_candidate(
                    tenant,
                    candidate_key,
                    action,
                    body.get("reviewer") or "Itachi",
                    body.get("reason") or "",
                )
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot candidate not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
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
        if parsed.path == "/api/reasoning/findings/revalidation-batch":
            try:
                body = self._read_json()
                result = self.reasoning_repository.batch_revalidate_findings(tenant, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/finding-actions/"):
            parts = parsed.path.removeprefix("/api/reasoning/finding-actions/").split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/reasoning/finding-actions/<action_key>/<action>")
                return
            action_key = unquote(parts[0])
            action = parts[1]
            try:
                body = self._read_json()
                result = self.reasoning_repository.update_finding_action(tenant, action_key, action, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Finding action not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/findings/") and parsed.path.endswith("/actions"):
            finding_key = unquote(parsed.path.removeprefix("/api/reasoning/findings/").removesuffix("/actions").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.finding_workspace_action(tenant, finding_key, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/findings/") and parsed.path.endswith("/change-proposals"):
            finding_key = unquote(parsed.path.removeprefix("/api/reasoning/findings/").removesuffix("/change-proposals").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.finding_change_proposal(tenant, finding_key, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
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
                elif action in {"needs-changes", "needs-evidence", "needs-more-evidence"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "needs_more_evidence", reviewer, reason)
                elif action in {"mark-stale", "stale"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "stale", reviewer, reason)
                elif action in {"supersede", "superseded"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "superseded", reviewer, reason)
                elif action in {"reaffirm", "reaffirmed"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "reaffirmed", reviewer, reason)
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
    ReviewWorkbenchHandler.instance_repository.reasoning_repository = ReviewWorkbenchHandler.reasoning_repository
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
