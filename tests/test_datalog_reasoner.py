import unittest

from aletheia.reasoning.datalog_reasoner import DatalogReasoner
from aletheia.reasoning.graph_facts import graph_to_facts, load_facts


class DatalogReasonerTest(unittest.TestCase):
    def test_recursive_ancestor_rule_derives_transitive_closure(self):
        reasoner = DatalogReasoner()
        reasoner.add_fact("parent(tom, bob)")
        reasoner.add_fact("parent(bob, ann)")
        reasoner.add_rule("ancestor(?X, ?Y) :- parent(?X, ?Y).")
        reasoner.add_rule("ancestor(?X, ?Y) :- parent(?X, ?Z), ancestor(?Z, ?Y).")

        derived = reasoner.derive_all()

        self.assertIn("ancestor(tom, bob)", derived)
        self.assertIn("ancestor(bob, ann)", derived)
        self.assertIn("ancestor(tom, ann)", derived)  # only reachable via the recursive rule

    def test_query_binds_variables_without_question_mark_prefix(self):
        reasoner = DatalogReasoner()
        reasoner.add_fact("parent(tom, bob)")
        reasoner.add_fact("parent(tom, liz)")
        reasoner.add_rule("ancestor(?X, ?Y) :- parent(?X, ?Y).")
        reasoner.derive_all()

        results = reasoner.query("ancestor(tom, ?Y)")

        self.assertEqual(sorted(r["Y"] for r in results), ["bob", "liz"])

    def test_query_with_bound_second_argument_finds_matching_first_argument(self):
        reasoner = DatalogReasoner()
        reasoner.add_fact("parent(tom, bob)")
        reasoner.add_fact("parent(bob, ann)")
        reasoner.add_rule("ancestor(?X, ?Y) :- parent(?X, ?Y).")
        reasoner.add_rule("ancestor(?X, ?Y) :- parent(?X, ?Z), ancestor(?Z, ?Y).")
        reasoner.derive_all()

        results = reasoner.query("ancestor(?X, ann)")

        self.assertEqual(sorted(r["X"] for r in results), ["bob", "tom"])

    def test_facts_must_be_ground(self):
        reasoner = DatalogReasoner()
        with self.assertRaises(ValueError):
            reasoner.add_fact("parent(?X, bob)")

    def test_rule_without_body_atoms_rejected(self):
        reasoner = DatalogReasoner()
        with self.assertRaises(ValueError):
            reasoner.add_rule("ancestor(?X, ?Y) :- .")

    def test_transitive_impact_propagation_two_hops(self):
        """The KubeBlocks Datalog demo's actual use case: does a change to
        commit A eventually affect commit B via a shared touched module,
        transitively? modifies(commit, module); affects(X, Y) :-
        modifies(X, Y). affects(X, Y) :- modifies(X, Z), affects(Z, Y)."""
        reasoner = DatalogReasoner()
        reasoner.add_fact("touches(commit_a, module_x)")
        reasoner.add_fact("touches(commit_b, module_x)")
        reasoner.add_fact("touches(commit_b, module_y)")
        reasoner.add_fact("touches(commit_c, module_y)")
        reasoner.add_rule("shares_module(?C1, ?C2) :- touches(?C1, ?M), touches(?C2, ?M).")

        derived = reasoner.derive_all()

        self.assertIn("shares_module(commit_a, commit_b)", derived)
        self.assertIn("shares_module(commit_b, commit_c)", derived)
        # not directly touching a shared module, and no transitive rule was
        # defined for shares_module (unlike ancestor above) -- confirms the
        # engine doesn't invent transitivity that wasn't declared
        self.assertNotIn("shares_module(commit_a, commit_c)", derived)


class GraphFactsTest(unittest.TestCase):
    def test_graph_to_facts_emits_type_and_edge_facts(self):
        nodes = [
            {"id": "kb_issue_1", "type": "Issue"},
            {"id": "kb_pr_1", "type": "PullRequest"},
        ]
        edges = [
            {"source": "kb_pr_1", "target": "kb_issue_1", "label": "CLOSES"},
        ]

        facts = graph_to_facts(nodes, edges)

        self.assertIn("is_type(kb_issue_1, Issue)", facts)
        self.assertIn("is_type(kb_pr_1, PullRequest)", facts)
        self.assertIn("closes(kb_pr_1, kb_issue_1)", facts)
        self.assertIn("edge(kb_pr_1, kb_issue_1, closes)", facts)

    def test_load_facts_adds_every_fact_into_a_reasoner(self):
        reasoner = DatalogReasoner()
        nodes = [{"id": "a", "type": "Commit"}, {"id": "b", "type": "File"}]
        edges = [{"source": "a", "target": "b", "label": "TOUCHES"}]

        count = load_facts(reasoner, nodes, edges)

        self.assertEqual(count, 4)  # 2 is_type facts + 1 touches fact + 1 generic edge() fact
        derived = reasoner.derive_all()
        self.assertIn("touches(a, b)", derived)
        self.assertIn("edge(a, b, touches)", derived)


if __name__ == "__main__":
    unittest.main()
