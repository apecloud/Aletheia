import argparse
from copy import deepcopy
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import create_engine, inspect, text
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
    row_count: int | None = None
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    column_value_samples: dict[str, list[Any]] = Field(default_factory=dict)


SCHEMA_GRAPH_CONTRACT_VERSION = "schema_graph_contract_v1"
SCHEMA_GRAPH_MIN_COMPATIBLE_VERSION = "schema_graph_contract_v1"
SCHEMA_GRAPH_REVIEW_BOUNDARY = "draft_only_until_human_review"
SCHEMA_GRAPH_CONTRACT_CONSTRAINTS = {
    "entity_type": [
        "node_type evidence must include at least one source-backed entry",
        "node_type keys are stable graph keys derived from schema evidence",
        "primary_key, when present, names the best source primary key",
        "mapped_tables, primary_key, and properties must reference source schema tables and columns",
        "confidence must be between 0 and 1 inclusive",
    ],
    "relation_type": [
        "edge_type evidence must include at least one source-backed entry",
        "source_node_key and target_node_key must reference node_types when node_types are present",
        "source_table, target_table, join columns, and edge properties must reference source schema tables and columns",
        "metrics and derived values belong in edge properties rather than separate ontology objects",
        "confidence must be between 0 and 1 inclusive",
    ],
    "review": [
        "drafts remain draft-only until human review",
        "canonical ontology writes are not allowed from this agent",
        "artifact specs carry schema_contract_version and prompt_version",
    ],
}
SCHEMA_GRAPH_COMPATIBILITY = {
    "breaking_changes_require_major_version": [
        "removing or renaming node/edge fields consumed by artifact_specs",
        "changing review_boundary semantics",
        "tightening endpoint, evidence, confidence, or rejected-candidate constraints for persisted drafts",
        "changing object/link artifact payload meanings",
    ],
    "minor_compatible_changes": [
        "adding optional fields to node_types, edge_types, or rejected_candidates",
        "adding artifact payload metadata while preserving existing keys",
        "loosening validation bounds without invalidating existing persisted drafts",
    ],
    "migration_required_for": [
        "natural-key or primary-key reinterpretation",
        "source_node_key or target_node_key semantic changes",
        "canonical_write_boundary or review-gate behavior changes",
    ],
}


@dataclass(frozen=True)
class SchemaTraceabilityIssue:
    code: str
    path: str
    message: str
    artifact_key: str | None = None
    table: str | None = None
    column: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.artifact_key:
            payload["artifact_key"] = self.artifact_key
        if self.table:
            payload["table"] = self.table
        if self.column:
            payload["column"] = self.column
        return payload


class SchemaTraceabilityValidationError(ValueError):
    def __init__(self, errors: list[SchemaTraceabilityIssue]):
        self.errors = [error.as_dict() for error in errors]
        super().__init__(
            "schema traceability validation failed: "
            + "; ".join(f"{error.code} at {error.path}" for error in errors[:5])
        )


@dataclass
class SchemaTraceabilityValidationResult:
    errors: list[SchemaTraceabilityIssue]

    @property
    def valid(self) -> bool:
        return not self.errors

    def error_dicts(self) -> list[dict[str, Any]]:
        return [error.as_dict() for error in self.errors]

    def raise_for_errors(self) -> None:
        if self.errors:
            raise SchemaTraceabilityValidationError(self.errors)


@dataclass(frozen=True)
class OntologyConsistencyIssue:
    code: str
    path: str
    message: str
    artifact_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "artifact_key": self.artifact_key or "",
        }


class OntologyConsistencyValidationError(ValueError):
    def __init__(self, errors: list[OntologyConsistencyIssue]):
        self.errors = [error.as_dict() for error in errors]
        super().__init__(
            "ontology consistency validation failed: "
            + "; ".join(f"{error.code} at {error.path}" for error in errors[:5])
        )


@dataclass
class OntologyConsistencyValidationResult:
    errors: list[OntologyConsistencyIssue]

    @property
    def valid(self) -> bool:
        return not self.errors

    def error_dicts(self) -> list[dict[str, Any]]:
        return [error.as_dict() for error in self.errors]

    def raise_for_errors(self) -> None:
        if self.errors:
            raise OntologyConsistencyValidationError(self.errors)


class GraphNodeTypeDraft(BaseModel):
    key: str = Field(description="Stable snake_case node type key inferred from schema evidence")
    name: str = Field(description="Human readable node type name")
    description: str = Field(description="Business meaning supported by table/column evidence")
    mapped_tables: list[str] = Field(description="Physical tables supporting this node type")
    primary_key: str | None = Field(default=None, description="Best source primary key, if known")
    properties: list[str] = Field(default_factory=list, description="Source columns exposed as node properties")
    evidence: list[str] = Field(default_factory=list, description="Table/column/comment/FK evidence")
    confidence: float = Field(ge=0.0, le=1.0)
    subclass_of: list[str] = Field(default_factory=list, description="Parent node type keys this type inherits from")
    disjoint_with: list[str] = Field(default_factory=list, description="Node type keys that must not share instances with this type")

    @field_validator("evidence")
    @classmethod
    def evidence_must_be_explicit(cls, value: list[str]) -> list[str]:
        if not any(str(item or "").strip() for item in value):
            raise ValueError("node_type evidence must include at least one source-backed entry")
        return value


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
    properties: list[str] = Field(default_factory=list, description="Source columns exposed as edge/fact properties")
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    domain: list[str] = Field(default_factory=list, description="Allowed source node type keys for this relation (defaults to source_node_key)")
    range: list[str] = Field(default_factory=list, description="Allowed target node type keys for this relation (defaults to target_node_key)")

    @field_validator("evidence")
    @classmethod
    def evidence_must_be_explicit(cls, value: list[str]) -> list[str]:
        if not any(str(item or "").strip() for item in value):
            raise ValueError("edge_type evidence must include at least one source-backed entry")
        return value


class GraphModelDraft(BaseModel):
    schema_version: str = Field(default=SCHEMA_GRAPH_CONTRACT_VERSION)
    min_compatible_version: str = Field(default=SCHEMA_GRAPH_MIN_COMPATIBLE_VERSION)
    node_types: list[GraphNodeTypeDraft] = Field(default_factory=list)
    edge_types: list[GraphEdgeTypeDraft] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    review_boundary: str = SCHEMA_GRAPH_REVIEW_BOUNDARY
    contract_constraints: dict[str, list[str]] = Field(default_factory=lambda: deepcopy(SCHEMA_GRAPH_CONTRACT_CONSTRAINTS))
    compatibility: dict[str, list[str]] = Field(default_factory=lambda: deepcopy(SCHEMA_GRAPH_COMPATIBILITY))

    @model_validator(mode="after")
    def validate_draft_contract(self) -> "GraphModelDraft":
        if self.schema_version != SCHEMA_GRAPH_CONTRACT_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_GRAPH_CONTRACT_VERSION}")
        if self.min_compatible_version != SCHEMA_GRAPH_MIN_COMPATIBLE_VERSION:
            raise ValueError(f"min_compatible_version must be {SCHEMA_GRAPH_MIN_COMPATIBLE_VERSION}")
        if self.review_boundary != SCHEMA_GRAPH_REVIEW_BOUNDARY:
            raise ValueError(f"review_boundary must be {SCHEMA_GRAPH_REVIEW_BOUNDARY}")

        node_keys = {node.key for node in self.node_types}
        if node_keys:
            for edge in self.edge_types:
                if edge.source_node_key not in node_keys:
                    raise ValueError(f"edge_type {edge.key} source_node_key must reference a node_type")
                if edge.target_node_key not in node_keys:
                    raise ValueError(f"edge_type {edge.key} target_node_key must reference a node_type")

        for index, candidate in enumerate(self.rejected_candidates):
            if not str(candidate.get("name") or "").strip():
                raise ValueError(f"rejected_candidates[{index}].name is required")
            if not str(candidate.get("reason") or "").strip():
                raise ValueError(f"rejected_candidates[{index}].reason is required")
            if not str(candidate.get("suggested_graph_treatment") or "").strip():
                raise ValueError(f"rejected_candidates[{index}].suggested_graph_treatment is required")
        return self


@dataclass
class SchemaGraphModelingResult:
    schema: list[dict[str, Any]]
    draft: GraphModelDraft
    artifacts: list[str]


def stable_graph_key(value: str) -> str:
    """Return a stable snake_case key without relying on domain vocabulary."""
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", str(value or "").strip()).strip("_").lower()
    return normalized or "unnamed"


def _column_names_for_tables(metadata_dump: list[dict[str, Any]], mapped_tables: Iterable[str]) -> list[str]:
    mapped = set(mapped_tables)
    columns: list[str] = []
    for table in metadata_dump:
        if table.get("table_name") not in mapped:
            continue
        for column in table.get("columns") or []:
            name = column.get("column") or column.get("name")
            if name and name not in columns:
                columns.append(name)
    return columns


def _table_evidence(metadata_dump: list[dict[str, Any]], mapped_tables: Iterable[str]) -> list[str]:
    mapped = set(mapped_tables)
    evidence: list[str] = []
    for table in metadata_dump:
        table_name = table.get("table_name")
        if table_name not in mapped:
            continue
        comment = table.get("table_comment")
        if comment:
            evidence.append(f"table:{table_name} comment: {comment}")
        for column in table.get("columns") or []:
            name = column.get("column") or column.get("name")
            data_type = column.get("type") or column.get("data_type")
            semantic_type = column.get("semantic_type")
            hint = f"table:{table_name} column:{name}"
            if data_type:
                hint += f" type:{data_type}"
            if semantic_type and semantic_type != "Unknown":
                hint += f" semantic_type:{semantic_type}"
            evidence.append(hint)
    return evidence


def _schema_table_column_index(schema_dump: list[dict[str, Any]]) -> dict[str, set[str]]:
    table_columns: dict[str, set[str]] = {}
    for table in schema_dump:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            continue
        columns = set()
        for column in table.get("columns") or []:
            column_name = str(column.get("name") or column.get("column") or "").strip()
            if column_name:
                columns.add(column_name)
        table_columns[table_name] = columns
    return table_columns


def _qualified_column_refs(value: Any) -> list[tuple[str, str]]:
    if value is None:
        return []
    text_value = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    return re.findall(r"\b([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z_][A-Za-z0-9_$]*)\b", text_value)


def _artifact_key(artifact_type: str, natural_key: str) -> str:
    return f"{artifact_type}:{natural_key}"


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

    def _profile_table(self, table_name: str, *, schema: str | None = None, sample_size: int = 3) -> dict[str, Any]:
        sample_size = max(0, min(int(sample_size), 10))
        qualified = f"{schema}.{table_name}" if schema else table_name
        profile: dict[str, Any] = {"row_count": None, "sample_rows": [], "column_value_samples": {}}
        try:
            with self.source_engine.connect() as conn:
                profile["row_count"] = int(conn.execute(text(f"SELECT COUNT(*) FROM {qualified}")).scalar() or 0)
                if sample_size:
                    rows = conn.execute(text(f"SELECT * FROM {qualified} LIMIT :limit"), {"limit": sample_size}).mappings().all()
                    profile["sample_rows"] = [
                        {key: str(value)[:120] if value is not None else None for key, value in dict(row).items()}
                        for row in rows
                    ]
        except Exception as exc:
            profile["profile_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return profile

    def inspect_source_schema(
        self,
        *,
        schema: str | None = None,
        include_tables: Iterable[str] | None = None,
        include_profile: bool = True,
        sample_size: int = 3,
    ) -> list[dict[str, Any]]:
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
            table_payload = SchemaTable(
                schema_name=schema,
                table_name=table_name,
                comment=table_comment,
                columns=[SchemaColumn(**col) for col in columns],
                primary_key=list(pk_columns),
                foreign_keys=fk_constraints,
            ).model_dump()
            if include_profile:
                table_payload.update(self._profile_table(table_name, schema=schema, sample_size=sample_size))
            tables.append(
                table_payload
            )
        return tables

    def build_prompt(self, schema_dump: list[dict[str, Any]]) -> str:
        return f"""
You are Aletheia's Schema Graph Modeling Agent.

Convert the raw physical database schema below into a draft graph ontology.

Hard rules:
- Do not use any built-in tenant/domain vocabulary, demo labels, or prior project-specific terms.
- Infer node types, edge types, link types, names, and descriptions only from the provided schema evidence: table names, column names, primary keys, foreign keys, comments, and optional samples.
- Keep ontology types distinct from graph nodes and fact/event instances.
- Do not invent review/finding/action/insight nodes unless the source schema explicitly contains durable agent/object concepts for them.
- If a concept is ambiguous, put it in rejected_candidates or assumptions instead of promoting it to a node/edge.
- Every node/edge must include evidence strings and confidence.
- Output is draft-only and requires human review before canonical ontology or formal graph writes.

Ontology policy for this system:
- A draft ontology object is a continuant-like, identity-bearing object type: a person, organization, account, asset, system, place, product, policy, contract, service, model, controller, or other entity that can persist through time while maintaining identity.
- An ontology object type may be active/agentic: it may receive events/facts, hold state, bear responsibility, make decisions, trigger workflows, or produce actions. Prefer object types that can participate in many events and support actions over one-off occurrence types.
- A graph node is an instance-level representation. Graph nodes may include event occurrences, fact assertions, measurements, observations, findings, evidence records, or derived risk signals, but those should not be promoted to ontology object types by default.
- An event is an occurrent-like occurrence: it happens/unfolds in time, has timestamps or intervals, and has participants. In this system, event rows should normally be modeled as graph/fact nodes or as evidence attached to relationships, not as ontology objects.
- A fact is an assertion/observation/provenance record about objects or relationships. In this system, fact rows should normally be graph/fact nodes, edge evidence, or properties, not ontology objects.
- Promote an event/fact table to an ontology object only if the schema clearly treats it as a durable managed object with its own lifecycle, stable identity across multiple events, ownership/responsibility, status transitions, and actions beyond recording that one occurrence. Otherwise reject it as graph_node_candidate, event_candidate, fact_candidate, evidence_candidate, or property_candidate.
- Use edge_types for durable relationship types between ontology objects. Do not create an ontology link merely because two columns co-occur in one event/fact row; require key/FK/schema evidence or a repeated business relationship supported by the table structure.
- Preserve source information even when you reject an event/fact/measurement table as an ontology object: map non-identity columns to node properties, edge properties, graph/fact treatment notes, or rejected_candidates. Do not silently drop source columns.
- For association/fact tables that connect two durable objects, put quantitative metrics, risk scores, statuses, timestamps, likelihoods, severities, and provenance columns in edge_type.properties. The edge is the relationship; those columns are facts about that relationship.
- For measurement/result tables keyed by the same durable object pair, prefer edge_type.properties over creating separate ontology objects. If multiple source tables support the same object pair, create separate relationship/fact edge types or explicitly record the table in rejected_candidates with its suggested graph treatment and property columns.
- Keep the durable ontology small. Do not create a separate ontology object for every metric, score, result row, claim, finding, or analytic conclusion.
- Situational claims, observations, metric changes, impact claims, indicator claims, evidence records, and recommendations are graph/fact proposal types, not durable ontology object types, unless the physical schema shows they are managed business objects with their own lifecycle and stable identity.
- When a source table primarily represents a measurement/result/observation over durable objects, model the durable objects and relationship first. Put observed values and derived quantities in edge_type.properties, and add rejected_candidates entries that describe the suggested graph/fact proposal treatment in plain language without using a fixed ontology class name.
- Relation names should describe business semantics derived from schema evidence, not table mechanics or cardinality. Prefer concise verb phrases only when the schema supports a durable predicate. Do not use "N:M", "join", "row", "result", or table names as relation semantics.
- If multiple tables describe the same durable object pair with different measurements, do not create duplicate object types for each table. Either add a separate edge type only when the relationship meaning differs, or keep the additional columns as properties/evidence on the same relationship and document the treatment.

Decision tests before creating each node_type:
1. Does this candidate maintain identity beyond a single timestamped occurrence or assertion?
2. Can many event/fact records refer to the same candidate instance?
3. Can it own state, receive events/facts, participate in actions, or be responsible for actions?
4. Is there schema evidence for a stable key, master table, foreign-key target, or durable lifecycle/status?
If the answer is mostly no, do not create node_type; place the concept in rejected_candidates with the reason and suggested graph/fact treatment.

Decision tests before creating each edge_type:
1. Are both endpoints durable ontology object types?
2. Does the relation express a stable business relationship rather than one row's measurement or analytic result?
3. Is there schema evidence from keys, joins, repeated columns, or table purpose?
4. Are metrics and derived values represented as edge properties rather than separate object types?
If the relation is only a measurement/assertion/claim, put it in rejected_candidates with suggested graph/fact treatment instead of inventing a durable ontology link.

Return a GraphModelDraft JSON object with:
- schema_version
- min_compatible_version
- node_types
- edge_types
- rejected_candidates
- assumptions
- review_boundary
- contract_constraints
- compatibility

Coverage requirement:
- Every source table and every non-empty source column must be traceable in the output as one of: node_type.primary_key, node_type.properties, edge_type.properties, edge join/key evidence, rejected_candidates[].suggested_graph_treatment, or assumptions.
- If a column is intentionally excluded, state why in rejected_candidates or assumptions.

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

    @classmethod
    def draft_from_legacy_object_model(cls, ontology_draft: Any, metadata_dump: list[dict[str, Any]]) -> GraphModelDraft:
        """Adapt the old ObjectModelerAgent output into the unified graph contract.

        Phase 1 keeps existing object-modeling call sites usable, but the
        persisted contract is the same draft graph model used by the new schema
        modeling agent. This adapter must stay vocabulary-free: it only copies
        names/tables/descriptions supplied by the LLM and schema metadata.
        """
        node_types: list[GraphNodeTypeDraft] = []
        for obj in getattr(ontology_draft, "business_objects", []) or []:
            name = getattr(obj, "name", "")
            mapped_tables = list(getattr(obj, "mapped_table_names", []) or [])
            columns = _column_names_for_tables(metadata_dump, mapped_tables)
            evidence = _table_evidence(metadata_dump, mapped_tables)
            node_types.append(
                GraphNodeTypeDraft(
                    key=stable_graph_key(name),
                    name=name,
                    description=getattr(obj, "description", "") or f"Business object inferred from {', '.join(mapped_tables)}.",
                    mapped_tables=mapped_tables,
                    primary_key=None,
                    properties=columns,
                    evidence=evidence or [f"legacy object model mapped tables: {', '.join(mapped_tables)}"],
                    confidence=0.75,
                )
            )
        return GraphModelDraft(node_types=node_types)

    @classmethod
    def draft_from_legacy_link_model(cls, links_draft: Any, ontology_dump: list[dict[str, Any]]) -> GraphModelDraft:
        """Adapt the old LinkWeaverAgent output into unified graph edge drafts."""
        table_by_object = {
            item.get("object_name"): list(item.get("underlying_tables") or [])
            for item in ontology_dump
            if item.get("object_name")
        }
        edge_types: list[GraphEdgeTypeDraft] = []
        for link in getattr(links_draft, "links", []) or []:
            source_name = getattr(link, "source_object_name", "")
            target_name = getattr(link, "target_object_name", "")
            source_tables = table_by_object.get(source_name) or []
            target_tables = table_by_object.get(target_name) or []
            link_type = getattr(link, "link_type", None)
            evidence = [
                f"legacy link model source object: {source_name} tables: {', '.join(source_tables)}",
                f"legacy link model target object: {target_name} tables: {', '.join(target_tables)}",
            ]
            edge_types.append(
                GraphEdgeTypeDraft(
                    key=stable_graph_key(f"{source_name}_{link_type or 'related'}_{target_name}"),
                    name=f"{source_name} {link_type or 'related_to'} {target_name}",
                    description=getattr(link, "description", "") or "Relationship inferred by legacy link model.",
                    source_node_key=stable_graph_key(source_name),
                    target_node_key=stable_graph_key(target_name),
                    cardinality=link_type,
                    source_table=source_tables[0] if source_tables else source_name,
                    target_table=target_tables[0] if target_tables else target_name,
                    join_condition=None,
                    evidence=evidence,
                    confidence=0.7,
                )
            )
        return GraphModelDraft(edge_types=edge_types)

    @classmethod
    def artifact_specs_for_draft(cls, draft: GraphModelDraft, *, prompt_version: str | None = None) -> list[dict[str, Any]]:
        prompt_version = prompt_version or cls.prompt_version
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
                        "prompt_version": prompt_version,
                        "schema_contract_version": draft.schema_version,
                        "min_compatible_schema_contract_version": draft.min_compatible_version,
                        "schema_contract_constraints": draft.contract_constraints,
                        "schema_contract_compatibility": draft.compatibility,
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
                        "properties": edge.properties,
                        "edge_properties": edge.properties,
                        "llm_inferred": True,
                        "prompt_version": prompt_version,
                        "schema_contract_version": draft.schema_version,
                        "min_compatible_schema_contract_version": draft.min_compatible_version,
                        "schema_contract_constraints": draft.contract_constraints,
                        "schema_contract_compatibility": draft.compatibility,
                        "canonical_write_boundary": draft.review_boundary,
                    },
                    "source_refs": [f"table:{edge.source_table}", f"table:{edge.target_table}"],
                    "evidence": edge.evidence,
                    "confidence": edge.confidence,
                }
            )
        return specs

    def artifact_specs(self, draft: GraphModelDraft) -> list[dict[str, Any]]:
        return self.artifact_specs_for_draft(draft, prompt_version=self.prompt_version)

    @classmethod
    def validate_schema_traceability(
        cls,
        draft: GraphModelDraft,
        schema_dump: list[dict[str, Any]],
        *,
        require_full_column_coverage: bool = True,
    ) -> SchemaTraceabilityValidationResult:
        table_columns = _schema_table_column_index(schema_dump)
        errors: list[SchemaTraceabilityIssue] = []
        traced_tables: set[str] = set()
        traced_columns: set[tuple[str, str]] = set()
        nodes_by_key = {node.key: node for node in draft.node_types}

        def add_error(
            code: str,
            path: str,
            message: str,
            *,
            artifact_key: str | None = None,
            table: str | None = None,
            column: str | None = None,
        ) -> None:
            errors.append(
                SchemaTraceabilityIssue(
                    code=code,
                    path=path,
                    message=message,
                    artifact_key=artifact_key,
                    table=table,
                    column=column,
                )
            )

        def trace_table(table: str, path: str, artifact_key: str) -> bool:
            if table not in table_columns:
                add_error(
                    "unknown_table",
                    path,
                    f"{artifact_key} references unknown source table {table!r}",
                    artifact_key=artifact_key,
                    table=table,
                )
                return False
            traced_tables.add(table)
            return True

        def trace_column_ref(
            value: str | None,
            allowed_tables: Iterable[str],
            path: str,
            artifact_key: str,
            *,
            required: bool = False,
        ) -> None:
            if not value:
                if required:
                    add_error(
                        "missing_column_ref",
                        path,
                        f"{artifact_key} must reference a source column",
                        artifact_key=artifact_key,
                    )
                return

            column_ref = str(value).strip()
            allowed = [table for table in allowed_tables if table]
            qualified_refs = _qualified_column_refs(column_ref)
            if qualified_refs:
                for table, column in qualified_refs:
                    if table not in allowed:
                        add_error(
                            "column_table_mismatch",
                            path,
                            f"{artifact_key} column reference {table}.{column} is outside allowed tables {allowed}",
                            artifact_key=artifact_key,
                            table=table,
                            column=column,
                        )
                        continue
                    if trace_table(table, path, artifact_key):
                        if column not in table_columns[table]:
                            add_error(
                                "unknown_column",
                                path,
                                f"{artifact_key} references unknown source column {table}.{column}",
                                artifact_key=artifact_key,
                                table=table,
                                column=column,
                            )
                        else:
                            traced_columns.add((table, column))
                return

            matches = [table for table in allowed if table in table_columns and column_ref in table_columns[table]]
            if not matches:
                add_error(
                    "unknown_column",
                    path,
                    f"{artifact_key} references source column {column_ref!r} that is not present on allowed tables {allowed}",
                    artifact_key=artifact_key,
                    column=column_ref,
                )
                return
            for table in matches:
                traced_tables.add(table)
                traced_columns.add((table, column_ref))

        def trace_text_refs(value: Any, path: str, artifact_key: str, allowed_tables: Iterable[str] | None = None) -> None:
            allowed = set(allowed_tables or table_columns.keys())
            for table, column in _qualified_column_refs(value):
                if table not in allowed:
                    add_error(
                        "column_table_mismatch",
                        path,
                        f"{artifact_key} column reference {table}.{column} is outside allowed tables {sorted(allowed)}",
                        artifact_key=artifact_key,
                        table=table,
                        column=column,
                    )
                    continue
                if trace_table(table, path, artifact_key):
                    if column not in table_columns[table]:
                        add_error(
                            "unknown_column",
                            path,
                            f"{artifact_key} references unknown source column {table}.{column}",
                            artifact_key=artifact_key,
                            table=table,
                            column=column,
                        )
                    else:
                        traced_columns.add((table, column))

        for node_index, node in enumerate(draft.node_types):
            artifact_key = _artifact_key("object", node.key)
            if not node.mapped_tables:
                add_error(
                    "missing_mapped_table",
                    f"node_types[{node_index}].mapped_tables",
                    f"{artifact_key} must map to at least one source table",
                    artifact_key=artifact_key,
                )
            for table_index, table in enumerate(node.mapped_tables):
                trace_table(table, f"node_types[{node_index}].mapped_tables[{table_index}]", artifact_key)
            trace_column_ref(
                node.primary_key,
                node.mapped_tables,
                f"node_types[{node_index}].primary_key",
                artifact_key,
                required=False,
            )
            for property_index, property_name in enumerate(node.properties):
                trace_column_ref(
                    property_name,
                    node.mapped_tables,
                    f"node_types[{node_index}].properties[{property_index}]",
                    artifact_key,
                    required=True,
                )
            trace_text_refs(node.evidence, f"node_types[{node_index}].evidence", artifact_key)

        for edge_index, edge in enumerate(draft.edge_types):
            artifact_key = _artifact_key("link", edge.key)
            allowed_tables = [edge.source_table, edge.target_table]
            source_node = nodes_by_key.get(edge.source_node_key)
            target_node = nodes_by_key.get(edge.target_node_key)
            if source_node and edge.source_table not in source_node.mapped_tables:
                add_error(
                    "edge_source_table_mismatch",
                    f"edge_types[{edge_index}].source_table",
                    f"{artifact_key} source_table {edge.source_table!r} is not mapped by source node {edge.source_node_key!r}",
                    artifact_key=artifact_key,
                    table=edge.source_table,
                )
            if target_node and edge.target_table not in target_node.mapped_tables:
                add_error(
                    "edge_target_table_mismatch",
                    f"edge_types[{edge_index}].target_table",
                    f"{artifact_key} target_table {edge.target_table!r} is not mapped by target node {edge.target_node_key!r}",
                    artifact_key=artifact_key,
                    table=edge.target_table,
                )
            trace_table(edge.source_table, f"edge_types[{edge_index}].source_table", artifact_key)
            trace_table(edge.target_table, f"edge_types[{edge_index}].target_table", artifact_key)
            for property_index, property_name in enumerate(edge.properties):
                trace_column_ref(
                    property_name,
                    allowed_tables,
                    f"edge_types[{edge_index}].properties[{property_index}]",
                    artifact_key,
                    required=True,
                )
            trace_text_refs(edge.join_condition, f"edge_types[{edge_index}].join_condition", artifact_key, allowed_tables)
            trace_text_refs(edge.evidence, f"edge_types[{edge_index}].evidence", artifact_key, allowed_tables)

        for candidate_index, candidate in enumerate(draft.rejected_candidates):
            name = str(candidate.get("name") or f"candidate_{candidate_index}")
            trace_text_refs(
                candidate,
                f"rejected_candidates[{candidate_index}]",
                _artifact_key("rejected_candidate", stable_graph_key(name)),
            )
        for assumption_index, assumption in enumerate(draft.assumptions):
            trace_text_refs(
                assumption,
                f"assumptions[{assumption_index}]",
                _artifact_key("assumption", str(assumption_index)),
            )

        if require_full_column_coverage:
            for table, columns in table_columns.items():
                if table not in traced_tables:
                    add_error(
                        "untraced_table",
                        f"schema[{table}].table_name",
                        f"source table {table!r} is not traceable to any draft node, edge, rejected candidate, or assumption",
                        table=table,
                    )
                for column in sorted(columns):
                    if (table, column) not in traced_columns:
                        add_error(
                            "untraced_column",
                            f"schema[{table}].columns[{column}]",
                            f"source column {table}.{column} is not traceable to any draft property, key, join/evidence, rejected candidate, or assumption",
                            table=table,
                            column=column,
                        )

        return SchemaTraceabilityValidationResult(errors)

    @classmethod
    def validate_ontology_consistency(
        cls,
        draft: GraphModelDraft,
    ) -> OntologyConsistencyValidationResult:
        errors: list[OntologyConsistencyIssue] = []
        node_keys = {node.key for node in draft.node_types}
        nodes_by_key = {node.key: node for node in draft.node_types}

        def add_error(code: str, path: str, message: str, *, artifact_key: str | None = None) -> None:
            errors.append(OntologyConsistencyIssue(code=code, path=path, message=message, artifact_key=artifact_key))

        # --- subclass hierarchy cycle detection ---
        subclass_graph: dict[str, list[str]] = {}
        for node in draft.node_types:
            for parent in node.subclass_of:
                if parent not in node_keys:
                    add_error(
                        "subclass_of_unknown",
                        f"node_types[{node.key}].subclass_of",
                        f"node_type {node.key!r} declares subclass_of {parent!r} which is not a declared node_type",
                        artifact_key=f"object:{node.key}",
                    )
                else:
                    subclass_graph.setdefault(node.key, []).append(parent)

        if subclass_graph:
            WHITE, GRAY, BLACK = 0, 1, 2
            color: dict[str, int] = {key: WHITE for key in node_keys}

            def dfs(node_key: str, path: list[str]) -> None:
                color[node_key] = GRAY
                path.append(node_key)
                for parent in subclass_graph.get(node_key, []):
                    if color.get(parent) == GRAY:
                        cycle = path[path.index(parent):] + [parent]
                        add_error(
                            "subclass_cycle",
                            f"node_types[{parent}].subclass_of",
                            f"subclass hierarchy cycle detected: {' -> '.join(cycle)}",
                            artifact_key=f"object:{parent}",
                        )
                    elif color.get(parent) == WHITE:
                        dfs(parent, path)
                path.pop()
                color[node_key] = BLACK

            for key in node_keys:
                if color[key] == WHITE:
                    dfs(key, [])

        # --- domain/range compatibility ---
        for edge in draft.edge_types:
            artifact_key = f"link:{edge.key}"
            declared_domain = edge.domain or [edge.source_node_key]
            declared_range = edge.range or [edge.target_node_key]

            if edge.source_node_key not in declared_domain:
                add_error(
                    "domain_mismatch",
                    f"edge_types[{edge.key}].source_node_key",
                    f"edge_type {edge.key!r} source_node_key {edge.source_node_key!r} is not in declared domain {declared_domain}",
                    artifact_key=artifact_key,
                )

            if edge.target_node_key not in declared_range:
                add_error(
                    "range_mismatch",
                    f"edge_types[{edge.key}].target_node_key",
                    f"edge_type {edge.key!r} target_node_key {edge.target_node_key!r} is not in declared range {declared_range}",
                    artifact_key=artifact_key,
                )

            for dom_key in declared_domain:
                if dom_key not in node_keys:
                    add_error(
                        "domain_unknown_node",
                        f"edge_types[{edge.key}].domain",
                        f"edge_type {edge.key!r} domain references unknown node_type {dom_key!r}",
                        artifact_key=artifact_key,
                    )

            for rng_key in declared_range:
                if rng_key not in node_keys:
                    add_error(
                        "range_unknown_node",
                        f"edge_types[{edge.key}].range",
                        f"edge_type {edge.key!r} range references unknown node_type {rng_key!r}",
                        artifact_key=artifact_key,
                    )

        # --- disjointness violation ---
        # If two node types declare disjoint_with each other (or one declares
        # the other), an edge connecting both as source and target violates
        # disjointness because the same relation would require an instance to
        # belong to both disjoint types.
        disjoint_pairs: set[tuple[str, str]] = set()
        for node in draft.node_types:
            for other in node.disjoint_with:
                if other in node_keys and other != node.key:
                    disjoint_pairs.add(tuple(sorted({node.key, other})))

        if disjoint_pairs:
            for edge in draft.edge_types:
                pair = tuple(sorted({edge.source_node_key, edge.target_node_key}))
                if len(pair) == 2 and pair in disjoint_pairs:
                    add_error(
                        "disjointness_violation",
                        f"edge_types[{edge.key}]",
                        f"edge_type {edge.key!r} connects disjoint node types {edge.source_node_key!r} and {edge.target_node_key!r}",
                        artifact_key=f"link:{edge.key}",
                    )

        return OntologyConsistencyValidationResult(errors)

    @classmethod
    def persist_draft_artifacts_in_session(
        cls,
        session,
        draft: GraphModelDraft,
        *,
        source_schema: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
        source_agent: str | None = None,
    ) -> list[str]:
        if source_schema is not None:
            cls.validate_schema_traceability(draft, source_schema).raise_for_errors()

        cls.validate_ontology_consistency(draft).raise_for_errors()

        canonical_keys: list[str] = []
        for spec in cls.artifact_specs_for_draft(draft):
            artifact = upsert_artifact(
                session,
                artifact_type=spec["artifact_type"],
                natural_key=spec["natural_key"],
                name=spec["name"],
                description=spec["description"],
                payload=spec["payload"],
                source_refs=spec["source_refs"],
                source_agent=source_agent or cls.source_agent,
                project_id=project_id,
                confidence=spec["confidence"],
                status="draft",
            )
            replace_evidence(
                session,
                artifact,
                [
                    {
                        "evidence_type": "schema_graph_inference",
                        "source_ref": (spec["source_refs"] or ["schema"])[idx % max(len(spec["source_refs"] or ["schema"]), 1)],
                        "summary": evidence,
                        "payload": {
                            "prompt_version": cls.prompt_version,
                            "schema_contract_version": draft.schema_version,
                            "artifact": spec["natural_key"],
                            "review_boundary": draft.review_boundary,
                        },
                        "confidence": spec["confidence"],
                    }
                    for idx, evidence in enumerate(spec["evidence"] or [spec["description"]])
                ],
            )
            canonical_keys.append(artifact.canonical_key)
        return canonical_keys

    def persist_draft_artifacts(self, draft: GraphModelDraft, *, source_schema: list[dict[str, Any]] | None = None) -> list[str]:
        if self.Session is None:
            raise ValueError("metadata_db_url is required to persist artifacts")
        with self.Session() as session:
            canonical_keys = self.persist_draft_artifacts_in_session(
                session,
                draft,
                source_schema=source_schema,
                project_id=self.project_id,
                source_agent=self.source_agent,
            )
            session.commit()
        return canonical_keys

    def run(
        self,
        *,
        schema: str | None = None,
        include_tables: Iterable[str] | None = None,
        include_profile: bool = True,
        sample_size: int = 3,
        persist: bool = False,
    ) -> SchemaGraphModelingResult:
        schema_dump = self.inspect_source_schema(
            schema=schema,
            include_tables=include_tables,
            include_profile=include_profile,
            sample_size=sample_size,
        )
        draft = self.infer_graph_model_with_llm(schema_dump)
        artifacts = self.persist_draft_artifacts(draft, source_schema=schema_dump) if persist else []
        return SchemaGraphModelingResult(schema=schema_dump, draft=draft, artifacts=artifacts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Infer draft graph ontology from raw database schema with an LLM")
    parser.add_argument("--source", default=os.environ.get("ALETHEIA_MYSQL_URL", "mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/aletheia_test_data"))
    parser.add_argument("--metadata", default=os.environ.get("ALETHEIA_PG_URL", "postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology"))
    parser.add_argument("--model", default=os.environ.get("ALETHEIA_SCHEMA_GRAPH_MODEL", "gpt-4o"))
    parser.add_argument("--tenant", default=os.environ.get("ALETHEIA_TENANT", "default"))
    parser.add_argument("--table", action="append", dest="tables", help="Restrict inference to a table; can be repeated")
    parser.add_argument("--no-profile", action="store_true", help="Disable generic row-count/sample evidence in the LLM prompt")
    parser.add_argument("--sample-size", type=int, default=3, help="Number of source rows to include per table as generic profile evidence")
    parser.add_argument("--persist", action="store_true", help="Persist inferred artifacts as draft ontology proposals")
    parser.add_argument("--report-json", default=None)
    args = parser.parse_args()

    agent = SchemaGraphModelingAgent(
        source_db_url=args.source,
        metadata_db_url=args.metadata,
        model_name=args.model,
        project_id=args.tenant,
    )
    result = agent.run(
        include_tables=args.tables,
        include_profile=not args.no_profile,
        sample_size=args.sample_size,
        persist=args.persist,
    )
    output = {
        "tenant": args.tenant,
        "prompt_version": agent.prompt_version,
        "schema_contract_version": result.draft.schema_version,
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
