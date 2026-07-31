"""Governed relation-type catalog for graph extraction.

Separates two things that both the SQL-backed onto reasoning engine and the
HotpotQA Nebula pipeline currently conflate:

- WHICH relations exist (governance -- a catalog of canonical names,
  descriptions, and known aliases; backend-agnostic).
- HOW edge data is physically stored (the SQL side creates a whole new
  table per relation; the Nebula HotpotQA side stores relation names as
  ungoverned free-text strings on a single generic EDGE type, with zero
  deduplication -- the same real-world relation gets reinvented under a
  new name every time an extraction call happens to phrase it differently).

This catalog gives the Nebula side real governance (every new relation name
is checked against known canonical types before being written -- cheap
exact/alias match first, LLM semantic match as fallback) without adding any
physical-storage weight: Nebula's single EDGE type is untouched, only the
string that ends up in its `relation_label` property changes from
"whatever the extractor felt like calling it this time" to "the catalog's
canonical name for that relation."

The catalog itself lives in the relational metadata store (Postgres), the
same place onto's other ontology artifacts are governed -- this is metadata
ABOUT the graph's relations (what they're called, what they mean, what
aliases collapse into them), not graph data, so it belongs alongside onto's
other governance, not in a standalone file next to the Nebula space. A local
JSON-file mode also exists (``load``/``path``) for offline unit tests that
shouldn't need a live Postgres connection.

Usage:
    from relation_catalog import RelationCatalog
    from tenant_registry import default_metadata_db_url
    catalog = RelationCatalog.load_from_postgres(default_metadata_db_url(), scope="hotpotqa")
    canonical = catalog.normalize("established_in", evidence="...founded in 1590...")
    catalog.save()
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_planner import LLMPlanner

logger = logging.getLogger("RelationCatalog")

MAX_CANDIDATES_IN_PROMPT = 60

DEFAULT_SYSTEM_PROMPT = """You maintain a governed catalog of relation types for a knowledge graph.

Given a NEW relation name (with a short evidence snippet) and a list of EXISTING canonical relation \
types (with descriptions), decide whether the new relation is the SAME real-world relation as one of \
the existing ones, just phrased differently (e.g. "established_in" and "founded_in" are the same \
relation), or whether it is genuinely a different, new relation.

Rules:
1. Only match an existing canonical relation if it describes the exact same kind of fact -- not \
merely a similar or related concept.
2. If it matches, return that canonical relation's exact name in "canonical_match".
3. If it is genuinely new, set "canonical_match" to an empty string."""

DEFAULT_USER_TEMPLATE = """New relation: "{raw_name}"
Evidence: {evidence}

Existing canonical relations:
{candidates}

Does the new relation match one of the existing canonical relations?"""


def _format_candidates(entries: dict[str, dict]) -> str:
    items = list(entries.items())[:MAX_CANDIDATES_IN_PROMPT]
    if not items:
        return "  (none yet)"
    lines = []
    for name, meta in items:
        desc = meta.get("description", "")
        lines.append(f'  - "{name}"' + (f": {desc}" if desc else ""))
    return "\n".join(lines)


@dataclass
class RelationCatalog:
    """A small, governed dictionary of canonical relation types.

    ``entries`` maps canonical_name -> {"description": str, "aliases": [str]}.
    Deliberately flat (no from_type/to_type join semantics like the SQL
    side's link_config) -- HotpotQA's closed-world per-question graphs don't
    have stable entity "types" to key relations on; governance here is just
    "is this the same relation as one we already know about."
    """

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None
    db_url: str | None = None
    scope: str = "hotpotqa"
    planner: LLMPlanner | None = None
    model: str = ""
    timeout: float = 30.0
    new_relations_this_session: int = 0
    merged_this_session: int = 0

    def __post_init__(self):
        if self.planner is None:
            self.planner = LLMPlanner(model=self.model or None)
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._engine = None

    @classmethod
    def load(cls, path: Path, planner: LLMPlanner | None = None) -> "RelationCatalog":
        """Local-JSON-file variant, for offline/unit-test use only -- the
        real integration path is ``load_from_postgres``, since the catalog
        is onto metadata/governance, not graph data, and belongs in the
        same relational metadata store as the rest of onto's ontology
        artifacts, not a standalone file next to the Nebula space."""
        entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            entries = json.loads(path.read_text(encoding="utf-8"))
        return cls(entries=entries, path=path, planner=planner)

    @classmethod
    def load_from_postgres(
        cls, db_url: str, scope: str = "hotpotqa", planner: LLMPlanner | None = None,
    ) -> "RelationCatalog":
        """The real integration path: relation governance metadata lives in
        the same Postgres metadata store onto's other ontology artifacts
        use (``agents.tenant_registry.default_metadata_db_url()``), keyed
        by ``scope`` so unrelated graphs (a future non-HotpotQA use of this
        catalog) don't collide. The graph database itself is untouched --
        Nebula's edges keep storing whatever canonical name this catalog
        settles on as their ``relation_label`` property; this table is
        purely the governance layer describing what those names mean."""
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS relation_catalog (
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
                text("SELECT canonical_name, description, aliases FROM relation_catalog WHERE scope = :scope"),
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
                    INSERT INTO relation_catalog (scope, canonical_name, description, aliases, updated_at)
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
        return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")

    def _cheap_match(self, raw_name: str) -> str | None:
        """Exact match after casing/punctuation normalization, against
        canonical names or their known aliases. No LLM call."""
        key = self._normalize_key(raw_name)
        if not key:
            return None
        for canonical, meta in self.entries.items():
            if key == self._normalize_key(canonical):
                return canonical
            if key in {self._normalize_key(a) for a in meta.get("aliases", [])}:
                return canonical
        return None

    def normalize(self, raw_name: str, evidence: str = "") -> str:
        """Return the canonical relation name to actually store for
        ``raw_name``. Registers a new canonical entry if nothing matches."""
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

        self.entries[raw_name] = {"description": evidence[:200] if evidence else "", "aliases": []}
        self.new_relations_this_session += 1
        return raw_name

    def register_identity(self, raw_name: str, description: str = "") -> str:
        """Register ``raw_name`` as its own canonical entry, with no LLM
        semantic-match call ever -- for source vocabularies that are already
        distinct/atomic (e.g. WebQSP/Freebase's dotted predicates like
        ``people.person.nationality``, reused verbatim across questions and
        never ad hoc-invented per extraction call the way HotpotQA's LLM-
        extracted relation names are). Still deduplicates via the cheap
        exact/alias match, so repeated calls for the same relation across
        many questions collapse to one entry without any network call."""
        raw_name = (raw_name or "").strip()
        if not raw_name:
            return raw_name

        cheap = self._cheap_match(raw_name)
        if cheap:
            return cheap

        self.entries[raw_name] = {"description": description[:200] if description else "", "aliases": []}
        self.new_relations_this_session += 1
        return raw_name

    def _completion_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if LLMPlanner._env_bool("ALETHEIA_LLM_PLANNER_JSON_MODE", True):
            kwargs["response_format"] = {"type": "json_object"}
        model = self.planner.model
        if model.startswith("openrouter/") and LLMPlanner._env_bool("ALETHEIA_LLM_PLANNER_DISABLE_REASONING", True):
            kwargs["reasoning"] = {"effort": "none", "exclude": True}
        return kwargs

    @staticmethod
    def _parse_response(content: Any) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload: Any) -> dict | None:
            if not isinstance(payload, dict) or not isinstance(payload.get("canonical_match"), str):
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

    def _semantic_match(self, raw_name: str, evidence: str) -> str | None:
        """One LLM call: does raw_name mean the same thing as an existing
        canonical relation? Degrades to "no match" (register as new) on any
        failure -- a missed dedup just adds one catalog entry, it never
        blocks ingestion."""
        try:
            from litellm import completion
        except ImportError:
            return None

        user_msg = DEFAULT_USER_TEMPLATE.format(
            raw_name=raw_name, evidence=evidence or "(none)", candidates=_format_candidates(self.entries),
        )
        json_instruction = (
            '\n\nRespond with ONLY a JSON object in this exact format '
            '(no markdown, no extra text):\n{"canonical_match": str}'
        )
        try:
            future = self._executor.submit(
                completion,
                model=self.planner.model,
                messages=[
                    {"role": "system", "content": DEFAULT_SYSTEM_PROMPT + json_instruction},
                    {"role": "user", "content": user_msg},
                ],
                timeout=self.timeout,
                temperature=0.0,
                **self._completion_kwargs(),
            )
            try:
                raw_response = future.result(timeout=self.timeout + 15)
            except FutureTimeoutError:
                future.cancel()
                logger.warning("RelationCatalog: hard timeout matching %r, treating as new", raw_name)
                return None

            if not raw_response or not raw_response.choices:
                return None
            candidates = LLMPlanner._response_text_candidates(raw_response.choices[0].message)
            for candidate in candidates:
                parsed = self._parse_response(candidate)
                if parsed is not None:
                    match = parsed.get("canonical_match", "").strip()
                    return match if match in self.entries else None
            return None
        except Exception as exc:
            logger.warning("RelationCatalog: match call failed for %r: %s", raw_name, exc)
            return None

    def as_link_config(self) -> list[dict[str, str]]:
        """Backend-agnostic relation catalog view, shaped like the SQL
        side's ``reasoning_link_config()`` entries minus the SQL-join-only
        fields (``fk_table``/``fk_col``/``join_condition``/``same_row_link``
        have no equivalent here -- the point of this catalog is that edges
        are already real graph edges, not reconstructed via joins)."""
        return [
            {"link": name, "description": meta.get("description", ""), "from": "*", "to": "*"}
            for name, meta in sorted(self.entries.items())
        ]
