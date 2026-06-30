import json
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine, text

from agents.ontology_artifacts import ensure_artifact_schema
from agents.reasoning_loop_harness import evaluate_reasoning_loop, load_reasoning_loop_config


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
                         'graph-scope-reasoning-v1', '[]', '[]',
                         :evidence_paths_json, :output_json, :eval_result_json,
                         :status, :latency_ms, 0.0, :created_at)
                    """
                ),
                {
                    "task_id": task_id,
                    "tenant": tenant,
                    "run_key": run_key,
                    "evidence_paths_json": json.dumps(evidence if evidence is not None else [{"kind": "graph"}]),
                    "output_json": json.dumps(
                        output
                        if output is not None
                        else {"structured_response": {"schema_version": "reasoning_response_v1"}}
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

    def test_missing_structured_response_after_contract_and_evidence_pass(self):
        url, engine = self._db()
        task_id = self._insert_task(engine, status="completed")
        run_id = self._insert_run(engine, task_id, output={"summary": "plain finding"})
        self._insert_finding(engine, run_id)

        report = evaluate_reasoning_loop(url, "tenant-a", config=load_reasoning_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "response_schema_repair")
        self.assertIn("reasoning_response_v1", report["repair_plan"]["items"][0]["reason"])

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


if __name__ == "__main__":
    unittest.main()
