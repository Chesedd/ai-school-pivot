# PR12: Manual / Offline Paper Review

Implemented locally on `codex/manual-paper-review`, based on accepted local main
`662899862259b8021bcee26124693dcaf6a55c67`. No push or merge.

## Result and provenance

Manual checking enters the existing PR8 review, PR9 publication, PR10 batch, and
PR11 remediation pipeline. It creates no CheckRun, ModelRun, PaperAIRevision,
CheckResult, CheckFinding, prompt, or provider attempt for teacher grading.

Both mutable reviews and immutable revisions carry `source_kind`:

| Source | check_run_id | ai_revision_id | Initial revision |
| --- | --- | --- | --- |
| ai | required | required | ai_seed, number 0 |
| manual | null | null | manual_seed, number 0 |

Database checks enforce these pairs; AI composite foreign keys remain in place.
A new composite revision foreign key enforces review identity, paper identity,
and source kind even when AI IDs are null. A partial unique index permits only
one manual review per paper. The review provenance trigger now includes source
kind, and the existing revision UPDATE/DELETE rejection remains unchanged.

The immutable manual seed includes the actor, timestamp, paper/review identity,
source kind, frozen grading-policy revision/fingerprint, and every frozen item
with its maximum. Draft items use `status=null`, `accepted_score=null`, and
`source_check_result_id=null`. Initial findings are empty and summary is empty.
Teacher findings have stable UUIDs and null source finding IDs; page membership
and normalized regions use the existing contracts.

## Frozen policy and validation

PR6 already freezes `ScanGradingPolicyRevision` independently of CheckRun
creation. Manual start reuses the current frozen policy for the paper's batch
and assigned variant. Later edits, approvals, and publication resolve the exact
immutable revision/fingerprint recorded by manual_seed. Item IDs/maxima never
come from mutable live assessment points. Missing frozen policies are conflicts.
No new policy representation or grading semantics were introduced.

The shared PR8 edit/CAS/revision/approval implementation handles both sources.
Approval requires exact item coverage and a nonempty teacher summary. Null,
unreadable, and insufficient-evidence statuses remain unresolved. Correct means
full maximum, partial means strictly between zero and maximum, and incorrect or
explicit not_attempted means zero. AcceptedItemScore, calculate_total,
calculate_percentage, calculate_grade, and GradeOverrideCommand remain the
calculation boundaries. Teacher override preserves calculated grade.

## Start eligibility, locks, and batch lifecycle

Manual start uses the same teacher/class authorization as other paper actions.
No CheckRun, failed_retryable, or failed_terminal permits start. Any pending or
running paper run produces `paper_manual_review_check_in_progress`. Historical
successful outcomes or any existing AI review produce
`paper_manual_review_ai_result_available`. Unknown outcomes are rejected.
Repeated starts return the same manual review, including after batch completion.
A brand-new lineage cannot start in a completed batch.

Both manual start and AI creation serialize in batch -> paper order. The manual
unique index backs up application idempotence. New AI creations after takeover
raise `paper_manual_review_active`; identical historical request replay remains
read-only. Retrying a historical failed paper run to pending also checks the same
locks and manual authority, so provider execution cannot restart behind review.
Historical failed runs and AI evidence are otherwise left intact.

Manual start uses PR10 begin_attempt: ready_for_checking -> checking, checking
stays checking, and checking_completed reopens to checking. Audit uses existing
ScanCheckingEvent with `paper.manual_review_started` and bounded review ID.
Idempotent starts do not create duplicate seed revisions or batch/audit events.

Progress adds manual_reviewing and manual_approved counters. Manual presence
wins over historical failed AI statuses. Manual reviewing is unsettled; approved
is settled. Shared reconciliation after approval can move checking to
checking_completed. Publication and batch finalization remain explicit.
Existing review correction remains permitted in completed batches.

## Publication, students, remediation

The existing PR9 publication service accepts the approved manual revision using
the same immutable result snapshot and active-pointer replacement semantics.
Editing or reapproval retains the old active publication until explicit publish.
Student projections retain the allowlist and hide source kind, internal AI IDs,
provider failures, and takeover details. Existing page ownership/publication
rules and source-PDF denial remain in place.

PR11 paper eligibility continues to use active published accepted scores below
maxima. Manual findings do not invent skill or typical-error taxonomy. Existing
assessment-item candidate fallback and remediation execution are reused.
Paper plans can have null source_check_run_id; digital plans still require it
through the source XOR constraint. Assignment verifies nullable paper provenance.
Starting, editing, approving, or publishing does not create remediation plans.

## Migration

`20261011_03`, down_revision `20261011_02`:

- Adds source_kind with deterministic ai default for historical reviews/revisions.
- Makes review/revision AI provenance IDs nullable with source checks.
- Adds manual uniqueness and null-safe revision source identity foreign key.
- Allows manual_seed and protects source immutability.
- Allows nullable paper remediation CheckRun while requiring digital provenance.
- Extends the bounded scan event vocabulary.

Following PR11 precedent, downgrade refuses before DDL while manual reviews or
null-run remediation plans exist. It neither deletes them nor fabricates AI IDs.
Representable AI rows and revisions survive the roundtrip. This is a data-preserving,
intentional limitation on downgrade of databases containing manual results.

## Acceptance

Real PostgreSQL is exercised using `backend/tests/pr12_acceptance.py` and two
disposable databases ending in `_test`. Test scratch paths stay in the workspace.
Ordinary data tests roll back; concurrency tests clone the disposable database,
commit seed data, prove blocking with pg_blocking_pids, and drop their clone.
Migration tests have a separate dedicated database. No production DB is targeted.

The final full unit run has **1,174 passed, 1 failed, 0 skipped**. The remaining
failure is `test_checking_handoff_v1_fixture_schema_and_invariants`: the existing
fixture expects NFC `Café`, while the current normalizer returns decomposed
`Café`. An earlier full run also reproduced
`test_privacy_admin_rules_and_cas` (stale timestamp was not rejected), yielding
1,170 passed / 2 failed before three additional PR12 unit cases were added.
That timestamp failure did not reproduce in the final full unit run. Neither
unrelated production behavior nor its fixtures were modified.

Migration acceptance: **2 passed**, on real PostgreSQL. It preserves an AI review
and its immutable seed through 02 -> 03 -> 02 -> 03, verifies both AI source
backfills, creates a fresh head, explicitly verifies `20261011_03`, and refuses
lossy downgrade with committed manual data while leaving the revision/data intact.

The ten-paper test uses nine genuine PR7 executions/reviews/publications and one
real terminal provider-response failure. It verifies the 9/10 publication gap,
manual reopening, deterministic approval, explicit tenth publication and explicit
completion, retaining the original failed CheckRun. A separate pure manual test
verifies zero original CheckRuns, ModelRuns, PaperAIRevisions, CheckResults,
CheckFindings, and checker/provider execution events.

The shared PR11 assignment adapter's unconditional CheckRun dereference was
removed for manual paper provenance. Re-activation of a historical retryable run
also now checks manual authority; new-run blocking alone would not cover that
boundary. No unrelated production fixes were made.

The new multi-item test originally used unchecked model_copy to build a policy
and could bypass the policy's UUID ordering. It was corrected to validate the
fixture through the existing policy contract; grading policy semantics are intact.

Final gate counts are recorded below.

## Changed files

Production and migration:

- `backend/app/application/paper_batch_orchestration.py`
- `backend/app/application/paper_checking_review.py`
- `backend/app/application/paper_result_publication.py`
- `backend/app/infrastructure/checking_repository.py`
- `backend/app/infrastructure/paper_batch_orchestration_repository.py`
- `backend/app/infrastructure/paper_checking_review_models.py`
- `backend/app/infrastructure/paper_checking_review_repository.py`
- `backend/app/infrastructure/paper_remediation_repository.py`
- `backend/app/infrastructure/paper_result_publication_repository.py`
- `backend/app/infrastructure/remediation_models.py`
- `backend/app/infrastructure/remediation_repository.py`
- `backend/app/infrastructure/scan_checking_models.py`
- `backend/app/presentation/scan_intake_routes.py`
- `backend/alembic/versions/20261011_03_manual_paper_review.py`

Tests, acceptance runner, and report:

- `backend/tests/unit/test_paper_batch_orchestration.py`
- `backend/tests/unit/test_manual_paper_review.py`
- `backend/tests/integration/test_manual_paper_review_database.py`
- `backend/tests/integration/test_manual_paper_review_migration.py`
- `backend/tests/integration/test_paper_batch_orchestration_migration.py`
- `backend/tests/pr12_acceptance.py`
- `docs/pr12-manual-paper-review.md`

The existing PR10 migration expectation now includes head `20261011_03`.
The batch unit adjustment includes both new counters in the existing aggregate
assertion. No unrelated fixtures or production code were changed.

## Test commands and suite mapping

Commands are run from the repository root through the dedicated DB runner;
pytest paths below are relative to `backend`:

```text
.venv/Scripts/python.exe backend/tests/pr12_acceptance.py matrix
.venv/Scripts/python.exe backend/tests/pr12_acceptance.py pytest -q tests/integration/test_manual_paper_review_database.py
.venv/Scripts/python.exe backend/tests/pr12_acceptance.py pytest -q tests/unit
```

Migration tests use the same runner with
`PR12_TEST_DB=manual_paper_review_pr12_migration_test`, selecting
`tests/integration/test_manual_paper_review_migration.py`. Cross-order gates
select these actual filenames in the indicated order:

| PR | Suite |
| --- | --- |
| PR12 | `tests/integration/test_manual_paper_review_database.py` |
| PR8 | `tests/integration/test_paper_checking_review_database.py` |
| PR9 | `tests/integration/test_paper_result_publication_database.py` |
| PR10 | `tests/integration/test_paper_batch_orchestration_database.py` |
| PR11 | `tests/integration/test_paper_remediation_database.py` |
| PR7 | `tests/integration/test_paper_checking_execution_database.py` |

Digital regression selects `test_remediation_database.py`,
`test_remediation_execution_database.py`, `test_remediation_checking_database.py`,
and `test_remediation_results_database.py` under `tests/integration`, together
with `test_remediation_checking.py`, `test_remediation_execution.py`,
`test_remediation_results.py`, and `test_remediation_presentation_security.py`
under `tests/unit`.

The broad integration gate uses the requested exact selector:
`pytest -q tests/integration -k "checking or scan or publication or batch or remediation or manual"`.


The initial broad selector found two new assertions counting unrelated committed
rows from earlier suites. They now scope zero-evidence assertions to each original
paper, including CheckResult/CheckFinding/CheckerEvent joins through its CheckRun.
The same run exposed the existing PR10 migration test's hardcoded head set; its
expectation now includes 03 while still checking both sides of the downgrade.
These were test isolation/version expectations, not production failures.

One reverse PR10 gate emitted a Windows WMI exception (`0x8007000e`) after
reporting 101 passed, writing zero failures/errors to JUnit, and returning exit 0.
Its original diagnostic is archived, and a repeat gate checks the final fixtures.

## Final gate results

| Gate | Passed | Skipped | Failed/errors |
| --- | ---: | ---: | ---: |
| Dedicated PR12 PostgreSQL | 58 | 0 | 0 |
| PR12 -> PR8 | 106 | 0 | 0 |
| PR8 -> PR12 | 106 | 0 | 0 |
| PR12 -> PR9 | 97 | 0 | 0 |
| PR9 -> PR12 | 97 | 0 | 0 |
| PR12 -> PR10 | 101 | 0 | 0 |
| PR10 -> PR12 (repeated after final test scoping) | 101 | 0 | 0 |
| PR12 -> PR11 | 86 | 0 | 0 |
| PR11 -> PR12 | 86 | 0 | 0 |
| PR7 paper execution | 41 | 0 | 0 |
| Existing digital remediation integration/unit gate | 51 | 0 | 0 |
| PR12 dedicated migration | 2 | 0 | 0 |
| PR10 migration at new head | 1 | 0 | 0 |
| Full unit suite | 1174 | 0 | 1 |
| Relevant integration selector | 439 | 4 | 0 |

All PostgreSQL counts above are actual executed tests, not environment skips.
The repeated reverse PR10 gate again returned exit 0 and 101 passed with zero
JUnit failures/errors, followed by the same Windows WMI diagnostic. This local
runtime limitation remains visible; neither pytest results nor application
behavior were patched to hide it.

Static validation after the final test changes: Ruff passed for all changed/new
Python files; compileall passed for `app`, `alembic/versions`, and `tests`;
`git diff --check` passed; `alembic heads` reports only `20261011_03`.

The final relevant integration run has **439 passed, 4 skipped, 255 deselected,
0 failed, 0 errors**. The four skips are the two PR12 migration cases (both
passed in their separate dedicated migration gate) and two PR11 migration cases
requiring the existing dedicated PR11 migration environment. No ordinary PR12
data test was skipped. Final selector process exit code: 0.

Local JUnit XML and console logs, including the initial failed selector and WMI
diagnostics, are preserved under `local-backups/pr12-acceptance-20261011/`.
Generated scratch files are kept under that same archive rather than included
in the commit. Existing user files and backups remain intact.
