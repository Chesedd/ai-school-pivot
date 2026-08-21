# Checking Engine Phase 4.10 — vertical acceptance

Phase 4.9 is accepted at merge `cd0dbbd`. Phase 4.10 adds a transport-neutral,
executable technical acceptance boundary; it does not change the database
schema or any Phase 4.9 migration.

## Versioned boundary and corpus

The exact versions are `checking_golden_dataset_v2`,
`checking_acceptance_report_v2`, and `checking_acceptance_thresholds_v1`.
The stable synthetic corpus contains exactly 60 independently identified cases:
8 exact-text, 12 choice (real single/multiple formats, two accepted OR alternatives, and complete per-option weighted policies), 10 numeric (including actual negative, zero, high-precision, tolerance-boundary, and invalid-methodology inputs),
10 structurally distinct structured-expression inputs, 12 LLM-rubric candidate-output behaviors, and 8 boundary cases with their natural checker formats.
Its SHA-256 contract fingerprint is
`35a13a0e35d665676cf9bffcbc13d54922f3dfc9f429f911e64507f2aab2527f`.
It is technical synthetic test data—not teacher-approved, human-approved,
production-quality, or evidence of real-provider model quality.

The deterministic gates are checker, outcome, reason and exact-score agreement
`1.0000`, required-review recall `1.0000`, unsafe automatic-score rate `0.0000`,
and zero privacy violations, missing results, or unexpected results. Reports also
separate structured-output validity, rubric/checker agreement, Decimal score MAE,
provider failure rate, and latency/token/cost aggregates. Reports expose only
bounded case IDs and technical aggregates/fingerprints.

## Boundaries and status

Every frozen case executes normalization, routing, its production checker, Phase 4.9 result preparation and the confidence gate. LLM cases use the real `LLMRubricChecker` and `ProviderExecutionService`, with a fake injected only at the provider port. Fake-provider success proves composition and safety only; it is not real-provider quality acceptance.

The PostgreSQL vertical boundary uses production repositories/persistence at
Alembic `20260820_01`; it is behavioral acceptance only when a disposable
`TEST_DATABASE_URL` ending in `_test` is supplied. Raw answers, solutions, rubric
prose, provider output and person/assignment identities are excluded from reports
and privacy-safe errors.

There are no final grades, Teacher Review, frontend/public API, workers/queues, or
AI Content Authoring Phase 4A behavior. Phase 4.10 application/deterministic technical acceptance is implemented. PostgreSQL technical acceptance remains pending until the real local 40-test continuation gate passes with no failures or skips. Real-provider quality evaluation remains a separate pending, explicitly configured gate.
