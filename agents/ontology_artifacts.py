import json
import hashlib
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def canonical_key_for(artifact_type: str, natural_key: str) -> str:
    return f"{artifact_type}:{natural_key}".lower().replace(" ", "_")


class ExtractedTable(Base):
    __tablename__ = "aletheia_extracted_tables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    schema_name = Column(String(255))
    table_name = Column(String(255), nullable=False)
    table_comment = Column(String(1000))
    extracted_at = Column(DateTime, default=datetime.utcnow)

    columns = relationship("ExtractedColumn", back_populates="table", cascade="all, delete")


class ExtractedColumn(Base):
    __tablename__ = "aletheia_extracted_columns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_id = Column(Integer, ForeignKey("aletheia_extracted_tables.id"), nullable=False)
    column_name = Column(String(255), nullable=False)
    data_type = Column(String(255), nullable=False)
    is_primary_key = Column(Boolean, default=False)
    is_nullable = Column(Boolean, default=True)
    column_comment = Column(String(1000))

    table = relationship("ExtractedTable", back_populates="columns")
    profile = relationship("ColumnProfile", back_populates="column", uselist=False)


class ColumnProfile(Base):
    __tablename__ = "aletheia_column_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    column_id = Column(Integer, ForeignKey("aletheia_extracted_columns.id"), nullable=False)
    semantic_type = Column(String(255))
    semantic_hypothesis = Column(Text)
    profiled_at = Column(DateTime, default=datetime.utcnow)

    column = relationship("ExtractedColumn", back_populates="profile")


class BusinessObject(Base):
    __tablename__ = "aletheia_business_objects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text)
    artifact_id = Column(Integer, ForeignKey("aletheia_ontology_artifacts.id"))
    graph_label = Column(String(255))
    extraction_sql = Column(Text)
    ngql_schema = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class ObjectTableMapping(Base):
    __tablename__ = "aletheia_object_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    object_id = Column(Integer, ForeignKey("aletheia_business_objects.id"))
    table_id = Column(Integer, ForeignKey("aletheia_extracted_tables.id"))


class BusinessLink(Base):
    __tablename__ = "aletheia_business_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_object_id = Column(Integer, ForeignKey("aletheia_business_objects.id"), nullable=False)
    target_object_id = Column(Integer, ForeignKey("aletheia_business_objects.id"), nullable=False)
    link_type = Column(String(50))
    description = Column(Text)
    artifact_id = Column(Integer, ForeignKey("aletheia_ontology_artifacts.id"))
    graph_edge_name = Column(String(255))
    extraction_sql = Column(Text)
    ngql_schema = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class BusinessAction(Base):
    __tablename__ = "aletheia_business_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    action_type = Column(String(50))
    source_name = Column(String(255), nullable=False)
    description = Column(Text)
    is_safe = Column(Boolean, default=False)
    inputs_json = Column(Text)
    outputs_json = Column(Text)
    artifact_id = Column(Integer, ForeignKey("aletheia_ontology_artifacts.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class OntologyArtifact(Base):
    __tablename__ = "aletheia_ontology_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(255), nullable=False, default="default")
    canonical_key = Column(String(255), nullable=False, unique=True)
    artifact_type = Column(String(50), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    payload_json = Column(Text, nullable=False, default="{}")
    confidence = Column(Float, nullable=False, default=1.0)
    source_refs_json = Column(Text, nullable=False, default="[]")
    status = Column(String(50), nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)
    source_agent = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    evidence = relationship("ArtifactEvidence", back_populates="artifact", cascade="all, delete-orphan")


class ArtifactEvidence(Base):
    __tablename__ = "aletheia_artifact_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(Integer, ForeignKey("aletheia_ontology_artifacts.id"), nullable=False)
    evidence_type = Column(String(50), nullable=False)
    source_ref = Column(String(500), nullable=False)
    content_hash = Column(String(128))
    summary = Column(Text)
    raw_payload_json = Column(Text, nullable=False, default="{}")
    confidence = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    artifact = relationship("OntologyArtifact", back_populates="evidence")


class ArtifactReviewEvent(Base):
    __tablename__ = "aletheia_artifact_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(Integer, ForeignKey("aletheia_ontology_artifacts.id"), nullable=False)
    canonical_key = Column(String(255), nullable=False)
    decision = Column(String(50), nullable=False)
    reviewer = Column(String(255), nullable=False)
    reason = Column(Text)
    before_status = Column(String(50))
    after_status = Column(String(50))
    before_version = Column(Integer)
    after_version = Column(Integer)
    before_payload_json = Column(Text)
    after_payload_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


def upsert_artifact(
    session,
    *,
    artifact_type: str,
    natural_key: str,
    name: str,
    description: str | None,
    payload: dict[str, Any],
    source_refs: Iterable[str],
    source_agent: str,
    project_id: str = "default",
    confidence: float = 1.0,
    status: str = "draft",
) -> OntologyArtifact:
    canonical_key = canonical_key_for(artifact_type, natural_key)
    source_refs_list = list(dict.fromkeys(source_refs))
    artifact = session.query(OntologyArtifact).filter_by(canonical_key=canonical_key).first()

    if artifact:
        is_new = False
    else:
        is_new = True
        artifact = OntologyArtifact(
            project_id=project_id,
            canonical_key=canonical_key,
            artifact_type=artifact_type,
            source_agent=source_agent,
        )
        session.add(artifact)

    payload_json = _json_dump(payload)
    source_refs_json = _json_dump(source_refs_list)
    changed = (
        artifact.project_id != project_id
        or artifact.name != name
        or artifact.description != description
        or artifact.payload_json != payload_json
        or artifact.confidence != confidence
        or artifact.source_refs_json != source_refs_json
        or artifact.status != status
    )
    artifact._is_new_artifact = is_new
    artifact._version_bumped = False
    if changed and not is_new:
        artifact.version += 1
        artifact._version_bumped = True

    artifact.project_id = project_id
    artifact.name = name
    artifact.description = description
    artifact.payload_json = payload_json
    artifact.confidence = confidence
    artifact.source_refs_json = source_refs_json
    artifact.status = status
    artifact.updated_at = datetime.utcnow()
    return artifact


def replace_evidence(
    session,
    artifact: OntologyArtifact,
    evidence_items: Iterable[dict[str, Any]],
) -> None:
    session.flush()
    desired = []
    for item in evidence_items:
        raw_payload = item.get("payload", {})
        desired.append(
            {
                "evidence_type": item["evidence_type"],
                "source_ref": item["source_ref"],
                "content_hash": item.get("content_hash") or _content_hash(
                    {
                        "evidence_type": item["evidence_type"],
                        "source_ref": item["source_ref"],
                        "summary": item.get("summary"),
                        "raw_payload": raw_payload,
                    }
                ),
                "summary": item.get("summary"),
                "raw_payload_json": _json_dump(raw_payload),
                "confidence": item.get("confidence", 1.0),
            }
        )
    current = [
        {
            "evidence_type": row.evidence_type,
            "source_ref": row.source_ref,
            "content_hash": row.content_hash,
            "summary": row.summary,
            "raw_payload_json": row.raw_payload_json,
            "confidence": row.confidence,
        }
        for row in session.query(ArtifactEvidence).filter_by(artifact_id=artifact.id).all()
    ]
    if (
        sorted(current, key=lambda x: (x["source_ref"], x["content_hash"] or ""))
        != sorted(desired, key=lambda x: (x["source_ref"], x["content_hash"] or ""))
        and not getattr(artifact, "_is_new_artifact", False)
        and not getattr(artifact, "_version_bumped", False)
    ):
        artifact.version += 1
        artifact._version_bumped = True

    session.query(ArtifactEvidence).filter_by(artifact_id=artifact.id).delete()
    for item in desired:
        session.add(
            ArtifactEvidence(
                artifact_id=artifact.id,
                evidence_type=item["evidence_type"],
                source_ref=item["source_ref"],
                content_hash=item["content_hash"],
                summary=item.get("summary"),
                raw_payload_json=item["raw_payload_json"],
                confidence=item.get("confidence", 1.0),
            )
        )


def delete_artifacts_by_type(session, artifact_types: Iterable[str]) -> None:
    artifact_type_list = list(artifact_types)
    if not artifact_type_list:
        return
    artifact_ids = [
        row[0]
        for row in session.query(OntologyArtifact.id)
        .filter(OntologyArtifact.artifact_type.in_(artifact_type_list))
        .all()
    ]
    if not artifact_ids:
        return
    session.query(ArtifactEvidence).filter(ArtifactEvidence.artifact_id.in_(artifact_ids)).delete(
        synchronize_session=False
    )
    session.query(OntologyArtifact).filter(OntologyArtifact.id.in_(artifact_ids)).delete(
        synchronize_session=False
    )


def ensure_artifact_schema(engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE aletheia_business_objects ADD COLUMN IF NOT EXISTS artifact_id INTEGER"))
        conn.execute(text("ALTER TABLE aletheia_business_objects ADD COLUMN IF NOT EXISTS graph_label VARCHAR(255)"))
        conn.execute(text("ALTER TABLE aletheia_business_objects ADD COLUMN IF NOT EXISTS extraction_sql TEXT"))
        conn.execute(text("ALTER TABLE aletheia_business_objects ADD COLUMN IF NOT EXISTS ngql_schema TEXT"))
        conn.execute(text("ALTER TABLE aletheia_business_links ADD COLUMN IF NOT EXISTS artifact_id INTEGER"))
        conn.execute(text("ALTER TABLE aletheia_business_links ADD COLUMN IF NOT EXISTS graph_edge_name VARCHAR(255)"))
        conn.execute(text("ALTER TABLE aletheia_business_links ADD COLUMN IF NOT EXISTS extraction_sql TEXT"))
        conn.execute(text("ALTER TABLE aletheia_business_links ADD COLUMN IF NOT EXISTS ngql_schema TEXT"))
        conn.execute(text("ALTER TABLE aletheia_business_actions ADD COLUMN IF NOT EXISTS artifact_id INTEGER"))


def sync_object_artifact(session, obj: BusinessObject, mapped_tables: list[ExtractedTable]) -> OntologyArtifact:
    source_refs = [f"table:{table.table_name}" for table in mapped_tables]
    artifact = upsert_artifact(
        session,
        artifact_type="object",
        natural_key=obj.name,
        name=obj.name,
        description=obj.description,
        payload={
            "name": obj.name,
            "description": obj.description,
            "mapped_table_names": [table.table_name for table in mapped_tables],
        },
        source_refs=source_refs,
        source_agent="ObjectModelerAgent",
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": "table",
                "source_ref": f"table:{table.table_name}",
                "summary": table.table_comment or f"Mapped physical table {table.table_name}",
                "payload": {
                    "schema_name": table.schema_name,
                    "table_name": table.table_name,
                    "table_comment": table.table_comment,
                },
            }
            for table in mapped_tables
        ],
    )
    obj.artifact_id = artifact.id
    return artifact


def sync_link_artifact(session, link: BusinessLink, source_obj: BusinessObject, target_obj: BusinessObject) -> OntologyArtifact:
    natural_key = f"{source_obj.name}:{link.link_type}:{target_obj.name}"
    artifact = upsert_artifact(
        session,
        artifact_type="link",
        natural_key=natural_key,
        name=f"{source_obj.name} {link.link_type} {target_obj.name}",
        description=link.description,
        payload={
            "source_object_name": source_obj.name,
            "target_object_name": target_obj.name,
            "link_type": link.link_type,
            "description": link.description,
        },
        source_refs=[
            f"object:{source_obj.name}",
            f"object:{target_obj.name}",
        ],
        source_agent="LinkWeaverAgent",
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": "object",
                "source_ref": f"object:{source_obj.name}",
                "summary": source_obj.description,
                "payload": {"artifact_id": source_obj.artifact_id},
            },
            {
                "evidence_type": "object",
                "source_ref": f"object:{target_obj.name}",
                "summary": target_obj.description,
                "payload": {"artifact_id": target_obj.artifact_id},
            },
        ],
    )
    link.artifact_id = artifact.id
    return artifact


def sync_action_artifact(session, action: BusinessAction) -> OntologyArtifact:
    artifact = upsert_artifact(
        session,
        artifact_type="action",
        natural_key=f"{action.action_type}:{action.source_name}:{action.name}",
        name=action.name,
        description=action.description,
        payload={
            "name": action.name,
            "action_type": action.action_type,
            "source_name": action.source_name,
            "is_safe": action.is_safe,
            "inputs_json": action.inputs_json,
            "outputs_json": action.outputs_json,
        },
        source_refs=[f"{action.action_type}:{action.source_name}"],
        source_agent="ActionSynthesizerAgent",
        status="draft" if action.is_safe else "needs_review",
    )
    replace_evidence(
        session,
        artifact,
        [
            {
                "evidence_type": action.action_type or "action_source",
                "source_ref": f"{action.action_type}:{action.source_name}",
                "summary": action.description,
                "payload": {
                    "is_safe": action.is_safe,
                    "inputs_json": action.inputs_json,
                    "outputs_json": action.outputs_json,
                },
            }
        ],
    )
    action.artifact_id = artifact.id
    return artifact
