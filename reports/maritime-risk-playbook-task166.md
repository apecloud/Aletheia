# Maritime-risk Graph Reasoning Playbook - task #166

## Result

Added a draft-only Autopilot playbook for tenant `maritime-risk`.

Endpoint:

```http
POST /api/reasoning/autopilot/playbooks/maritime-risk/run?tenant=maritime-risk
```

Reasoning page:

- When the active tenant is `maritime-risk`, the Autopilot tab shows `Run maritime-risk playbook`.
- The playbook populates a visible hypothesis queue and draft candidate Finding Inbox.

Validation session:

- `autopilot:maritime-risk:task166-validation`
- JSON payload: `reports/maritime-risk-playbook-task166.json`

## Hypothesis Queue

The playbook creates 4 hypotheses:

1. `Single-chokepoint dependency can create concentrated country exposure`
2. `Hazard severity should be joined to dependent trade value before ranking chokepoints`
3. `Red Sea / Bab el-Mandeb escalation should prioritize dependent countries by systemic risk`
4. `High throughput alone is not enough for a graph reasoning finding`

The fourth hypothesis is intentionally `pruned` with a reason: volume-only ranking does not show a complete graph reasoning path.

## Draft Candidate Findings

The playbook creates 3 draft candidate findings:

1. `Single chokepoint dependency creates concentrated country exposure`
2. `Hazard-adjusted chokepoint risk should drive review priority`
3. `Bab el-Mandeb risk propagation identifies countries for immediate review`

Each candidate has an evidence chain with at least five steps and includes a recommended action. The intended graph path is:

```text
hazard -> chokepoint -> dependent country -> trade/risk metric -> recommended action
```

## Example Multi-hop Chain

For `Bab el-Mandeb risk propagation identifies countries for immediate review`, the candidate evidence chain includes:

- Hazard fields from `maritime_chokepoint_risk_indicators`: `likelihood_conflict`, `severity_conflict`.
- Chokepoint: `Bab el-Mandeb Strait`.
- Downstream countries from `maritime_chokepoint_systemic_risk_results`: top `trade_at_risk_v` rows include CHN, IND, and USA.
- Risk metrics: `trade_at_risk_v`, `trade_impacted`.
- Action: assign analyst review to top exposed countries and request live event enrichment.

This is deliberately not a ranking-only demo. The candidate can explain why an upstream Red Sea / Bab el-Mandeb risk signal changes downstream country review priority.

## Safety Boundary

- `canonical_writes=disabled`
- `auto_approve_findings=false`
- `write_scope=draft_only`
- Candidate findings stay draft until a human approves them through the existing review gate.
- The playbook does not approve ontology artifacts and does not write canonical graph state.

## Verification

Validated on local `http://127.0.0.1:8772`:

- Playbook run returned 4 hypotheses and 3 candidate findings.
- Every candidate has `evidence_chain` length >= 5.
- Every candidate includes a `recommended_action` evidence step.
- The pruned volume-only hypothesis has a `pruned_reason`.
- Safety profile keeps canonical writes disabled and auto approval disabled.
