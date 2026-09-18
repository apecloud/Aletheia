import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aletheia.ontology.registry import _normalize_guardrails, get_action, get_all_actions, propose_action
from aletheia.ontology.store import ensure_artifact_schema


class OntologyRegistryActionTests(unittest.TestCase):
    def _engine(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return create_engine(f"sqlite:///{tmpdir.name}/metadata.db")

    def test_propose_action_stores_preconditions_and_structured_guardrails(self):
        engine = self._engine()
        ensure_artifact_schema(engine)
        Session = sessionmaker(bind=engine)
        with Session() as session:
            propose_action(
                session,
                tenant_id="tenant-a",
                name="CloseWaterway",
                applies_to=["Waterway"],
                trigger_event="Waterway disruption threshold crossed",
                preconditions=["waterway is not already closed"],
                input_parameters=["closure_reason"],
                expected_effects=["set operational_status to Closed"],
                guardrails={
                    "is_destructive": True,
                    "is_reversible": False,
                    "requires_human_approval": True,
                    "notes": ["requires authorized operator approval"],
                },
                status="approved",
            )
            session.commit()

            action = get_action(session, "tenant-a", "CloseWaterway")
            self.assertEqual(action["preconditions"], ["waterway is not already closed"])
            self.assertEqual(
                action["guardrails"],
                {
                    "is_destructive": True,
                    "is_reversible": False,
                    "requires_human_approval": True,
                    "notes": ["requires authorized operator approval"],
                },
            )

    def test_propose_action_defaults_preconditions_and_guardrails_when_omitted(self):
        engine = self._engine()
        ensure_artifact_schema(engine)
        Session = sessionmaker(bind=engine)
        with Session() as session:
            propose_action(
                session,
                tenant_id="tenant-a",
                name="SuggestAssignee",
                applies_to=["Issue"],
                trigger_event="Issue has no assignee after review window",
                status="approved",
            )
            session.commit()

            actions = get_all_actions(session, "tenant-a")
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["preconditions"], [])
            self.assertEqual(
                actions[0]["guardrails"],
                {"is_destructive": False, "is_reversible": True, "requires_human_approval": False, "notes": []},
            )

    def test_normalize_guardrails_accepts_legacy_list_shape(self):
        normalized = _normalize_guardrails(["requires review", "  ", "advisory only"])
        self.assertEqual(
            normalized,
            {
                "is_destructive": False,
                "is_reversible": True,
                "requires_human_approval": False,
                "notes": ["requires review", "advisory only"],
            },
        )

    def test_normalize_guardrails_preserves_dict_shape_and_fills_defaults(self):
        normalized = _normalize_guardrails({"is_destructive": True, "notes": ["irreversible delete"]})
        self.assertEqual(
            normalized,
            {
                "is_destructive": True,
                "is_reversible": True,
                "requires_human_approval": False,
                "notes": ["irreversible delete"],
            },
        )

    def test_normalize_guardrails_handles_none(self):
        self.assertEqual(
            _normalize_guardrails(None),
            {"is_destructive": False, "is_reversible": True, "requires_human_approval": False, "notes": []},
        )


if __name__ == "__main__":
    unittest.main()
