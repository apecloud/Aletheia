import unittest

from scripts.import_us_iran_war_dataset import (
    COUNTRY_EXPOSURES,
    GRAPH_DATABASE,
    GRAPH_EDGES,
    OBJECT_SPECS,
    TENANT_ID,
    WEB_SOURCES,
    build_frames,
)


class USIranWarImportFixtureTest(unittest.TestCase):
    def test_fixture_has_graph_path_and_provenance(self):
        self.assertEqual(TENANT_ID, "us-iran-war")
        self.assertEqual(GRAPH_DATABASE, "us_iran_war")
        source_ids = {source["source_id"] for source in WEB_SOURCES}
        for source in WEB_SOURCES:
            self.assertTrue(source["url"].startswith("https://"))
            self.assertIn("query", source)
            self.assertIn("retrieved_at", source)
            self.assertIn("license_risk", source)

        edges = {(source, relation, target) for source, relation, target, _source_id, _confidence in GRAPH_EDGES}
        self.assertIn(
            ("event_2025_june_us_iran_escalation", "raises_risk_of", "channel_hormuz_oil_flow"),
            edges,
        )
        self.assertIn(("channel_hormuz_oil_flow", "impacts", "country_IND"), edges)
        self.assertIn(("country_IND", "requires_action", "action_energy_importer_stress_test"), edges)
        for _source, _relation, _target, source_id, _confidence in GRAPH_EDGES:
            self.assertIn(source_id, source_ids)

    def test_source_tables_and_artifacts_are_draft_ready(self):
        frames = build_frames()
        self.assertEqual(len(frames["us_iran_war_web_sources"]), len(WEB_SOURCES))
        self.assertEqual(len(frames["us_iran_war_country_exposures"]), len(COUNTRY_EXPOSURES))
        self.assertEqual(len(frames["us_iran_war_graph_edges"]), len(GRAPH_EDGES))
        self.assertEqual({spec["table"] for spec in OBJECT_SPECS}, set(frames.keys()))
        self.assertTrue(all(spec["primary_key"] in frames[spec["table"]].columns for spec in OBJECT_SPECS))


if __name__ == "__main__":
    unittest.main()
