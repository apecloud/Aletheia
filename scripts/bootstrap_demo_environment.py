#!/usr/bin/env python3
"""Bootstrap metadata required by the local Aletheia demo server.

This script is intentionally metadata-only. It creates/migrates the Postgres
tables used by Ontology/Reasoning pages, registers demo tenants, and seeds the
minimum ontology artifacts needed for a fresh 8772 review environment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "agents"))

from ontology_artifacts import ensure_artifact_schema, replace_evidence, upsert_artifact  # noqa: E402
from tenant_registry import TenantRegistry, default_metadata_db_url  # noqa: E402


NORTHWIND_OBJECTS = [
    {
        "key": "employee",
        "name": "Employee",
        "description": "Northwind employee who can own and process customer orders.",
        "table": "employees",
        "primary_key": "employeeID",
        "columns": [
            "employeeID",
            "lastName",
            "firstName",
            "title",
            "reportsTo",
            "city",
            "country",
        ],
    },
    {
        "key": "order",
        "name": "Order",
        "description": "Customer order handled by an employee.",
        "table": "orders",
        "primary_key": "orderID",
        "columns": ["orderID", "customerID", "employeeID", "orderDate", "freight"],
    },
    {
        "key": "customer",
        "name": "Customer",
        "description": "Northwind customer account placing orders.",
        "table": "customers",
        "primary_key": "customerID",
        "columns": ["customerID", "companyName", "contactName", "country"],
    },
    {
        "key": "product",
        "name": "Product",
        "description": "Northwind product sold through order details.",
        "table": "products",
        "primary_key": "productID",
        "columns": ["productID", "productName", "categoryID", "unitPrice"],
    },
    {
        "key": "category",
        "name": "Category",
        "description": "Product category grouping Northwind products.",
        "table": "categories",
        "primary_key": "categoryID",
        "columns": ["categoryID", "categoryName", "description"],
    },
]


NORTHWIND_LINKS = [
    {
        "key": "employee:1:n:order",
        "name": "Employee 1:N Order",
        "description": "One employee can handle many orders through orders.employeeID.",
        "source": "Employee",
        "target": "Order",
        "cardinality": "1:N",
        "source_table": "employees",
        "target_table": "orders",
        "join_condition": "orders.employeeID = employees.employeeID",
    },
    {
        "key": "customer:1:n:order",
        "name": "Customer 1:N Order",
        "description": "One customer can place many orders through orders.customerID.",
        "source": "Customer",
        "target": "Order",
        "cardinality": "1:N",
        "source_table": "customers",
        "target_table": "orders",
        "join_condition": "orders.customerID = customers.customerID",
    },
    {
        "key": "category:1:n:product",
        "name": "Category 1:N Product",
        "description": "One product category groups many products through products.categoryID.",
        "source": "Category",
        "target": "Product",
        "cardinality": "1:N",
        "source_table": "categories",
        "target_table": "products",
        "join_condition": "products.categoryID = categories.categoryID",
    },
]


CREDITCARDFRAUD_OBJECTS = [
    {
        "key": "credit_card_transaction",
        "name": "Credit Card Transaction",
        "description": "Masked transaction event used for fraud pattern discovery.",
        "table": "credit_card_transactions_safe",
        "primary_key": "transaction_id",
        "columns": [
            "transaction_id",
            "accountNumber",
            "customerId",
            "transactionDateTime",
            "transactionAmount",
            "merchantName",
            "merchantCategoryCode",
            "cardPresent",
            "cvvMatch",
            "isFraud",
        ],
    },
    {
        "key": "account",
        "name": "Account",
        "description": "Customer account context for credit limit, balance, and available money.",
        "table": "credit_card_transactions_safe",
        "primary_key": "accountNumber",
        "columns": ["accountNumber", "customerId", "creditLimit", "availableMoney", "currentBalance"],
    },
    {
        "key": "card",
        "name": "Card",
        "description": "Masked card identity and derived verification state.",
        "table": "credit_card_transactions_safe",
        "primary_key": "cardLast4Digits",
        "columns": ["cardLast4Digits", "cvvMatch", "expirationDateKeyInMatch", "cardPresent"],
    },
    {
        "key": "merchant",
        "name": "Merchant",
        "description": "Merchant identity, category, and geography involved in a transaction.",
        "table": "credit_card_transactions_safe",
        "primary_key": "merchantName",
        "columns": ["merchantName", "merchantCategoryCode", "merchantCountryCode", "acqCountry"],
    },
]


CREDITCARDFRAUD_LINKS = [
    {
        "key": "account:1:n:credit_card_transaction",
        "name": "Account 1:N Credit Card Transaction",
        "description": "One account can have many credit card transactions.",
        "source": "Account",
        "target": "Credit Card Transaction",
        "cardinality": "1:N",
        "source_ref": "credit_card_transactions_safe.accountNumber",
    },
    {
        "key": "card:1:n:credit_card_transaction",
        "name": "Card 1:N Credit Card Transaction",
        "description": "One masked card identity can appear in many transactions.",
        "source": "Card",
        "target": "Credit Card Transaction",
        "cardinality": "1:N",
        "source_ref": "credit_card_transactions_safe.cardLast4Digits",
    },
    {
        "key": "merchant:1:n:credit_card_transaction",
        "name": "Merchant 1:N Credit Card Transaction",
        "description": "One merchant can receive many credit card transactions.",
        "source": "Merchant",
        "target": "Credit Card Transaction",
        "cardinality": "1:N",
        "source_ref": "credit_card_transactions_safe.merchantName",
    },
]


def _seed_object(session, tenant_id: str, spec: dict, status: str) -> str:
    artifact = upsert_artifact(
        session,
        artifact_type="object",
        natural_key=spec["key"],
        name=spec["name"],
        description=spec["description"],
        payload={
            "object_name": spec["name"],
            "mapped_table_names": [spec["table"]],
            "primary_key": spec["primary_key"],
            "properties": spec["columns"],
        },
        source_refs=[f"table:{spec['table']}"],
        source_agent="DemoEnvironmentBootstrap",
        status=status,
        confidence=0.95,
        project_id=tenant_id,
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": "source_schema",
                "source_ref": f"table:{spec['table']}",
                "summary": f"Seeded from {spec['table']} for repeatable demo metadata.",
                "payload": {
                    "table": spec["table"],
                    "primary_key": spec["primary_key"],
                    "columns": spec["columns"],
                },
                "confidence": 0.95,
            }
        ],
    )
    return artifact.canonical_key


def _seed_link(session, tenant_id: str, spec: dict, status: str) -> str:
    payload = {
        "source_object_name": spec["source"],
        "target_object_name": spec["target"],
        "link_type": spec["cardinality"],
        "description": spec["description"],
    }
    if spec.get("join_condition"):
        payload["join_condition"] = spec["join_condition"]
    artifact = upsert_artifact(
        session,
        artifact_type="link",
        natural_key=spec["key"],
        name=spec["name"],
        description=spec["description"],
        payload=payload,
        source_refs=[spec.get("source_ref") or f"table:{spec['target_table']}"],
        source_agent="DemoEnvironmentBootstrap",
        status=status,
        confidence=0.9,
        project_id=tenant_id,
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": "relationship_schema",
                "source_ref": spec.get("source_ref") or spec.get("join_condition") or spec["key"],
                "summary": spec["description"],
                "payload": payload,
                "confidence": 0.9,
            }
        ],
    )
    return artifact.canonical_key


def ensure_creditcardfraud_tenant(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO aletheia_tenants
                (tenant_id, namespace, display_name, graph_database, status, created_at, updated_at)
                VALUES
                ('creditcardfraud', 'creditcardfraud', 'Credit Card Fraud Dataset',
                 'creditcardfraud', 'active', NOW(), NOW())
                ON CONFLICT (tenant_id) DO UPDATE SET
                  namespace = EXCLUDED.namespace,
                  display_name = EXCLUDED.display_name,
                  graph_database = EXCLUDED.graph_database,
                  status = EXCLUDED.status,
                  updated_at = NOW()
                """
            )
        )


def sanitize_existing_autopilot_safety_profiles(engine) -> int:
    inspector = inspect(engine)
    if "aletheia_autopilot_sessions" not in inspector.get_table_names():
        return 0
    changed = 0
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, safety_profile_json
                FROM aletheia_autopilot_sessions
                WHERE project_id = 'creditcardfraud'
                """
            )
        ).mappings().all()
        for row in rows:
            try:
                safety_profile = json.loads(row["safety_profile_json"] or "{}")
            except json.JSONDecodeError:
                safety_profile = {}
            blocked = safety_profile.get("blocked_fields") or []
            if "cardCVV" not in blocked and "enteredCVV" not in blocked:
                continue
            filtered = [
                field
                for field in blocked
                if field not in {"cardCVV", "enteredCVV", "card_verification_code_fields"}
            ]
            filtered.append("card_verification_code_fields")
            safety_profile["blocked_fields"] = filtered
            safety_profile["allow_sensitive_fields"] = False
            safety_profile["masked_fields_only"] = True
            safety_profile["safe_views_only"] = True
            safety_profile["canonical_writes"] = "disabled"
            safety_profile["auto_approve_findings"] = False
            conn.execute(
                text(
                    """
                    UPDATE aletheia_autopilot_sessions
                    SET safety_profile_json = :payload, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": row["id"], "payload": json.dumps(safety_profile, ensure_ascii=False, sort_keys=True)},
            )
            changed += 1
    return changed


def bootstrap(db_url: str) -> dict:
    engine = create_engine(db_url)
    ensure_artifact_schema(engine)
    TenantRegistry.load().ensure_metadata(engine)
    ensure_creditcardfraud_tenant(engine)
    sanitized_autopilot_sessions = sanitize_existing_autopilot_safety_profiles(engine)

    Session = sessionmaker(bind=engine)
    seeded = {"default": [], "creditcardfraud": []}
    with Session() as session:
        for spec in NORTHWIND_OBJECTS:
            seeded["default"].append(_seed_object(session, "default", spec, "approved"))
        for spec in NORTHWIND_LINKS:
            seeded["default"].append(_seed_link(session, "default", spec, "approved"))
        for spec in CREDITCARDFRAUD_OBJECTS:
            seeded["creditcardfraud"].append(_seed_object(session, "creditcardfraud", spec, "draft"))
        for spec in CREDITCARDFRAUD_LINKS:
            seeded["creditcardfraud"].append(_seed_link(session, "creditcardfraud", spec, "draft"))
        session.commit()

    with engine.connect() as conn:
        tables = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'aletheia_tenants',
                    'aletheia_ontology_artifacts',
                    'aletheia_artifact_evidence',
                    'aletheia_artifact_reviews',
                    'aletheia_reasoning_tasks',
                    'aletheia_reasoning_findings'
                  )
                ORDER BY table_name
                """
            )
        ).scalars().all()
        counts = conn.execute(
            text(
                """
                SELECT project_id, status, COUNT(*) AS count
                FROM aletheia_ontology_artifacts
                WHERE project_id IN ('default', 'creditcardfraud')
                GROUP BY project_id, status
                ORDER BY project_id, status
                """
            )
        ).mappings().all()
    return {
        "metadata_db_url": db_url,
        "tables": list(tables),
        "artifact_counts": [dict(row) for row in counts],
        "seeded": seeded,
        "sanitized_autopilot_sessions": sanitized_autopilot_sessions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap the local Aletheia demo metadata environment")
    parser.add_argument("--db-url", default=default_metadata_db_url(), help="Postgres metadata SQLAlchemy URL")
    args = parser.parse_args()
    result = bootstrap(args.db_url)
    print("Aletheia demo metadata bootstrap complete")
    print(f"metadata_db_url={result['metadata_db_url']}")
    print("tables=" + ",".join(result["tables"]))
    for row in result["artifact_counts"]:
        print(f"artifacts[{row['project_id']}][{row['status']}]={row['count']}")
    print(f"sanitized_autopilot_sessions={result['sanitized_autopilot_sessions']}")


if __name__ == "__main__":
    main()
