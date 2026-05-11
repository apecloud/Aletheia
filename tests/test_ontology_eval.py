import json
import tempfile
import unittest
from pathlib import Path

from evals.ontology_eval import build_report, evaluate, load_snapshot


class OntologyEvalTest(unittest.TestCase):
    def test_evaluates_missing_and_extra_objects_and_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            golden = tmp / "golden.json"
            actual = tmp / "actual.json"
            golden.write_text(
                json.dumps(
                    {
                        "objects": [{"name": "Customer"}, {"name": "Order"}],
                        "links": [{"source": "Customer", "target": "Order"}],
                    }
                ),
                encoding="utf-8",
            )
            actual.write_text(
                json.dumps(
                    {
                        "business_objects": [{"object_name": "Customer"}, {"object_name": "Supplier"}],
                        "business_links": [{"source_name": "Supplier", "target_name": "Order"}],
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(golden, actual)

        self.assertEqual(report["evaluation"]["objects"]["precision"], 0.5)
        self.assertEqual(report["evaluation"]["objects"]["recall"], 0.5)
        self.assertEqual(report["evaluation"]["objects"]["missing"], [{"name": "Order", "tables": []}])
        self.assertEqual(report["evaluation"]["objects"]["extra"], [{"name": "Supplier", "tables": []}])
        self.assertEqual(report["evaluation"]["links"]["precision"], 0.0)
        self.assertEqual(report["evaluation"]["links"]["recall"], 0.0)

    def test_link_matching_is_direction_insensitive_for_v1_relationship_coverage(self):
        golden = load_snapshot(Path("evals/fixtures/northwind_golden.json"))
        actual = load_snapshot(Path("evals/fixtures/northwind_actual.sample.json"))

        result = evaluate(golden, actual)

        self.assertEqual(result["objects"]["precision"], 1.0)
        self.assertEqual(result["objects"]["recall"], 1.0)
        self.assertEqual(result["links"]["precision"], 1.0)
        self.assertEqual(result["links"]["recall"], 1.0)

    def test_stability_diff_reports_added_and_removed_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            golden = tmp / "golden.json"
            previous = tmp / "previous.json"
            actual = tmp / "actual.json"
            golden.write_text(
                json.dumps(
                    {
                        "objects": [{"name": "Customer"}, {"name": "Order"}, {"name": "Product"}],
                        "links": [{"source": "Customer", "target": "Order"}],
                    }
                ),
                encoding="utf-8",
            )
            previous.write_text(
                json.dumps(
                    {
                        "objects": [{"name": "Customer"}, {"name": "Order"}],
                        "links": [{"source": "Customer", "target": "Order"}],
                    }
                ),
                encoding="utf-8",
            )
            actual.write_text(
                json.dumps(
                    {
                        "objects": [{"name": "Customer"}, {"name": "Product"}],
                        "links": [{"source": "Order", "target": "Product"}],
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(golden, actual, previous)

        self.assertEqual(report["stability_diff"]["objects"]["added"], [{"name": "Product", "tables": []}])
        self.assertEqual(report["stability_diff"]["objects"]["removed"], [{"name": "Order", "tables": []}])
        self.assertEqual(report["stability_diff"]["links"]["added"], [{"source": "Order", "target": "Product"}])
        self.assertEqual(report["stability_diff"]["links"]["removed"], [{"source": "Customer", "target": "Order"}])

    def test_loads_future_artifact_snapshot_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "artifact_snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {
                                "artifact_type": "object",
                                "name": "Customer",
                                "canonical_key": "object:customer",
                                "payload_json": {"mapped_tables": ["customers"]},
                            },
                            {
                                "artifact_type": "link",
                                "name": "Customer places Order",
                                "canonical_key": "link:customer:places:order",
                                "payload_json": {
                                    "source_object": "Customer",
                                    "target_object": "Order",
                                    "cardinality": "1:N",
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            snapshot = load_snapshot(snapshot_path)

        self.assertEqual(snapshot.objects[0].as_dict(), {"name": "Customer", "tables": ["customers"]})
        self.assertEqual(
            snapshot.links[0].as_dict(),
            {"source": "Customer", "target": "Order", "link_type": "1:N"},
        )

    def test_artifact_snapshot_can_fallback_to_canonical_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "artifact_snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {
                                "artifact_type": "object",
                                "canonical_key": "object:customer",
                                "payload": {"mapped_tables": ["customers"]},
                            },
                            {
                                "artifact_type": "link",
                                "canonical_key": "link:customer:places:order",
                                "payload": {},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            snapshot = load_snapshot(snapshot_path)

        self.assertEqual(snapshot.objects[0].key, "customer")
        self.assertEqual(snapshot.links[0].key, "customer--order")

    def test_required_optional_report_separates_optional_hits_from_required_recall(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            required = tmp / "required.json"
            optional = tmp / "optional.json"
            actual = tmp / "actual.json"
            required.write_text(
                json.dumps(
                    {
                        "objects": [{"name": "Customer"}, {"name": "Order"}],
                        "links": [{"source": "Customer", "target": "Order"}],
                    }
                ),
                encoding="utf-8",
            )
            optional.write_text(
                json.dumps(
                    {
                        "objects": [{"name": "Employee"}],
                        "links": [{"source": "Employee", "target": "Order"}],
                    }
                ),
                encoding="utf-8",
            )
            actual.write_text(
                json.dumps(
                    {
                        "objects": [{"name": "Customer"}, {"name": "Order"}, {"name": "Employee"}],
                        "links": [
                            {"source": "Customer", "target": "Order"},
                            {"source": "Employee", "target": "Order"},
                            {"source": "Employee", "target": "Employee"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(required, actual, optional_path=optional)

        objects = report["required_optional_evaluation"]["objects"]
        links = report["required_optional_evaluation"]["links"]
        self.assertEqual(objects["required_recall"], 1.0)
        self.assertEqual(objects["optional_hit"], [{"name": "Employee", "tables": []}])
        self.assertEqual(objects["unexpected_extra"], [])
        self.assertEqual(links["required_recall"], 1.0)
        self.assertEqual(links["optional_hit"], [{"source": "Employee", "target": "Order"}])
        self.assertEqual(links["unexpected_extra"], [{"source": "Employee", "target": "Employee"}])


if __name__ == "__main__":
    unittest.main()
