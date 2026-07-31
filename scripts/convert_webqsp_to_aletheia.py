#!/usr/bin/env python3
"""Convert WebQSP questions to Aletheia tenant format for benchmark evaluation.

Extracts topic-entity neighborhoods from WebQSP Freebase subgraphs, derives
entity_config and link_config (pruned to topic type's direct links), and
produces benchmark questions with gold answer relation paths for planner-layer
evaluation.

Usage:
    python scripts/convert_webqsp_to_aletheia.py
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

try:
    from scripts.generate_relation_descriptions import generate_relation_description
except ImportError:
    from generate_relation_descriptions import generate_relation_description

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "benchmarks" / "webqsp" / "validation.parquet"
OUTPUT_DIR = ROOT / "benchmarks" / "webqsp"


DOMAIN_TO_TYPE: dict[str, str] = {
    "people": "person",
    "film": "film",
    "music": "music",
    "book": "book",
    "location": "location",
    "organization": "organization",
    "business": "business",
    "government": "government",
    "education": "education",
    "award": "award",
    "sports": "sports",
    "aviation": "aviation",
    "broadcast": "broadcast",
    "military": "military",
    "medicine": "medicine",
    "transportation": "transportation",
    "travel": "travel",
    "architecture": "architecture",
    "biology": "biology",
    "language": "language",
    "base": "entity",
    "common": "entity",
    "freebase": "entity",
    "influence": "entity",
    "symbols": "entity",
    "celebrities": "person",
    "fictional_universe": "fictional",
    "media_common": "media",
    "metropolitan_transit": "transit",
    "amusement_parks": "amusement",
    "tv": "tv",
    "ice_hockey": "sports",
    "geography": "location",
    "periodicals": "book",
    "time": "time",
    "religion": "religion",
    "internet": "internet",
    "computer": "computer",
    "engineering": "engineering",
    "astronomy": "astronomy",
    "chemistry": "chemistry",
    "physics": "physics",
    "mathematics": "mathematics",
    "meteorology": "meteorology",
    "boats": "vehicle",
    "automotive": "vehicle",
    "railway": "transit",
    "operating_system": "software",
    "baseball": "sports",
    "basketball": "sports",
    "football": "sports",
    "soccer": "sports",
    "boxing": "sports",
    "cycling": "sports",
    "golf": "sports",
    "olympics": "sports",
    "winter_sports": "sports",
    "cricket": "sports",
    "tennis": "sports",
    "swimming": "sports",
    "volleyball": "sports",
    "hockey": "sports",
    "rugby": "sports",
}


def _infer_entity_type(name: str, subj_rels: set[str], obj_rels: set[str]) -> str:
    """Infer an Aletheia entity type from Freebase relation domains."""
    all_rels = subj_rels | obj_rels
    domains = Counter()
    for r in all_rels:
        parts = r.split(".")
        if parts and parts[0] not in ("common", "freebase", "base", "symbols", "influence"):
            domains[parts[0]] += 1

    if domains:
        top_domain = domains.most_common(1)[0][0]
        return DOMAIN_TO_TYPE.get(top_domain, "entity")

    if re.match(r"^[mg]\.\w+$", name):
        return "entity"

    return "entity"


@dataclass
class WebQSPQuestion:
    qid: str
    question: str
    topic_entity: str
    topic_type: str
    answers: list[str]
    answer_entities: list[str]
    hop_count: int
    gold_relation_paths: list[list[str]]
    gold_link_keys: list[str]  # matching link_config keys for gold paths
    entity_config: dict[str, dict]
    link_config: list[dict]
    descriptions: dict[str, str]
    graph_size: int


def find_gold_paths(
    topic: str,
    answers: list[str],
    graph: list[list[str]],
) -> tuple[int, list[list[str]], list[str]]:
    """Find gold answer relation paths from topic to answer entities.

    Returns (hop_count, relation_paths, gold_relation_names).
    """
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for triple in graph:
        s, r, o = triple[0], triple[1], triple[2]
        outgoing[s].append((r, o))
        incoming[o].append((r, s))

    answer_norms = set()
    for a in answers:
        answer_norms.add(a.lower().strip())
        answer_norms.add(a.strip())

    def is_match(entity: str) -> bool:
        el = entity.lower().strip()
        for an in answer_norms:
            if el == an or an in el or el in an:
                return True
        return False

    # 1-hop
    one_hop = []
    gold_rel_names = []
    for r, target in outgoing.get(topic, []):
        if is_match(target):
            one_hop.append([r])
            gold_rel_names.append(r)

    if one_hop:
        return 1, one_hop, gold_rel_names

    # 2-hop
    two_hop = []
    for r1, mid in outgoing.get(topic, []):
        for r2, target in outgoing.get(mid, []):
            if is_match(target):
                two_hop.append([r1, r2])
                gold_rel_names.extend([r1, r2])
    for r1, mid in incoming.get(topic, []):
        for r2, target in outgoing.get(mid, []):
            if is_match(target):
                two_hop.append([f"REVERSE:{r1}", r2])
                gold_rel_names.extend([r1, r2])
        for r2, source in incoming.get(mid, []):
            if is_match(source):
                two_hop.append([f"REVERSE:{r1}", f"REVERSE:{r2}"])
                gold_rel_names.extend([r1, r2])

    if two_hop:
        return 2, two_hop, gold_rel_names

    return 0, [], []


def build_aletheia_configs(
    topic: str,
    topic_type: str,
    graph: list[list[str]],
    gold_rel_names: list[str],
) -> tuple[dict[str, dict], list[dict], dict[str, str]]:
    """Build Aletheia entity_config and link_config.

    Strategy: Include only entity types and links that appear in the topic
    entity's 1-2 hop neighborhood, and prune link_config to include only
    links from/to the topic type plus links that match gold relations.
    """
    # Build adjacency from full graph
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for triple in graph:
        s, r, o = triple[0], triple[1], triple[2]
        outgoing[s].append((r, o))
        incoming[o].append((r, s))

    # Collect all entities in topic's 2-hop neighborhood
    visited = {topic}
    frontier = {topic}
    all_triples = []

    for _ in range(2):
        next_frontier = set()
        for entity in frontier:
            for r, target in outgoing.get(entity, []):
                all_triples.append((entity, r, target))
                if target not in visited:
                    next_frontier.add(target)
            for r, source in incoming.get(entity, []):
                all_triples.append((source, r, entity))
                if source not in visited:
                    next_frontier.add(source)
        visited |= next_frontier
        frontier = next_frontier
        if not frontier:
            break

    # Infer entity types for all entities in neighborhood
    subj_rels = defaultdict(set)
    obj_rels = defaultdict(set)
    for s, r, o in all_triples:
        subj_rels[s].add(r)
        obj_rels[o].add(r)

    entity_types = {}
    for ent in visited:
        entity_types[ent] = _infer_entity_type(ent, subj_rels.get(ent, set()), obj_rels.get(ent, set()))

    # Build link_config: include links from/to topic_type, plus gold relation links
    # Group by (from_type, to_type, relation)
    link_map = {}
    link_config = []
    descriptions = {}
    gold_rel_set = set()
    for r in gold_rel_names:
        gold_rel_set.add(r)
        gold_rel_set.add(r.replace(".", "_"))

    for s, r, o in all_triples:
        s_type = entity_types.get(s, "entity")
        o_type = entity_types.get(o, "entity")
        r_norm = r.replace(".", "_")

        # Include this link if:
        # 1. It's from/to the topic type, OR
        # 2. It matches a gold relation
        is_topic_related = (s_type == topic_type or o_type == topic_type)
        is_gold = (r in gold_rel_set or r_norm in gold_rel_set)

        if not (is_topic_related or is_gold):
            continue

        key = (s_type, o_type, r_norm)
        if key not in link_map:
            link_key = f"{s_type}:n:m:{r_norm}"
            if len(link_key) > 100:
                link_key = link_key[:100]
            link_map[key] = link_key
            link_config.append({
                "link": link_key,
                "from": s_type,
                "to": o_type,
                "fk_table": f"webqsp_{s_type}",
                "fk_col": r_norm,
            })
            descriptions[link_key] = generate_relation_description(r)

    # Build entity_config: only types that appear in the pruned link_config
    used_types = set()
    for lc in link_config:
        used_types.add(lc["from"])
        used_types.add(lc["to"])
    used_types.add(topic_type)

    entity_config = {}
    for etype in used_types:
        entity_config[etype] = {
            "table": f"webqsp_{etype}",
            "pk": "id",
            "artifact": f"object:{etype}",
        }

    return entity_config, link_config, descriptions


def convert_webqsp_to_aletheia(
    parquet_path: Path,
    max_questions: int = 100,
    min_hop: int = 1,
) -> list[WebQSPQuestion]:
    """Convert WebQSP questions to Aletheia benchmark format."""
    df = pd.read_parquet(parquet_path)
    results = []

    for i in range(len(df)):
        if len(results) >= max_questions:
            break

        row = df.iloc[i]
        question = row["question"]
        q_entities = list(row["q_entity"])
        answers = list(row["answer"])
        a_entities = list(row["a_entity"])
        graph = [list(t) for t in row["graph"]]

        if not q_entities or not answers:
            continue

        topic = q_entities[0]

        # Find gold paths
        hop_count, gold_paths, gold_rel_names = find_gold_paths(topic, a_entities, graph)
        if hop_count < min_hop or not gold_paths:
            continue

        # Determine topic entity type
        topic_subj_rels = set()
        topic_obj_rels = set()
        for triple in graph:
            s, r, o = triple[0], triple[1], triple[2]
            if s == topic:
                topic_subj_rels.add(r)
            if o == topic:
                topic_obj_rels.add(r)
        topic_type = _infer_entity_type(topic, topic_subj_rels, topic_obj_rels)

        # Build pruned configs
        entity_config, link_config, descriptions = build_aletheia_configs(
            topic, topic_type, graph, gold_rel_names
        )

        if not entity_config or not link_config:
            continue

        # Find gold link keys in link_config
        # Only match links where the from or to type is the topic type,
        # to avoid inflating gold keys with same relation across unrelated type pairs
        gold_link_keys = []
        for lc in link_config:
            link_suffix = lc["link"].split(":n:m:")[-1]
            is_topic_link = lc["from"] == topic_type or lc["to"] == topic_type
            for r in gold_rel_names:
                r_norm = r.replace(".", "_")
                if link_suffix == r_norm and is_topic_link:
                    gold_link_keys.append(lc["link"])
                    break

        results.append(WebQSPQuestion(
            qid=row["id"],
            question=question,
            topic_entity=topic,
            topic_type=topic_type,
            answers=answers[:5],
            answer_entities=a_entities[:5],
            hop_count=hop_count,
            gold_relation_paths=gold_paths[:5],
            gold_link_keys=list(set(gold_link_keys)),
            entity_config=entity_config,
            link_config=link_config,
            descriptions=descriptions,
            graph_size=len(graph),
        ))

    return results


def main() -> None:
    questions = convert_webqsp_to_aletheia(DATA_PATH, max_questions=100, min_hop=1)

    output = []
    for q in questions:
        output.append({
            "qid": q.qid,
            "question": q.question,
            "topic_entity": q.topic_entity,
            "topic_type": q.topic_type,
            "answers": q.answers,
            "answer_entities": q.answer_entities,
            "hop_count": q.hop_count,
            "gold_relation_paths": q.gold_relation_paths,
            "gold_link_keys": q.gold_link_keys,
            "entity_config": q.entity_config,
            "link_config": q.link_config,
            "descriptions": q.descriptions,
            "graph_size": q.graph_size,
        })

    output_path = OUTPUT_DIR / "webqsp_aletheia_benchmark.json"
    output_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"Saved {len(output)} questions to {output_path}")

    hop_dist = defaultdict(int)
    type_counts = Counter()
    link_counts = []
    gold_found = 0
    for q in output:
        hop_dist[q["hop_count"]] += 1
        type_counts[q["topic_type"]] += 1
        link_counts.append(len(q["link_config"]))
        if q["gold_link_keys"]:
            gold_found += 1

    print(f"\nHop distribution: {dict(hop_dist)}")
    print(f"Topic types: {dict(type_counts.most_common(10))}")
    print(f"Link config sizes: min={min(link_counts)}, max={max(link_counts)}, avg={sum(link_counts)/len(link_counts):.0f}")
    print(f"Questions with gold link keys found: {gold_found}/{len(output)}")


if __name__ == "__main__":
    main()
