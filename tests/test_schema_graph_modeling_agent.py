import unittest

from sqlalchemy import create_engine, text

from agents.schema_graph_modeling_agent import (
    GraphEdgeTypeDraft,
    GraphModelDraft,
    GraphNodeTypeDraft,
    SchemaGraphModelingAgent,
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

        prompt = self.agent.build_prompt(schema)
        self.assertIn("Do not use any built-in tenant/domain vocabulary", prompt)
        self.assertIn("Do not invent review/finding/action/insight nodes", prompt)
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


if __name__ == "__main__":
    unittest.main()
