import unittest

from agents.graph_search_loop_harness import evaluate_graph_search_loop, load_graph_search_loop_config


class Tenant:
    tenant_id = "tenant-a"

    def public_dict(self):
        return {"tenant_id": self.tenant_id}


class FakeRepo:
    def __init__(self, *, contexts, query_routes=None):
        self.contexts = contexts
        self.query_routes = query_routes or {}
        self.local_context_call_count = 0

    def full_graph(self, tenant, limit=200):
        nodes = [
            {"id": "Waterway:Red Sea", "label": "Red Sea", "type": "Waterway"},
            {"id": "Waterway:Gulf of Aden", "label": "Gulf of Aden", "type": "Waterway"},
        ]
        edges = [
            {"id": "edge:1", "source": "Waterway:Red Sea", "target": "Waterway:Gulf of Aden", "label": "connects_to"}
        ]
        return {"approved": True, "nodes": nodes, "edges": edges, "scope": {"projection_source": "test"}}

    def local_rag_context(self, tenant, object_type, instance_id, question=None, limit=80):
        self.local_context_call_count += 1
        return self.contexts.get(f"{object_type}:{instance_id}")

    def graph_community_summaries(self, tenant, limit=300):
        return {
            "approved": True,
            "retrieval_mode": "community_summary",
            "communities": [{"community_id": "community:1", "title": "Red Sea", "summary": "2 objects"}],
            "summary_text": "- Red Sea: 2 objects",
            "eval": {"community_count": 1, "node_count": 2, "edge_count": 1},
        }

    def graph_rag_query_context(self, tenant, question, limit=80):
        route = self.query_routes.get(question, "local")
        if route == "global":
            return {"retrieval_mode": "community_summary", "query_route": {"route": "global", "reason": "global_question"}}
        if route == "local_no_relation":
            return {
                "retrieval_mode": "local_graph_context",
                "query_route": {"route": "local", "reason": "question_matched_approved_object", "matched_node": "Waterway:Red Sea"},
                "eval": {"coverage": {"has_relation": False, "has_evidence": True, "has_semantic_context": True}},
            }
        return {
            "retrieval_mode": "local_graph_context",
            "query_route": {"route": "local", "reason": "question_matched_approved_object", "matched_node": "Waterway:Red Sea"},
            "eval": {"coverage": {"has_relation": True, "has_evidence": True, "has_semantic_context": True}},
        }


def context(node_id, *, edges=1, evidence=1, semantic=1, unsupported=0):
    return {
        "center": {"id": node_id, "label": node_id.split(":", 1)[-1], "type": node_id.split(":", 1)[0]},
        "eval": {
            "edge_count": edges,
            "evidence_count": evidence,
            "semantic_item_count": semantic,
            "unsupported_edge_count": unsupported,
            "coverage": {
                "has_center": True,
                "has_relation": edges > 0,
                "has_evidence": evidence > 0,
                "has_semantic_context": semantic > 0,
            },
        },
    }


class GraphSearchLoopHarnessTest(unittest.TestCase):
    def test_relation_coverage_repair_for_isolated_nodes(self):
        repo = FakeRepo(
            contexts={
                "Waterway:Red Sea": context("Waterway:Red Sea", edges=1),
                "Waterway:Gulf of Aden": context("Waterway:Gulf of Aden", edges=0),
            }
        )
        config = load_graph_search_loop_config(None)
        config["evaluation"]["use_repository_local_context"] = True
        config["evaluation"]["query_eval_mode"] = "real"
        report = evaluate_graph_search_loop(repo, Tenant(), config=config, query_samples=["What is connected to Red Sea?"])

        self.assertEqual(report["verdict"]["next_focus"], "relation_coverage_repair")
        self.assertEqual(report["repair_plan"]["item_count"], 1)
        self.assertEqual(report["repair_plan"]["items"][0]["frontier_item"]["key"], "graph-search-coverage:Waterway:Gulf of Aden")

    def test_semantic_context_repair_after_relation_and_evidence_pass(self):
        repo = FakeRepo(
            contexts={
                "Waterway:Red Sea": context("Waterway:Red Sea", edges=1, evidence=1, semantic=0),
                "Waterway:Gulf of Aden": context("Waterway:Gulf of Aden", edges=1, evidence=1, semantic=1),
            }
        )
        config = load_graph_search_loop_config(None)
        config["evaluation"]["use_repository_local_context"] = True
        config["evaluation"]["query_eval_mode"] = "real"
        report = evaluate_graph_search_loop(repo, Tenant(), config=config, query_samples=["What is connected to Red Sea?"])

        self.assertEqual(report["verdict"]["next_focus"], "semantic_context_repair")
        self.assertEqual(report["repair_plan"]["items"][0]["frontier_item"]["payload"]["repair_reason"], "local graph context lacks semantic item")

    def test_query_alias_repair_when_queries_fall_back_global(self):
        repo = FakeRepo(
            contexts={
                "Waterway:Red Sea": context("Waterway:Red Sea", edges=1, evidence=1, semantic=1),
                "Waterway:Gulf of Aden": context("Waterway:Gulf of Aden", edges=1, evidence=1, semantic=1),
            },
            query_routes={"Unknown maritime query": "global"},
        )
        config = load_graph_search_loop_config(None)
        config["evaluation"]["use_repository_local_context"] = True
        config["evaluation"]["query_eval_mode"] = "real"
        config["targets"]["max_global_fallback_ratio"] = 0.0
        report = evaluate_graph_search_loop(repo, Tenant(), config=config, query_samples=["Unknown maritime query"])

        self.assertEqual(report["verdict"]["next_focus"], "query_alias_repair")
        self.assertEqual(report["repair_plan"]["items"][0]["kind"], "query_alias_repair")

    def test_global_summary_query_does_not_trigger_alias_repair(self):
        repo = FakeRepo(
            contexts={
                "Waterway:Red Sea": context("Waterway:Red Sea", edges=1, evidence=1, semantic=1),
                "Waterway:Gulf of Aden": context("Waterway:Gulf of Aden", edges=1, evidence=1, semantic=1),
            },
            query_routes={"Summarize the approved graph.": "global"},
        )
        config = load_graph_search_loop_config(None)
        config["evaluation"]["use_repository_local_context"] = True
        config["evaluation"]["query_eval_mode"] = "real"
        config["targets"]["max_global_fallback_ratio"] = 0.0
        report = evaluate_graph_search_loop(repo, Tenant(), config=config, query_samples=["Summarize the approved graph."])

        self.assertEqual(report["verdict"]["next_focus"], "continue_graph_search_monitoring")
        self.assertFalse(report["repair_plan"]["actionable"])

    def test_local_query_without_relation_triggers_context_repair(self):
        repo = FakeRepo(
            contexts={
                "Waterway:Red Sea": context("Waterway:Red Sea", edges=1, evidence=1, semantic=1),
                "Waterway:Gulf of Aden": context("Waterway:Gulf of Aden", edges=1, evidence=1, semantic=1),
            },
            query_routes={"What is connected to Red Sea?": "local_no_relation"},
        )
        config = load_graph_search_loop_config(None)
        config["evaluation"]["use_repository_local_context"] = True
        config["evaluation"]["query_eval_mode"] = "real"
        report = evaluate_graph_search_loop(repo, Tenant(), config=config, query_samples=["What is connected to Red Sea?"])

        self.assertEqual(report["verdict"]["next_focus"], "query_context_repair")
        self.assertEqual(report["repair_plan"]["items"][0]["frontier_item"]["source_kind"], "graph_search_query_context_repair")

    def test_fast_graph_index_does_not_call_local_context_per_node(self):
        repo = FakeRepo(contexts={})
        config = load_graph_search_loop_config(None)
        config["targets"]["min_semantic_items_per_object"] = 0

        report = evaluate_graph_search_loop(repo, Tenant(), config=config, query_samples=["Summarize the approved graph."])

        self.assertEqual(repo.local_context_call_count, 0)
        self.assertEqual(report["metrics"]["sampled_object_count"], 2)
        self.assertEqual(report["metrics"]["objects_with_relation"], 2)

    def test_fast_query_eval_uses_in_memory_graph_labels(self):
        repo = FakeRepo(contexts={})
        config = load_graph_search_loop_config(None)
        config["targets"]["min_semantic_items_per_object"] = 0

        report = evaluate_graph_search_loop(repo, Tenant(), config=config, query_samples=["What is connected to Gulf of Aden?"])

        self.assertEqual(report["query_results"][0]["route"], "local")
        self.assertEqual(report["query_results"][0]["reason"], "fast_label_match")
        self.assertEqual(report["query_results"][0]["matched_node"], "Waterway:Gulf of Aden")
        self.assertEqual(repo.local_context_call_count, 0)


if __name__ == "__main__":
    unittest.main()
