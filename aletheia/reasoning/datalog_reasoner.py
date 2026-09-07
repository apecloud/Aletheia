"""Datalog-style recursive rule reasoning.

A small, self-contained (no new dependency) bottom-up Datalog engine: add
ground facts and Horn-clause rules, derive the full fixpoint closure, and
query the resulting fact base with variable bindings. Appropriate at the
scale this is meant for (hundreds to low thousands of facts, e.g. one
Aletheia tenant's graph) -- naive bottom-up evaluation, not a production
Datalog engine (no stratified negation, no aggregation, no indexing beyond
a per-predicate bucket).

Syntax (deliberately uniform, unlike some Prolog-flavored Datalogs that mix
bare-uppercase rule variables with sigil-prefixed query variables): a
variable is always written ``?Name``; anything else (bare word or quoted
string) is a constant.

    reasoner = DatalogReasoner()
    reasoner.add_fact("parent(tom, bob)")
    reasoner.add_fact("parent(bob, ann)")
    reasoner.add_rule("ancestor(?X, ?Y) :- parent(?X, ?Y).")
    reasoner.add_rule("ancestor(?X, ?Y) :- parent(?X, ?Z), ancestor(?Z, ?Y).")
    reasoner.derive_all()
    reasoner.query("ancestor(tom, ?Y)")  # -> [{"Y": "bob"}, {"Y": "ann"}]

See aletheia/reasoning/graph_facts.py for converting a pulled graph
snapshot (GraphInstanceRepository.full_graph()'s node/edge shape) into
facts this reasoner can run rules over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

Fact = tuple[str, ...]  # (predicate, arg1, arg2, ...)

_ATOM_RE = re.compile(r"(\w+)\(([^()]*)\)")


def _is_var(token: str) -> bool:
    return token.startswith("?")


def _parse_args(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    return tuple(part.strip() for part in raw.split(","))


def _parse_atom(text: str) -> Fact:
    match = _ATOM_RE.fullmatch(text.strip())
    if not match:
        raise ValueError(f"Not a valid atom: {text!r}")
    predicate, raw_args = match.groups()
    return (predicate, *_parse_args(raw_args))


@dataclass(frozen=True)
class Rule:
    head: Fact
    body: tuple[Fact, ...]

    def __str__(self) -> str:
        head = f"{self.head[0]}({', '.join(self.head[1:])})"
        body = ", ".join(f"{atom[0]}({', '.join(atom[1:])})" for atom in self.body)
        return f"{head} :- {body}."


def _parse_rule(text: str) -> Rule:
    text = text.strip().rstrip(".").strip()
    if ":-" not in text:
        raise ValueError(f"Not a valid rule (missing ':-'): {text!r}")
    head_text, body_text = text.split(":-", 1)
    head = _parse_atom(head_text)
    body = tuple(_parse_atom(f"{m.group(1)}({m.group(2)})") for m in _ATOM_RE.finditer(body_text))
    if not body:
        raise ValueError(f"Rule body has no atoms: {text!r}")
    return Rule(head=head, body=body)


class DatalogReasoner:
    def __init__(self):
        self._facts: set[Fact] = set()
        self._rules: list[Rule] = []
        self._derived: set[Fact] = set()

    def add_fact(self, fact_str: str) -> None:
        atom = _parse_atom(fact_str.strip().rstrip("."))
        if any(_is_var(arg) for arg in atom[1:]):
            raise ValueError(f"Facts must be ground (no variables): {fact_str!r}")
        self._facts.add(atom)

    def add_rule(self, rule_str: str) -> None:
        self._rules.append(_parse_rule(rule_str))

    def derive_all(self) -> list[str]:
        """Naive bottom-up fixpoint: repeatedly apply every rule to the
        current fact set until a full pass adds nothing new. Returns every
        fact (given + derived) as ``"predicate(arg1, arg2)"`` strings,
        sorted for deterministic output."""
        all_facts = set(self._facts)
        changed = True
        while changed:
            changed = False
            by_predicate = self._index(all_facts)
            for rule in self._rules:
                for binding in self._match_body(rule.body, by_predicate, {}):
                    new_fact = self._substitute(rule.head, binding)
                    if new_fact not in all_facts:
                        all_facts.add(new_fact)
                        changed = True
        self._derived = all_facts - self._facts
        return sorted(self._format(f) for f in all_facts)

    def query(self, pattern: str) -> list[dict[str, str]]:
        """Match ``pattern`` (e.g. ``"ancestor(tom, ?Y)"``) against the
        current fact base (call derive_all() first to include derived
        facts, not just the ground facts added so far) and return one dict
        of variable->value bindings per match."""
        atom = _parse_atom(pattern.strip().rstrip("."))
        by_predicate = self._index(self._facts | self._derived)
        return [
            {k.lstrip("?"): v for k, v in binding.items()}
            for binding in self._match_body((atom,), by_predicate, {})
        ]

    @staticmethod
    def _index(facts: set[Fact]) -> dict[str, list[Fact]]:
        by_predicate: dict[str, list[Fact]] = {}
        for fact in facts:
            by_predicate.setdefault(fact[0], []).append(fact)
        return by_predicate

    def _match_body(self, body: tuple[Fact, ...], by_predicate: dict[str, list[Fact]], binding: dict[str, str]):
        if not body:
            yield binding
            return
        first, *rest = body
        predicate, args = first[0], first[1:]
        for fact in by_predicate.get(predicate, []):
            if len(fact) - 1 != len(args):
                continue
            new_binding = dict(binding)
            if self._unify(args, fact[1:], new_binding):
                yield from self._match_body(tuple(rest), by_predicate, new_binding)

    @staticmethod
    def _unify(args: tuple[str, ...], values: tuple[str, ...], binding: dict[str, str]) -> bool:
        for arg, value in zip(args, values):
            if _is_var(arg):
                existing = binding.get(arg)
                if existing is None:
                    binding[arg] = value
                elif existing != value:
                    return False
            elif arg != value:
                return False
        return True

    @staticmethod
    def _substitute(atom: Fact, binding: dict[str, str]) -> Fact:
        return (atom[0], *(binding.get(arg, arg) for arg in atom[1:]))

    @staticmethod
    def _format(fact: Fact) -> str:
        return f"{fact[0]}({', '.join(fact[1:])})"
