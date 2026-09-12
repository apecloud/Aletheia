import unittest
from unittest.mock import patch

from aletheia.interfaces.api.server import ReasoningRepository


class FakeReasoningTenant:
    tenant_id = "demo"

    def public_dict(self):
        return {"tenant_id": self.tenant_id}


class ChainGraphInstanceRepository:
    """A -> B -> C -> D chain: B is a direct neighbor of the center node A,
    C and D are only reachable via B -- exactly the shape that should make
    the Datalog transitive-closure strategy surface something beyond what
    the existing 1-hop related_edges already show."""

    def local_rag_context(self, *args, **kwargs):
        return None

    def full_graph(self, *args, **kwargs):
        return {
            "approved": True,
            "nodes": [
                {"id": "a", "type": "Object", "label": "A"},
                {"id": "b", "type": "Object", "label": "B"},
                {"id": "c", "type": "Object", "label": "C"},
                {"id": "d", "type": "Object", "label": "D"},
            ],
            "edges": [
                {"source": "a", "target": "b", "label": "touches"},
                {"source": "b", "target": "c", "label": "touches"},
                {"source": "c", "target": "d", "label": "touches"},
            ],
            "scope": {"projection_source": "SchemaGraphModelingAgent"},
        }

    def edge_detail(self, *args, **kwargs):
        return None


class ReverseChainGraphInstanceRepository:
    """D -> C -> B -> A chain, center node is A, the chain's terminal target
    -- A has only an INCOMING edge (B -> A), no outgoing edges. Aletheia's
    real edge types are directional the same way (e.g. Commit -TOUCHES->
    File: a File node never has outgoing edges), so a rule that only
    follows edges in their stored direction finds nothing starting from A.
    This is the exact shape of a real bug caught against the live
    kubeblocks-github-v1 tenant (a File center node produced zero
    datalog_facts events despite being touched by 19 commits)."""

    def local_rag_context(self, *args, **kwargs):
        return None

    def full_graph(self, *args, **kwargs):
        return {
            "approved": True,
            "nodes": [
                {"id": "a", "type": "Object", "label": "A"},
                {"id": "b", "type": "Object", "label": "B"},
                {"id": "c", "type": "Object", "label": "C"},
                {"id": "d", "type": "Object", "label": "D"},
            ],
            "edges": [
                {"source": "b", "target": "a", "label": "touches"},
                {"source": "c", "target": "b", "label": "touches"},
                {"source": "d", "target": "c", "label": "touches"},
            ],
            "scope": {"projection_source": "SchemaGraphModelingAgent"},
        }

    def edge_detail(self, *args, **kwargs):
        return None


class SingleEdgeInstanceRepository:
    """Only a direct A -> B edge, nothing beyond it -- Datalog has nothing
    transitive to derive here, so no datalog_facts event should fire."""

    def local_rag_context(self, *args, **kwargs):
        return None

    def full_graph(self, *args, **kwargs):
        return {
            "approved": True,
            "nodes": [
                {"id": "a", "type": "Object", "label": "A"},
                {"id": "b", "type": "Object", "label": "B"},
            ],
            "edges": [
                {"source": "a", "target": "b", "label": "touches"},
            ],
            "scope": {"projection_source": "SchemaGraphModelingAgent"},
        }

    def edge_detail(self, *args, **kwargs):
        return None


class EmptyReasoningEngine:
    def __init__(self, *args, **kwargs):
        pass

    def analyze(self, *args, **kwargs):
        return None


def _repo_with_task(instance_repository, scope):
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
        captured["run"] = {"status": status, "output": output, "evidence_paths": evidence_paths}
        return captured["run"]

    def record_finding(tenant, run, finding):
        captured["finding"] = dict(finding, id=20)
        return captured["finding"]

    repo._record_run = record_run
    repo._record_finding = record_finding
    return repo, captured


class DatalogEvidenceTest(unittest.TestCase):
    def test_streaming_scoped_graph_task_emits_datalog_facts_for_multi_hop_chain(self):
        repo, captured = _repo_with_task(
            ChainGraphInstanceRepository(),
            {
                "center_node": "Object:a",
                "evidence_paths": [{"kind": "graph_node", "node": "a"}],
            },
        )

        with patch("aletheia.interfaces.api.repositories.reasoning.traversal.ReasoningEngine", EmptyReasoningEngine):
            events = list(repo.run_scoped_graph_task_streaming(FakeReasoningTenant(), "task-chain"))

        datalog_events = [event for event in events if event["event"] == "datalog_facts"]
        self.assertEqual(len(datalog_events), 1)
        data = datalog_events[0]["data"]
        self.assertEqual(data["kind"], "datalog_derived")
        self.assertEqual(data["center_node"], "Object:a")
        self.assertEqual(set(data["derived_reachable_nodes"]), {"c", "d"})
        self.assertNotIn("b", data["derived_reachable_nodes"])

        evidence_kinds = [item.get("kind") for item in captured["run"]["evidence_paths"]]
        self.assertIn("datalog_derived", evidence_kinds)

    def test_streaming_scoped_graph_task_emits_datalog_facts_when_center_node_has_only_incoming_edges(self):
        repo, captured = _repo_with_task(
            ReverseChainGraphInstanceRepository(),
            {
                "center_node": "Object:a",
                "evidence_paths": [{"kind": "graph_node", "node": "a"}],
            },
        )

        with patch("aletheia.interfaces.api.repositories.reasoning.traversal.ReasoningEngine", EmptyReasoningEngine):
            events = list(repo.run_scoped_graph_task_streaming(FakeReasoningTenant(), "task-reverse-chain"))

        datalog_events = [event for event in events if event["event"] == "datalog_facts"]
        self.assertEqual(len(datalog_events), 1)
        data = datalog_events[0]["data"]
        self.assertEqual(set(data["derived_reachable_nodes"]), {"c", "d"})
        self.assertNotIn("b", data["derived_reachable_nodes"])

    def test_streaming_scoped_graph_task_omits_datalog_facts_without_transitive_reach(self):
        repo, captured = _repo_with_task(
            SingleEdgeInstanceRepository(),
            {
                "center_node": "Object:a",
                "evidence_paths": [{"kind": "graph_node", "node": "a"}],
            },
        )

        with patch("aletheia.interfaces.api.repositories.reasoning.traversal.ReasoningEngine", EmptyReasoningEngine):
            events = list(repo.run_scoped_graph_task_streaming(FakeReasoningTenant(), "task-single-edge"))

        datalog_events = [event for event in events if event["event"] == "datalog_facts"]
        self.assertEqual(datalog_events, [])
        evidence_kinds = [item.get("kind") for item in captured["run"]["evidence_paths"]]
        self.assertNotIn("datalog_derived", evidence_kinds)


if __name__ == "__main__":
    unittest.main()
