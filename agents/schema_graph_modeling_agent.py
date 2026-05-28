import argparse
import json
import os
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

try:
    from ontology_artifacts import ensure_artifact_schema, replace_evidence, upsert_artifact
except ModuleNotFoundError:
    from agents.ontology_artifacts import ensure_artifact_schema, replace_evidence, upsert_artifact


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: bool = False
    references: str | None = None
    comment: str | None = None


class SchemaTable(BaseModel):
    schema_name: str | None = None
    table_name: str
    comment: str | None = None
    columns: list[SchemaColumn]
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[dict[str, Any]] = Field(default_factory=list)


class GraphNodeTypeDraft(BaseModel):
    key: str = Field(description="Stable snake_case node type key inferred from schema evidence")
    name: str = Field(description="Human readable node type name")
    description: str = Field(description="Business meaning supported by table/column evidence")
    mapped_tables: list[str] = Field(description="Physical tables supporting this node type")
    primary_key: str | None = Field(default=None, description="Best source primary key, if known")
    properties: list[str] = Field(default_factory=list, description="Source columns exposed as node properties")
    evidence: list[str] = Field(default_factory=list, description="Table/column/comment/FK evidence")
    confidence: float = Field(ge=0.0, le=1.0)


class GraphEdgeTypeDraft(BaseModel):
    key: str = Field(description="Stable snake_case edge type key inferred from schema evidence")
    name: str
    description: str
    source_node_key: str
    target_node_key: str
    cardinality: str | None = None
    source_table: str
    target_table: str
    join_condition: str | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class GraphModelDraft(BaseModel):
    node_types: list[GraphNodeTypeDraft] = Field(default_factory=list)
    edge_types: list[GraphEdgeTypeDraft] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    review_boundary: str = "draft_only_until_human_review"


@dataclass
class SchemaGraphModelingResult:
    schema: list[dict[str, Any]]
    draft: GraphModelDraft
    artifacts: list[str]


class SchemaGraphModelingAgent:
    """Infer graph ontology drafts from physical database schema using an LLM.

    This agent is intentionally generic. It does not contain tenant/domain
    vocabularies; source table names, column names, keys, comments, and optional
    samples are the only evidence allowed in the LLM prompt.
    """

    source_agent = "SchemaGraphModelingAgent"
    prompt_version = "schema_graph_modeling_v1"

    def __init__(
        self,
        source_db_url: str,
        metadata_db_url: str | None = None,
        *,
        model_name: str = "gpt-4o",
        project_id: str | None = None,
    ):
        self.source_engine = create_engine(source_db_url)
        self.metadata_engine = create_engine(metadata_db_url) if metadata_db_url else None
        self.model_name = model_name
        self.project_id = project_id or os.environ.get("ALETHEIA_TENANT", "default")
        if self.metadata_engine is not None:
            ensure_artifact_schema(self.metadata_engine)
            self.Session = sessionmaker(bind=self.metadata_engine)
        else:
            self.Session = None

    def inspect_source_schema(self, *, schema: str | None = None, include_tables: Iterable[str] | None = None) -> list[dict[str, Any]]:
        inspector = inspect(self.source_engine)
        include = set(include_tables or [])
        tables: list[dict[str, Any]] = []
        for table_name in inspector.get_table_names(schema=schema):
            if include and table_name not in include:
                continue
            try:
                table_comment = inspector.get_table_comment(table_name, schema=schema).get("text")
            except Exception:
                table_comment = None
            pk = inspector.get_pk_constraint(table_name, schema=schema) or {}
            pk_columns = pk.get("constrained_columns") or []
            fk_constraints = inspector.get_foreign_keys(table_name, schema=schema) or []
            fk_by_column: dict[str, str] = {}
            for fk in fk_constraints:
                referred_table = fk.get("referred_table")
                referred_columns = fk.get("referred_columns") or []
                for column, referred_column in zip(fk.get("constrained_columns") or [], referred_columns):
                    fk_by_column[column] = f"{referred_table}.{referred_column}"
            columns = []
            for col in inspector.get_columns(table_name, schema=schema):
                col_name = col["name"]
                columns.append(
                    SchemaColumn(
                        name=col_name,
                        data_type=str(col["type"]),
                        nullable=bool(col.get("nullable", True)),
                        primary_key=col_name in pk_columns,
                        foreign_key=col_name in fk_by_column,
                        references=fk_by_column.get(col_name),
                        comment=col.get("comment"),
                    ).model_dump()
                )
            tables.append(
                SchemaTable(
                    schema_name=schema,
                    table_name=table_name,
                    comment=table_comment,
                    columns=[SchemaColumn(**col) for col in columns],
                    primary_key=list(pk_columns),
                    foreign_keys=fk_constraints,
                ).model_dump()
            )
        return tables

    def build_prompt(self, schema_dump: list[dict[str, Any]]) -> str:
        return f"""
You are Aletheia's Schema Graph Modeling Agent.

Convert the raw physical database schema below into a draft graph ontology.

Hard rule:
- Do not use any built-in tenant/domain vocabulary, demo labels, or prior project-specific terms.
- Infer node types, edge types, link types, names, and descriptions only from the provided schema evidence: table names, column names, primary keys, foreign keys, comments, and optional samples.
- Do not invent review/finding/action/insight nodes unless the source schema explicitly contains those concepts.
- If a concept is ambiguous, put it in rejected_candidates or assumptions instead of promoting it to a node/edge.
- Every node/edge must include evidence strings and confidence.
- Output is draft-only and requires human review before canonical ontology or formal graph writes.

Return a GraphModelDraft JSON object with:
- node_types
- edge_types
- rejected_candidates
- assumptions
- review_boundary

Raw schema:
{json.dumps(schema_dump, ensure_ascii=False, indent=2, sort_keys=True)}
""".strip()

    def infer_graph_model_with_llm(self, schema_dump: list[dict[str, Any]]) -> GraphModelDraft:
        from litellm import completion
        import instructor

        client = instructor.from_litellm(completion)
        return client.chat.completions.create(
            model=self.model_name,
            response_model=GraphModelDraft,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You infer graph ontology drafts from database schema evidence. "
                        "You must not rely on hardcoded project/domain terms."
                    ),
                },
                {"role": "user", "content": self.build_prompt(schema_dump)},
            ],
            temperature=0.1,
        )

    def artifact_specs(self, draft: GraphModelDraft) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for node in draft.node_types:
            specs.append(
                {
                    "artifact_type": "object",
                    "natural_key": node.key,
                    "name": node.name,
                    "description": node.description,
                    "payload": {
                        "object_name": node.name,
                        "mapped_table_names": node.mapped_tables,
                        "primary_key": node.primary_key,
                        "properties": node.properties,
                        "llm_inferred": True,
                        "prompt_version": self.prompt_version,
                        "canonical_write_boundary": draft.review_boundary,
                    },
                    "source_refs": [f"table:{table}" for table in node.mapped_tables],
                    "evidence": node.evidence,
                    "confidence": node.confidence,
                }
            )
        for edge in draft.edge_types:
            specs.append(
                {
                    "artifact_type": "link",
                    "natural_key": edge.key,
                    "name": edge.name,
                    "description": edge.description,
                    "payload": {
                        "source_object_key": edge.source_node_key,
                        "target_object_key": edge.target_node_key,
                        "link_type": edge.cardinality,
                        "source_table": edge.source_table,
                        "target_table": edge.target_table,
                        "join_condition": edge.join_condition,
                        "llm_inferred": True,
                        "prompt_version": self.prompt_version,
                        "canonical_write_boundary": draft.review_boundary,
                    },
                    "source_refs": [f"table:{edge.source_table}", f"table:{edge.target_table}"],
                    "evidence": edge.evidence,
                    "confidence": edge.confidence,
                }
            )
        return specs

    def persist_draft_artifacts(self, draft: GraphModelDraft) -> list[str]:
        if self.Session is None:
            raise ValueError("metadata_db_url is required to persist artifacts")
        canonical_keys: list[str] = []
        with self.Session() as session:
            for spec in self.artifact_specs(draft):
                artifact = upsert_artifact(
                    session,
                    artifact_type=spec["artifact_type"],
                    natural_key=spec["natural_key"],
                    name=spec["name"],
                    description=spec["description"],
                    payload=spec["payload"],
                    source_refs=spec["source_refs"],
                    source_agent=self.source_agent,
                    project_id=self.project_id,
                    confidence=spec["confidence"],
                    status="draft",
                )
                replace_evidence(
                    session,
                    artifact,
                    [
                        {
                            "evidence_type": "schema_graph_inference",
                            "source_ref": source_ref,
                            "summary": evidence,
                            "payload": {
                                "prompt_version": self.prompt_version,
                                "artifact": spec["natural_key"],
                                "review_boundary": draft.review_boundary,
                            },
                            "confidence": spec["confidence"],
                        }
                        for source_ref, evidence in zip(spec["source_refs"] or ["schema"], spec["evidence"] or [spec["description"]])
                    ],
                )
                canonical_keys.append(artifact.canonical_key)
            session.commit()
        return canonical_keys

    def run(
        self,
        *,
        schema: str | None = None,
        include_tables: Iterable[str] | None = None,
        persist: bool = False,
    ) -> SchemaGraphModelingResult:
        schema_dump = self.inspect_source_schema(schema=schema, include_tables=include_tables)
        draft = self.infer_graph_model_with_llm(schema_dump)
        artifacts = self.persist_draft_artifacts(draft) if persist else []
        return SchemaGraphModelingResult(schema=schema_dump, draft=draft, artifacts=artifacts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Infer draft graph ontology from raw database schema with an LLM")
    parser.add_argument("--source", default=os.environ.get("ALETHEIA_MYSQL_URL", "mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/aletheia_test_data"))
    parser.add_argument("--metadata", default=os.environ.get("ALETHEIA_PG_URL", "postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology"))
    parser.add_argument("--model", default=os.environ.get("ALETHEIA_SCHEMA_GRAPH_MODEL", "gpt-4o"))
    parser.add_argument("--tenant", default=os.environ.get("ALETHEIA_TENANT", "default"))
    parser.add_argument("--table", action="append", dest="tables", help="Restrict inference to a table; can be repeated")
    parser.add_argument("--persist", action="store_true", help="Persist inferred artifacts as draft ontology proposals")
    parser.add_argument("--report-json", default=None)
    args = parser.parse_args()

    agent = SchemaGraphModelingAgent(
        source_db_url=args.source,
        metadata_db_url=args.metadata,
        model_name=args.model,
        project_id=args.tenant,
    )
    result = agent.run(include_tables=args.tables, persist=args.persist)
    output = {
        "tenant": args.tenant,
        "prompt_version": agent.prompt_version,
        "schema_table_count": len(result.schema),
        "draft": result.draft.model_dump(),
        "artifacts": result.artifacts,
    }
    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
