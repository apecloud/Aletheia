"""
Aletheia Universal Reasoning Engine
====================================
Schema-agnostic deep analysis for any entity type. Retrieval is graph-native:
the repository (self.repo) fetches an entity and its neighborhood as plain
node/edge dicts, and this engine plans which relations/entity mentions a
question needs (via the LLM planner), walks paths between named centers, and
composes findings -- with no dependency on SQL introspection or raw
SQLAlchemy engines. Entity/link configuration and relation descriptions come
from approved schema-graph projection metadata and ontology artifact
descriptions, with legacy ENTITY_CONFIG/LINK_CONFIG fixtures as repository
fallbacks only when approved SchemaGraphModelingAgent projection metadata is
not available.

Usage:
    from aletheia.reasoning.engine import ReasoningEngine
    engine = ReasoningEngine(instance_repository)
    result = engine.analyze(tenant, "Employee:1", "Is Nancy a top performer?")
"""

import os
import re
from collections import deque
from dataclasses import dataclass, field

from aletheia.llms.planner import LLMPlanner, PlannerMapping


def _jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class ReasoningEngine:
    def __init__(self, instance_repository, llm_planner=None):
        self.repo = instance_repository
        self._llm_planner = llm_planner

    def _entity_config(self, tenant):
        if hasattr(self.repo, "reasoning_entity_config"):
            return self.repo.reasoning_entity_config(tenant)
        return getattr(self.repo, "ENTITY_CONFIG")

    def _link_config(self, tenant):
        if hasattr(self.repo, "reasoning_link_config"):
            return self.repo.reasoning_link_config(tenant)
        return getattr(self.repo, "LINK_CONFIG")

    # ------------------------------------------------------------------
    # Step 2: Artifact descriptions
    # ------------------------------------------------------------------

    def _artifact_descriptions(self, tenant, keys):
        arts = self.repo._approved_artifacts(tenant, keys)
        return {k: v.get("description") or "" for k, v in arts.items()}

    # ------------------------------------------------------------------
    # Step 3: Format entity properties
    # ------------------------------------------------------------------

    def _format_properties(self, row):
        """Format an entity's own properties for display, excluding its
        identifier and label. A graph-backed row is already a flat property
        dict -- unlike a SQL row, it has no foreign-key columns to filter out
        (a graph vertex's relationships live as real edges, never as extra
        row columns), so no schema introspection is needed to know what to
        skip. "type" is also skipped -- it's the vertex's real Nebula TAG
        name (surfaced by _fetch_entity for entity_config lookups/neighbor
        bucketing), not a fact about the entity, and leaving it in falsely
        makes a graph-native row's props list non-empty: that silently
        starved _llm_derive_relational_answer's "no props -> fall back to
        this center's own edges" heuristic of real facts for every
        multi-center/comparison question (observed as a ~24-point graph-hit
        regression -- 94.3% to 70.0% -- when this was fixed elsewhere but
        missed here)."""
        skip = {"id", "label", "type"}
        import re
        _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
        props = []
        for k, v in row.items():
            if k in skip or v is None:
                continue
            v_str = str(_jsonable(v))
            if not v_str.strip() or len(v_str) > 200:
                continue
            if hasattr(v, "strftime"):
                v_str = v.strftime("%Y-%m-%d")
            elif _date_re.match(v_str):
                v_str = v_str[:10]
            props.append({"col": k, "value": v_str})
        return props

    # ------------------------------------------------------------------
    # Step 7: Self-referencing link resolution
    # ------------------------------------------------------------------

    def _resolve_self_refs(self, tenant, object_type, row, cfg):
        refs = {}
        for lc in self._link_config(tenant):
            if lc["from"] == object_type.lower() and lc["to"] == object_type.lower() and lc.get("reverse"):
                fk_col = lc["fk_col"]
                fk_val = row.get(fk_col)
                if fk_val:
                    parent = self.repo._fetch_entity(tenant, object_type, str(int(float(fk_val))))
                    if parent:
                        node = self.repo._entity_node(tenant, object_type, parent)
                        refs[fk_col] = {"id": str(int(float(fk_val))), "label": node.get("label", str(fk_val))}
        return refs

    # ------------------------------------------------------------------
    # LLM-enhanced question-to-relation mapping
    # ------------------------------------------------------------------

    def _get_llm_planner(self):
        """Lazily initialize the LLM planner if not provided.

        Only activates when explicitly enabled via constructor argument
        or the ALETHEIA_LLM_PLANNER_ENABLED environment variable.
        This prevents tests and non-LLM deployments from making
        network calls.
        """
        if self._llm_planner is not None:
            return self._llm_planner
        if not os.environ.get("ALETHEIA_LLM_PLANNER_ENABLED", "").lower() in ("1", "true", "yes"):
            return None
        try:
            self._llm_planner = LLMPlanner()
        except Exception:
            self._llm_planner = None
        return self._llm_planner

    def _llm_map_question_to_relations(
        self,
        question: str,
        topic_type: str,
        link_config: list[dict],
        descriptions: dict[str, str],
    ) -> PlannerMapping:
        """Use LLM to map question to relevant relation types.

        Returns a PlannerMapping. Empty mapping means LLM is unavailable,
        failed, or intentionally fell back to keyword planning.
        Falls back to empty sets if LLM is unavailable or fails.
        """
        planner = self._get_llm_planner()
        if planner is None:
            return PlannerMapping()

        result = planner.map_question_to_relations(
            question=question,
            topic_type=topic_type,
            link_config=link_config,
            descriptions=descriptions,
            capabilities=self.RETRIEVAL_CAPABILITIES,
        )

        if result.used_fallback or not result.matched_link_keys:
            # Preserve selected_capabilities/entity_mentions even when no
            # relation was matched -- a pure entity-comparison question may
            # legitimately select zero relations while still needing its
            # capabilities/entity_mentions judgment carried through.
            return PlannerMapping(
                latency_ms=result.latency_ms,
                model=result.model,
                used_fallback=result.used_fallback,
                error=result.error,
                error_type=getattr(result, "error_type", ""),
                selected_capabilities=set() if result.used_fallback else set(result.selected_capabilities),
                entity_mentions=[] if result.used_fallback else list(result.entity_mentions),
            )

        return result

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.environ.get(name)
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        value = os.environ.get(name)
        if value is None or not value.strip():
            return default
        try:
            return int(value)
        except ValueError:
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        value = os.environ.get(name)
        if value is None or not value.strip():
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def _rank_llm_link_keys(self, llm_mapping: PlannerMapping) -> list[str]:
        """Return LLM-selected link keys in model rank order when available."""
        ranked = []
        seen = set()
        for key in llm_mapping.ranked_link_keys:
            if key in llm_mapping.matched_link_keys and key not in seen:
                ranked.append(key)
                seen.add(key)

        remaining = [
            key for key in llm_mapping.matched_link_keys
            if key not in seen
        ]
        remaining.sort(
            key=lambda key: (-llm_mapping.confidence_scores.get(key, 0.0), key)
        )
        return ranked + remaining

    def _link_config_by_key(self, link_config: list[dict] | None) -> dict[str, dict]:
        return {lc["link"]: lc for lc in (link_config or []) if lc.get("link")}

    def _topic_compatible_link_key(
        self,
        link_key: str,
        object_type: str,
        link_by_key: dict[str, dict],
    ) -> bool:
        if not object_type:
            return True
        lc = link_by_key.get(link_key)
        if not lc:
            return False
        obj = object_type.lower()
        return lc.get("from", "").lower() == obj or lc.get("to", "").lower() == obj

    def _topic_compatibility_rank(
        self,
        link_key: str,
        object_type: str,
        link_by_key: dict[str, dict],
    ) -> int:
        if not object_type:
            return 0
        lc = link_by_key.get(link_key)
        if not lc:
            return 2
        obj = object_type.lower()
        if lc.get("from", "").lower() == obj:
            return 0
        if lc.get("to", "").lower() == obj:
            return 1
        return 2

    def _rerank_topic_compatible_link_keys(
        self,
        ranked: list[str],
        object_type: str,
        link_config: list[dict] | None,
    ) -> list[str]:
        """Prefer relations whose domain/range touches the topic type.

        WebQSP relation sets contain many cross-domain candidates. The LLM can
        rank semantically adjacent but wrong-domain relations above usable topic
        relations. Keep the model's relative order inside each bucket, but put
        topic-compatible links first when they exist.
        """
        if not ranked or not object_type or not link_config:
            return ranked
        link_by_key = self._link_config_by_key(link_config)
        if not any(self._topic_compatible_link_key(key, object_type, link_by_key) for key in ranked):
            return ranked
        return sorted(
            ranked,
            key=lambda key: self._topic_compatibility_rank(key, object_type, link_by_key),
        )

    def _llm_convergence_trace(
        self,
        llm_mapping: PlannerMapping,
        object_type: str = "",
        link_config: list[dict] | None = None,
    ) -> dict:
        """Return convergence output plus diagnostic trace without changing policy."""
        ranked = self._rank_llm_link_keys(llm_mapping)
        ranked = self._rerank_topic_compatible_link_keys(ranked, object_type, link_config)
        min_confidence = self._env_float("ALETHEIA_LLM_PLANNER_MIN_CONFIDENCE", 0.0)
        top_k = self._env_int("ALETHEIA_LLM_PLANNER_TOP_K", 10)
        trace = {
            "llm_ranked_link_keys": list(ranked),
            "min_confidence": min_confidence,
            "top_k": top_k,
            "confidence_filtered_link_keys": [],
            "top_k_link_keys": [],
            "truncated_link_keys": [],
            "selected_after_convergence_keys": [],
            "selection_sources": {},
        }
        ranked_before_confidence = list(ranked)
        if min_confidence > 0:
            ranked = [
                key for key in ranked
                if llm_mapping.confidence_scores.get(key, 0.0) >= min_confidence
            ]
            trace["confidence_filtered_link_keys"] = [
                key for key in ranked_before_confidence if key not in ranked
            ]

        ranked_before_top_k = list(ranked)
        if top_k > 0:
            ranked = ranked[:top_k]
            trace["truncated_link_keys"] = ranked_before_top_k[top_k:]
        trace["top_k_link_keys"] = list(ranked)

        converged = set(ranked)
        trace["selected_after_convergence_keys"] = sorted(converged)
        trace["selection_sources"] = {key: ["llm_top_k"] for key in converged}
        return trace

    # ------------------------------------------------------------------
    # Question-to-path planning
    # ------------------------------------------------------------------

    @dataclass
    class QuestionPathPlan:
        """Describes which retrieval paths to execute based on question analysis."""
        question: str | None = None
        selected_link_keys: set[str] = field(default_factory=set)
        selected_target_types: set[str] = field(default_factory=set)
        keyword_link_keys: set[str] = field(default_factory=set)
        llm_link_keys: set[str] = field(default_factory=set)
        llm_ranked_link_keys: list[str] = field(default_factory=list)
        llm_confidence_scores: dict[str, float] = field(default_factory=dict)
        llm_convergence_applied: bool = False
        selected_after_convergence_keys: list[str] = field(default_factory=list)
        llm_confidence_filtered_link_keys: list[str] = field(default_factory=list)
        llm_truncated_link_keys: list[str] = field(default_factory=list)
        llm_top_k_link_keys: list[str] = field(default_factory=list)
        planner_selection_sources: dict[str, list[str]] = field(default_factory=dict)
        planner_convergence_config: dict[str, int | float] = field(default_factory=dict)
        admissible_chains: list[tuple[str, str]] = field(default_factory=list)
        include_rankings: bool = True
        include_link_stats: bool = True
        include_value_aggs: bool = True
        include_source_key_profile: bool = True
        include_self_refs: bool = True
        is_full_aggregation: bool = True
        # Specific named entities the question refers to, when it names
        # more than one -- not tied to any particular question "type"
        # (comparison, relationship-check, joint analysis, ...); a question
        # naming zero/one entity leaves this empty. Resolving these text
        # spans to real instance ids (see _resolve_entity_mentions) is
        # optional and only happens when the caller supplies candidate_labels.
        entity_mentions: list[str] = field(default_factory=list)
        resolved_entity_centers: list[str] = field(default_factory=list)

    # Open registry of retrieval capabilities this engine can execute, each
    # tied to a concrete method below -- NOT a fixed taxonomy of "question
    # types". Passed to the LLM planner (see _llm_map_question_to_relations)
    # so it can select which ones this question needs; extending the engine
    # with a new capability only means adding an entry here, never touching
    # QuestionPathPlan's schema or the planner's prompt structure.
    #
    # Empty for now: "rankings"/"link_stats"/"value_aggregation"/
    # "source_key_profile" were SQL-join-only features (peer ranking via
    # GROUP BY, per-link aggregation, multi-hop value totals, shared-source-key
    # profiling) with no graph-native replacement built yet, so there is
    # nothing to advertise to the LLM planner -- retrieval today is path-
    # finding + relational derivation over real graph edges.
    RETRIEVAL_CAPABILITIES: dict[str, str] = {}

    # Default synonym-to-entity-type mapping (configurable per tenant/domain).
    # Keys are lowercase synonyms/paraphrases; values are the canonical
    # entity type keys they should resolve to.
    DEFAULT_ENTITY_TYPE_SYNONYMS: dict[str, list[str]] = {
        "evidence": ["risk_indicator"],
        "hazard signal": ["risk_indicator"],
        "indicator": ["risk_indicator"],
        "barrier": ["chokepoint"],
        "strait": ["chokepoint"],
        "canal": ["chokepoint"],
        "alternative route": ["trade_route"],
        "shipping route": ["trade_route"],
        "disruption": ["chokepoint"],
        "dependency": ["trade_dependency"],
        "trade flow": ["trade_dependency"],
        "finding": ["risk_finding"],
        "risk result": ["systemic_risk_result"],
        "systemic risk": ["systemic_risk_result"],
        "impact": ["systemic_risk_result"],
        "action": ["mitigation_action"],
        "recommendation": ["mitigation_action"],
        "mitigation": ["mitigation_action"],
        "nation": ["country"],
        "state": ["country"],
    }

    def _resolve_entity_types_from_question(
        self,
        question: str,
        known_types: set[str],
        synonym_map: dict[str, list[str]] | None = None,
    ) -> set[str]:
        """Resolve entity types from question using synonym/paraphrase mapping.

        Returns the set of canonical entity type keys that the question
        references via synonyms or paraphrases, even when the exact type
        name is not present in the question text.
        """
        if not question:
            return set()

        q_lower = question.lower()
        q_normalized = q_lower.replace("_", " ")
        synonyms = synonym_map if synonym_map is not None else self.DEFAULT_ENTITY_TYPE_SYNONYMS
        resolved: set[str] = set()

        for synonym, type_keys in synonyms.items():
            synonym_spaced = synonym.replace("_", " ")
            if synonym in q_lower or synonym_spaced in q_normalized:
                for tk in type_keys:
                    if tk.lower() in known_types:
                        resolved.add(tk.lower())

        return resolved

    @staticmethod
    def _strip_parenthetical_and_normalize(text: str) -> str:
        stripped = re.sub(r"\s*\([^)]*\)", "", text or "").strip()
        return re.sub(r"[^0-9a-z ]+", " ", stripped.lower()).split()

    @classmethod
    def _resolve_entity_mentions(cls, entity_mentions: list[str], candidate_labels: list[str]) -> list[str]:
        """Which of a known set of entity labels match the LLM's extracted mentions?

        Matches each candidate label (disambiguating suffixes like "(1574)"
        stripped before matching) against the LLM's own extracted entity
        mentions (``PlannerMapping.entity_mentions``) rather than
        re-scanning the raw question text, since the LLM's extraction is the
        more precise signal (avoids false positives from incidental token
        overlap elsewhere in the question). Not tied to any question "type"
        -- this serves any question naming multiple specific entities,
        whatever the
        reason (comparison, relationship-check, joint analysis, ...).
        """
        mention_tokens = set()
        for mention in entity_mentions:
            mention_tokens |= set(re.sub(r"[^0-9a-z ]+", " ", (mention or "").lower()).split())
        resolved = []
        for label in candidate_labels:
            core_tokens = cls._strip_parenthetical_and_normalize(label)
            if core_tokens and all(tok in mention_tokens for tok in core_tokens):
                resolved.append(label)
        return resolved

    def _plan_question_paths(
        self,
        question,
        object_type,
        entity_config,
        link_config,
        descriptions,
        candidate_labels=None,
    ):
        """Map a reasoning question to type-constrained retrieval paths.

        Inspired by OntGQA planner-judge: the planner predicts which entity
        types and relation types are relevant to the question, then constrains
        the admissible retrieval paths. When the question is absent or does not
        match any known type, falls back to full aggregation.
        """
        plan = self.QuestionPathPlan(
            question=question,
            selected_link_keys=set(),
            selected_target_types=set(),
            admissible_chains=[],
            is_full_aggregation=True,
        )
        if not question or not question.strip():
            return plan

        q_lower = question.lower()

        known_types = {k.lower() for k in entity_config.keys()}
        for lc in link_config:
            known_types.add(lc["to"].lower())
            known_types.add(lc["from"].lower())

        # Build bidirectional adjacency: forward (from->to) and reverse (to->from)
        links_from = {}
        links_to = {}
        for lc in link_config:
            links_from.setdefault(lc["from"].lower(), []).append(lc)
            links_to.setdefault(lc["to"].lower(), []).append(lc)

        # Normalize underscores to spaces for natural-language matching
        q_normalized = q_lower.replace("_", " ")

        # Resolve synonyms/paraphrases to entity type names before exact matching
        synonym_resolved_types = self._resolve_entity_types_from_question(
            question, known_types
        )

        matched_types = set()
        for t in known_types:
            t_spaced = t.replace("_", " ")
            if t in q_lower or t_spaced in q_normalized:
                matched_types.add(t)
            elif t + "s" in q_lower or t_spaced + "s" in q_normalized:
                matched_types.add(t)
            elif t + "es" in q_lower or t_spaced + "es" in q_normalized:
                matched_types.add(t)
            elif t.endswith("y") and (t[:-1] + "ies" in q_lower or t_spaced[:-1] + "ies" in q_normalized):
                matched_types.add(t)

        # Merge synonym-resolved types into matched_types
        matched_types |= synonym_resolved_types

        # LLM-enhanced mapping: primary path for question->relation selection.
        # Rank and confidence are preserved so downstream planning can reduce
        # fanout without losing the fallback keyword path.
        llm_mapping = self._llm_map_question_to_relations(
            question, object_type, link_config, descriptions
        )
        llm_link_keys = set(llm_mapping.matched_link_keys)
        llm_ranked_link_keys = self._rank_llm_link_keys(llm_mapping)
        plan.llm_link_keys = llm_link_keys
        plan.llm_ranked_link_keys = llm_ranked_link_keys
        plan.llm_confidence_scores = dict(llm_mapping.confidence_scores)

        # Retrieval capabilities are selected by the LLM itself against the
        # open RETRIEVAL_CAPABILITIES registry (see llm_planner.DEFAULT_SYSTEM_PROMPT),
        # not keyword matching -- when the LLM is unavailable/fails
        # (used_fallback=True) this stays empty, which leaves the plan at
        # its safe is_full_aggregation=True default below rather than guessing.
        selected_capabilities = llm_mapping.selected_capabilities
        plan.entity_mentions = list(llm_mapping.entity_mentions)
        if llm_mapping.entity_mentions and candidate_labels:
            plan.resolved_entity_centers = self._resolve_entity_mentions(
                llm_mapping.entity_mentions, candidate_labels
            )

        llm_types = {t.lower() for t in llm_mapping.matched_entity_types}
        if llm_types:
            matched_types |= llm_types

        keyword_link_keys = set()
        obj_lower = object_type.lower()

        # Forward links: center entity is the "from" side
        for lc in link_config:
            if lc["from"] != obj_lower:
                continue
            target = lc["to"].lower()
            link_key = lc["link"]
            link_desc = (descriptions.get(link_key, "") or "").lower()
            if target in matched_types:
                keyword_link_keys.add(link_key)
            elif link_desc:
                # Match on description words, excluding the center entity type
                # itself (otherwise every question about a chokepoint matches
                # links whose description merely mentions "chokepoint").
                desc_words = [w for w in link_desc.split()
                              if len(w) > 3 and w != obj_lower]
                if any(word in q_lower for word in desc_words):
                    keyword_link_keys.add(link_key)

        # Reverse links: center entity is the "to" side
        for lc in link_config:
            if lc["to"] != obj_lower:
                continue
            source_type = lc["from"].lower()
            link_key = lc["link"]
            link_desc = (descriptions.get(link_key, "") or "").lower()
            if source_type in matched_types:
                keyword_link_keys.add(link_key)
            elif link_desc:
                desc_words = [w for w in link_desc.split()
                              if len(w) > 3 and w != obj_lower]
                if any(word in q_lower for word in desc_words):
                    keyword_link_keys.add(link_key)

        # 2-hop chain enumeration (bidirectional)
        admissible_chains: list[tuple[str, str]] = []
        if len(matched_types) >= 2:
            for link_a in links_from.get(obj_lower, []):
                # Forward+forward: obj ->A-> mid ->B-> target
                mid_type = link_a["to"].lower()
                for link_b in links_from.get(mid_type, []):
                    if link_b["to"].lower() in matched_types and link_b["to"].lower() != obj_lower:
                        admissible_chains.append((link_a["link"], link_b["link"]))
                        keyword_link_keys.add(link_a["link"])
                # Forward+reverse: obj ->A-> mid <-B- target
                for link_b in links_to.get(mid_type, []):
                    if link_b["from"].lower() in matched_types and link_b["from"].lower() != obj_lower:
                        admissible_chains.append((link_a["link"], link_b["link"]))
                        keyword_link_keys.add(link_a["link"])

            for link_a in links_to.get(obj_lower, []):
                # Reverse+forward: obj <-A- mid ->B-> target
                mid_type = link_a["from"].lower()
                for link_b in links_from.get(mid_type, []):
                    if link_b["to"].lower() in matched_types and link_b["to"].lower() != obj_lower:
                        admissible_chains.append((link_a["link"], link_b["link"]))
                        keyword_link_keys.add(link_a["link"])
                # Reverse+reverse: obj <-A- mid <-B- target
                for link_b in links_to.get(mid_type, []):
                    if link_b["from"].lower() in matched_types and link_b["from"].lower() != obj_lower:
                        admissible_chains.append((link_a["link"], link_b["link"]))
                        keyword_link_keys.add(link_a["link"])

        convergence_enabled = (
            (bool(llm_link_keys) or (bool(llm_mapping.model) and not llm_mapping.used_fallback))
            and self._env_bool("ALETHEIA_LLM_PLANNER_CONVERGENCE_ENABLED", True)
        )
        include_keyword_union = self._env_bool(
            "ALETHEIA_LLM_PLANNER_INCLUDE_KEYWORD_UNION", False
        )
        if convergence_enabled:
            convergence_trace = self._llm_convergence_trace(
                llm_mapping,
                object_type=object_type,
                link_config=link_config,
            )
            converged_llm_keys = set(convergence_trace["selected_after_convergence_keys"])
            plan.selected_after_convergence_keys = list(convergence_trace["selected_after_convergence_keys"])
            plan.llm_confidence_filtered_link_keys = list(convergence_trace["confidence_filtered_link_keys"])
            plan.llm_truncated_link_keys = list(convergence_trace["truncated_link_keys"])
            plan.llm_top_k_link_keys = list(convergence_trace["top_k_link_keys"])
            plan.planner_selection_sources = {
                key: list(value)
                for key, value in convergence_trace["selection_sources"].items()
            }
            plan.planner_convergence_config = {
                "min_confidence": convergence_trace["min_confidence"],
                "top_k": convergence_trace["top_k"],
            }
            if converged_llm_keys and include_keyword_union:
                matched_link_keys = keyword_link_keys | converged_llm_keys
            elif converged_llm_keys:
                matched_link_keys = converged_llm_keys
                plan.llm_convergence_applied = True
            else:
                matched_link_keys = set(keyword_link_keys)
        else:
            matched_link_keys = set(keyword_link_keys) | llm_link_keys

        plan.keyword_link_keys = set(keyword_link_keys)

        if matched_link_keys or matched_types or selected_capabilities:
            plan.is_full_aggregation = False
            if matched_link_keys:
                plan.selected_link_keys = matched_link_keys
            else:
                # Fallback: include both forward and reverse links from this entity
                plan.selected_link_keys = {lc["link"] for lc in link_config
                                           if lc["from"] == obj_lower or lc["to"] == obj_lower}
            plan.selected_target_types = matched_types
            plan.include_rankings = "rankings" in selected_capabilities or bool(matched_link_keys)
            plan.include_link_stats = "link_stats" in selected_capabilities or bool(matched_link_keys)
            plan.include_value_aggs = "value_aggregation" in selected_capabilities
            plan.include_source_key_profile = "source_key_profile" in selected_capabilities or not matched_link_keys
            plan.include_self_refs = True
            plan.admissible_chains = admissible_chains

        return plan

    # ------------------------------------------------------------------
    # Main entry: analyze
    # ------------------------------------------------------------------

    def _gather_center_data(self, tenant, center_node, entity_config, link_config, path_plan, depth, limit):
        """Fetch + assemble everything ``_compose``/``_compose_relational`` need
        for ONE center. Extracted from ``analyze()`` so multi-center
        resolution can call this once per center without duplicating the
        single-center pipeline. Returns None if the entity/graph isn't found
        (same "not found" semantics as the single-center path)."""
        if not center_node or ":" not in center_node:
            return None
        object_type, instance_id = center_node.split(":", 1)
        cfg = entity_config.get(object_type.lower())
        if not cfg:
            return None

        row = self.repo._fetch_entity(tenant, object_type, instance_id)
        if not row:
            return None

        graph = self.repo.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
        if not graph or not graph.get("approved"):
            return None

        center = graph.get("center") or {}
        label = center.get("label") or center_node
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []

        desc_keys = [cfg.get("artifact", f"object:{object_type}")]
        for lc in link_config:
            if lc["from"] == object_type.lower() or lc["to"] == object_type.lower():
                desc_keys.append(lc["link"])
        descriptions = self._artifact_descriptions(tenant, desc_keys)
        entity_desc = descriptions.get(cfg.get("artifact", ""), "")

        self_refs = self._resolve_self_refs(tenant, object_type, row, cfg) if path_plan.include_self_refs else {}
        props = self._format_properties(row)

        # Bucketed by (type, connecting relation) when the neighbor has a
        # direct edge to this center, not just type -- a single-TAG graph
        # tenant (every vertex the same "type") would otherwise dump every
        # neighbor into one undifferentiated bucket, and _compose's "first 5
        # + N more" narrative sampling would truncate away whichever
        # relation actually answers the question on any tenant with more
        # than a handful of neighbors of the same type. Multi-hop neighbors
        # (no direct edge to this center) fall back to a type-only bucket,
        # same as before.
        center_id = center.get("id")
        relation_by_neighbor_id = {}
        for edge in edges:
            if edge.get("source") == center_id:
                relation_by_neighbor_id[edge.get("target")] = edge.get("label", "")
            elif edge.get("target") == center_id:
                relation_by_neighbor_id[edge.get("source")] = edge.get("label", "")

        neighbors_by_type = {}
        for node in nodes:
            if node.get("id") == center_id:
                continue
            ntype = node.get("type", "unknown")
            relation = relation_by_neighbor_id.get(node.get("id"))
            bucket_key = f"{ntype}: {relation}" if relation else ntype
            neighbors_by_type.setdefault(bucket_key, []).append(node)

        return {
            "center_node": center_node,
            "object_type": object_type,
            "instance_id": instance_id,
            "label": label,
            "cfg": cfg,
            "entity_desc": entity_desc,
            "descriptions": descriptions,
            "props": props,
            "self_refs": self_refs,
            "neighbors_by_type": neighbors_by_type,
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def _find_path_between_centers(center_node, target_center_node, nodes, edges):
        """BFS over one center's own fetched neighborhood (nodes/edges) for a
        real graph path to another specific center.

        This is tried BEFORE any LLM reasoning for comparison questions --
        if two named entities are actually connected in the graph (e.g. "X
        acquired Y"), that connection IS the answer, more reliable than an
        LLM guessing from independent facts. Returns the ordered list of
        edge dicts forming the shortest path, ``[]`` if the two centers are
        literally the same node, or ``None`` if unreachable within this
        neighborhood (which is already bounded by the caller's depth/limit).
        """
        if center_node == target_center_node:
            return []
        adjacency = {}
        for edge in edges or []:
            source = edge.get("source")
            target = edge.get("target")
            if not source or not target:
                continue
            adjacency.setdefault(source, []).append(edge)
            adjacency.setdefault(target, []).append({**edge, "source": target, "target": source, "_reversed": True})

        visited = {center_node}
        queue = deque([(center_node, [])])
        while queue:
            node_id, path = queue.popleft()
            for edge in adjacency.get(node_id, []):
                other = edge.get("target")
                if not other or other in visited:
                    continue
                next_path = path + [edge]
                if other == target_center_node:
                    return next_path
                visited.add(other)
                queue.append((other, next_path))
        return None

    @staticmethod
    def _build_evidence_chains(center_instance_id, nodes, edges, max_chains=30):
        """BFS from ``center_instance_id`` over its own already-gathered
        neighborhood (``nodes``/``edges`` -- depth-limited by
        ``_gather_center_data``'s own ``depth`` param, no extra graph calls
        here), enumerating every distinct reachable node's path and
        formatting each as an explicit "evidence chain" string, e.g.
        ``"Ludwig van Beethoven -composed-> Violin Sonata No. 4
        -dedicated_to-> Count Moritz von Fries"``. Borrowed from StepChain
        GraphRAG's BFS Reasoning Flow (arXiv:2510.02827 Eq. 7-9), replacing
        the previous flat "direct edge, or floating same-string endpoint
        pair for anything deeper" fact representation, which relied on the
        model noticing a shared label string across two disconnected facts
        to chain them itself and had no representation at all past depth 2.
        Reuses the same visited-set BFS shape already proven in
        ``_find_path_between_centers``, just enumerating every reachable
        node instead of stopping at one target. ``max_chains`` bounds
        output size for centers with a large neighborhood -- BFS order
        means the nodes it drops are the FARTHEST from the center, not an
        arbitrary sample."""
        nodes_by_id = {n.get("id"): n for n in nodes or []}
        adjacency: dict[str, list[tuple[str, str, bool]]] = {}
        for edge in edges or []:
            source = edge.get("source")
            target = edge.get("target")
            if not source or not target:
                continue
            adjacency.setdefault(source, []).append((target, edge.get("label", ""), False))
            adjacency.setdefault(target, []).append((source, edge.get("label", ""), True))

        chains: list[list[tuple[str, str, bool]]] = []
        visited = {center_instance_id}
        queue = deque([(center_instance_id, [])])
        while queue and len(chains) < max_chains:
            node_id, path = queue.popleft()
            for neighbor_id, relation, reversed_edge in adjacency.get(node_id, []):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                new_path = path + [(relation, neighbor_id, reversed_edge)]
                chains.append(new_path)
                queue.append((neighbor_id, new_path))
                if len(chains) >= max_chains:
                    break

        center_label = nodes_by_id.get(center_instance_id, {}).get("label", center_instance_id)
        chain_strings = []
        for path in chains:
            parts = [center_label]
            for relation, node_id, reversed_edge in path:
                label = nodes_by_id.get(node_id, {}).get("label") or node_id
                parts.append(f" <-{relation}- " if reversed_edge else f" -{relation}-> ")
                parts.append(label)
            chain_strings.append("".join(parts))
        return chain_strings

    @staticmethod
    def _build_centers_facts(centers_data):
        """Shared by ``_llm_derive_relational_answer`` (single-shot
        multi-center questions) and ``analyze_decomposed`` (one call per
        sub-question) -- turns gathered center data into the
        ``[{"center_node": str, "facts": [...]}]`` shape
        ``LLMPlanner.derive_relational_answer`` expects, deliberately
        EXCLUDING each center's own identity/label from what's sent, so the
        model must derive the answer from the actual facts rather than
        recognizing which name happens to match (closes the leakage bug
        found in the HotpotQA Nebula benchmark -- see GraphHitJudge's Dain
        Rauscher Wessels/Berenberg Bank case)."""
        centers_facts = []
        for data in centers_data:
            facts = []
            for prop in data.get("props") or []:
                facts.append({"relation": prop.get("col", ""), "value": prop.get("value", "")})
            if not facts:
                # Graph-native entities (a single Nebula TAG with no
                # per-vertex properties beyond id/label) carry their real
                # facts as edges, not row columns -- fall back to explicit
                # BFS evidence chains over this center's own already-
                # gathered neighborhood (see _build_evidence_chains). Only
                # engages when props is empty, so SQL tenants with real
                # column data are unaffected.
                instance_id = data.get("instance_id", "")
                for chain in ReasoningEngine._build_evidence_chains(
                    instance_id, data.get("nodes"), data.get("edges"),
                ):
                    facts.append({"relation": "evidence_chain", "value": chain})
            centers_facts.append({
                "center_node": data["center_node"],
                # Deliberately no "label"/identity field here -- see
                # docstring above.
                "facts": facts,
            })
        return centers_facts

    def _llm_derive_relational_answer(self, question, centers_data):
        """When no direct graph path connects the named centers, derive an
        answer via one LLM call given each center's own relevant facts --
        explicitly EXCLUDING each center's own identity/label from what's
        sent, so the model must reason from the actual facts rather than
        recognizing which name happens to match. Not comparison-specific --
        serves any question naming multiple entities, whatever shape the
        answer takes (picking one center, naming a trait shared by several
        of them, describing a relationship, ...). Returns a dict with
        resolution metadata; gracefully degrades (no crash) if the LLM
        planner isn't available/enabled."""
        planner = self._get_llm_planner()
        if planner is None:
            return {
                "resolution": "unavailable",
                "answer": None,
                "supporting_center_nodes": [],
                "supporting_labels": [],
                "reasoning": "LLM planner not enabled -- cannot derive an answer without a direct graph path.",
                "error": "llm_planner_unavailable",
            }

        centers_facts = self._build_centers_facts(centers_data)
        derivation = planner.derive_relational_answer(question, centers_facts)
        if derivation.used_fallback:
            return {
                "resolution": "llm_reasoning",
                "answer": None,
                "supporting_center_nodes": [],
                "supporting_labels": [],
                "reasoning": "",
                "error": derivation.error,
                "error_type": derivation.error_type,
            }

        by_center_node = {d["center_node"]: d for d in centers_data}
        supporting_labels = [
            by_center_node[c]["label"] for c in derivation.supporting_center_nodes if c in by_center_node
        ]
        return {
            "resolution": "llm_reasoning",
            "answer": derivation.answer,
            "supporting_center_nodes": list(derivation.supporting_center_nodes),
            "supporting_labels": supporting_labels,
            "reasoning": derivation.reasoning,
            "error": "",
        }

    def _analyze_multi_center(self, tenant, center_node, additional_center_nodes, question,
                               entity_config, link_config, path_plan, depth, limit):
        """Multi-center resolution flow: gather each center independently
        (same per-center pipeline as single-entity analyze()), try a direct
        graph path between them first, and only fall back to LLM reasoning
        over each center's own facts when no such path exists. General
        mechanism -- doesn't assume WHY multiple entities were named
        (comparison, relationship-check, joint analysis, ...)."""
        all_center_nodes = [center_node] + additional_center_nodes
        centers_data = []
        for node in all_center_nodes:
            data = self._gather_center_data(tenant, node, entity_config, link_config, path_plan, depth, limit)
            if data is None:
                return {
                    "title": f"{node} profile unavailable",
                    "profile_summary": f"{node} not found in the controlled data source -- cannot relate.",
                    "key_facts": [],
                    "business_interpretation": ["Entity record missing for one of the named centers."],
                    "evidence_limits": [f"Missing source table record for {node}."],
                    "next_questions": [],
                }
            centers_data.append(data)

        relation = None
        for other in centers_data[1:]:
            path = self._find_path_between_centers(
                centers_data[0]["center_node"], other["center_node"],
                centers_data[0]["nodes"], centers_data[0]["edges"],
            )
            if path is not None:
                relation = {
                    "resolution": "path_found",
                    "path": path,
                    "answer": None,
                    "supporting_center_nodes": [],
                    "supporting_labels": [],
                    "reasoning": f"Direct graph path found between {centers_data[0]['label']} and {other['label']}.",
                    "error": "",
                }
                break

        if relation is None:
            relation = self._llm_derive_relational_answer(question, centers_data)
            relation.setdefault("path", None)

        return self._compose_relational(centers_data, relation, question, path_plan)

    def analyze(self, tenant, center_node, question=None, depth=1, limit=200, additional_center_nodes=None):
        if not center_node or ":" not in center_node:
            return None
        object_type, instance_id = center_node.split(":", 1)
        entity_config = self._entity_config(tenant)
        link_config = self._link_config(tenant)
        cfg = entity_config.get(object_type.lower())
        if not cfg:
            return None

        # --- Question-driven path planning (shared across all centers when
        # comparing -- the question is asked once, not once per center) ---
        desc_keys = [cfg.get("artifact", f"object:{object_type}")]
        for lc in link_config:
            if lc["from"] == object_type.lower() or lc["to"] == object_type.lower():
                desc_keys.append(lc["link"])
        descriptions_for_plan = self._artifact_descriptions(tenant, desc_keys)
        path_plan = self._plan_question_paths(
            question,
            object_type,
            entity_config,
            link_config,
            descriptions_for_plan,
        )

        # Deduplicated against the primary and each other -- extraction
        # pipelines sometimes name the primary center again as one of its
        # own "additional" mentions, which would otherwise make
        # _analyze_multi_center/_find_path_between_centers find a trivial
        # zero-hop "path" from the primary to itself and short-circuit
        # before ever considering a real second entity.
        deduped_additional = [n for n in dict.fromkeys(additional_center_nodes or []) if n and n != center_node]
        if deduped_additional:
            return self._analyze_multi_center(
                tenant, center_node, deduped_additional, question,
                entity_config, link_config, path_plan, depth, limit,
            )

        data = self._gather_center_data(tenant, center_node, entity_config, link_config, path_plan, depth, limit)
        if data is None:
            row = self.repo._fetch_entity(tenant, object_type, instance_id)
            if not row:
                return {
                    "title": f"{center_node} profile unavailable",
                    "profile_summary": f"{center_node} not found in the controlled data source.",
                    "key_facts": [],
                    "business_interpretation": ["Entity record missing — cannot perform analysis."],
                    "evidence_limits": [f"Missing {object_type} source table record."],
                    "next_questions": ["Verify entity ID exists in the current tenant data source."],
                }
            return None

        return self._compose(
            center_node=data["center_node"],
            object_type=data["object_type"],
            instance_id=data["instance_id"],
            label=data["label"],
            cfg=data["cfg"],
            entity_desc=data["entity_desc"],
            descriptions=data["descriptions"],
            props=data["props"],
            self_refs=data["self_refs"],
            neighbors_by_type=data["neighbors_by_type"],
            nodes=data["nodes"],
            edges=data["edges"],
            question=question,
            path_plan=path_plan,
        )

    def analyze_decomposed(self, tenant, question, sub_question_centers, depth=1, limit=200):
        """Question-decomposition entry point -- borrowed from StepChain
        GraphRAG (arXiv:2510.02827), whose own ablation study found
        decomposition to be the single biggest lever on HotpotQA accuracy.
        Entity linking (splitting ``question`` into sub-questions and
        resolving each to graph ids) stays the CALLER's job, same division
        of labor ``analyze()`` already has with its pre-resolved
        ``center_node``/``additional_center_nodes`` params.

        ``sub_question_centers``: ``[{"sub_question": str, "center_node":
        str, "additional_center_nodes": [str, ...]}, ...]``. An entry whose
        ``center_node`` is empty (that sub-question's entities never
        resolved) is recorded with a ``None`` partial answer and skipped
        for gathering -- not a hard failure; the final merge works with
        whatever partial answers DID resolve.

        For each entry with a resolved center, gathers facts per center
        (reusing ``_gather_center_data``, exactly ``_analyze_multi_center``'s
        own per-center loop when an entry names more than one), turns them
        into a partial answer via ``LLMPlanner.derive_relational_answer``
        keyed by that entry's OWN sub-question text (not the original
        question) -- mirrors StepChain's Eq. 9's per-sub-question evidence
        chain -> partial answer step. All partial answers are then combined
        via ``LLMPlanner.merge_partial_answers`` (StepChain's Eq. 10-11
        two-tier merge), re-grounded against the original ``question``.

        Returns ``None`` if the LLM planner isn't available at all (nothing
        useful can be produced); otherwise the same
        title/profile_summary/key_facts/business_interpretation/
        evidence_limits/next_questions/metrics shape ``_compose_relational``
        already produces, so existing scoring code (keyed off
        ``metrics.answer``/``metrics.centers``) doesn't need to change."""
        planner = self._get_llm_planner()
        if planner is None:
            return None

        entity_config = self._entity_config(tenant)
        link_config = self._link_config(tenant)

        sub_answers = []
        all_centers_data = []
        for entry in sub_question_centers:
            sub_question = entry.get("sub_question") or question
            center_node = entry.get("center_node") or ""
            if not center_node:
                sub_answers.append({"sub_question": sub_question, "answer": None, "reasoning": ""})
                continue

            nodes_to_gather = [n for n in dict.fromkeys(
                [center_node] + list(entry.get("additional_center_nodes") or [])
            ) if n]
            centers_data = []
            for node in nodes_to_gather:
                object_type = node.split(":", 1)[0]
                cfg = entity_config.get(object_type.lower())
                if not cfg:
                    continue
                # Path plan scoped to THIS sub-question's own text/type --
                # mirrors analyze()'s own path-planning setup, just done
                # once per sub-question instead of once for the whole
                # original question (each sub-question targets a different
                # entity/relation shape, so a shared plan wouldn't fit all
                # of them the way it fits centers of a single comparison).
                desc_keys = [cfg.get("artifact", f"object:{object_type}")]
                for lc in link_config:
                    if lc["from"] == object_type.lower() or lc["to"] == object_type.lower():
                        desc_keys.append(lc["link"])
                descriptions_for_plan = self._artifact_descriptions(tenant, desc_keys)
                path_plan = self._plan_question_paths(
                    sub_question, object_type, entity_config, link_config, descriptions_for_plan,
                )
                data = self._gather_center_data(tenant, node, entity_config, link_config, path_plan, depth, limit)
                if data is not None:
                    centers_data.append(data)

            if not centers_data:
                sub_answers.append({"sub_question": sub_question, "answer": None, "reasoning": ""})
                continue
            all_centers_data.extend(centers_data)

            centers_facts = self._build_centers_facts(centers_data)
            derivation = planner.derive_relational_answer(sub_question, centers_facts)
            if derivation.used_fallback or not derivation.answer:
                sub_answers.append({"sub_question": sub_question, "answer": None, "reasoning": derivation.error or ""})
            else:
                sub_answers.append({
                    "sub_question": sub_question, "answer": derivation.answer, "reasoning": derivation.reasoning,
                })

        if not all_centers_data:
            return {
                "title": "profile unavailable",
                "profile_summary": "None of the decomposed sub-questions' entities were found in the graph.",
                "key_facts": [],
                "business_interpretation": ["Entity record missing for every named sub-question center."],
                "evidence_limits": ["No sub-question resolved to a graph vertex."],
                "next_questions": [],
            }

        merged = planner.merge_partial_answers(question, sub_answers)
        # Reuses _compose_relational's existing "llm_reasoning" branch as-is
        # (only checks resolution == "llm_reasoning" and a truthy answer) --
        # the merge step IS an LLM-reasoning-over-facts derivation, just
        # synthesized from per-sub-question partial answers instead of one
        # single-shot call over all centers' facts together.
        relation = {
            "resolution": "llm_reasoning",
            "path": None,
            "answer": merged.answer if not (merged.used_fallback or not merged.answer) else None,
            "supporting_center_nodes": [d["center_node"] for d in all_centers_data],
            "supporting_labels": [d["label"] for d in all_centers_data],
            "reasoning": merged.reasoning or merged.error,
            "error": merged.error,
        }
        return self._compose_relational(all_centers_data, relation, question, None)

    # ------------------------------------------------------------------
    # Narrative builder
    # ------------------------------------------------------------------

    def _build_narrative(self, label, object_type, entity_desc, props, self_refs,
                         neighbors_by_type, question, path_plan=None):
        """Synthesize computed data into an analytical paragraph."""
        sentences = []

        # Identity sentence — role/title + context
        title_prop = next((p for p in props if p["col"].lower() in ("title", "contacttitle", "jobtitle", "role")), None)
        identity = f"{label}"
        if title_prop:
            identity += f" ({title_prop['value']})"
        if self_refs:
            ref = list(self_refs.values())[0]
            identity += f", reporting to {ref['label']}"

        sentences.append(f"{identity} is present in the approved graph with {sum(len(v) for v in neighbors_by_type.values())} related entities.")

        # Question-focused summary when path_plan is available
        if path_plan and not path_plan.is_full_aggregation and question:
            targets = sorted(path_plan.selected_target_types) if path_plan.selected_target_types else []
            if targets:
                target_text = ", ".join(targets)
                sentences.append(
                    f"Analysis focused on {target_text} based on the question: \"{question}\"."
                )

        return " ".join(sentences)

    # ------------------------------------------------------------------
    # Compose structured output
    # ------------------------------------------------------------------

    def _compose(self, *, center_node, object_type, instance_id, label, cfg,
                 entity_desc, descriptions, props, self_refs, neighbors_by_type, nodes, edges, question,
                 path_plan=None):

        key_facts = []
        interpretations = []
        source_table = cfg.get("table", object_type)

        if path_plan and not path_plan.is_full_aggregation:
            selected = sorted(path_plan.selected_link_keys) if path_plan.selected_link_keys else []
            key_facts.append({
                "label": "question_path_plan",
                "value": f"Selected paths: {', '.join(selected) if selected else 'all matching'}; targets: {', '.join(sorted(path_plan.selected_target_types)) if path_plan.selected_target_types else 'all'}",
                "source_ref": "question_driven_path_planner",
            })
            if path_plan.llm_link_keys:
                ranked = path_plan.llm_ranked_link_keys[:5]
                ranked_text = ", ".join(
                    f"{key} ({path_plan.llm_confidence_scores.get(key, 0.0):.2f})"
                    for key in ranked
                )
                key_facts.append({
                    "label": "llm_question_path_plan",
                    "value": (
                        f"LLM ranked {len(path_plan.llm_link_keys)} relation(s); "
                        f"selected {len(path_plan.selected_link_keys)} after convergence; "
                        f"keyword candidates {len(path_plan.keyword_link_keys)}; "
                        f"convergence={'on' if path_plan.llm_convergence_applied else 'off'}; "
                        f"top: {ranked_text if ranked_text else 'none'}"
                    ),
                    "source_ref": "llm_question_path_planner",
                })

        # -- Base info --
        if props:
            notable = [p for p in props if p["col"] not in ("photo", "notes", "photoPath")][:8]
            prop_text = "; ".join(f"{p['col']}: {p['value']}" for p in notable)
            key_facts.append({"label": f"{label} attributes", "value": prop_text, "source_ref": f"{source_table}"})

        # -- Self-references --
        for fk_col, ref in self_refs.items():
            key_facts.append({"label": f"{fk_col}", "value": f"{ref['label']} ({object_type}:{ref['id']})", "source_ref": source_table})

        # -- Entity type context --
        if entity_desc:
            interpretations.append(f"[{object_type} definition] {entity_desc}")

        # -- Neighbors, bucketed by (type, connecting relation) --
        for ntype, nlist in sorted(neighbors_by_type.items()):
            samples = ", ".join(n.get("label", n["id"]) for n in nlist[:5])
            suffix = f" and {len(nlist) - 5} more" if len(nlist) > 5 else ""
            key_facts.append({
                "label": f"related {ntype}",
                "value": f"{len(nlist)}: {samples}{suffix}",
                "source_ref": "graph edges",
            })

        if not interpretations:
            interpretations.append(f"{label} has {len(edges)} direct relationships in the approved graph.")

        # --- Profile summary (analytical narrative) ---
        profile_summary = self._build_narrative(
            label, object_type, entity_desc, props, self_refs, neighbors_by_type, question,
            path_plan=path_plan,
        )

        return {
            "title": f"{label} Business Profile",
            "profile_summary": profile_summary,
            "key_facts": key_facts,
            "business_interpretation": interpretations,
            "evidence_limits": [
                f"Profile based on {source_table} source table and approved graph controlled aggregation.",
                "Conclusions are based solely on the approved graph; external benchmarks, thresholds, and unapproved evidence are not included.",
            ],
            "next_questions": [
                f"How do {label}'s relationship patterns change over time?",
                f"How does {label} compare to typical {object_type}(s) in the same segment?",
                "Are there anomalous patterns or potential risks?",
            ],
            "metrics": {
                "center_node": center_node,
                "object_type": object_type,
                "instance_id": instance_id,
                "label": label,
                "neighbor_count": len(nodes) - 1,
                "edge_count": len(edges),
                "neighbor_types": {k: len(v) for k, v in neighbors_by_type.items()},
            },
        }

    def _compose_relational(self, centers_data, relation, question, path_plan):
        """Compose the final result for a multi-center question, mirroring
        ``_compose``'s shape (title/profile_summary/key_facts/
        business_interpretation/evidence_limits/next_questions/metrics) but
        relating N centers instead of profiling one. General mechanism --
        doesn't assume WHY multiple entities were named (comparison,
        relationship-check, joint analysis, ...)."""
        labels = [d["label"] for d in centers_data]
        resolution = relation.get("resolution")

        key_facts = [{
            "label": f"{d['label']} attributes",
            "value": "; ".join(f"{p['col']}: {p['value']}" for p in (d.get("props") or [])[:8]) or "(no properties)",
            "source_ref": d["center_node"],
        } for d in centers_data]

        if resolution == "path_found":
            path = relation.get("path") or []
            path_text = " -> ".join(
                f"{edge.get('label') or edge.get('link_key') or 'relation'}"
                for edge in path
            ) or "(direct)"
            title = f"{' & '.join(labels)}: connected in graph"
            profile_summary = (
                f"{labels[0]} and {labels[-1]} are directly connected in the approved graph "
                f"via: {path_text}."
            )
            interpretations = [
                f"Resolution method: direct graph path ({len(path)} hop(s)) -- no LLM reasoning was needed.",
            ]
            answer = None
            supporting_labels = []
        elif resolution == "llm_reasoning" and relation.get("answer"):
            answer = relation["answer"]
            supporting_labels = relation.get("supporting_labels") or []
            title = f"{' & '.join(labels)}: {answer}"
            support_text = (
                f" (supported by {', '.join(supporting_labels)})" if supporting_labels else ""
            )
            profile_summary = (
                f"{answer}{support_text} -- derived from each center's own facts "
                f"(no direct graph path connects {' and '.join(labels)}). Reasoning: {relation.get('reasoning', '')}"
            )
            interpretations = [
                "Resolution method: LLM reasoning over facts only "
                "(each center's own name/identity was withheld from the model).",
            ]
        else:
            answer = None
            supporting_labels = []
            title = f"{' & '.join(labels)}: unresolved"
            profile_summary = (
                f"Could not determine an answer for {' & '.join(labels)}: "
                f"{relation.get('reasoning') or relation.get('error') or 'no direct graph path and no LLM available.'}"
            )
            interpretations = ["Resolution method: none -- neither a graph path nor LLM reasoning was available."]

        return {
            "title": title,
            "profile_summary": profile_summary,
            "key_facts": key_facts,
            "business_interpretation": interpretations,
            "evidence_limits": [
                "Based solely on the approved graph and each center's own extracted "
                "facts -- no external knowledge was used.",
                "LLM reasoning (when used) only sees relevant facts, never the centers' own "
                "names, to avoid the model picking a name it merely recognizes as familiar.",
            ],
            "next_questions": [],
            "metrics": {
                "centers": [
                    {"center_node": d["center_node"], "label": d["label"]}
                    for d in centers_data
                ],
                "resolution": resolution,
                "path": relation.get("path"),
                "answer": answer,
                "supporting_center_nodes": relation.get("supporting_center_nodes") or [],
                "supporting_labels": supporting_labels,
                "reasoning": relation.get("reasoning", ""),
                "error": relation.get("error", ""),
            },
        }

