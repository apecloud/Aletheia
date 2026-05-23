# Task #169 Bilingual Reasoning Result Validation

Status: PASS after #168 rework
Validator: @Saskue
Service: <http://127.0.0.1:8772>
Implementation under test: commit `679229a` (`Localize default reasoning answers`)

## Scope

Validated bilingual display for:

- `default` tenant current answer / finding result.
- `creditcardfraud` tenant Autopilot candidate findings and Approved Finding Registry surface.
- `maritime-risk` tenant Autopilot graph-reasoning findings and multi-hop evidence chain.
- Negative gates: do not translate canonical keys, tenant ids, source refs, table names, field names, metric names, or raw evidence payloads; language switch must not change finding status or draft/canonical boundaries.

## Rechecked Former Fail

The former FAIL URL now passes:

`http://127.0.0.1:8772/?screen=reasoning&tenant=default&lang=zh&task=reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-4-d1-n60-e60-qdbe7a0a648-rtask151a1779419584`

DOM evidence captured at `/tmp/task169_recheck_default_zh.txt`:

```text
CURRENT ANSWER
Conclusion
approved
Margaret Peacock（Sales Representative，向 Andrew Fuller 汇报）是高活跃 Employee：处理 156 单，在 9 名同类中排名 #1，约为平均值 92.2 的 1.7 倍。收入贡献为 232,890.85（占总计 1,265,793.04 的 18.4%）...
建议动作
将该草稿作为审核提示使用；在通过审核关口前，不要把它当作已批准发现。
反向证据 / 边界
结论仅基于已批准图谱和受控聚合；不包含绩效目标、利用率、利润率或满意度数据。
```

The old English body strings are absent:

- `Margaret Peacock (Sales Representative), reporting to Andrew Fuller is a high-activity Employee...`
- `Use this draft as a reviewer prompt...`
- `Conclusions are based solely on the approved graph...`

Raw identifiers remain intact: `Employee:4`, `Margaret Peacock`, canonical `finding:graph-scope...`, and source/canonical references are not translated.

## Regression Checks

### creditcardfraud

Chinese mode still renders the Autopilot/finding result surface in Chinese while preserving raw identifiers:

- `发现收件箱` / `候选发现草稿`
- `非面对面交易具有更高欺诈风险`
- `聚合指标 · credit_card_transactions_safe · baseline_fraud_rate = 1.58%`
- Raw `cardCVV / enteredCVV` absent from API and DOM.

### maritime-risk

Chinese mode still renders the graph reasoning result surface in Chinese while preserving evidence identifiers:

- `Bab el-Mandeb 风险传播识别需立即复核的国家`
- `图路径为：Bab el-Mandeb 风险因子 -> 咽喉点 -> 依赖国家 -> 系统性风险指标 -> 分析师行动`
- `风险因子 · maritime_chokepoint_risk_indicators · likelihood_conflict = ...`
- `风险指标 · maritime_chokepoint_systemic_risk_results · trade_at_risk_v = ...`

## Negative Gates

Passed:

- `creditcardfraud` active Registry statuses remained `approved` only.
- `maritime-risk` Autopilot candidates remained `draft`.
- `maritime-risk` session still has `canonical_writes=disabled`.
- Raw `cardCVV / enteredCVV` absent from checked API payloads and DOM.
- Source refs / table names / metrics such as `credit_card_transactions_safe`, `maritime_chokepoint_risk_indicators`, `trade_at_risk_v`, `likelihood_conflict` remain untranslated.
- Language display changes did not mutate the API payload fingerprints used for Registry/session boundary checks.

## Evidence Files

- Browser summary: `reports/bilingual-reasoning-results-validation-task169-saskue.browser.json`
- API boundary summary: `reports/bilingual-reasoning-results-validation-task169-saskue.api.json`
- DOM captures:
  - `/tmp/task169_recheck_default_zh.txt`
  - `/tmp/task169_recheck_fraud_zh.txt`
  - `/tmp/task169_recheck_maritime_zh.txt`

## Verification Commands

Passed:

```bash
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py
node --check web/review_workbench/api.js
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```
