# Reasoning Lexical Hints

`ReasoningEngine` uses lexical hints as a bounded fallback when the LLM planner
misses an obvious relation or returns a clean empty rank. Hints are not answers
and must not be keyed by benchmark qid. They only adjust relation scoring inside
the existing `ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K` limit.

## Config Location

The checked-in WebQSP/Freebase hint set lives at:

`config/reasoning_lexical_hints.webqsp_freebase.json`

The runtime binding order is:

1. `ReasoningEngine(..., lexical_hint_config=...)` constructor override.
2. Repository hook `reasoning_lexical_hint_config(tenant)`.
3. Environment path `ALETHEIA_REASONING_LEXICAL_HINTS_PATH`.
4. Default WebQSP/Freebase config above.

Set `ALETHEIA_REASONING_LEXICAL_HINTS_PATH=off` to disable configured hints and
keep only generic lexical overlap behavior.

## Schema

Top-level metadata:

- `version`: integer config version.
- `id`: stable config id.
- `description`: human-readable purpose.
- `applies_to`: advisory binding metadata for tenants, ontology, and relation set.

`term_expansions` entries add query terms before lexical overlap scoring:

- `name`: rule id for review.
- `when_any`: trigger when any question token is present.
- `when_all`: trigger only when all question tokens are present.
- `unless_any`: skip when any token is present.
- `add`: extra terms to include during relation text scoring.

`relation_bonuses` entries adjust specific relation scores:

- `name`: rule id for review.
- `when_any`, `when_all`, `unless_any`: same question-token gates.
- `link_contains_any` / `link_contains_all`: substring gates on relation key.
- `relation_contains_any` / `relation_contains_all`: substring gates on relation key plus description/domain/range text.
- `bonus`: integer score delta. Negative values are allowed.

## Compatibility

When no config is present, the planner does not crash and does not add
domain-specific expansions or bonuses. Existing bounded lexical recall still
uses literal question/relation overlap, so generic behavior remains available.

For tenant-specific production use, prefer the repository hook so a tenant,
ontology version, or relation set can select an explicit hint config without
changing `reasoning_engine.py`.
