"""Convert an Aletheia graph snapshot (GraphInstanceRepository.neighborhood()/
full_graph()'s ``{"nodes": [...], "edges": [...]}`` shape) into Datalog facts
for aletheia.reasoning.datalog_reasoner.DatalogReasoner.

Two fact shapes are emitted per edge, both useful for different rules:
- one named after the edge's own relation label, lowercased (e.g. an edge
  with label "TOUCHES" becomes ``touches(source_id, target_id)``) -- lets
  rules reason about a specific relation type directly;
- a generic ``edge(source_id, target_id, relation_label)`` fact -- lets
  rules reason across all relation types uniformly (e.g. "connected via any
  edge") without needing to know every relation name in advance.

Plus one ``is_type(node_id, type_name)`` fact per node.
"""

from __future__ import annotations

from typing import Any


def _quote(value: str) -> str:
    """Datalog constants here are just identifier-safe printable tokens --
    Aletheia vertex ids (kb_issue_10816, kb_file_...) and edge labels are
    already alphanumeric/underscore, so no quoting/escaping is needed. If
    that ever stops being true (e.g. a raw label with spaces), this is the
    one place to add it."""
    return value


def graph_to_facts(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []
    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type") or (node.get("types") or [None])[0]
        if node_id and node_type:
            facts.append(f"is_type({_quote(node_id)}, {_quote(node_type)})")
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if not source or not target:
            continue
        label = (edge.get("label") or "related").lower()
        facts.append(f"{label}({_quote(source)}, {_quote(target)})")
        facts.append(f"edge({_quote(source)}, {_quote(target)}, {_quote(label)})")
    return facts


def load_facts(reasoner, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> int:
    """Convenience: add every fact from graph_to_facts() to `reasoner` via
    add_fact(). Returns the number of facts added."""
    count = 0
    for fact in graph_to_facts(nodes, edges):
        reasoner.add_fact(fact)
        count += 1
    return count
