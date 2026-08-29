import json
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine, text

from aletheia.ontology.store import ensure_artifact_schema
from aletheia.reasoning.loop_harness import evaluate_reasoning_loop, load_reasoning_loop_config


class ReasoningLoopHarnessTest(unittest.TestCase):
    def _db(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        url = f"sqlite:///{tmpdir.name}/metadata.db"
        engine = create_engine(url)
        ensure_artifact_schema(engine)
        return url, engine

    def _insert_task(self, engine, *, key="task-a", status="active", tenant="tenant-a"):
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_tasks
                        (project_id, canonical_key, question, scope_json,
                         allowed_tools_json, status, created_at, updated_at)
                    VALUES
                        (:tenant, :key, 'Assess Red Sea risk', '{}', '[]',
                         :status, :created_at, :updated_at)
                    """
                ),
                {
                    "tenant": tenant,
                    "key": key,
                    "status": status,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
            )
            return result.lastrowid

    def _observable_query_plan(self):
        return [
            {
                "step_id": "plan-retrieve-context",
                "kind": "tool_use",
                "decision_reason": "Need approved graph evidence before drafting a reasoning finding.",
                "input": {"question": "Assess Red Sea risk"},
                "expected_output": "grounded evidence paths",
            }
        ]

    def _observable_tool_calls(self):
        return [
            {
                "id": "tool-retrieve-context-1",
                "step_id": "tool-retrieve-context-1",
                "step": "retrieve_context",
                "tool_name": "graph.search_paths",
                "attempt": 1,
                "status": "completed",
                "idempotency_key": "reasoning:task-a:graph-search",
                "input": {"query": "Red Sea risk evidence paths"},
                "output": {"evidence_path_count": 1},
            }
        ]

    def _insert_run(
        self,
        engine,
        task_id,
        *,
        run_key="run-a",
        status="completed",
        evidence=None,
        output=None,
        eval_result=None,
        query_plan=None,
        tool_calls=None,
        latency_ms=10,
        tenant="tenant-a",
    ):
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_runs
                        (task_id, project_id, run_key, agent_name, prompt_version,
                         query_plan_json, tool_calls_json, evidence_paths_json,
                         output_json, eval_result_json, status, latency_ms,
                         cost_estimate, created_at)
                    VALUES
                        (:task_id, :tenant, :run_key, 'ReasoningWorkbenchAgent',
                         'graph-scope-reasoning-v1', :query_plan_json, :tool_calls_json,
                         :evidence_paths_json, :output_json, :eval_result_json,
                         :status, :latency_ms, 0.0, :created_at)
                    """
                ),
                {
                    "task_id": task_id,
                    "tenant": tenant,
                    "run_key": run_key,
                    "query_plan_json": json.dumps(query_plan if query_plan is not None else self._observable_query_plan()),
                    "tool_calls_json": json.dumps(tool_calls if tool_calls is not None else self._observable_tool_calls()),
                    "evidence_paths_json": json.dumps(evidence if evidence is not None else [{"kind": "graph"}]),
                    "output_json": json.dumps(
                        output
                        if output is not None
                        else {
                            "structured_response": {"schema_version": "reasoning_response_v1"},
                            "final_result": {"status": "answered", "finding_keys": ["finding-a"]},
                        }
                    ),
                    "eval_result_json": json.dumps(
                        eval_result
                        if eval_result is not None
                        else {"passed": True, "approved_only": True, "draft_only": True, "unsupported_claims": []}
                    ),
                    "status": status,
                    "latency_ms": latency_ms,
                    "created_at": datetime.utcnow(),
                },
            )
            return result.lastrowid

    def _insert_finding(
        self,
        engine,
        run_id,
        *,
        key="finding-a",
        status="approved",
        confidence=0.8,
        evidence=None,
        tenant="tenant-a",
    ):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_findings
                        (run_id, project_id, canonical_key, title, conclusion,
                         confidence, supporting_evidence_json,
                         counter_evidence_json, recommended_action_json,
                         status, version, source_agent, created_at, updated_at)
                    VALUES
                        (:run_id, :tenant, :key, 'Finding', 'Conclusion',
                         :confidence, :evidence, '[]', '{}',
                         :status, 1, 'ReasoningWorkbenchAgent',
                         :created_at, :updated_at)
                    """
                ),
                {
                    "run_id": run_id,
                    "tenant": tenant,
                    "key": key,
                    "confidence": confidence,
                    "evidence": json.dumps(evidence if evidence is not None else [{"kind": "graph"}]),
                    "status": status,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
            )

    def test_targets_met_continue_monitoring(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(engine, task_id)
        self._insert_finding(engine, run_id)

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["metrics"]["completed_run_ratio"], 1.0)
        self.assertEqual(report["metrics"]["structured_response_ratio"], 1.0)
        self.assertEqual(report["metrics"]["trace_observability_ratio"], 1.0)
        self.assertEqual(report["metrics"]["final_result_state_ratio"], 1.0)
        self.assertEqual(report["metrics"]["observable_retry_path_ratio"], 1.0)
        self.assertEqual(report["verdict"]["next_focus"], "continue_reasoning_monitoring")
        self.assertFalse(report["repair_plan"]["actionable"])

    def test_blocked_run_prioritizes_run_health(self):
        url, engine = self._db()
        task_id = self._insert_task(engine)
        self._insert_run(
            engine,
            task_id,
            status="blocked",
            evidence=[],
            output={"summary": "blocked"},
            eval_result={"passed": False, "approved_only": True, "draft_only": True, "unsupported_claims": ["missing evidence path"]},
        )

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "run_health_diagnosis")
        self.assertEqual(report["repair_plan"]["item_count"], 1)
        self.assertEqual(report["repair_plan"]["items"][0]["frontier_item"]["source_kind"], "reasoning_loop_repair")

    def test_missing_structured_response_triggers_trace_observability_repair(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(engine, task_id, output={"summary": "plain finding"})
        self._insert_finding(engine, run_id)

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "trace_observability_repair")
        self.assertIn("observable reasoning trace", report["repair_plan"]["items"][0]["reason"])

    def test_missing_tool_calls_triggers_trace_observability_repair(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(engine, task_id, tool_calls=[])
        self._insert_finding(engine, run_id)

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["metrics"]["trace_observability_ratio"], 0.0)
        self.assertEqual(report["verdict"]["next_focus"], "trace_observability_repair")
        self.assertEqual(report["repair_plan"]["items"][0]["run_key"], "run-a")

    def test_completed_run_without_final_result_state_triggers_repair(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(
            engine,
            task_id,
            output={"structured_response": {"schema_version": "reasoning_response_v1"}},
        )
        self._insert_finding(engine, run_id)

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["metrics"]["trace_observability_ratio"], 1.0)
        self.assertEqual(report["metrics"]["final_result_state_ratio"], 0.0)
        self.assertEqual(report["verdict"]["next_focus"], "final_result_state_repair")
        self.assertIn("final result state", report["repair_plan"]["items"][0]["reason"])

    def test_completed_run_without_finding_gets_generation_repair(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        self._insert_run(engine, task_id)

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "finding_generation_repair")
        self.assertEqual(report["metrics"]["completed_runs_without_findings"], 1)

    def test_pending_review_high_after_quality_targets_pass(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(engine, task_id)
        self._insert_finding(engine, run_id, status="draft")

        config = load_reasoning_loop_config(None)
        config["targets"]["max_pending_review_ratio"] = 0.0
        report = evaluate_reasoning_loop(url, "tenant-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "review_queue_drain")
        self.assertEqual(report["repair_plan"]["items"][0]["finding_key"], "finding-a")

    def test_completed_run_records_observable_tool_failure_retry_path(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        query_plan = [
            {
                "step_id": "plan-retrieve-context",
                "kind": "tool_use",
                "decision_reason": "Need approved graph evidence before drafting a reasoning finding.",
                "input": {"question": "Assess Red Sea risk"},
                "expected_output": "grounded evidence paths",
            }
        ]
        tool_calls = [
            {
                "id": "tool-retrieve-context-1",
                "step_id": "tool-retrieve-context-1",
                "step": "retrieve_context",
                "tool_name": "graph.search_paths",
                "attempt": 1,
                "status": "failed",
                "retryable": True,
                "idempotency_key": "reasoning:task-a:graph-search",
                "input": {"query": "Red Sea risk evidence paths"},
                "output": {},
                "error": {
                    "code": "TRANSIENT_TIMEOUT",
                    "message": "graph path search timed out",
                    "retryable": True,
                },
                "state_diff": {"tool_status": {"before": "not_started", "after": "failed_retryable"}},
            },
            {
                "id": "tool-retrieve-context-2",
                "step_id": "tool-retrieve-context-2",
                "step": "retrieve_context",
                "tool_name": "graph.search_paths",
                "attempt": 2,
                "status": "completed",
                "idempotency_key": "reasoning:task-a:graph-search",
                "retry_of": "tool-retrieve-context-1",
                "retry_of_step_id": "tool-retrieve-context-1",
                "input": {"query": "Red Sea risk evidence paths"},
                "output": {"evidence_path_count": 1},
                "state_diff": {"tool_status": {"before": "failed_retryable", "after": "completed"}},
            },
        ]
        run_id = self._insert_run(
            engine,
            task_id,
            query_plan=query_plan,
            tool_calls=tool_calls,
            evidence=[{"kind": "graph", "path": ["Red Sea", "affects", "ShippingRisk"]}],
            output={
                "structured_response": {"schema_version": "reasoning_response_v1"},
                "final_result": {"status": "answered", "finding_keys": ["finding-a"]},
            },
        )
        self._insert_finding(
            engine,
            run_id,
            evidence=[{"kind": "graph", "path": ["Red Sea", "affects", "ShippingRisk"]}],
        )

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["metrics"]["observable_trace_count"], 1)
        self.assertEqual(report["metrics"]["trace_observability_ratio"], 1.0)
        self.assertEqual(report["metrics"]["linked_retry_run_count"], 1)
        self.assertEqual(report["metrics"]["final_result_state_count"], 1)
        self.assertEqual(report["metrics"]["final_result_state_ratio"], 1.0)
        self.assertEqual(report["metrics"]["retryable_tool_error_run_count"], 1)
        self.assertEqual(report["metrics"]["observable_retry_path_run_count"], 1)
        self.assertEqual(report["verdict"]["next_focus"], "continue_reasoning_monitoring")

    def test_retry_metrics_accept_same_retry_link_shape(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(
            engine,
            task_id,
            query_plan=[
                {
                    "step_id": "plan-retrieve-context",
                    "kind": "tool_use",
                    "decision_reason": "Need graph evidence.",
                    "input": {"question": "Assess Red Sea risk"},
                    "expected_output": "evidence paths",
                }
            ],
            tool_calls=[
                {
                    "id": "tool-retrieve-context-1",
                    "step": "retrieve_context",
                    "tool_name": "graph.search_paths",
                    "attempt": 1,
                    "status": "failed",
                    "retryable": True,
                    "idempotency_key": "reasoning:task-a:graph-search",
                    "error": {"code": "TRANSIENT_TIMEOUT", "message": "graph path search timed out"},
                },
                {
                    "id": "tool-retrieve-context-2",
                    "step": "retrieve_context",
                    "tool_name": "graph.search_paths",
                    "attempt": 2,
                    "status": "ok",
                    "retry_of": "tool-retrieve-context-1",
                    "idempotency_key": "reasoning:task-a:graph-search",
                    "output": {"evidence_path_count": 1},
                },
            ],
            evidence=[{"kind": "graph", "path": ["Red Sea", "affects", "ShippingRisk"]}],
            output={
                "structured_response": {"schema_version": "reasoning_response_v1"},
                "final_result": {"status": "answered", "finding_keys": ["finding-a"]},
            },
        )
        self._insert_finding(
            engine,
            run_id,
            evidence=[{"kind": "graph", "path": ["Red Sea", "affects", "ShippingRisk"]}],
        )

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["metrics"]["linked_retry_run_count"], 1)
        self.assertEqual(report["metrics"]["retryable_tool_error_run_count"], 1)
        self.assertEqual(report["metrics"]["observable_retry_path_run_count"], 1)
        self.assertEqual(report["metrics"]["observable_retry_path_ratio"], 1.0)

    def test_retryable_error_without_observable_retry_path_triggers_repair(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(
            engine,
            task_id,
            tool_calls=[
                {
                    "id": "tool-retrieve-context-1",
                    "step": "retrieve_context",
                    "tool_name": "graph.search_paths",
                    "attempt": 1,
                    "status": "failed",
                    "retryable": True,
                    "idempotency_key": "reasoning:task-a:graph-search",
                    "error": {"code": "TRANSIENT_TIMEOUT", "message": "graph path search timed out"},
                }
            ],
            evidence=[{"kind": "graph", "path": ["Red Sea", "affects", "ShippingRisk"]}],
        )
        self._insert_finding(
            engine,
            run_id,
            evidence=[{"kind": "graph", "path": ["Red Sea", "affects", "ShippingRisk"]}],
        )

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["metrics"]["retryable_tool_error_run_count"], 1)
        self.assertEqual(report["metrics"]["observable_retry_path_run_count"], 0)
        self.assertEqual(report["metrics"]["observable_retry_path_ratio"], 0.0)
        self.assertEqual(report["verdict"]["next_focus"], "retry_path_observability_repair")
        self.assertIn("retryable tool failure", report["repair_plan"]["items"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
