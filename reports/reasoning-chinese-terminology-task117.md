# Task 117 - Reasoning Chinese Terminology Implementation

Time: 2026-05-13 16:02 CST
Base commit: `c0221b9` (`Unify reasoning question evidence workflow`)
Status: implementation complete, awaiting task #118 validation

## Scope

Task #117 aligns user-visible Chinese terminology across the reasoning workflow. The product term "推理闭环" is removed from the UI layer and replaced with "推理过程". This is a copy/i18n pass only; reasoning execution, review actions, canonical write boundaries, and safety gates were not changed.

## Files Changed

- `web/review_workbench/shell_app.js`
- `web/review_workbench/reasoning.html`
- `web/review_workbench/reasoning_app.js`
- `web/review_workbench/questions.html`
- `web/review_workbench/questions_app.js`
- `web/review_workbench/evidence_app.js`

## Terminology Updates

- `Reasoning Loop` / `Unified reasoning loop` UI wording changed to `Reasoning Process` / `Unified reasoning process`, translated as `推理过程` / `统一推理过程`.
- Chinese navigation `Findings` changed from `推理结果` to `推理结论`.
- `Open reasoning loop` links in Questions and Evidence changed to `Open reasoning process`, translated as `查看推理过程`.
- `Run reasoning` / `Rerun reasoning` translated as `执行推理` / `重新执行推理`.
- `Create scoped question` translated as `提交限定范围问题`.
- `Limit` translated as `数量上限`.
- `Review gate` / `Review Gate` translated as `审核关口`.
- Governance notes now explain:
  - `draft-only` as `草稿态（未批准）`
  - `approved-only` as `仅使用已批准图谱`
  - `canonical boundary` as `正式知识写入边界`
- Raw audit fields such as `source_ref`, artifact keys, payload/code blocks remain untranslated.

## Static Scan

Command:

```bash
rg -n "推理闭环|Reasoning Loop|Open reasoning loop|审核门禁|运行推理|重新运行推理|创建范围问题" web/review_workbench -S || true
```

Result: no hits.

Additional governance scan:

```bash
rg -n "canonical|draft-only|approved-only" web/review_workbench/reasoning.html web/review_workbench/reasoning_app.js web/review_workbench/questions.html web/review_workbench/questions_app.js web/review_workbench/evidence.html web/review_workbench/evidence_app.js web/review_workbench/shell_app.js -S
```

Result: remaining hits are implementation identifiers, English i18n source keys, raw `canonical_key` field access, or exact English strings that translate in Chinese mode. Browser-rendered Chinese main flow did not expose `canonical`, `draft-only`, or `approved-only` as untranslated user-facing Chinese terminology.

## Browser Smoke

Headless Chrome DevTools smoke set `localStorage["aletheia.portal.lang"]="zh"` and inspected rendered `document.body.innerText`.

- `/reasoning.html?tenant=default&task=reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-5-d1-n200-e200-q0957854216`
  - Forbidden terms: none
  - Found target terms: `推理过程`, `当前结论`, `证据链`, `审核关口`, `草稿态（未批准）`, `正式知识写入边界`
- `/questions.html?tenant=default`
  - Forbidden terms: none
  - Found target terms: `推理过程`, `证据链`, `查看推理过程`
- `/evidence.html?tenant=default`
  - Forbidden terms: none
  - Found target terms: `推理过程`, `证据链`, `审核关口`, `查看推理过程`, `仅使用已批准图谱`

Forbidden terms checked:

- `推理闭环`
- `Reasoning Loop`
- `Open reasoning loop`
- `审核门禁`
- `运行推理`
- `重新运行推理`
- `创建范围问题`

## Validation Commands

```bash
node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/questions_app.js && node --check web/review_workbench/evidence_app.js && node --check web/review_workbench/shell_app.js && node --check web/review_workbench/graph_app.js && node --check web/review_workbench/findings_app.js && git diff --check
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Result: all passed.

## Handoff

Task #117 is ready for task #118 browser validation. Do not commit or push until task #118 passes and product acceptance is granted.

## Second Pass After Task #118 FAIL

Time: 2026-05-13 17:08 CST

Task #118 initially failed on user-visible mixed labels in three areas. The second pass fixed only those UI labels:

- `/reasoning.html` current answer area:
  - `关键依据 / Key basis` -> `关键依据`
  - `下一步 / Next step` -> `下一步`
  - `Governance status` -> `治理状态`
  - `After review` -> `审核后存放`
  - `confidence 0.xx` -> `置信度 0.xx`
  - status pills translate `approved/draft` while retaining version suffix `vN`
- `/questions.html` form and history cards:
  - select options translate to `全局租户 / 选定实体 / 选定图谱节点 / 已有推理结论`
  - `Graph node` scope labels render as `图谱节点`
  - status pills and latest run render with Chinese status text
  - field labels render as `推理范围 / 来源 / 深度 / 数量上限`
- `/findings.html` main detail:
  - `Explainable conclusion` -> `可解释结论`
  - `confidence 0.xx` -> `置信度 0.xx`
  - status pills translate `draft/approved` while retaining version suffix `vN`

Raw payload, `source_ref`, artifact keys, `canonical_key`, `draft_only`, and ontology link keys remain untranslated when shown as technical/audit values.

### Second Pass DOM Smoke

Rendered Chinese DOM was checked with Headless Chrome DevTools on:

- `/reasoning.html?tenant=default&task=reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-5-d1-n200-e200-q0957854216`
- `/questions.html?tenant=default`
- `/findings.html?tenant=default`
- `/evidence.html?tenant=default`

Forbidden rendered terms checked:

- `推理闭环`
- `Reasoning Loop`
- `Open reasoning loop`
- `审核门禁`
- `运行推理`
- `重新运行推理`
- `创建范围问题`
- `Key basis`
- `Next step`
- `Governance status`
- `After review`
- `confidence`
- `Explainable conclusion`
- `Global tenant`
- `Selected entity`
- `Selected graph node`
- `Existing finding`
- `DEPTH / LIMIT`

Result: no forbidden terms in rendered Chinese DOM.

Target terms found:

- Reasoning: `推理过程`, `当前结论`, `证据链`, `审核关口`, `草稿态（未批准）`, `正式知识写入边界`, `关键依据`, `下一步`, `治理状态`, `审核后存放`, `置信度`
- Questions: `推理过程`, `证据链`, `查看推理过程`, `全局租户`, `选定实体`, `选定图谱节点`, `已有推理结论`, `深度 / 数量上限`
- Findings: `证据链`, `审核关口`, `仅使用已批准图谱`, `置信度`, `可解释结论`
- Evidence: `推理过程`, `证据链`, `审核关口`, `查看推理过程`, `仅使用已批准图谱`
