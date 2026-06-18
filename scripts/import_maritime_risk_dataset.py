#!/usr/bin/env python3
"""Import the maritime chokepoint dataset into the local Aletheia demo.

The import is intentionally small and repeatable: it downloads the three
Zenodo CSV files when missing, loads them into the shared source MySQL
database, and registers the `maritime-risk` tenant. It can optionally seed
legacy draft ontology artifacts for graph reasoning validation.

The `OBJECT_SPECS` and `LINK_SPECS` below are curated demo/bootstrap fixtures.
They are not the production schema-to-graph modeling path. Production rebuilds
must load raw source tables first and let SchemaGraphModelingAgent infer draft
node/edge types from source schema/profile evidence before human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "agents"))

from ontology_artifacts import ensure_artifact_schema, replace_evidence, upsert_artifact  # noqa: E402
from tenant_registry import TenantRegistry, default_metadata_db_url, default_source_db_url  # noqa: E402


TENANT_ID = "maritime-risk"
TENANT_DISPLAY = "Maritime Chokepoint Risk"
ZENODO_RECORD = "https://zenodo.org/records/13841882"
ZENODO_DOI = "10.5281/zenodo.13841882"
ZENODO_LICENSE = "CC-BY-4.0"

FILES = {
    "chokepoint_country_dependencies.csv": {
        "url": "https://zenodo.org/api/records/13841882/files/chokepoint_country_dependencies.csv/content",
        "table": "maritime_chokepoint_country_dependencies",
        "id_column": "dependency_id",
    },
    "chokepoint_risk_indicators.csv": {
        "url": "https://zenodo.org/api/records/13841882/files/chokepoint_risk_indicators.csv/content",
        "table": "maritime_chokepoint_risk_indicators",
        "id_column": "risk_indicator_id",
    },
    "chokepoint_systemic_risk_results.csv": {
        "url": "https://zenodo.org/api/records/13841882/files/chokepoint_systemic_risk_results.csv/content",
        "table": "maritime_chokepoint_systemic_risk_results",
        "id_column": "risk_result_id",
    },
}


OBJECT_SPECS = [
    {
        "key": "chokepoint",
        "name": "Chokepoint",
        "description": "Maritime chokepoint or strait that can concentrate trade disruption risk.",
        "table": "maritime_chokepoint_risk_indicators",
        "primary_key": "risk_indicator_id",
        "columns": ["risk_indicator_id", "canal", "drought", "TC1", "TC3", "severity_conflict", "severity_piracy", "severity_geopolitical"],
    },
    {
        "key": "country",
        "name": "Country",
        "description": "Country with maritime trade dependency on one or more chokepoints.",
        "table": "maritime_chokepoint_country_dependencies",
        "primary_key": "iso3",
        "columns": ["iso3", "q", "v", "q_sea_predict", "v_sea_predict", "revenue_USD"],
    },
    {
        "key": "trade_dependency",
        "name": "TradeDependency",
        "description": "Country-to-chokepoint trade dependency measured by quantity and value moving through a chokepoint.",
        "table": "maritime_chokepoint_country_dependencies",
        "primary_key": "dependency_id",
        "columns": ["dependency_id", "iso3", "canal", "q_canal", "v_canal", "q", "v", "revenue_USD"],
    },
    {
        "key": "hazard",
        "name": "Hazard",
        "description": "Hazard signal at a chokepoint, including conflict, piracy, blockage, terrorism, drought, cyclone, and geopolitical risk.",
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
    {
        "key": "risk_indicator",
        "name": "RiskIndicator",
        "description": "Hazard likelihood, duration, and severity indicator set for a maritime chokepoint.",
        "table": "maritime_chokepoint_risk_indicators",
        "primary_key": "risk_indicator_id",
        "columns": [
            "risk_indicator_id", "canal", "piracy", "geopolitical", "drought", "TC1", "TC3",
            "likelihood_conflict", "severity_conflict", "likelihood_blockage", "severity_blockage",
        ],
    },
    {
        "key": "systemic_risk_result",
        "name": "SystemicRiskResult",
        "description": "Expected disrupted trade and economic risk result for a country/chokepoint pair.",
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "v_share", "v_share_mar", "trade_at_risk_v", "trade_at_risk_q", "revenue_at_risk", "trade_impacted"],
    },
    {
        "key": "risk_finding",
        "name": "RiskFinding",
        "description": "Draft reasoning finding that explains a graph path from hazard to chokepoint dependency and action.",
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "trade_at_risk_v", "trade_impacted"],
    },
    {
        "key": "mitigation_action",
        "name": "MitigationAction",
        "description": "Recommended analyst or operations action derived from a maritime risk finding.",
        "table": "maritime_chokepoint_systemic_risk_results",
        "primary_key": "risk_result_id",
        "columns": ["risk_result_id", "iso3", "canal", "trade_at_risk_v", "trade_impacted"],
    },
]


LINK_SPECS = [
    {
        "key": "country:n:m:chokepoint_dependency",
        "name": "Country N:M Chokepoint Dependency",
        "description": "Countries depend on chokepoints through measured trade dependency rows.",
        "source": "Country",
        "target": "Chokepoint",
        "cardinality": "N:M",
        "source_table": "maritime_chokepoint_country_dependencies",
        "target_table": "maritime_chokepoint_risk_indicators",
        "join_condition": "maritime_chokepoint_country_dependencies.canal = maritime_chokepoint_risk_indicators.canal",
    },
    {
        "key": "chokepoint:1:n:risk_indicator",
        "name": "Chokepoint 1:N RiskIndicator",
        "description": "A chokepoint owns the hazard likelihood, timescale, and severity indicators measured for it.",
        "source": "Chokepoint",
        "target": "RiskIndicator",
        "cardinality": "1:N",
        "source_table": "maritime_chokepoint_risk_indicators",
        "target_table": "maritime_chokepoint_risk_indicators",
        "join_condition": "risk_indicator.canal = chokepoint.canal",
    },
    {
        "key": "country:1:n:systemic_risk_result",
        "name": "Country 1:N SystemicRiskResult",
        "description": "A country can have systemic risk results across multiple maritime chokepoints.",
        "source": "Country",
        "target": "SystemicRiskResult",
        "cardinality": "1:N",
        "source_table": "maritime_chokepoint_country_dependencies",
        "target_table": "maritime_chokepoint_systemic_risk_results",
        "join_condition": "maritime_chokepoint_country_dependencies.iso3 = maritime_chokepoint_systemic_risk_results.iso3",
    },
    {
        "key": "trade_dependency:n:1:country",
        "name": "TradeDependency N:1 Country",
        "description": "Each dependency row belongs to one country.",
        "source": "TradeDependency",
        "target": "Country",
        "cardinality": "N:1",
        "source_table": "maritime_chokepoint_country_dependencies",
        "target_table": "maritime_chokepoint_country_dependencies",
        "join_condition": "trade_dependency.iso3 = country.iso3",
    },
    {
        "key": "trade_dependency:n:1:chokepoint",
        "name": "TradeDependency N:1 Chokepoint",
        "description": "Each dependency row belongs to one maritime chokepoint.",
        "source": "TradeDependency",
        "target": "Chokepoint",
        "cardinality": "N:1",
        "source_table": "maritime_chokepoint_country_dependencies",
        "target_table": "maritime_chokepoint_risk_indicators",
        "join_condition": "trade_dependency.canal = chokepoint.canal",
    },
    {
        "key": "risk_finding:n:m:evidence",
        "name": "RiskFinding N:M Evidence",
        "description": "A maritime risk finding is supported by dependency, hazard, and systemic risk evidence.",
        "source": "RiskFinding",
        "target": "RiskIndicator",
        "cardinality": "N:M",
        "source_table": "maritime_chokepoint_systemic_risk_results",
        "target_table": "maritime_chokepoint_risk_indicators",
        "join_condition": "risk_finding.canal = risk_indicator.canal",
    },
    {
        "key": "mitigation_action:n:1:risk_finding",
        "name": "MitigationAction N:1 RiskFinding",
        "description": "Recommended mitigation actions are generated from a reviewed maritime risk finding.",
        "source": "MitigationAction",
        "target": "RiskFinding",
        "cardinality": "N:1",
        "source_table": "maritime_chokepoint_systemic_risk_results",
        "target_table": "maritime_chokepoint_systemic_risk_results",
        "join_condition": "mitigation_action.risk_result_id = risk_finding.risk_result_id",
    },
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_files(data_dir: Path) -> dict[str, dict]:
    data_dir.mkdir(parents=True, exist_ok=True)
    downloaded = {}
    for name, spec in FILES.items():
        path = data_dir / name
        if not path.exists():
            with urlopen(spec["url"], timeout=60) as response:
                path.write_bytes(response.read())
        downloaded[name] = {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "source_url": spec["url"],
        }
    return downloaded


def _prepare_frame(filename: str, path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if filename == "chokepoint_country_dependencies.csv":
        frame.insert(0, "dependency_id", frame["iso3"].astype(str) + "::" + frame["canal"].astype(str))
    elif filename == "chokepoint_risk_indicators.csv":
        frame.insert(0, "risk_indicator_id", frame["canal"].astype(str))
    elif filename == "chokepoint_systemic_risk_results.csv":
        frame.insert(0, "risk_result_id", frame["iso3"].astype(str) + "::" + frame["canal"].astype(str))
    return frame


def import_source_tables(source_db_url: str, data_dir: Path) -> dict[str, dict]:
    engine = create_engine(source_db_url)
    imported = {}
    for filename, spec in FILES.items():
        frame = _prepare_frame(filename, data_dir / filename)
        table = spec["table"]
        frame.to_sql(table, engine, if_exists="replace", index=False, chunksize=1000, method="multi")
        with engine.begin() as conn:
            for column in ("iso3", "canal", spec["id_column"]):
                if column in frame.columns:
                    conn.execute(text(f"CREATE INDEX ix_{table}_{column} ON {table} (`{column}`(191))"))
            row_count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        imported[table] = {
            "source_file": filename,
            "rows": int(row_count),
            "columns": list(frame.columns),
        }
    return imported


def ensure_maritime_tenant(engine) -> None:
    TenantRegistry.load().ensure_metadata(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO aletheia_tenants
                (tenant_id, namespace, display_name, graph_database, status, created_at, updated_at)
                VALUES
                (:tenant_id, :namespace, :display_name, :graph_database, 'active', NOW(), NOW())
                ON CONFLICT (tenant_id) DO UPDATE SET
                  namespace = EXCLUDED.namespace,
                  display_name = EXCLUDED.display_name,
                  graph_database = EXCLUDED.graph_database,
                  status = EXCLUDED.status,
                  updated_at = NOW()
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "namespace": "maritime_risk",
                "display_name": TENANT_DISPLAY,
                "graph_database": "maritime_risk",
            },
        )


def _seed_object(session, spec: dict) -> str:
    payload = {
        "object_name": spec["name"],
        "mapped_table_names": [spec["table"]],
        "primary_key": spec["primary_key"],
        "properties": spec["columns"],
        "source_license": ZENODO_LICENSE,
        "source_record": ZENODO_RECORD,
        "canonical_write_boundary": "draft_only_until_human_review",
    }
    artifact = upsert_artifact(
        session,
        artifact_type="object",
        natural_key=spec["key"],
        name=spec["name"],
        description=spec["description"],
        payload=payload,
        source_refs=[f"table:{spec['table']}", ZENODO_RECORD],
        source_agent="MaritimeRiskDatasetImport",
        status="draft",
        confidence=0.9,
        project_id=TENANT_ID,
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": "source_schema",
                "source_ref": f"table:{spec['table']}",
                "summary": f"{spec['name']} draft object mapped from {spec['table']}.",
                "payload": payload,
                "confidence": 0.9,
            },
            {
                "evidence_type": "source_license",
                "source_ref": ZENODO_RECORD,
                "summary": f"Zenodo dataset {ZENODO_DOI}, access_right=open, license={ZENODO_LICENSE}.",
                "payload": {"doi": ZENODO_DOI, "license": ZENODO_LICENSE, "record": ZENODO_RECORD},
                "confidence": 1.0,
            },
        ],
    )
    return artifact.canonical_key


def _seed_link(session, spec: dict) -> str:
    payload = {
        "source_object_name": spec["source"],
        "target_object_name": spec["target"],
        "link_type": spec["cardinality"],
        "description": spec["description"],
        "source_table": spec["source_table"],
        "target_table": spec["target_table"],
        "join_condition": spec["join_condition"],
        "canonical_write_boundary": "draft_only_until_human_review",
    }
    artifact = upsert_artifact(
        session,
        artifact_type="link",
        natural_key=spec["key"],
        name=spec["name"],
        description=spec["description"],
        payload=payload,
        source_refs=[f"table:{spec['source_table']}", f"table:{spec['target_table']}", ZENODO_RECORD],
        source_agent="MaritimeRiskDatasetImport",
        status="draft",
        confidence=0.88,
        project_id=TENANT_ID,
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": "relationship_schema",
                "source_ref": spec["join_condition"],
                "summary": spec["description"],
                "payload": payload,
                "confidence": 0.88,
            },
            {
                "evidence_type": "source_license",
                "source_ref": ZENODO_RECORD,
                "summary": f"Zenodo dataset {ZENODO_DOI}, access_right=open, license={ZENODO_LICENSE}.",
                "payload": {"doi": ZENODO_DOI, "license": ZENODO_LICENSE, "record": ZENODO_RECORD},
                "confidence": 1.0,
            },
        ],
    )
    return artifact.canonical_key


def seed_metadata(metadata_db_url: str) -> dict:
    engine = create_engine(metadata_db_url)
    ensure_artifact_schema(engine)
    ensure_maritime_tenant(engine)
    Session = sessionmaker(bind=engine)
    seeded = []
    with Session() as session:
        for spec in OBJECT_SPECS:
            seeded.append(_seed_object(session, spec))
        for spec in LINK_SPECS:
            seeded.append(_seed_link(session, spec))
        session.commit()
    with engine.connect() as conn:
        counts = conn.execute(
            text(
                """
                SELECT artifact_type, status, COUNT(*) AS count
                FROM aletheia_ontology_artifacts
                WHERE project_id = :tenant_id
                GROUP BY artifact_type, status
                ORDER BY artifact_type, status
                """
            ),
            {"tenant_id": TENANT_ID},
        ).mappings().all()
    return {"seeded": seeded, "artifact_counts": [dict(row) for row in counts]}


def import_dataset(data_dir: Path, source_db_url: str, metadata_db_url: str, *, seed_fixtures: bool = False) -> dict:
    files = download_files(data_dir)
    source_tables = import_source_tables(source_db_url, data_dir)
    if seed_fixtures:
        metadata = seed_metadata(metadata_db_url)
    else:
        engine = create_engine(metadata_db_url)
        ensure_artifact_schema(engine)
        ensure_maritime_tenant(engine)
        metadata = {
            "seeded": [],
            "artifact_counts": [],
            "bootstrap_fixtures_skipped": True,
        }
    return {
        "tenant_id": TENANT_ID,
        "display_name": TENANT_DISPLAY,
        "source": {
            "record": ZENODO_RECORD,
            "doi": ZENODO_DOI,
            "license": ZENODO_LICENSE,
            "files": files,
        },
        "source_tables": source_tables,
        "metadata": metadata,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import maritime-risk chokepoint dataset into Aletheia")
    parser.add_argument("--data-dir", default=str(ROOT / "datasets" / "maritime_chokepoints"))
    parser.add_argument("--source-db-url", default=default_source_db_url())
    parser.add_argument("--metadata-db-url", default=default_metadata_db_url())
    parser.add_argument("--report-json", default=str(ROOT / "reports" / "maritime-risk-import-task165.json"))
    parser.set_defaults(seed_bootstrap_fixtures=False)
    parser.add_argument(
        "--seed-bootstrap-fixtures",
        dest="seed_bootstrap_fixtures",
        action="store_true",
        help="Also seed legacy demo ontology fixtures. Production rebuilds should leave this disabled.",
    )
    parser.add_argument(
        "--skip-bootstrap-fixtures",
        dest="seed_bootstrap_fixtures",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    result = import_dataset(
        Path(args.data_dir),
        args.source_db_url,
        args.metadata_db_url,
        seed_fixtures=args.seed_bootstrap_fixtures,
    )
    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"tenant={result['tenant_id']}")
    for table, meta in result["source_tables"].items():
        print(f"{table}: rows={meta['rows']} columns={len(meta['columns'])}")
    for row in result["metadata"]["artifact_counts"]:
        print(f"artifacts[{row['artifact_type']}][{row['status']}]={row['count']}")
    print(f"report_json={report_path}")


if __name__ == "__main__":
    main()
