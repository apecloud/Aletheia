"""Governed node-TYPE catalog for graph extraction -- the "ontology mapping"
stage between text-QA passage extraction and ontology-type registration.

Mirrors ``scripts/relation_catalog.py``'s ``RelationCatalog`` exactly (same
two-tier matching: cheap exact/alias match first, one LLM semantic-match
call as fallback, via the shared algorithm in
``scripts/type_catalog_matching.py``) but governs node TYPE names (e.g.
"Person" vs "Human") instead of relation names. Before this module existed,
``scripts/import_hotpotqa_nebula_tenant.py``'s node-type registration
(``_register_node_type_if_new``) only did an exact-string match against
``agents/graph_ontology_registry.py``'s already-approved types -- an
independent, stateless extraction call that happens to invent "Human"
instead of reusing "Person" would silently create a whole new, permanent
ontology type. This catalog catches that case the same way
``RelationCatalog`` already catches "established_in" vs "founded_in".

The catalog itself lives in the same Postgres metadata store as onto's other
ontology governance (``agents.tenant_registry.default_metadata_db_url()``),
scoped per-tenant the same way ``RelationCatalog``/``graph_ontology_registry``
are, so unrelated tenants never collide.

Usage:
    from aletheia.ontology.node_type_catalog import NodeTypeCatalog
    from aletheia.core.tenant_registry import default_metadata_db_url
    catalog = NodeTypeCatalog.load_from_postgres(default_metadata_db_url(), scope="hotpotqa-graph-v1-typed")
    canonical = catalog.normalize("Human", evidence="...a person born in...")
    catalog.save()
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aletheia.llms.hard_timeout import call_with_hard_timeout

from aletheia.llms.planner import LLMPlanner
from type_catalog_matching import cheap_match, format_candidates, normalize_key, semantic_match_via_llm

logger = logging.getLogger("NodeTypeCatalog")

DEFAULT_SYSTEM_PROMPT = """You maintain a governed catalog of entity (node) types for a knowledge graph's ontology.

Given a NEW candidate entity type (with a short evidence snippet showing an entity that was classified \
as this type) and a list of EXISTING canonical entity types (with descriptions), decide whether the new \
type is the SAME real-world kind of entity as one of the existing ones, just phrased differently (e.g. \
"Human" and "Person" are the same type, "Company" and "Organization" are the same type), or whether it \
is genuinely a different, new type.

Rules:
1. Only match an existing canonical type if it describes the exact same kind of real-world entity -- \
not merely a similar, related, or overlapping concept (e.g. "Actor" is NOT the same type as "Person", \
it is a more specific one -- do not match those as the same).
2. If it matches, return that canonical type's exact name in "canonical_match".
3. If it is genuinely new, set "canonical_match" to an empty string."""

DEFAULT_USER_TEMPLATE = """New entity type: "{raw_name}"
Evidence: {evidence}

Existing canonical entity types:
{candidates}

Does the new entity type match one of the existing canonical entity types?"""

# Ontology mapping's second question, asked only for a type that survived
# DEFAULT_SYSTEM_PROMPT's "is this the SAME type" check as genuinely new --
# populates propose_node_type's subclass_of (see
# scripts/import_hotpotqa_nebula_tenant.py's _register_node_type_if_new),
# which agents/graph_instance_repository.py's reasoning_entity_config then
# walks so a not-yet-approved subtype inherits reasoning-eligibility from
# an already-approved ancestor (e.g. draft "GuideDog" via approved "Dog").
DEFAULT_PARENT_SYSTEM_PROMPT = """You classify a new entity type into a knowledge graph's existing type hierarchy.

Given a NEW entity type (with a short evidence snippet) and a list of EXISTING approved entity types, \
decide whether the new type is a MORE SPECIFIC KIND of exactly one of the existing types (e.g. "Actor" \
is a more specific kind of "Person"; "GuideDog" is a more specific kind of "Dog").

Rules:
1. Only pick a parent if every instance of the new type is necessarily ALSO an instance of that parent \
type -- not merely related, similar, or often found together.
2. If it matches, return that parent type's exact name in "suggested_parent".
3. If the new type isn't a specialization of any of them (it's a peer, unrelated, or too different), \
set "suggested_parent" to an empty string."""

DEFAULT_PARENT_USER_TEMPLATE = """New entity type: "{raw_name}"
Evidence: {evidence}

Existing approved entity types:
{candidates}

Is the new entity type a more specific kind of exactly one of these? If so, which one?"""


@dataclass
class NodeTypeCatalog:
    """A small, governed dictionary of canonical node (entity) types.

    ``entries`` maps canonical_name -> {"description": str, "aliases": [str]}
    -- same shape as ``RelationCatalog.entries``, deliberately, so both
    catalogs can share ``type_catalog_matching.py``'s matching functions
    unchanged.
    """

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None
    db_url: str | None = None
    scope: str = "hotpotqa"
    planner: LLMPlanner | None = None
    model: str = ""
    timeout: float = 30.0
    new_types_this_session: int = 0
    merged_this_session: int = 0

    def __post_init__(self):
        if self.planner is None:
            self.planner = LLMPlanner(model=self.model or None)
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._engine = None

    @classmethod
    def load(cls, path: Path, planner: LLMPlanner | None = None) -> "NodeTypeCatalog":
        """Local-JSON-file variant, for offline/unit-test use only -- the
        real integration path is ``load_from_postgres`` (see
        ``RelationCatalog.load``'s identical rationale)."""
        entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            entries = json.loads(path.read_text(encoding="utf-8"))
        return cls(entries=entries, path=path, planner=planner)

    @classmethod
    def load_from_postgres(
        cls, db_url: str, scope: str = "hotpotqa", planner: LLMPlanner | None = None,
    ) -> "NodeTypeCatalog":
        """The real integration path -- same Postgres metadata store as
        ``RelationCatalog``/``graph_ontology_registry``, its own table so
        node-type governance never collides with relation-name governance."""
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS node_type_catalog (
                    id SERIAL PRIMARY KEY,
                    scope VARCHAR(255) NOT NULL,
                    canonical_name VARCHAR(255) NOT NULL,
                    description TEXT DEFAULT '',
                    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMP DEFAULT now(),
                    updated_at TIMESTAMP DEFAULT now(),
                    UNIQUE (scope, canonical_name)
                );
            """))
            conn.commit()
            rows = conn.execute(
                text("SELECT canonical_name, description, aliases FROM node_type_catalog WHERE scope = :scope"),
                {"scope": scope},
            ).fetchall()

        entries = {
            row.canonical_name: {"description": row.description or "", "aliases": list(row.aliases or [])}
            for row in rows
        }
        instance = cls(entries=entries, db_url=db_url, scope=scope, planner=planner)
        instance._engine = engine
        return instance

    def save(self) -> None:
        if self.db_url:
            self._save_to_postgres()
        if self.path is not None:
            self._save_to_json()

    def _save_to_postgres(self) -> None:
        from sqlalchemy import create_engine, text

        engine = self._engine or create_engine(self.db_url)
        with engine.connect() as conn:
            for canonical, meta in self.entries.items():
                conn.execute(text("""
                    INSERT INTO node_type_catalog (scope, canonical_name, description, aliases, updated_at)
                    VALUES (:scope, :name, :description, :aliases, now())
                    ON CONFLICT (scope, canonical_name)
                    DO UPDATE SET description = EXCLUDED.description, aliases = EXCLUDED.aliases, updated_at = now();
                """), {
                    "scope": self.scope,
                    "name": canonical,
                    "description": meta.get("description", ""),
                    "aliases": json.dumps(meta.get("aliases", [])),
                })
            conn.commit()

    def _save_to_json(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _normalize_key(name: str) -> str:
        return normalize_key(name)

    def _cheap_match(self, raw_name: str) -> str | None:
        return cheap_match(raw_name, self.entries)

    def normalize(
        self, raw_name: str, evidence: str = "", approved_node_types: list[str] | None = None,
    ) -> str:
        """Return the canonical node type name to actually register for
        ``raw_name``. Registers a new canonical entry if nothing matches --
        the returned name is what the caller should pass to
        ``graph_ontology_registry.propose_node_type``, not the raw
        extractor-produced string.

        ``approved_node_types``, when given a non-empty list, also asks (one
        extra LLM call, only for a genuinely new type -- never for a cheap
        or semantic match) whether the new type is a more specific kind of
        one of them. The suggestion is stored on the new entry as
        ``"suggested_parent"`` -- read it back via ``self.entries[canonical]
        .get("suggested_parent")`` to populate ``propose_node_type``'s
        ``subclass_of``. None (no suggestion) if nothing matched, the LLM
        call failed, or ``approved_node_types`` wasn't given."""
        raw_name = (raw_name or "").strip()
        if not raw_name:
            return raw_name

        cheap = self._cheap_match(raw_name)
        if cheap:
            return cheap

        canonical = self._semantic_match(raw_name, evidence) if self.entries else None
        if canonical:
            aliases = self.entries[canonical].setdefault("aliases", [])
            if raw_name not in aliases:
                aliases.append(raw_name)
            self.merged_this_session += 1
            return canonical

        suggested_parent = (
            self._suggest_parent(raw_name, evidence, approved_node_types) if approved_node_types else None
        )
        self.entries[raw_name] = {
            "description": evidence[:200] if evidence else "", "aliases": [], "suggested_parent": suggested_parent,
        }
        self.new_types_this_session += 1
        return raw_name

    def _completion_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if LLMPlanner._env_bool("ALETHEIA_LLM_PLANNER_JSON_MODE", True):
            kwargs["response_format"] = {"type": "json_object"}
        model = self.planner.model
        if model.startswith("openrouter/") and LLMPlanner._env_bool("ALETHEIA_LLM_PLANNER_DISABLE_REASONING", True):
            kwargs["reasoning"] = {"effort": "none", "exclude": True}
        return kwargs

    def _semantic_match(self, raw_name: str, evidence: str) -> str | None:
        """One LLM call: does raw_name mean the same real-world entity type
        as an existing canonical type? Degrades to "no match" (register as
        new) on any failure -- see ``type_catalog_matching.semantic_match_via_llm``."""
        return semantic_match_via_llm(
            raw_name, evidence, self.entries,
            planner=self.planner, executor=self._executor, timeout=self.timeout,
            system_prompt=DEFAULT_SYSTEM_PROMPT, user_template=DEFAULT_USER_TEMPLATE,
            completion_kwargs=self._completion_kwargs(), logger=logger, label="NodeTypeCatalog",
        )

    @staticmethod
    def _parse_parent_response(content: Any) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload: Any) -> dict | None:
            if not isinstance(payload, dict) or not isinstance(payload.get("suggested_parent"), str):
                return None
            return payload

        try:
            return valid(json.loads(content))
        except (json.JSONDecodeError, TypeError):
            pass
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if match:
            try:
                return valid(json.loads(match.group(1).strip()))
            except (json.JSONDecodeError, TypeError):
                pass
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            try:
                return valid(json.loads(match.group(0)))
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def _suggest_parent(self, raw_name: str, evidence: str, approved_node_types: list[str]) -> str | None:
        """One LLM call: is ``raw_name`` a more specific kind of one of the
        tenant's already-approved types? Degrades to None (no suggestion,
        registered as a root-level type) on any failure -- a missed
        subclass_of link only means reasoning won't inherit through it, it
        is never a reason to fail registration."""
        try:
            from litellm import completion
        except ImportError:
            return None

        candidates_source = {name: {} for name in approved_node_types}
        user_msg = DEFAULT_PARENT_USER_TEMPLATE.format(
            raw_name=raw_name, evidence=evidence or "(none)", candidates=format_candidates(candidates_source),
        )
        json_instruction = (
            '\n\nRespond with ONLY a JSON object in this exact format '
            '(no markdown, no extra text):\n{"suggested_parent": str}'
        )
        try:
            try:
                raw_response = call_with_hard_timeout(
                    self._executor, completion,
                    model=self.planner.model,
                    messages=[
                        {"role": "system", "content": DEFAULT_PARENT_SYSTEM_PROMPT + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
                    timeout=self.timeout,
                    temperature=0.0,
                    **self._completion_kwargs(),
                )
            except TimeoutError:
                logger.warning("NodeTypeCatalog: hard timeout suggesting parent for %r", raw_name)
                return None

            if not raw_response or not raw_response.choices:
                return None
            approved_set = set(approved_node_types)
            for candidate in LLMPlanner._response_text_candidates(raw_response.choices[0].message):
                parsed = self._parse_parent_response(candidate)
                if parsed is not None:
                    parent = parsed.get("suggested_parent", "").strip()
                    return parent if parent in approved_set else None
            return None
        except Exception as exc:
            logger.warning("NodeTypeCatalog: parent-suggestion call failed for %r: %s", raw_name, exc)
            return None
