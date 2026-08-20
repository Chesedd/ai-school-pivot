# Checking Engine Phase 4.10 — vertical acceptance

Phase 4.9 is accepted at merge `cd0dbbd`. Phase 4.10 adds a transport-neutral,
provider-neutral technical acceptance boundary; it does not change the database
schema or any Phase 4.9 migration.

## Versioned boundary and corpus

The exact versions are `checking_golden_dataset_v1`,
`checking_acceptance_report_v1`, and `checking_acceptance_thresholds_v1`.
The stable synthetic corpus contains exactly 60 independently identified cases:
8 exact-text, 12 choice (single, multiple, OR and weighted), 10 numeric,
10 structured-expression, 12 LLM-rubric candidate-output, and 8 boundary cases.
Its SHA-256 contract fingerprint is
`47782edb9be6bd6bebb31bd553014c74b1d6c146a1bce129ba986618d3561003`.
It is technical synthetic test data—not teacher-approved, human-approved,
production-quality, or evidence of real-provider model quality.

The deterministic gates are checker, outcome, reason and exact-score agreement
`1.0000`, required-review recall `1.0000`, unsafe automatic-score rate `0.0000`,
and zero privacy violations, missing results, or unexpected results. Reports also
separate structured-output validity, rubric/checker agreement, Decimal score MAE,
provider failure rate, and latency/token/cost aggregates. Reports expose only
bounded case IDs and technical aggregates/fingerprints.

## Boundaries and status

External systems may explicitly opt in, execute any provider outside this
boundary, strictly validate results as `ObservedCheckingResultV1`, and evaluate
those observations. No provider, URL, credential, or concrete model is selected
automatically. Fake-provider tests prove composition and safety only.

The PostgreSQL vertical boundary uses production repositories/persistence at
Alembic `20260820_01`; it is behavioral acceptance only when a disposable
`TEST_DATABASE_URL` ending in `_test` is supplied. Raw answers, solutions, rubric
prose, provider output and person/assignment identities are excluded from reports
and privacy-safe errors.

There are no final grades, Teacher Review, frontend/public API, workers/queues, or
AI Content Authoring Phase 4A behavior. Overall Checking Phase 4.10 remains
**pending** until the configured real-provider quality gate is genuinely executed;
a missing provider configuration is not a deterministic implementation failure.
