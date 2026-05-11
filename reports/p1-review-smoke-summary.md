# P1 Review Smoke Summary

Graph space: `aletheia_p1_review_smoke`

## Approved Consumed

- link:category:1:n:product
- link:customer:1:n:order
- link:order:n:m:product
- object:category
- object:customer
- object:order
- object:product

Required keys all approved: `True`

## Excluded Non-Approved

- link:employee:1:n:employee status=rejected version=2
- link:employee:1:n:order status=draft version=1
- object:employee status=draft version=1

## Audit Trace

- object:category: approved by Itachi (draft -> approved, v1 -> v2)
- object:customer: approved by Itachi (draft -> approved, v1 -> v2)
- object:order: approved by Itachi (draft -> approved, v1 -> v2)
- object:product: approved by Itachi (draft -> approved, v1 -> v2)
- link:category:1:n:product: approved by Itachi (draft -> approved, v1 -> v2)
- link:customer:1:n:order: approved by Itachi (draft -> approved, v1 -> v2)
- link:order:n:m:product: approved by Itachi (draft -> approved, v1 -> v2)
- link:employee:1:n:employee: rejected by Itachi (draft -> rejected, v1 -> v2)

## Ingestion Smoke

- processed: Product, Category, Customer, Order, PLACED_ORDER, CONTAINS_PRODUCT, HAS_PRODUCT
- failures on final fresh-space rerun: 0
- log: `reports/p1-review-smoke-ingestion-fresh-space-rerun.log`

## Notes

- Default-space attempt failed from existing Nebula schema drift, not review-gate logic.
- Fresh graph space first run hit Nebula schema propagation delay; immediate rerun succeeded.
