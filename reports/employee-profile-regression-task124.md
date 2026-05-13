# Employee Profile Regression Fix - task #124

## Scope

Fix the real user path where a historical `Employee:5` reasoning task still rendered the old count-summary finding:

- `Employee:5 work snapshot: Steven Buchanan has 42 approved order relationships`
- `loaded in the current evidence scope`

The fix must cover historical task detail, left history-card click paths, new Question Center tasks, and Graph handoff tasks.

## Changes

- Backend `review_workbench.py`
  - Expanded legacy scoped finding detection to include `work snapshot`, `approved order relationships`, and `loaded in the current evidence scope`.
  - When a scoped Employee finding lacks `structured_answer`, read-time normalization now builds the deterministic Employee profile from approved graph scope plus controlled Northwind aggregate.
  - Normalized legacy findings expose `structured_answer`, structured section fields, profile title/conclusion, and `controlled_aggregate` supporting evidence without writing canonical graph data.

- Frontend `web/review_workbench/reasoning_app.js`
  - Removed the Current Answer fallback that generated `work snapshot` / `approved order relationships` from graph relation counts.
  - Legacy scoped findings without a structured answer now show an upgrade message instead of a count-summary conclusion.
  - Task history labels normalize old Employee work-snapshot questions to `Employee:5 员工画像分析`.
  - Trace Output uses the structured answer when available, so the visible page no longer repeats stale legacy output as the primary explanation.

## Verification

User screenshot path:

`/reasoning.html?tenant=default&task=reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-5-d1-n200-e200-q815ceadee5`

API verification:

- `findings=3`
- first finding title: `Steven Buchanan 员工画像：低订单负载、客户覆盖较分散`
- `structured_answer=true`
- evidence kinds: `question_scope`, `controlled_aggregate`
- forbidden strings absent from title/conclusion:
  - `work snapshot`
  - `approved order relationships`
  - `loaded in the current evidence scope`

Browser DOM smoke:

- Required terms present:
  - `画像判断`
  - `关键事实`
  - `业务含义`
  - `证据边界`
  - `下一步验证`
  - `低订单负载`
  - `客户覆盖较分散`
  - `订单量排名 9/9`
- Forbidden terms absent from rendered DOM:
  - `work snapshot`
  - `approved order relationships`
  - `loaded in the current evidence scope`

Additional entry checks:

- New Question Center Employee:5 task produced structured profile and no forbidden title/conclusion terms.
- Graph handoff Employee:5 task produced structured profile and no forbidden title/conclusion terms.

Validation commands:

```bash
node --check web/review_workbench/reasoning_app.js
python3 -m py_compile review_workbench.py
git diff --check
```

## Boundary

The fix does not approve findings, write canonical graph data, or invent unsupported performance/satisfaction/profit claims. Profile generation still uses approved graph context and controlled aggregate evidence.
