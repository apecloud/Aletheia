import unittest
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import create_engine, text

from agents.schema_graph_modeling_agent import (
    GraphEdgeTypeDraft,
    GraphModelDraft,
    GraphNodeTypeDraft,
    OntologyConsistencyValidationError,
    SchemaGraphModelingAgent,
    SchemaTraceabilityValidationError,
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

    def _source_schema_dump(self):
        return [
            {
                "table_name": "customers",
                "columns": [
                    {"name": "customer_id", "data_type": "INTEGER"},
                    {"name": "customer_name", "data_type": "TEXT"},
                ],
            },
            {
                "table_name": "invoices",
                "columns": [
                    {"name": "invoice_id", "data_type": "INTEGER"},
                    {"name": "customer_id", "data_type": "INTEGER"},
                    {"name": "amount", "data_type": "NUMERIC"},
                ],
            },
        ]

    def _complete_traceable_draft(self):
        return GraphModelDraft(
            node_types=[
                GraphNodeTypeDraft(
                    key="customer",
                    name="Customer",
                    description="Customer account inferred from customers table.",
                    mapped_tables=["customers"],
                    primary_key="customer_id",
                    properties=["customer_id", "customer_name"],
                    evidence=["customers.customer_id primary key", "customers.customer_name display name"],
                    confidence=0.9,
                ),
                GraphNodeTypeDraft(
                    key="invoice",
                    name="Invoice",
                    description="Invoice inferred from invoices table.",
                    mapped_tables=["invoices"],
                    primary_key="invoice_id",
                    properties=["invoice_id", "customer_id", "amount"],
                    evidence=["invoices.invoice_id primary key", "invoices.customer_id references customers.customer_id"],
                    confidence=0.86,
                ),
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
                    properties=["invoices.amount"],
                    evidence=["invoices.customer_id foreign key references customers.customer_id"],
                    confidence=0.88,
                )
            ],
        )

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
                ),
                GraphNodeTypeDraft(
                    key="invoice",
                    name="Invoice",
                    description="Invoice inferred from invoices table.",
                    mapped_tables=["invoices"],
                    primary_key="invoice_id",
                    properties=["invoice_id", "customer_id", "amount"],
                    evidence=["invoices.invoice_id is primary key"],
                    confidence=0.86,
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

        self.assertEqual([spec["artifact_type"] for spec in specs], ["object", "object", "link"])
        for spec in specs:
            self.assertEqual(spec["payload"]["canonical_write_boundary"], "draft_only_until_human_review")
            self.assertEqual(spec["payload"]["schema_contract_version"], "schema_graph_contract_v1")
            self.assertEqual(spec["payload"]["min_compatible_schema_contract_version"], "schema_graph_contract_v1")
            self.assertIn("entity_type", spec["payload"]["schema_contract_constraints"])
            self.assertIn("breaking_changes_require_major_version", spec["payload"]["schema_contract_compatibility"])
            self.assertTrue(spec["payload"]["llm_inferred"])
            self.assertEqual(spec["payload"]["prompt_version"], "schema_graph_modeling_v1")
        self.assertEqual(specs[2]["payload"]["edge_properties"], ["invoice_total", "invoice_status"])

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

    def test_draft_contract_requires_evidence_and_valid_edge_endpoints(self):
        with self.assertRaisesRegex(ValidationError, "node_type evidence"):
            GraphNodeTypeDraft(
                key="customer",
                name="Customer",
                description="Customer account inferred from source schema.",
                mapped_tables=["customers"],
                primary_key="customer_id",
                properties=["customer_id"],
                evidence=[],
                confidence=0.9,
            )

        customer = GraphNodeTypeDraft(
            key="customer",
            name="Customer",
            description="Customer account inferred from source schema.",
            mapped_tables=["customers"],
            primary_key="customer_id",
            properties=["customer_id"],
            evidence=["customers.customer_id primary key"],
            confidence=0.9,
        )
        orphan_edge = GraphEdgeTypeDraft(
            key="customer_invoice",
            name="Customer Invoice",
            description="Invoices reference customers.",
            source_node_key="customer",
            target_node_key="invoice",
            cardinality="1:N",
            source_table="customers",
            target_table="invoices",
            join_condition="invoices.customer_id = customers.customer_id",
            evidence=["invoices.customer_id foreign key references customers.customer_id"],
            confidence=0.85,
        )

        with self.assertRaisesRegex(ValidationError, "target_node_key must reference a node_type"):
            GraphModelDraft(node_types=[customer], edge_types=[orphan_edge])

    def test_rejected_candidates_require_localized_reason_and_treatment(self):
        valid = GraphModelDraft(
            rejected_candidates=[
                {
                    "name": "invoice_total",
                    "reason": "Metric column is not a durable ontology object.",
                    "suggested_graph_treatment": "Keep as an edge property on customer_invoice.",
                }
            ]
        )

        self.assertEqual(valid.review_boundary, "draft_only_until_human_review")
        self.assertEqual(valid.schema_version, "schema_graph_contract_v1")
        self.assertIn("relation_type", valid.contract_constraints)
        self.assertIn("migration_required_for", valid.compatibility)

        with self.assertRaisesRegex(ValidationError, "suggested_graph_treatment is required"):
            GraphModelDraft(
                rejected_candidates=[
                    {
                        "name": "invoice_total",
                        "reason": "Metric column is not a durable ontology object.",
                    }
                ]
            )

    def test_graph_model_contract_rejects_wrong_version_or_boundary(self):
        with self.assertRaisesRegex(ValidationError, "schema_version must be schema_graph_contract_v1"):
            GraphModelDraft(schema_version="schema_graph_contract_v2")

        with self.assertRaisesRegex(ValidationError, "min_compatible_version must be schema_graph_contract_v1"):
            GraphModelDraft(min_compatible_version="schema_graph_contract_v0")

        with self.assertRaisesRegex(ValidationError, "review_boundary must be draft_only_until_human_review"):
            GraphModelDraft(review_boundary="canonical_write_allowed")

    def test_schema_traceability_validator_accepts_complete_table_column_mapping(self):
        result = SchemaGraphModelingAgent.validate_schema_traceability(
            self._complete_traceable_draft(),
            self._source_schema_dump(),
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.error_dicts(), [])

    def test_schema_traceability_validator_rejects_missing_table_and_column(self):
        draft = GraphModelDraft(
            node_types=[
                GraphNodeTypeDraft(
                    key="customer",
                    name="Customer",
                    description="Customer account inferred from customers table.",
                    mapped_tables=["missing_customers"],
                    primary_key="customer_id",
                    properties=["missing_column"],
                    evidence=["missing_customers.customer_id primary key"],
                    confidence=0.9,
                )
            ]
        )

        result = SchemaGraphModelingAgent.validate_schema_traceability(draft, self._source_schema_dump())
        errors = result.error_dicts()

        self.assertFalse(result.valid)
        self.assertIn("unknown_table", {error["code"] for error in errors})
        self.assertIn("unknown_column", {error["code"] for error in errors})
        self.assertTrue(any(error.get("artifact_key") == "object:customer" for error in errors))
        self.assertTrue(any("node_types[0].properties[0]" == error["path"] for error in errors))

    def test_schema_traceability_validator_rejects_uncovered_source_column(self):
        draft = self._complete_traceable_draft()
        draft.node_types[1].properties.remove("amount")
        draft.edge_types[0].properties = []

        result = SchemaGraphModelingAgent.validate_schema_traceability(draft, self._source_schema_dump())

        self.assertFalse(result.valid)
        self.assertIn(
            {
                "code": "untraced_column",
                "path": "schema[invoices].columns[amount]",
                "message": "source column invoices.amount is not traceable to any draft property, key, join/evidence, rejected candidate, or assumption",
                "table": "invoices",
                "column": "amount",
            },
            result.error_dicts(),
        )

    def test_schema_traceability_validator_rejects_edge_endpoint_table_mismatch(self):
        draft = self._complete_traceable_draft()
        draft.edge_types[0].source_table = "invoices"
        draft.edge_types[0].join_condition = "invoices.customer_id = invoices.customer_id"

        result = SchemaGraphModelingAgent.validate_schema_traceability(draft, self._source_schema_dump())
        errors = result.error_dicts()

        self.assertFalse(result.valid)
        self.assertIn("edge_source_table_mismatch", {error["code"] for error in errors})
        self.assertTrue(
            any(
                error["artifact_key"] == "link:customer_invoice"
                and error["path"] == "edge_types[0].source_table"
                for error in errors
            )
        )

    def test_persistence_rejects_untraceable_draft_before_artifact_write(self):
        draft = self._complete_traceable_draft()
        draft.node_types[0].properties.append("ghost_column")

        with self.assertRaises(SchemaTraceabilityValidationError) as ctx:
            SchemaGraphModelingAgent.persist_draft_artifacts_in_session(
                object(),
                draft,
                source_schema=self._source_schema_dump(),
            )

        self.assertIn(
            {
                "code": "unknown_column",
                "path": "node_types[0].properties[2]",
                "message": "object:customer references source column 'ghost_column' that is not present on allowed tables ['customers']",
                "artifact_key": "object:customer",
                "column": "ghost_column",
            },
            ctx.exception.errors,
        )

    def test_ontology_consistency_accepts_clean_schema(self):
        draft = self._complete_traceable_draft()
        result = SchemaGraphModelingAgent.validate_ontology_consistency(draft)
        self.assertTrue(result.valid)
        self.assertEqual(result.error_dicts(), [])

    def test_ontology_consistency_rejects_subclass_cycle(self):
        draft = self._complete_traceable_draft()
        draft.node_types[0].subclass_of = ["invoice"]
        draft.node_types[1].subclass_of = ["customer"]
        result = SchemaGraphModelingAgent.validate_ontology_consistency(draft)
        codes = {e["code"] for e in result.error_dicts()}
        self.assertFalse(result.valid)
        self.assertIn("subclass_cycle", codes)

    def test_ontology_consistency_rejects_subclass_of_unknown_node(self):
        draft = self._complete_traceable_draft()
        draft.node_types[0].subclass_of = ["nonexistent"]
        result = SchemaGraphModelingAgent.validate_ontology_consistency(draft)
        codes = {e["code"] for e in result.error_dicts()}
        self.assertFalse(result.valid)
        self.assertIn("subclass_of_unknown", codes)
        self.assertTrue(
            any(e["artifact_key"] == "object:customer" for e in result.error_dicts())
        )

    def test_ontology_consistency_rejects_domain_range_mismatch(self):
        draft = self._complete_traceable_draft()
        draft.edge_types[0].domain = ["invoice"]
        draft.edge_types[0].range = ["customer"]
        result = SchemaGraphModelingAgent.validate_ontology_consistency(draft)
        codes = {e["code"] for e in result.error_dicts()}
        self.assertFalse(result.valid)
        self.assertIn("domain_mismatch", codes)
        self.assertIn("range_mismatch", codes)
        self.assertTrue(
            any(
                e["code"] == "domain_mismatch" and e["artifact_key"] == "link:customer_invoice"
                for e in result.error_dicts()
            )
        )

    def test_ontology_consistency_rejects_domain_range_unknown_node(self):
        draft = self._complete_traceable_draft()
        draft.edge_types[0].domain = ["ghost_node"]
        draft.edge_types[0].range = ["other_ghost"]
        result = SchemaGraphModelingAgent.validate_ontology_consistency(draft)
        codes = {e["code"] for e in result.error_dicts()}
        self.assertFalse(result.valid)
        self.assertIn("domain_unknown_node", codes)
        self.assertIn("range_unknown_node", codes)

    def test_ontology_consistency_rejects_disjointness_violation(self):
        draft = self._complete_traceable_draft()
        draft.node_types[0].disjoint_with = ["invoice"]
        result = SchemaGraphModelingAgent.validate_ontology_consistency(draft)
        codes = {e["code"] for e in result.error_dicts()}
        self.assertFalse(result.valid)
        self.assertIn("disjointness_violation", codes)
        self.assertTrue(
            any(
                e["code"] == "disjointness_violation"
                and e["artifact_key"] == "link:customer_invoice"
                for e in result.error_dicts()
            )
        )

    def test_persistence_rejects_ontology_inconsistent_draft(self):
        draft = self._complete_traceable_draft()
        draft.edge_types[0].domain = ["invoice"]
        draft.edge_types[0].range = ["customer"]

        with self.assertRaises(OntologyConsistencyValidationError) as ctx:
            SchemaGraphModelingAgent.persist_draft_artifacts_in_session(
                object(),
                draft,
                source_schema=self._source_schema_dump(),
            )

        codes = {e["code"] for e in ctx.exception.errors}
        self.assertIn("domain_mismatch", codes)
        self.assertIn("range_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
