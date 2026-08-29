import unittest
from unittest.mock import patch

from aletheia.interfaces.api.server import InstanceRepository, ReasoningRepository


class FakeReasoningTenant:
    tenant_id = "demo"

    def public_dict(self):
        return {"tenant_id": self.tenant_id}


class EmptyGraphInstanceRepository:
    def local_rag_context(self, *args, **kwargs):
        return None

    def full_graph(self, *args, **kwargs):
        return None

    def edge_detail(self, *args, **kwargs):
        return None


class ApprovedGraphInstanceRepository(EmptyGraphInstanceRepository):
    def full_graph(self, *args, **kwargs):
        return {
            "approved": True,
            "nodes": [
                {"id": "Object:entity-a", "type": "Object", "label": "Entity A"},
            ],
            "edges": [],
            "scope": {"projection_source": "SchemaGraphModelingAgent"},
        }


class EmptyReasoningEngine:
    def __init__(self, *args, **kwargs):
        pass

    def analyze(self, *args, **kwargs):
        return None


class StructuredReasoningEngine:
    def __init__(self, *args, **kwargs):
        pass

    def analyze(self, *args, **kwargs):
        return {
            "title": "Entity A Profile",
            "profile_summary": "Entity A has approved graph context.",
            "metrics": {"label": "Entity A"},
        }


class DeepGraphReasoningTest(unittest.TestCase):
    def _repo_with_task(self, instance_repository, scope):
        repo = object.__new__(ReasoningRepository)
        repo.instance_repository = instance_repository
        repo._get_task_row = lambda tenant, task_key: {
            "id": 1,
            "canonical_key": task_key,
            "question": "What does the scoped graph show?",
            "status": "active",
            "scope": scope,
        }
        repo.update_task_status = lambda *args, **kwargs: None
        captured = {}

        def record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, status, started):
            captured["run"] = {
                "id": 10,
                "status": status,
                "query_plan": query_plan,
                "tool_calls": tool_calls,
                "evidence_paths": evidence_paths,
                "output": output,
                "eval_result": eval_result,
            }
            return captured["run"]

        def record_finding(tenant, run, finding):
            captured["finding"] = dict(finding, id=20)
            return captured["finding"]

        repo._record_run = record_run
        repo._record_finding = record_finding
        return repo, captured

    def test_scoped_graph_task_blocks_without_approved_projection_or_demo_mode(self):
        repo, captured = self._repo_with_task(
            EmptyGraphInstanceRepository(),
            {
                "center_node": "Object:entity-a",
                "evidence_paths": [{"kind": "graph_node", "node": "Object:entity-a"}],
            },
        )

        with patch("aletheia.interfaces.api.server.ReasoningEngine", side_effect=AssertionError("engine must not run")):
            result = repo.run_scoped_graph_task(FakeReasoningTenant(), "task-no-projection")

        self.assertFalse(result["approved"])
        self.assertEqual(result["findings"], [])
        self.assertNotIn("finding", captured)
        self.assertEqual(captured["run"]["status"], "blocked")
        self.assertEqual(captured["run"]["tool_calls"][0]["status"], "blocked")
        self.assertEqual(captured["run"]["tool_calls"][1]["status"], "skipped")
        self.assertEqual(captured["run"]["output"]["projection_source"], "none")
        self.assertFalse(captured["run"]["output"]["demo_mode"])
        self.assertIn("No reviewed SchemaGraphModelingAgent projection", captured["run"]["output"]["degraded_reason"])
        self.assertIn("missing approved graph projection", captured["run"]["eval_result"]["unsupported_claims"])

    def test_streaming_scoped_graph_task_blocks_without_approved_projection(self):
        repo, captured = self._repo_with_task(
            EmptyGraphInstanceRepository(),
            {
                "center_node": "Object:entity-a",
                "evidence_paths": [{"kind": "graph_node", "node": "Object:entity-a"}],
            },
        )

        with patch("aletheia.interfaces.api.server.ReasoningEngine", side_effect=AssertionError("engine must not run")):
            events = list(repo.run_scoped_graph_task_streaming(FakeReasoningTenant(), "task-stream-no-projection"))

        response_events = [event for event in events if event["event"] == "llm_response_body"]
        self.assertEqual(response_events[0]["data"]["response_body"]["status"], "blocked")
        self.assertEqual(response_events[0]["data"]["response_body"]["projection_source"], "none")
        self.assertFalse(response_events[0]["data"]["response_body"]["demo_mode"])
        self.assertEqual(captured["run"]["status"], "blocked")
        self.assertEqual(events[-1]["event"], "run_complete")
        self.assertFalse(events[-1]["data"]["approved"])
        self.assertEqual(events[-1]["data"]["findings"], [])

    def test_scoped_graph_task_allows_static_fallback_only_in_explicit_demo_mode(self):
        repo, captured = self._repo_with_task(
            EmptyGraphInstanceRepository(),
            {
                "center_node": "Object:entity-a",
                "demo_mode": True,
                "evidence_paths": [{"kind": "graph_node", "node": "Object:entity-a"}],
            },
        )

        with patch("aletheia.interfaces.api.server.ReasoningEngine", EmptyReasoningEngine):
            result = repo.run_scoped_graph_task(FakeReasoningTenant(), "task-demo")

        self.assertTrue(result["approved"])
        self.assertEqual(captured["run"]["status"], "completed")
        self.assertEqual(captured["run"]["output"]["projection_source"], "explicit_demo_mode")
        self.assertTrue(captured["run"]["output"]["demo_mode"])
        self.assertIsNone(captured["run"]["output"]["degraded_reason"])
        self.assertIn("finding", captured)

    def test_scoped_graph_task_allows_approved_projection_without_demo_mode(self):
        repo, captured = self._repo_with_task(
            ApprovedGraphInstanceRepository(),
            {
                "center_node": "Object:entity-a",
                "evidence_paths": [{"kind": "graph_node", "node": "Object:entity-a"}],
            },
        )

        with patch("aletheia.interfaces.api.server.ReasoningEngine", StructuredReasoningEngine):
            result = repo.run_scoped_graph_task(FakeReasoningTenant(), "task-approved")

        self.assertTrue(result["approved"])
        self.assertEqual(captured["run"]["status"], "completed")
        self.assertEqual(captured["run"]["output"]["projection_source"], "SchemaGraphModelingAgent")
        self.assertFalse(captured["run"]["output"]["demo_mode"])
        self.assertIsNone(captured["run"]["output"]["degraded_reason"])
        self.assertIn("structured_response", captured["run"]["output"])

    def test_instance_repository_exposes_schema_reasoning_entity_adapters(self):
        self.assertTrue(callable(getattr(InstanceRepository, "_fetch_entity", None)))
        self.assertTrue(callable(getattr(InstanceRepository, "_entity_node", None)))

    def test_complete_source_relation_target_evidence_action_chain_is_deep_graph_finding(self):
        repo = object.__new__(ReasoningRepository)
        profile = repo._deep_graph_profile(
            [
                {"kind": "source_entity", "source_ref": "source_table", "value": "Entity A"},
                {"kind": "relation", "source_label": "Entity A", "target_label": "Entity B", "value": "depends_on"},
                {"kind": "target_entity", "source_ref": "source_table", "value": "Entity B"},
                {"kind": "evidence", "source_ref": "source_table", "metric": "supporting_metric", "value": 123},
                {"kind": "action", "source_ref": "playbook", "metric": "review", "value": "Assign analyst review"},
            ]
        )

        self.assertTrue(profile["multi_hop"])
        self.assertEqual(profile["reasoning_type"], "graph_multi_hop")
        self.assertEqual(profile["finding_emphasis"], "deep_graph_finding")
        self.assertEqual(profile["hop_count"], 4)
        self.assertEqual(profile["missing_steps"], [])

    def test_metric_only_chain_is_not_deep_graph_finding(self):
        repo = object.__new__(ReasoningRepository)
        profile = repo._deep_graph_profile(
            [
                {"kind": "aggregate", "source_ref": "source_table", "metric": "sum_value", "value": 123},
                {"kind": "action", "source_ref": "playbook", "metric": "review", "value": "Review ranking"},
            ]
        )

        self.assertFalse(profile["multi_hop"])
        self.assertEqual(profile["finding_emphasis"], "candidate_finding")
        self.assertIn("source_entity", profile["missing_steps"])
        self.assertIn("relation", profile["missing_steps"])
        self.assertIn("target_entity", profile["missing_steps"])

    def test_plain_reasoning_conclusion_is_human_summary_not_metric_dump(self):
        repo = object.__new__(ReasoningRepository)
        question = "对象 A 的主要关联路径是什么 — Entity A"
        ranked_paths = [
            {"label": "Path Alpha", "metric": "value", "metric_value": 1317},
            {"label": "Path Beta", "metric": "value", "metric_value": 936},
            {"label": "Path Gamma", "metric": "value", "metric_value": 609},
        ]
        second_hop_paths = [
            {"label": "Path Alpha", "top_peers": [{"key": "Peer A"}, {"key": "Peer B"}]},
            {"label": "Path Beta", "top_peers": [{"key": "Peer C"}, {"key": "Peer D"}]},
        ]

        title = repo._plain_reasoning_title(question, "Entity A", ranked_paths, second_hop_paths)
        conclusion = repo._plain_reasoning_conclusion(
            question,
            "Entity A",
            "Entity A Business Profile: 25 source_path(s) (#1/198, high)",
            ranked_paths,
            second_hop_paths,
            {"source_key_row_degree": 49},
        )

        self.assertIn("Entity A", title)
        self.assertIn("Path Alpha", conclusion)
        self.assertIn("Peer A", conclusion)
        self.assertNotIn("source_path(s)", conclusion)
        self.assertNotIn("#1/198", conclusion)

    def test_reasoning_response_builds_ranked_paths_from_graph_context_fallback(self):
        repo = object.__new__(ReasoningRepository)
        tenant = type("Tenant", (), {"tenant_id": "demo"})()
        task = {"question": "Entity A 的主要关联路径是什么 — Entity A", "canonical_key": "task"}
        scope = {"center_node": "Object:entity-a", "depth": 1, "node_limit": 200}
        structured_answer = {
            "title": "Entity A Business Profile: 25 source_path(s) (#1/198, high)",
            "profile_summary": "Entity A Business Profile: 25 source_path(s) (#1/198, high)",
            "metrics": {"label": "Entity A"},
        }
        graph_context = {
            "degree": {"source_key_row_degree": 49},
            "source_backed_related_nodes": [
                {"id": "SourcePath:Path Alpha", "label": "Path Alpha"},
                {"id": "SourcePath:Path Beta", "label": "Path Beta"},
            ],
            "source_backed_related_edges": [
                {"target": "SourcePath:Path Alpha", "metric": "value", "metric_value": 1317, "row_count": 1, "source_table": "source"},
                {"target": "SourcePath:Path Beta", "metric": "value", "metric_value": 936, "row_count": 1, "source_table": "source"},
            ],
        }

        response = repo._reasoning_response_v1(tenant, task, scope, structured_answer, [], graph_context)

        self.assertEqual([p["label"] for p in response["ranked_paths"][:2]], ["Path Alpha", "Path Beta"])
        self.assertIn("Path Alpha", response["answer"]["conclusion"])
        self.assertNotIn("source_path(s)", response["answer"]["conclusion"])

    def test_reasoning_response_for_chokepoint_explains_business_meaning(self):
        repo = object.__new__(ReasoningRepository)
        tenant = type("Tenant", (), {"tenant_id": "maritime-risk"})()
        task = {
            "question": "Assess maritime risk monitoring action for Strait of Hormuz",
            "canonical_key": "task",
        }
        scope = {"center_node": "MaritimeChokepoint:Strait of Hormuz", "depth": 2, "node_limit": 120}
        structured_answer = {
            "title": "Strait of Hormuz Maritime Exposure Profile",
            "profile_summary": "Strait of Hormuz has 397 source rows across 3 related source table(s).",
            "metrics": {
                "label": "Strait of Hormuz",
                "source_key_profile": {
                    "total_key_rows": 397,
                    "related_tables": [{"table": "maritime_chokepoint_country_dependencies"}],
                    "top_paths": [
                        {"label": "Strait of Hormuz", "metric": "v_canal", "metric_value": 1770271463166.0774},
                        {"label": "Strait of Hormuz", "metric": "trade_at_risk_piracy_v", "metric_value": 569354729.0706857},
                    ],
                },
            },
        }
        graph_context = {
            "degree": {"source_key_row_degree": 397},
            "related_nodes": [
                {"id": "MaritimeChokepoint:Strait of Hormuz", "label": "Strait of Hormuz", "type": "MaritimeChokepoint"},
                {"id": "Country:ARE", "label": "ARE", "type": "Country"},
                {"id": "Port:Jebel Ali", "label": "Jebel Ali", "type": "Port"},
            ],
            "related_edges": [
                {
                    "source": "MaritimeChokepoint:Strait of Hormuz",
                    "target": "Country:ARE",
                    "label": "Country Chokepoint Dependency",
                    "properties": {"trade_at_risk_piracy_v": 1200.0, "v_canal": 9000.0},
                },
                {
                    "source": "Country:ARE",
                    "target": "Port:Jebel Ali",
                    "label": "uses_port",
                },
            ],
            "retrieval_context": {
                "nodes": [
                    {"id": "MaritimeChokepoint:Strait of Hormuz", "label": "Strait of Hormuz", "type": "MaritimeChokepoint"},
                    {"id": "Country:ARE", "label": "ARE", "type": "Country"},
                ],
                "edges": [
                    {
                        "id": "edge-1",
                        "source": "MaritimeChokepoint:Strait of Hormuz",
                        "target": "Country:ARE",
                        "relation": "Country Chokepoint Dependency",
                        "properties": {"trade_at_risk_piracy_v": 1200.0, "v_canal": 9000.0},
                    }
                ],
                "semantic_items": [
                    {
                        "element_key": "metric:are-hormuz-risk",
                        "element_type": "metric_observation",
                        "label": "ARE Hormuz trade-at-risk observation",
                        "summary": "ARE has measurable exposure to Strait of Hormuz disruption.",
                        "metric_key": "trade_at_risk_piracy_v",
                        "status": "approved",
                    }
                ],
            },
            "source_backed_related_nodes": [{"id": "SourcePath:Strait of Hormuz", "label": "Strait of Hormuz"}],
            "source_backed_related_edges": [
                {"target": "SourcePath:Strait of Hormuz", "metric": "v_canal", "metric_value": 1770271463166.0774}
            ],
        }

        response = repo._reasoning_response_v1(tenant, task, scope, structured_answer, [], graph_context)

        conclusion = response["answer"]["conclusion"]
        self.assertIn("systemic maritime risk priority", conclusion)
        self.assertIn("propagate", conclusion)
        self.assertIn("rerouting", conclusion)
        self.assertIn("monitoring escalation", conclusion)
        self.assertIn("edge-level exposure", conclusion)
        self.assertNotIn("source rows", conclusion)
        self.assertNotIn("Strait of Hormuz, Strait of Hormuz", conclusion)
        self.assertEqual(response["traversal_analysis"]["strategy"], "joint_bfs_dfs_approved_graph_reasoning_v1")
        self.assertGreaterEqual(response["traversal_analysis"]["max_observed_depth"], 2)
        self.assertTrue(response["traversal_analysis"]["depth_paths"])
        self.assertEqual(response["edge_target_reasoning"]["strategy"], "per_edge_target_then_aggregate_reasoning_v1")
        self.assertTrue(response["edge_target_reasoning"]["units"])
        self.assertTrue(response["edge_target_reasoning"]["units"][0]["local_metrics"])
        self.assertTrue(response["edge_target_reasoning"]["units"][0]["attached_semantic_items"])
        self.assertTrue(response["conclusion_evaluation"]["passed"])
        self.assertTrue(response["conclusion_evaluation"]["checks"]["uses_breadth_traversal"])
        self.assertTrue(response["conclusion_evaluation"]["checks"]["uses_depth_paths"])
        self.assertTrue(response["conclusion_evaluation"]["checks"]["uses_edge_target_units"])
        self.assertTrue(response["conclusion_evaluation"]["checks"]["uses_attached_edge_or_source_metrics"])
        self.assertTrue(response["conclusion_evaluation"]["checks"]["uses_attached_findings_or_semantic_context"])


if __name__ == "__main__":
    unittest.main()
