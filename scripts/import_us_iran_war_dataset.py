#!/usr/bin/env python3
"""Import a web-researched U.S.-Iran conflict economic-impact graph demo.

The script uses a deterministic web-search snapshot: each fact keeps the search
query, URL, publisher, retrieval date, confidence, and review/licensing notes.
It loads source tables into the shared MySQL source DB, registers a tenant graph
space, and seeds draft ontology artifacts only. No canonical ontology or graph
writes happen in this import.

The `OBJECT_SPECS` and `LINK_SPECS` below are curated demo/bootstrap fixtures
for this snapshot. They are not a production schema-to-graph decision path.
Production imports should persist raw source tables and route schema/profile
evidence through SchemaGraphModelingAgent before creating reviewable drafts.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "agents"))

from ontology_artifacts import ensure_artifact_schema, replace_evidence, upsert_artifact  # noqa: E402
from tenant_registry import TenantRegistry, default_metadata_db_url, default_source_db_url  # noqa: E402


TENANT_ID = "us-iran-war"
TENANT_DISPLAY = "U.S.-Iran Conflict Economic Impact"
NAMESPACE = "us_iran_war"
GRAPH_DATABASE = "us_iran_war"
RETRIEVED_AT = "2026-05-24T00:00:00+08:00"

WEB_SOURCES = [
    {
        "source_id": "src_eia_hormuz_2025",
        "title": "The Strait of Hormuz is the world's most important oil transit chokepoint",
        "publisher": "U.S. Energy Information Administration",
        "url": "https://www.eia.gov/todayinenergy/detail.php?id=65504",
        "query": "EIA Strait of Hormuz oil LNG flows 2024 2025",
        "retrieved_at": RETRIEVED_AT,
        "license_risk": "U.S. government source; reviewer should verify downstream reuse terms.",
        "robots_risk": "public web page; robots not interpreted by this import.",
        "summary": (
            "EIA reports 20 million barrels per day of oil and petroleum products transited "
            "the Strait of Hormuz in 2024; about 69% of that crude and condensate went to "
            "Asian markets, and LNG flows through Hormuz averaged 11 Bcf/d."
        ),
        "confidence": 0.92,
    },
    {
        "source_id": "src_iea_hormuz",
        "title": "Strait of Hormuz",
        "publisher": "International Energy Agency",
        "url": "https://www.iea.org/about/oil-security-and-emergency-reserve/strait-of-hormuz",
        "query": "IEA Strait of Hormuz oil security emergency reserve Asia importers",
        "retrieved_at": RETRIEVED_AT,
        "license_risk": "IEA source; use extracted metrics/summary only and verify reuse terms.",
        "robots_risk": "public web page; robots not interpreted by this import.",
        "summary": (
            "IEA describes Hormuz as a critical route for oil exports from Saudi Arabia, Iran, "
            "the UAE, Kuwait, Iraq, and Qatar, and notes available bypass capacity through "
            "Saudi/UAE pipelines is limited relative to normal flows."
        ),
        "confidence": 0.86,
    },
    {
        "source_id": "src_crs_iran_2025",
        "title": "Iran and U.S. Policy",
        "publisher": "Congressional Research Service",
        "url": "https://www.congress.gov/crs-product/R47321",
        "query": "CRS Iran and U.S. policy June 2025 conflict United States Iran Israel",
        "retrieved_at": RETRIEVED_AT,
        "license_risk": "U.S. congressional research product; reviewer should verify reuse terms.",
        "robots_risk": "public web page; robots not interpreted by this import.",
        "summary": (
            "CRS provides context on Iran, U.S. policy, regional conflict, sanctions, nuclear "
            "concerns, proxy forces, and the June 2025 Iran-Israel-U.S. escalation."
        ),
        "confidence": 0.84,
    },
    {
        "source_id": "src_imf_war_oil",
        "title": "How War in the Middle East Could Affect the World Economy",
        "publisher": "International Monetary Fund",
        "url": "https://www.imf.org/en/Blogs/Articles/2023/10/24/how-war-in-the-middle-east-could-affect-the-world-economy",
        "query": "IMF Middle East war oil price inflation global economy impact",
        "retrieved_at": RETRIEVED_AT,
        "license_risk": "IMF blog/source; use extracted summary only and verify reuse terms.",
        "robots_risk": "public web page; robots not interpreted by this import.",
        "summary": (
            "IMF analysis frames Middle East conflict spillovers through oil prices, inflation, "
            "financial conditions, trade/shipping disruption, confidence, and tourism."
        ),
        "confidence": 0.82,
    },
    {
        "source_id": "src_worldbank_cmo",
        "title": "Commodity Markets Outlook",
        "publisher": "World Bank",
        "url": "https://www.worldbank.org/en/research/commodity-markets",
        "query": "World Bank commodity markets oil price conflict Middle East Strait of Hormuz",
        "retrieved_at": RETRIEVED_AT,
        "license_risk": "World Bank source; use extracted summary only and verify reuse terms.",
        "robots_risk": "public web page; robots not interpreted by this import.",
        "summary": (
            "World Bank commodity-market analysis treats geopolitical shocks and oil-supply "
            "disruptions as major upside risks for energy prices and inflation."
        ),
        "confidence": 0.78,
    },
    {
        "source_id": "src_ofac_iran_sanctions",
        "title": "Iran Sanctions",
        "publisher": "U.S. Treasury OFAC",
        "url": "https://ofac.treasury.gov/sanctions-programs-and-country-information/iran-sanctions",
        "query": "OFAC Iran sanctions program economic impact oil shipping finance",
        "retrieved_at": RETRIEVED_AT,
        "license_risk": "U.S. government source; reviewer should verify downstream reuse terms.",
        "robots_risk": "public web page; robots not interpreted by this import.",
        "summary": (
            "OFAC Iran sanctions are a persistent policy channel affecting finance, shipping, "
            "energy trade, counterparties, and compliance actions."
        ),
        "confidence": 0.85,
    },
]

CONFLICT_EVENTS = [
    {
        "event_id": "event_2025_june_us_iran_escalation",
        "event_date": "2025-06",
        "event_type": "regional_military_escalation",
        "actors": "United States; Iran; Israel",
        "location": "Iran; Israel; Persian Gulf",
        "summary": "June 2025 escalation involving Iran, Israel, and the United States raised nuclear, missile, and regional conflict risk.",
        "source_id": "src_crs_iran_2025",
        "confidence": 0.84,
    },
    {
        "event_id": "event_iran_sanctions_persistent",
        "event_date": "ongoing",
        "event_type": "sanctions_pressure",
        "actors": "United States; Iran; global counterparties",
        "location": "global_financial_system",
        "summary": "Iran sanctions constrain finance, energy transactions, shipping counterparties, and compliance risk.",
        "source_id": "src_ofac_iran_sanctions",
        "confidence": 0.85,
    },
]

ECONOMIC_CHANNELS = [
    {
        "channel_id": "channel_hormuz_oil_flow",
        "channel_name": "Hormuz oil flow disruption",
        "mechanism": "Conflict around Iran can threaten the Strait of Hormuz, raising crude and refined-product supply risk.",
        "primary_metric": "20 million barrels per day oil and petroleum products through Hormuz in 2024",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.92,
    },
    {
        "channel_id": "channel_hormuz_lng_flow",
        "channel_name": "Hormuz LNG flow disruption",
        "mechanism": "Qatar and UAE LNG exports through Hormuz create gas-market exposure, especially for Asian buyers.",
        "primary_metric": "11 Bcf/d LNG through Hormuz in 2024; Qatar 9.3 Bcf/d, UAE 0.7 Bcf/d",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.9,
    },
    {
        "channel_id": "channel_inflation_growth",
        "channel_name": "Energy price inflation and growth drag",
        "mechanism": "Higher energy prices can raise inflation, tighten financial conditions, and reduce consumption or output.",
        "primary_metric": "qualitative macro shock channel",
        "source_id": "src_imf_war_oil",
        "confidence": 0.82,
    },
    {
        "channel_id": "channel_sanctions_compliance",
        "channel_name": "Sanctions and compliance exposure",
        "mechanism": "Sanctions increase legal, financial, insurance, and counterparty-screening obligations.",
        "primary_metric": "OFAC Iran sanctions program",
        "source_id": "src_ofac_iran_sanctions",
        "confidence": 0.85,
    },
]

COUNTRY_EXPOSURES = [
    {
        "country_id": "country_CHN",
        "iso3": "CHN",
        "country_name": "China",
        "exposure_type": "energy_importer",
        "exposure_level": "high",
        "impact_summary": "High exposure through Asian-bound crude/condensate and LNG flows via Hormuz; likely import-cost and supply-security impact.",
        "key_metric": "Asia receives about 69% of crude/condensate through Hormuz; China is a major destination.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.88,
    },
    {
        "country_id": "country_IND",
        "iso3": "IND",
        "country_name": "India",
        "exposure_type": "energy_importer",
        "exposure_level": "high",
        "impact_summary": "High exposure as a major Asian buyer of Gulf crude and LNG; likely inflation, current-account, and refining margin pressure.",
        "key_metric": "Asia receives about 69% of crude/condensate through Hormuz; India is a major destination.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.87,
    },
    {
        "country_id": "country_JPN",
        "iso3": "JPN",
        "country_name": "Japan",
        "exposure_type": "energy_importer",
        "exposure_level": "high",
        "impact_summary": "High import-cost and energy-security exposure because Japan is a major Asian destination for Hormuz crude and LNG.",
        "key_metric": "Asia receives about 69% of crude/condensate through Hormuz and 52% of LNG flows.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.86,
    },
    {
        "country_id": "country_KOR",
        "iso3": "KOR",
        "country_name": "South Korea",
        "exposure_type": "energy_importer",
        "exposure_level": "high",
        "impact_summary": "High exposure through refinery/feedstock dependence on Gulf crude and LNG flows.",
        "key_metric": "Asia receives about 69% of crude/condensate through Hormuz and 52% of LNG flows.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.85,
    },
    {
        "country_id": "country_USA",
        "iso3": "USA",
        "country_name": "United States",
        "exposure_type": "global_price_and_policy",
        "exposure_level": "medium",
        "impact_summary": "Direct physical import exposure is lower than Asian importers, but global oil-price, sanctions, naval-security, and inflation channels remain material.",
        "key_metric": "EIA notes U.S. direct imports from Hormuz are small relative to total U.S. petroleum consumption.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.82,
    },
    {
        "country_id": "country_QAT",
        "iso3": "QAT",
        "country_name": "Qatar",
        "exposure_type": "lng_exporter",
        "exposure_level": "high",
        "impact_summary": "Export revenue and LNG delivery risk because nearly all Qatari LNG exports route through Hormuz.",
        "key_metric": "Qatar LNG through Hormuz averaged 9.3 Bcf/d in 2024.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.9,
    },
    {
        "country_id": "country_SAU",
        "iso3": "SAU",
        "country_name": "Saudi Arabia",
        "exposure_type": "oil_exporter_with_bypass",
        "exposure_level": "high",
        "impact_summary": "Large oil-export exposure through Hormuz, partly mitigated by pipeline bypass capacity.",
        "key_metric": "IEA/EIA describe limited Saudi/UAE bypass capacity relative to normal Hormuz flows.",
        "source_id": "src_iea_hormuz",
        "confidence": 0.84,
    },
    {
        "country_id": "country_EU",
        "iso3": "EUR",
        "country_name": "Euro area",
        "exposure_type": "macro_price_channel",
        "exposure_level": "medium",
        "impact_summary": "Main impact comes through global energy prices, inflation expectations, industrial input costs, and financial conditions.",
        "key_metric": "IMF conflict-spillover channel: oil prices, inflation, financial conditions, trade and confidence.",
        "source_id": "src_imf_war_oil",
        "confidence": 0.78,
    },
]

GRAPH_EDGES = [
    ("event_2025_june_us_iran_escalation", "raises_risk_of", "channel_hormuz_oil_flow", "src_crs_iran_2025", 0.78),
    ("event_2025_june_us_iran_escalation", "raises_risk_of", "channel_hormuz_lng_flow", "src_crs_iran_2025", 0.75),
    ("event_iran_sanctions_persistent", "drives", "channel_sanctions_compliance", "src_ofac_iran_sanctions", 0.85),
    ("channel_hormuz_oil_flow", "impacts", "country_CHN", "src_eia_hormuz_2025", 0.88),
    ("channel_hormuz_oil_flow", "impacts", "country_IND", "src_eia_hormuz_2025", 0.87),
    ("channel_hormuz_oil_flow", "impacts", "country_JPN", "src_eia_hormuz_2025", 0.86),
    ("channel_hormuz_lng_flow", "impacts", "country_KOR", "src_eia_hormuz_2025", 0.85),
    ("channel_hormuz_lng_flow", "impacts", "country_QAT", "src_eia_hormuz_2025", 0.9),
    ("channel_hormuz_oil_flow", "impacts", "country_USA", "src_eia_hormuz_2025", 0.74),
    ("channel_inflation_growth", "impacts", "country_EU", "src_imf_war_oil", 0.78),
    ("channel_sanctions_compliance", "impacts", "country_USA", "src_ofac_iran_sanctions", 0.82),
    ("country_CHN", "requires_action", "action_energy_importer_stress_test", "src_eia_hormuz_2025", 0.82),
    ("country_IND", "requires_action", "action_energy_importer_stress_test", "src_eia_hormuz_2025", 0.82),
    ("country_QAT", "requires_action", "action_lng_export_contingency", "src_eia_hormuz_2025", 0.86),
    ("country_USA", "requires_action", "action_sanctions_counterparty_review", "src_ofac_iran_sanctions", 0.8),
]

RECOMMENDED_ACTIONS = [
    {
        "action_id": "action_energy_importer_stress_test",
        "action_name": "Stress-test importer energy exposure",
        "owner_role": "energy-risk analyst",
        "trigger": "Hormuz oil/LNG disruption risk rises after U.S.-Iran escalation",
        "recommended_action": "Review crude/LNG sourcing, strategic inventory, freight insurance, and inflation pass-through for high-exposure importers.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.82,
    },
    {
        "action_id": "action_lng_export_contingency",
        "action_name": "Review LNG export continuity plan",
        "owner_role": "energy operations analyst",
        "trigger": "Hormuz LNG flow disruption threatens Qatari/UAE export routes",
        "recommended_action": "Assess cargo scheduling, delivery obligations, force-majeure scenarios, and customer substitution options.",
        "source_id": "src_eia_hormuz_2025",
        "confidence": 0.84,
    },
    {
        "action_id": "action_sanctions_counterparty_review",
        "action_name": "Run sanctions counterparty review",
        "owner_role": "compliance analyst",
        "trigger": "Iran sanctions or escalation changes counterparty/shipping risk",
        "recommended_action": "Screen financial, shipping, insurance, and energy counterparties against current sanctions guidance before action.",
        "source_id": "src_ofac_iran_sanctions",
        "confidence": 0.82,
    },
]

OBJECT_SPECS = [
    {
        "key": "conflict_event",
        "name": "ConflictEvent",
        "description": "U.S.-Iran related conflict or sanctions event extracted from web sources.",
        "table": "us_iran_war_conflict_events",
        "primary_key": "event_id",
        "columns": ["event_id", "event_date", "event_type", "actors", "location", "summary", "source_id", "confidence"],
    },
    {
        "key": "economic_channel",
        "name": "EconomicChannel",
        "description": "Economic transmission channel such as oil flows, LNG flows, inflation/growth, or sanctions compliance.",
        "table": "us_iran_war_economic_channels",
        "primary_key": "channel_id",
        "columns": ["channel_id", "channel_name", "mechanism", "primary_metric", "source_id", "confidence"],
    },
    {
        "key": "country_exposure",
        "name": "CountryExposure",
        "description": "Country-level economic exposure to U.S.-Iran conflict channels.",
        "table": "us_iran_war_country_exposures",
        "primary_key": "country_id",
        "columns": ["country_id", "iso3", "country_name", "exposure_type", "exposure_level", "impact_summary", "key_metric", "source_id", "confidence"],
    },
    {
        "key": "recommended_action",
        "name": "RecommendedAction",
        "description": "Draft analyst action derived from a graph reasoning path.",
        "table": "us_iran_war_recommended_actions",
        "primary_key": "action_id",
        "columns": ["action_id", "action_name", "owner_role", "trigger", "recommended_action", "source_id", "confidence"],
    },
    {
        "key": "source_document",
        "name": "SourceDocument",
        "description": "Web source used as provenance for extracted facts and graph edges.",
        "table": "us_iran_war_web_sources",
        "primary_key": "source_id",
        "columns": ["source_id", "title", "publisher", "url", "query", "retrieved_at", "license_risk", "robots_risk", "summary", "confidence"],
    },
    {
        "key": "graph_edge",
        "name": "GraphEdge",
        "description": "Extracted edge connecting event, channel, country exposure, and recommended action nodes.",
        "table": "us_iran_war_graph_edges",
        "primary_key": "edge_id",
        "columns": ["edge_id", "source_node", "relation", "target_node", "source_id", "confidence"],
    },
]

LINK_SPECS = [
    ("conflict_event:1:n:economic_channel", "ConflictEvent 1:N EconomicChannel", "ConflictEvent", "EconomicChannel", "raises_risk_of", "Event risk propagates into economic transmission channels."),
    ("economic_channel:1:n:country_exposure", "EconomicChannel 1:N CountryExposure", "EconomicChannel", "CountryExposure", "impacts", "Economic channels affect countries through energy, trade, financial, and sanctions exposure."),
    ("country_exposure:n:m:recommended_action", "CountryExposure N:M RecommendedAction", "CountryExposure", "RecommendedAction", "requires_action", "Country exposures create analyst review or mitigation actions."),
    ("source_document:n:m:evidence", "SourceDocument N:M Evidence", "SourceDocument", "GraphEdge", "supports", "Web source documents support extracted graph edges and node facts."),
]


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def build_frames() -> dict[str, pd.DataFrame]:
    edges = [
        {
            "edge_id": f"edge_{idx:03d}",
            "source_node": source,
            "relation": relation,
            "target_node": target,
            "source_id": source_id,
            "confidence": confidence,
        }
        for idx, (source, relation, target, source_id, confidence) in enumerate(GRAPH_EDGES, start=1)
    ]
    return {
        "us_iran_war_web_sources": _frame(WEB_SOURCES),
        "us_iran_war_conflict_events": _frame(CONFLICT_EVENTS),
        "us_iran_war_economic_channels": _frame(ECONOMIC_CHANNELS),
        "us_iran_war_country_exposures": _frame(COUNTRY_EXPOSURES),
        "us_iran_war_recommended_actions": _frame(RECOMMENDED_ACTIONS),
        "us_iran_war_graph_edges": _frame(edges),
    }


def import_source_tables(source_db_url: str) -> dict[str, dict]:
    engine = create_engine(source_db_url)
    imported = {}
    for table, frame in build_frames().items():
        frame.to_sql(table, engine, if_exists="replace", index=False, chunksize=1000, method="multi")
        with engine.begin() as conn:
            for column in ("source_id", "event_id", "channel_id", "country_id", "action_id", "edge_id", "source_node", "target_node"):
                if column in frame.columns:
                    conn.execute(text(f"CREATE INDEX ix_{table}_{column} ON {table} (`{column}`(191))"))
            row_count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        imported[table] = {"rows": int(row_count), "columns": list(frame.columns)}
    return imported


def ensure_tenant(engine) -> None:
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
                "namespace": NAMESPACE,
                "display_name": TENANT_DISPLAY,
                "graph_database": GRAPH_DATABASE,
            },
        )


def _source_payload(source_ids: list[str]) -> list[dict]:
    by_id = {row["source_id"]: row for row in WEB_SOURCES}
    return [by_id[source_id] for source_id in source_ids if source_id in by_id]


def _seed_object(session, spec: dict) -> str:
    payload = {
        "object_name": spec["name"],
        "mapped_table_names": [spec["table"]],
        "primary_key": spec["primary_key"],
        "properties": spec["columns"],
        "extraction_method": "web_search_curated_snapshot",
        "retrieved_at": RETRIEVED_AT,
        "canonical_write_boundary": "draft_only_until_human_review",
    }
    artifact = upsert_artifact(
        session,
        artifact_type="object",
        natural_key=spec["key"],
        name=spec["name"],
        description=spec["description"],
        payload=payload,
        source_refs=[f"table:{spec['table']}", "web_search_snapshot:us_iran_war"],
        source_agent="USIranWarWebSearchImport",
        status="draft",
        confidence=0.84,
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
                "confidence": 0.88,
            },
            {
                "evidence_type": "web_search_provenance",
                "source_ref": "web_search_snapshot:us_iran_war",
                "summary": "Curated web search sources with URL, query, retrieval time, license risk, and confidence.",
                "payload": {"sources": WEB_SOURCES, "retrieved_at": RETRIEVED_AT},
                "confidence": 0.86,
            },
        ],
    )
    return artifact.canonical_key


def _seed_link(session, item: tuple[str, str, str, str, str, str]) -> str:
    key, name, source, target, relation, description = item
    payload = {
        "source_object_name": source,
        "target_object_name": target,
        "link_type": "semantic_graph_relation",
        "relation": relation,
        "description": description,
        "source_table": "us_iran_war_graph_edges",
        "target_table": "us_iran_war_graph_edges",
        "join_condition": f"graph_edges.relation = '{relation}'",
        "canonical_write_boundary": "draft_only_until_human_review",
    }
    artifact = upsert_artifact(
        session,
        artifact_type="link",
        natural_key=key,
        name=name,
        description=description,
        payload=payload,
        source_refs=["table:us_iran_war_graph_edges", "web_search_snapshot:us_iran_war"],
        source_agent="USIranWarWebSearchImport",
        status="draft",
        confidence=0.82,
        project_id=TENANT_ID,
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": "relationship_schema",
                "source_ref": payload["join_condition"],
                "summary": description,
                "payload": payload,
                "confidence": 0.82,
            },
            {
                "evidence_type": "web_search_provenance",
                "source_ref": "web_search_snapshot:us_iran_war",
                "summary": "Relationship extracted from web-search-supported graph edge rows.",
                "payload": {"sources": _source_payload(["src_eia_hormuz_2025", "src_crs_iran_2025", "src_imf_war_oil", "src_ofac_iran_sanctions"])},
                "confidence": 0.84,
            },
        ],
    )
    return artifact.canonical_key


def seed_metadata(metadata_db_url: str) -> dict:
    engine = create_engine(metadata_db_url)
    ensure_artifact_schema(engine)
    ensure_tenant(engine)
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
        tenant = conn.execute(
            text(
                """
                SELECT tenant_id, namespace, display_name, graph_database, status
                FROM aletheia_tenants
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": TENANT_ID},
        ).mappings().first()
    return {
        "tenant": dict(tenant) if tenant else None,
        "seeded": seeded,
        "artifact_counts": [dict(row) for row in counts],
    }


def write_dataset_files(data_dir: Path) -> dict[str, str]:
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for table, frame in build_frames().items():
        path = data_dir / f"{table}.csv"
        frame.to_csv(path, index=False)
        paths[table] = str(path)
    snapshot = {
        "tenant_id": TENANT_ID,
        "retrieved_at": RETRIEVED_AT,
        "search_queries": sorted({row["query"] for row in WEB_SOURCES}),
        "sources": WEB_SOURCES,
    }
    snapshot_path = data_dir / "web_search_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    paths["web_search_snapshot"] = str(snapshot_path)
    return paths


def import_dataset(data_dir: Path, source_db_url: str, metadata_db_url: str) -> dict:
    files = write_dataset_files(data_dir)
    source_tables = import_source_tables(source_db_url)
    metadata = seed_metadata(metadata_db_url)
    return {
        "tenant_id": TENANT_ID,
        "display_name": TENANT_DISPLAY,
        "namespace": NAMESPACE,
        "graph_database": GRAPH_DATABASE,
        "retrieved_at": RETRIEVED_AT,
        "data_files": files,
        "source_tables": source_tables,
        "metadata": metadata,
        "web_sources": WEB_SOURCES,
        "graph_summary": {
            "nodes": len(CONFLICT_EVENTS) + len(ECONOMIC_CHANNELS) + len(COUNTRY_EXPOSURES) + len(RECOMMENDED_ACTIONS),
            "edges": len(GRAPH_EDGES),
            "high_value_path": [
                "event_2025_june_us_iran_escalation",
                "channel_hormuz_oil_flow",
                "country_IND",
                "action_energy_importer_stress_test",
            ],
            "write_boundary": {
                "ontology_artifacts": "draft",
                "canonical_writes": False,
                "graph_writes": False,
                "web_sources_review_required": True,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import U.S.-Iran conflict economic-impact graph demo into Aletheia")
    parser.add_argument("--data-dir", default=str(ROOT / "datasets" / "us_iran_war"))
    parser.add_argument("--source-db-url", default=default_source_db_url())
    parser.add_argument("--metadata-db-url", default=default_metadata_db_url())
    parser.add_argument("--report-json", default=str(ROOT / "reports" / "us-iran-war-import-task175.json"))
    args = parser.parse_args()
    result = import_dataset(Path(args.data_dir), args.source_db_url, args.metadata_db_url)
    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"tenant={result['tenant_id']}")
    print(f"graph_database={result['graph_database']}")
    for table, meta in result["source_tables"].items():
        print(f"{table}: rows={meta['rows']} columns={len(meta['columns'])}")
    for row in result["metadata"]["artifact_counts"]:
        print(f"artifacts[{row['artifact_type']}][{row['status']}]={row['count']}")
    print(f"report_json={report_path}")


if __name__ == "__main__":
    main()
