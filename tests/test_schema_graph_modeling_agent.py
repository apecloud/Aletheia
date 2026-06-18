import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine, text

from agents.schema_graph_modeling_agent import (
    GraphEdgeTypeDraft,
    GraphModelDraft,
    GraphNodeTypeDraft,
    SchemaGraphModelingAgent,
)
from agents.ontology_artifacts import (
    BusinessLink,
    BusinessObject,
    ObjectTableMapping,
    SchemaLinkCandidate,
    SchemaObjectCandidate,
    SchemaObjectTableMapping,
)


class SchemaGraphModelingAgentTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, customer_name TEXT)"))
            conn.execute(
                text(
                    "CREATE TABLE invoices ("
                    "invoice_id INTEGER PRIMARY KEY, "
                    "customer_id INTEGER NOT NULL, "
                    "amount NUMERIC, "
                    "FOREIGN KEY(customer_id) REFERENCES customers(customer_id)"
                    ")"
                )
            )
            conn.execute(text("INSERT INTO customers (customer_id, customer_name) VALUES (1, 'Acme')"))
        self.agent = SchemaGraphModelingAgent(source_db_url="sqlite:///:memory:")
        self.agent.source_engine = self.engine

    def test_inspects_raw_schema_without_domain_terms(self):
        schema = self.agent.inspect_source_schema()
        table_names = {table["table_name"] for table in schema}

        self.assertEqual(table_names, {"customers", "invoices"})
        invoice = next(table for table in schema if table["table_name"] == "invoices")
        customer_id = next(column for column in invoice["columns"] if column["name"] == "customer_id")
        self.assertTrue(customer_id["foreign_key"])
        self.assertEqual(customer_id["references"], "customers.customer_id")
        customers = next(table for table in schema if table["table_name"] == "customers")
        self.assertEqual(customers["row_count"], 1)
        self.assertEqual(customers["sample_rows"][0]["customer_name"], "Acme")

        prompt = self.agent.build_prompt(schema)
        self.assertIn("Do not use any built-in tenant/domain vocabulary", prompt)
        self.assertIn("Keep ontology types distinct from graph nodes and fact/event instances", prompt)
        self.assertIn("A draft ontology object is a continuant-like, identity-bearing object type", prompt)
        self.assertIn("event rows should normally be modeled as graph/fact nodes", prompt)
        self.assertIn("fact rows should normally be graph/fact nodes, edge evidence, or properties", prompt)
        self.assertIn("receive events/facts, participate in actions", prompt)
        self.assertIn("Keep the durable ontology small", prompt)
        self.assertIn("Situational claims, observations, metric changes, impact claims, indicator claims", prompt)
        self.assertIn("without using a fixed ontology class name", prompt)
        self.assertIn("Decision tests before creating each edge_type", prompt)
        self.assertIn("graph_node_candidate", prompt)
        for forbidden in ("RiskFinding", "TradeDependency", "Chokepoint", "maritime-risk"):
            self.assertNotIn(forbidden, prompt)

    def test_artifact_specs_are_llm_inferred_draft_contract(self):
        draft = GraphModelDraft(
            node_types=[
                GraphNodeTypeDraft(
                    key="customer",
                    name="Customer",
                    description="Customer account inferred from customers table.",
                    mapped_tables=["customers"],
                    primary_key="customer_id",
                    properties=["customer_id", "customer_name"],
                    evidence=["customers.customer_id is primary key"],
                    confidence=0.9,
                )
            ],
            edge_types=[
                GraphEdgeTypeDraft(
                    key="customer_invoice",
                    name="Customer Invoice",
                    description="Invoices reference customers by customer_id.",
                    source_node_key="customer",
                    target_node_key="invoice",
                    cardinality="1:N",
                    source_table="customers",
                    target_table="invoices",
                    join_condition="invoices.customer_id = customers.customer_id",
                    properties=["invoice_total", "invoice_status"],
                    evidence=["invoices.customer_id foreign key references customers.customer_id"],
                    confidence=0.88,
                )
            ],
        )

        specs = self.agent.artifact_specs(draft)

        self.assertEqual([spec["artifact_type"] for spec in specs], ["object", "link"])
        for spec in specs:
            self.assertEqual(spec["payload"]["canonical_write_boundary"], "draft_only_until_human_review")
            self.assertTrue(spec["payload"]["llm_inferred"])
            self.assertEqual(spec["payload"]["prompt_version"], "schema_graph_modeling_v1")
        self.assertEqual(specs[1]["payload"]["edge_properties"], ["invoice_total", "invoice_status"])

    def test_legacy_object_model_adapter_uses_unified_contract(self):
        legacy_objects = SimpleNamespace(
            business_objects=[
                SimpleNamespace(
                    name="Customer Account",
                    description="Customer account grouped from source schema.",
                    mapped_table_names=["customers"],
                )
            ]
        )
        metadata_dump = [
            {
                "table_name": "customers",
                "table_comment": "Customer master table",
                "columns": [
                    {"column": "customer_id", "type": "INTEGER", "semantic_type": "Identifier"},
                    {"column": "customer_name", "type": "TEXT", "semantic_type": "Name"},
                ],
            }
        ]

        draft = SchemaGraphModelingAgent.draft_from_legacy_object_model(legacy_objects, metadata_dump)

        self.assertEqual(len(draft.node_types), 1)
        self.assertEqual(draft.node_types[0].key, "customer_account")
        self.assertEqual(draft.node_types[0].mapped_tables, ["customers"])
        self.assertIn("customer_id", draft.node_types[0].properties)
        self.assertEqual(draft.review_boundary, "draft_only_until_human_review")

    def test_legacy_link_model_adapter_uses_unified_contract(self):
        legacy_links = SimpleNamespace(
            links=[
                SimpleNamespace(
                    source_object_name="Customer",
                    target_object_name="Invoice",
                    link_type="1:N",
                    description="Invoices reference customers.",
                )
            ]
        )
        ontology_dump = [
            {"object_name": "Customer", "underlying_tables": ["customers"]},
            {"object_name": "Invoice", "underlying_tables": ["invoices"]},
        ]

        draft = SchemaGraphModelingAgent.draft_from_legacy_link_model(legacy_links, ontology_dump)

        self.assertEqual(len(draft.edge_types), 1)
        self.assertEqual(draft.edge_types[0].source_node_key, "customer")
        self.assertEqual(draft.edge_types[0].target_node_key, "invoice")
        self.assertEqual(draft.edge_types[0].source_table, "customers")
        self.assertEqual(draft.edge_types[0].target_table, "invoices")

    def test_legacy_candidate_tables_use_schema_modeling_names(self):
        self.assertEqual(SchemaObjectCandidate.__tablename__, "aletheia_schema_object_candidates")
        self.assertEqual(SchemaLinkCandidate.__tablename__, "aletheia_schema_link_candidates")
        self.assertEqual(SchemaObjectTableMapping.__tablename__, "aletheia_schema_object_mappings")
        self.assertIs(BusinessObject, SchemaObjectCandidate)
        self.assertIs(BusinessLink, SchemaLinkCandidate)
        self.assertIs(ObjectTableMapping, SchemaObjectTableMapping)


if __name__ == "__main__":
    unittest.main()
