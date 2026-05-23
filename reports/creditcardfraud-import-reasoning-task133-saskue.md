# Credit Card Fraud Import and Reasoning - task #133

Result: DONE

## Import

- Source dataset: `/Users/slc/code/Aletheia/datasets/creditcardfraud`
- Source format: JSONL, one transaction per line
- Source size: 786,363 rows, about 582 MB
- Target source DB: `aletheia_test_data`
- Imported table: `credit_card_transactions`
- Safe reviewer view: `credit_card_transactions_safe`
- Imported rows: 786,363
- Columns in raw table: 39, including derived analysis fields
- Indexes: 8
- Table size after import: about 180.3 MB

I first attempted to create a separate MySQL source database named `aletheia_creditcardfraud`, but the configured MySQL user does not have `CREATE DATABASE` permission. To complete the import without waiting, I imported the dataset into the existing Aletheia source DB as a distinct table. Metadata/artifacts/reasoning output are tenant-scoped under `creditcardfraud`.

Sensitive-field handling:
- Raw fields `cardCVV`, `enteredCVV`, `cardLast4Digits`, and `accountNumber` are present in the imported source table because the task requested dataset import.
- A safe view `credit_card_transactions_safe` excludes raw `cardCVV` and `enteredCVV`.
- Reasoning uses derived `cvvMatch` and does not need to expose raw CVV values.

Derived fields added during import:
- `cvvMatch`
- `countryMismatch`
- `availableRatio`
- `balanceRatio`
- `amountToLimitRatio`
- `transactionHour`
- `transactionDate`
- `transactionMonth`
- `riskSignalCount`

## Aletheia Registration

- Tenant metadata inserted: `creditcardfraud / creditcardfraud / Credit Card Fraud Dataset / aletheia_creditcardfraud`
- Ontology artifacts created under tenant `creditcardfraud`: 7 draft artifacts
- Objects:
  - `object:credit_card_transaction`
  - `object:account`
  - `object:card`
  - `object:merchant`
- Links:
  - `link:account:1:n:credit_card_transaction`
  - `link:card:1:n:credit_card_transaction`
  - `link:merchant:1:n:credit_card_transaction`
- Reasoning task created:
  - `reasoning:creditcardfraud:dataset-risk-profile:v1`
- Draft finding created:
  - `finding:creditcardfraud:dataset-risk-profile:v1`

These artifacts are intentionally `draft`, not canonical-approved. They are ready for review but do not automatically enter the canonical graph.

## Data Profile

- Transactions: 786,363
- Accounts: 5,000
- Merchants: 2,490
- Merchant categories: 19
- Fraud labels: 12,417
- Base fraud rate: 1.58%
- Average transaction amount: 136.99
- Average fraud amount: 225.22
- Average non-fraud amount: 135.57

Missing / empty fields:
- `echoBuffer`: 786,363 empty
- `merchantCity`: 786,363 empty
- `posOnPremises`: 786,363 empty
- `acqCountry`: 4,562 missing
- `posEntryMode`: 4,054 missing
- `transactionType`: 698 missing
- `merchantCountryCode`: 724 missing

## Reasoning Analysis

Main conclusion:

The dataset has a low base fraud rate, but fraud is not random. It concentrates in online/card-not-present contexts, missing POS entry mode, high-utilization account states, and repeated same-account/merchant/amount patterns.

Key facts:
- Card-not-present transactions: 433,495 rows, fraud rate 2.07% versus 1.58% base.
- CVV mismatch transactions: 7,015 rows, fraud rate 2.89%.
- Missing POS entry mode: 4,054 rows, fraud rate 6.64%.
- Fraud amount is materially higher than legitimate amount on average: 225.22 vs 135.57.
- Highest category fraud rates:
  - `airline`: 3.46%
  - `rideshare`: 2.49%
  - `online_retail`: 2.44%
  - `online_gifts`: 2.42%
- Duplicate same-account/merchant/amount/day clusters: 12,761 clusters.

High-risk examples:
- `transaction_id=571924`, account `693329001`, `apple.com`, `online_retail`, amount 818.69, card-not-present, balance ratio 0.8124, amount-to-limit ratio 0.3275, risk signals 4, fraud label true.
- `transaction_id=149886`, account `204494014`, `discount.com`, `online_retail`, amount 770.80, card-not-present, balance ratio 0.9421, amount-to-limit ratio 0.3083, risk signals 4, fraud label true.
- `transaction_id=391987`, account `849733512`, `Lyft`, `rideshare`, amount 540.74, card-not-present, CVV mismatch, balance ratio 1.0969, amount-to-limit ratio 2.1630, risk signals 4, fraud label true.

High-risk accounts by fraud count:
- `380680241`: 32,850 transactions, 783 fraud labels, 2.38% fraud rate.
- `782081187`: 2,435 transactions, 307 fraud labels, 12.61% fraud rate.
- `246251253`: 10,172 transactions, 278 fraud labels, 2.73% fraud rate.
- `700725639`: 3,313 transactions, 272 fraud labels, 8.21% fraud rate.
- `472288969`: 1,790 transactions, 266 fraud labels, 14.86% fraud rate.

Evidence boundary:
- This is deterministic SQL/profile reasoning over observed labels and derived fields, not a trained fraud model.
- Raw CVV values are sensitive and should not be surfaced in reviewer-facing UI; use `cvvMatch`.
- Country mismatch is rare in this import and has no observed fraud, so it is not a strong standalone signal here.
- A production fraud model should use temporal train/test split and leakage checks before using these features operationally.

## Verification Commands

- JSONL size/count: `wc -l datasets/creditcardfraud`
- Import verification: `SELECT COUNT(*) FROM credit_card_transactions`
- Schema verification: `information_schema.columns` for `credit_card_transactions`
- Ontology verification: `.venv/bin/python query_artifacts.py list --tenant creditcardfraud`
- Reasoning verification: direct SQL query against `aletheia_reasoning_tasks` and `aletheia_reasoning_findings`

JSON report:
- `/Users/slc/code/Aletheia/reports/creditcardfraud-import-reasoning-task133-saskue.json`
