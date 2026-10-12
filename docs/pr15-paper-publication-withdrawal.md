# PR15: paper result publication withdrawal

Branch: `codex/paper-publication-withdrawal`.
Accepted local parent: `5355eb39d2918158b121e5ff332b396e29d5d1ec`.
No push or merge. Backend scope only.

## Persistence and command semantics

Migration `20261012_02`, down revision `20261012_01`, adds
`paper_result_publication_withdrawals`. Its UUID primary key identifies one
immutable withdrawal with a publication FK, user FK, reason (maximum 500
characters), and server-generated timezone-aware `created_at`.
`UNIQUE(publication_id)` permits at most one withdrawal per publication.
The reason check rejects blank or untrimmed ordinary whitespace. Application
validation strips whitespace before enforcing the length bound.

The withdrawal table uses the existing `checking_reject_change()` trigger to
reject updates and deletes. Publication rows keep their existing immutable
trigger and snapshot constraints. No academic, checking, review, grade, finding,
remediation, or batch records are deleted or rewritten by withdrawal.

The existing publication state row remains present. Its active publication FK
becomes nullable; its version continues to record the latest publication number.
Withdrawal clears only the active pointer, preserving that version. A new publish
increments the publication number and links to the preceding immutable row, even
if that row was withdrawn. There is no restore/unwithdraw operation.

Only the exact current publication can be newly withdrawn. Cross-paper identities
return `paper_publication_selection_mismatch` (404); a superseded, nonwithdrawn
publication returns `paper_publication_not_active` (409). The repository validates
the entire selected withdrawal before making writes. The caller owns transaction
commit/rollback; HTTP commands commit once and roll back bounded command errors.

An exact publication ID plus the same normalized reason returns the original
withdrawal, including its original actor/time. A different reason returns
`paper_publication_already_withdrawn` (409), without changing history. Replaying
old withdrawn IDs after newer publication B does not clear B's pointer.

PostgreSQL guards require an active publication when inserting a withdrawal and
valid withdrawal provenance when clearing the pointer. A deferred constraint
trigger rejects commit if a withdrawn publication remains active. State guards
also reject reactivation of an already-withdrawn row. Update/delete of the
publication state itself remains guarded.

## API and authorization

All paths have the existing `/api/assessment-core` prefix:

| Command | Route | Payload |
| --- | --- | --- |
| Single withdrawal | `POST /paper-submissions/{paper_submission_id}/publications/{publication_id}/withdraw` | `{ "reason": "Correction" }` |
| Selected withdrawal | `POST /scan-batches/{batch_id}/publications/withdraw/selected` | Common reason and `entries` with explicit paper/publication IDs |
| Bulk withdrawal | `POST /scan-batches/{batch_id}/publications/withdraw/bulk` | Same bounded, explicit-target payload |

The selected/bulk maximum is the existing publication limit of 500 papers, with
one target per paper. Both aliases execute the same all-or-none transaction.
Every target must belong to the specified batch; every target is authorized
through the existing publication boundary.

The existing `PrincipalDep` (`ASSESSMENT_SCAN_CHECK_MANAGE`) capability boundary and `_authorized_batch` class
access are reused. An assigned class teacher can withdraw another teacher's
publication; no original-author requirement is added. Existing admin access is
preserved. Students receive 403. Teachers outside the class receive the existing
404 concealment response. No digital permissions change.

Teacher history includes `is_active`, `is_withdrawn`, `withdrawn_at`,
`withdrawn_by_user_id`, and `withdrawal_reason`, alongside the unchanged frozen
snapshot. History uses one joined SQL statement so pointer and withdrawal
metadata come from the same database snapshot. No provider/storage fields are
added to the withdrawal DTO.

After withdrawal, the student result and known page ID endpoints return the
existing `paper_result_not_found` (404). The student's result list excludes null
active pointers. Student DTOs contain no withdrawal reason, timestamp, history,
or badge. Authorized teacher page reads continue during grace; after physical
retention purge they return the existing `scan_page_content_purged` (410).

## Republishing and existing replay behavior

Same approved revision R1 can publish A, withdraw A, publish B, withdraw B, and
publish C. Each publication is a different immutable UUID and monotonically
numbered row. The earlier snapshots remain queryable. The same revision while
already active returns the current publication with `already_current`, as in PR9.
Ordinary newer-revision publication also continues to work. AI and manual review
sources share the lifecycle; grades are frozen independently without recalculation.

The authoritative local PR9 API has **no publish request key, creation key, or
durable command fingerprint**. Its payload identifies only an approved revision.
There was no revision-level unique constraint to remove. Accordingly no command
identity or publication uniqueness is weakened by PR15. An explicit publish
call after withdrawal is the new publication operation. The API cannot distinguish
an old unkeyed HTTP retry from a new explicit call with identical revision-only
input; durable old-key replay tests do not apply to this local architecture.
Withdrawal replay itself is durable through the exact publication ID and unique
withdrawal relation.

Before creating a new publication, all confirmed paper page memberships must
resolve to ready normalized render pages. The repository checks that no retention
attempt exists, verifies private PNG artifact provenance, reads actual bytes, and
checks PNG signature, length, and SHA-256 against the frozen metadata. Missing,
corrupt, claimed, or purged pages reject publication with
`paper_publication_pages_unavailable` (409). References alone are insufficient.
Existing active same-revision replay does not run this new-creation precondition.
No source recovery, rerender, provider call, or storage restoration is performed.

## Batch and remediation

Withdrawal does not change checking settlement or batch status. A
`checking_completed` batch cannot finalize while any paper lacks an active
publication; explicit republish restores eligibility. A `completed` batch remains
completed, including replay of the finalization command. PR10 already returned
completed before testing current publication completeness; this behavior is
preserved.

Paper remediation source validation now treats an existing state with a null
active pointer the same as no active publication. New creation and draft
assignment still require the exact active publication ID. Drafts from A remain
queryable but stale after withdrawal, and do not silently switch to B. New plans
from B retain B's exact provenance. Already-assigned A plans retain their status,
student visibility, execution/checking behavior, and A provenance. No automatic
cancellation or regeneration occurs.

## Retention and concurrency

`PaperRetentionSettings.withdrawal_page_grace_days` defaults to 30 and is bounded
to positive integers up to 3650. The environment variable is
`PAPER_WITHDRAWAL_PAGE_GRACE_DAYS`, documented in `.env.example` and wired through
Compose. Grace protection is derived in authoritative SQL from immutable
publication/withdrawal history, rather than a mutable page deadline.

A required render remains protected while a current publication is active, or
while **any** relevant withdrawal satisfies `created_at + grace > now`.
At the exact expiry boundary it is no longer protected by that withdrawal.
A later withdrawal extends protection. Ordinary PR14 completion/age/shared-input
eligibility rules still apply. Source PDF retention is independent of page grace.
Withdrawal does not run the sweeper or mutate the retention ledger.

The current policy is `paper_storage_retention.v2`. Existing v1 ledger identities
remain readable and immutable; retries preserve their original policy version.
New lifecycle entries use v2. PR14 stores policy version as a bounded string,
with no version allowlist constraint, so no policy schema adjustment is needed.

Publication and withdrawal lock batches in UUID order, then papers, then render
pages. Finalization's batch lock serializes it with visibility changes. Existing
remediation paper locks serialize active-source creation and draft assignment
with withdrawal. Retention's final claim takes the same page lock and rechecks
SQL eligibility. A committed claim fences later publication even while physical
deletion/finalization is pending.

The real PostgreSQL tests synchronize contenders with `pg_blocking_pids`, or
hold explicit page locks while invoking retention's `SKIP LOCKED` claim. They
do not infer correctness from timing sleeps. They cover both withdraw/publish
orderings, duplicate withdrawal, both withdraw/finalize orderings, both create
and assign orderings, student result/page reads across withdrawal commit,
withdrawal/retention's active-to-grace transition, and both publication/retention
orderings with real filesystem deletion. There is no committed protection gap.

## Migration and downgrade

Existing publications receive no backfill, and existing active pointers and
retention entries are unchanged. Fresh PostgreSQL upgrade and
`20261012_01 -> 20261012_02 -> 20261012_01 -> 20261012_02` roundtrip are tested.
The repository has one head: `20261012_02`.

Downgrade refuses **any null active pointer**, with a clear migration exception,
because PR9 cannot represent withdrawn visibility. It does not reactivate a row,
delete a publication, or restore storage bytes. If every pointer is nonnull,
downgrade restores the PR9 pointer guard and drops the withdrawal table: that
explicitly loses withdrawal audit records. Multiple publications of the same
revision were already representable in PR9 and are preserved. PostgreSQL tests
prove both refusal with withdrawn-only state and successful downgrade after
republish preserving both publication rows and current B. v2 ledger records are
not rewritten or dropped by this downgrade.

## Acceptance results

Validation uses disposable PostgreSQL databases ending in `_test`, physical
filesystem artifacts, and workspace-local pytest temporary directories. The
local `.test.env` contains empty URLs, so the validation runner reads local
connection configuration privately and selects dedicated test database names.
Credentials are not included in reports or logs.

The final full unit run: **1282 passed, 1 skipped, 3 failed, 4 setup errors**.
The initial full unit run before the clock fix reported **1283 passed, 1 skipped,
2 failed, 4 setup errors**. The two stable
failures are `test_checking_handoff_v1_fixture_schema_and_invariants` (Unicode
normalization) and `test_privacy_admin_rules_and_cas` (classroom-note privacy/CAS).
The four errors are Windows environment-variable length failures while pytest
sets `PYTEST_CURRENT_TEST` for huge input-artifact parameter IDs. All reproduce
on the accepted baseline, with no changes to unrelated code. The baseline full run
reported **1263 passed, 1 skipped, 3 failed, 4 setup errors**, also including the
same transient fresh-interpreter process failure in model-registry testing
(Windows process exit `3221227274`). Its focused baseline rerun passed.
Focused baseline confirmation: **9 passed,
2 failed**, reproducing the two stable unit failures. Focused PR9/PR14/PR15 units:
**72 passed, 1 skipped**, including all new command/grace tests.
The final isolated model-registry rerun reports **3 passed**, confirming that
the fresh-interpreter failure in the full unit run is environmental.

Dedicated PostgreSQL withdrawal acceptance: **59 passed, 0 skipped, 0 failed,
0 setup errors**. It covers actual PNG availability, deletion and rejected
post-purge republish; manual and AI publications; authorization/privacy;
idempotency; exact-ID bulk replay/rollback; and controlled real-connection races.
The final PR9 both-order reruns also execute all 59 withdrawal cases against the
final implementation, including the paper-checking timestamp correction.

| Cross-order pair | Existing module then withdrawal | Withdrawal then existing module |
| --- | ---: | ---: |
| PR9 publication | 98 passed | 98 passed |
| PR10 batch | 102 passed | 102 passed |
| PR11 remediation | 87 passed | 87 passed |
| PR12 manual review | 117 passed | 117 passed |
| PR14 retention | 95 passed | 95 passed |

All ten final order results have **0 skipped, 0 failed, 0 setup errors**. Each
order uses an isolated disposable database and one pytest session containing the
existing module plus both withdrawal modules. The initial PR9 results were
**97 passed, 1 failed** in each order; these exposed the baseline clock-reversal
defects documented below and were rerun after fixing them.

The exact requested selector:

```text
pytest -q tests/integration -k "scan or publication or withdrawal or batch or remediation or manual or retention or checking or operational"
```

Result: **564 passed, 4 skipped, 255 deselected, 1 failed, 0 setup errors**,
5 existing Alembic configuration warnings. Its only failure was the existing
PR10 migration test's subprocess exiting `3221227274` with empty stdout/stderr
before executing the migration. The same Windows fresh-interpreter exit occurs
in baseline model-registry testing. Targeted rerun of the entire PR10 migration
module: **1 passed**, including its real downgrade/upgrade and fresh-schema
checks. The four selector skips are the existing environment-gated PR11/PR12
dedicated migration cases. No fixture failure is represented as a PR15 defect,
and the selector's failed count is retained rather than reported as a clean run.

PR15 migration acceptance separately passes the requested real PostgreSQL
roundtrip and a fresh full upgrade. The populated downgrade incompatibility and
post-republish downgrade preservation tests pass in the dedicated suite and all
order suites. Ruff on every changed Python file, compileall across app/migrations/
tests, `git diff --check`, single-head Alembic inspection, and
`docker compose config --quiet` pass.

End-to-end acceptance includes actual grouping confirmation through checking,
review, publication, finalization, withdrawal and same-revision republish; HTTP
student result/page denial and teacher page access; real render purge followed
by teacher 410 and rejected republish; and PR11's full assigned-plan answer,
submission and checking workflow after withdrawal. Uploaded source PDFs and
normalized PNGs are physically stored by the shared intake fixtures; provider
responses are deterministic test doubles. No external provider or notification
service is called.

## Exact changed files

- `.env.example`
- `docker-compose.yml`
- `backend/alembic/versions/20261012_02_paper_publication_withdrawal.py`
- `backend/app/application/paper_result_publication.py`
- `backend/app/application/scan_storage_retention.py`
- `backend/app/infrastructure/paper_remediation_repository.py`
- `backend/app/infrastructure/checking_repository.py`
- `backend/app/infrastructure/paper_checking_execution_repository.py`
- `backend/app/infrastructure/paper_result_publication_models.py`
- `backend/app/infrastructure/paper_result_publication_repository.py`
- `backend/app/infrastructure/scan_storage_retention_repository.py`
- `backend/app/presentation/paper_result_publication_routes.py`
- `backend/tests/integration/test_paper_checking_execution_database.py`
- `backend/tests/integration/test_paper_checking_clock_regression_database.py`
- `backend/tests/integration/test_paper_publication_withdrawal_database.py`
- `backend/tests/integration/test_paper_publication_withdrawal_races.py`
- `backend/tests/integration/test_scan_storage_retention_database.py`
- `backend/tests/pr15_migration_acceptance.py`
- `backend/tests/unit/test_paper_publication_withdrawal.py`
- `backend/tests/unit/test_scan_storage_retention.py`
- `docs/pr15-paper-publication-withdrawal.md`

Shared execution fixtures now point publication's actual-byte validation to their
temporary filesystem. PR14 expectations now recognize v2 and the bounded
publication-unavailable error. These are acceptance-fixture updates; preexisting
untracked files are preserved.

## Production changes and review corrections

The lifecycle closes the new visibility gap with an atomic withdrawal record and
pointer update, keeps withdrawn drafts out of active-source remediation operations,
and blocks creation of publications whose render bytes are unavailable. Batch-first
locking makes finalization and visibility changes serializable. Teacher history is
read from one MVCC snapshot. During review, the new whitespace check was corrected
to use explicit character codes: PostgreSQL's escape handling must not treat the
letter `v` as whitespace. A PostgreSQL regression withdraws with the reason `verify`.
No unrelated unit-test defects were changed.

Both initial PR9 cross-order runs exposed a preexisting paper-checking timestamp
defect before any withdrawal operation: PostgreSQL's wall clock stepped backwards,
so `finished_at` preceded the recorded start and violated `ck_model_runs_time` or
`ck_check_runs_finished_order`. Four controlled cases on the untouched accepted
baseline reproduce the defect (success/failure, attempt/run clock reversal):
**4 failed**. The narrow fix bounds new paper attempt/run timestamps by the
existing recorded chronology; the constraints remain intact, and no historical
rows are rewritten. Paper transition timestamp handling is scoped to paper runs,
leaving digital transitions unchanged. The same four regressions now report
**4 passed**. The affected PR9 orders are rerun against the final implementation.
