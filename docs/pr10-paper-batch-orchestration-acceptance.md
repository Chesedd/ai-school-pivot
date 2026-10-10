# PR10 — Batch checking orchestration and completion

Branch: `codex/paper-batch-orchestration`, based on accepted PR9 commit
`a72b77290424b24ac72d66a40cbb18a745f88295`. The final local commit SHA is supplied
in the completion message. No push, merge, PR11, frontend, remediation, worker,
provider batching, automatic approval or automatic publication is included.

## Lifecycle and authoritative progress

The existing `AssessmentScanBatch` model remains authoritative. PR10 implements:

```text
ready_for_checking -> checking
checking -> checking_completed
checking_completed -> checking  (only creation of a legitimate new AI attempt)
checking_completed -> completed (explicit teacher finalization)
```

`completed` has no outgoing transition. Existing earlier intake/cancellation and
batch infrastructure failure transitions retain their previous semantics.
PR10 orchestration never targets `processing_failed`; a failed paper cannot
produce that batch status.

`PaperBatchProgress` is an immutable application contract. Its deterministic
fields contain batch ID/status, paper_count, not_started, pending, running,
completed, completed_with_review_required, failed_retryable, failed_terminal,
settled_count, active_publication_count, can_mark_checking_completed,
can_reconcile, can_finalize, and ordered bounded blocking_reasons. The database
stores no duplicated progress counters. A single SELECT reads batch status,
paper membership, run history and active pointers in one PostgreSQL statement
snapshot. Only IDs belonging to this batch contribute.

The effective run is the highest `CheckRun.attempt_no` for the paper, using the
existing unique paper-attempt constraint. These are paper CheckRun attempts,
not provider ModelRun attempts. Older failures disappear from aggregate counts
after a newer success; a newer retryable failure supersedes an older success.

A settled paper's effective status is `completed`,
`completed_with_review_required`, or `failed_terminal`. No run, pending,
running and failed_retryable are unsettled. Unknown future statuses fail closed.
Empty batches cannot settle. `can_mark_checking_completed` means paper_count > 0
and settled_count == paper_count. `can_reconcile` means the batch is checking.
A retryable failure keeps the batch checking; a terminal failure settles its
checking work but still requires an active publication before finalization.

Publication counts use only `PaperResultPublicationState.active_publication_id`,
with the existing same-paper composite FK. Approval and historical publication
rows do not count. An old active publication continues to count after a newer
unpublished approval. Decimal scores and grades do not affect this lifecycle.
`can_finalize` requires status checking_completed, a nonempty batch, and an
active publication for every paper.

## Operations and compatibility

All HTTP paths are relative to `/api/assessment-core`:

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/scan-batches/{batch_id}/checking` | Aggregate progress only |
| POST | `/scan-batches/{batch_id}/checking/start` | Explicit ready_for_checking -> checking |
| POST | `/scan-batches/{batch_id}/checking/reconcile` | Settle checking when every effective run is settled |
| POST | `/scan-batches/{batch_id}/complete` | Explicit completion after all active publications exist |

The existing scan-management capability, trusted-origin dependency and
batch/class teacher authorization protect every endpoint. Unrelated teachers
receive 404; students/accounts without the capability receive 403. Responses
contain no raw provider output, page bytes, source PDF, credentials or student
details.

Start verifies a nonempty batch, confirmed grouping provenance and frozen
grading-policy coverage for represented variants. Already checking is an
idempotent replay; inappropriate earlier/later states reject without writes.
Start creates no runs and calls no provider.

Direct per-paper intake remains compatible. New CheckRun creation locks the
batch before the paper and starts a ready batch or reopens checking_completed
inside the same transaction as run creation. Invalid pre-grouping/cancelled
states reject. A new run after completed rejects with `paper_batch_completed`.
An identical existing run request is replayed without a new event or reopening,
including after completion; replay is not new AI checking.

Reconcile locks and refreshes the batch, derives effective counts, and performs
checking -> checking_completed only when nonempty and settled. Other states
are preserved. Repeated reconciliation creates no duplicate transition events.
It creates no runs, approvals or publications and calls no provider.

The PR7 application execution boundary invokes reconciliation after committed
success or final failure, and on successful execution replay. It uses a separate
transaction. Hook failures are logged and do not change the committed paper
result or returned successful result; explicit reconciliation repairs progress.
No provider adapter contains cross-aggregate orchestration.

Finalization is explicit and idempotent. It only verifies existing active
publications; neither settling checking nor finalizing creates publication rows.
Missing/partial publications and approval alone block it. Review edit,
reapproval and replacement publication continue to work after completed and
preserve the completed batch status.

## Audit, locks and migration

The existing `ScanCheckingEvent` records `batch.checking_started`,
`batch.checking_completed`, `batch.checking_reopened` and `batch.completed`.
Details contain only paper_count, settled_count, failed_terminal_count and
active_publication_count. Explicit operations and HTTP run creation use the
acting teacher. Automatic reconciliation and adapter calls without an actor
use the original batch creator, since the existing event schema requires a user.

PostgreSQL `FOR UPDATE` batch locks serialize aggregate transitions and new
attempts. `populate_existing=True` refreshes a row loaded before a competing
transition. Each actual transition increments row_version exactly once and
updates updated_at. No-op replays do neither. State/event writes share the
caller transaction. Independent PostgreSQL connections prove the second caller
blocks, then observes the committed status: one transition, one event and one
version increment for both competing reconciliation and competing completion.
Publication pointers are monotonic under PR9 (no unpublish), so concurrent
corrections do not invalidate the all-active prerequisite.

Migration was genuinely required to extend the bounded audit-event CHECK.
Revision `20261011_01` has `down_revision = "20261010_02"`. There are no new
tables, counters or speculative indexes. Each `op.execute` contains one
top-level SQL command and works with asyncpg.

Downgrade restores the old event vocabulary using a PostgreSQL `NOT VALID`
CHECK. This preserves existing PR10 audit history while enforcing the old
vocabulary for every new write. Re-upgrade validates the expanded vocabulary.
The migration gate tests a populated database retaining its new audit event,
the down/up/down/up cycle, and a fresh public-schema upgrade to head.

## Validation

All database runs use disposable PostgreSQL databases ending in `_test`, with
credentials read from the local environment file without printing them. Ordinary
tests use the established outer rollback transaction plus savepoint sessions.
Concurrency tests clone a disposable database for independently committed
connections, then drop the clone. Migration gates run separately from fixture
transactions. Local untracked runners/logs stay outside the commit.

| Check | Result |
| --- | --- |
| Focused aggregate unit module | 22 passed |
| Dedicated PostgreSQL module | 43 passed |
| PR9 publication -> PR10 | 82 passed |
| PR10 -> PR9 publication | 82 passed |
| PR8 review -> PR10 | 91 passed, 1 warning |
| PR10 -> PR8 review | 91 passed, 1 warning |
| PR7 execution -> PR10 | 84 passed, 1 warning |
| PR10 -> PR7 execution | 84 passed, 1 warning |
| Relevant integrations: `checking or scan or publication or batch` | 327 passed, 281 deselected, 4 warnings |
| Full units with existing Windows-safe byte parameter-ID helper | 1,123 passed, 2 existing failures, 1 warning |
| Migration roundtrip / populated downgrade / fresh head | 1 passed |
| Ruff on all changed Python files | Passed |
| compileall: app, migrations, tests | Passed |
| git diff --check | Passed |
| alembic heads | Sole head `20261011_01` |

Dedicated coverage includes all checking status categories, historical/latest
attempt selection, start prerequisites, empty and wrong-state rejection,
retryable/terminal behavior, idempotency/events, direct intake/reopening/replay,
publication prerequisites/history/old-active-new-approval, completed-batch
corrections, all four endpoints' authorization/privacy, batch isolation,
foreign publication pointers, real execution failure hooks and hook-outage
repair, plus independent-connection concurrency. The full happy path invokes
the actual grouping confirmation repository before PR6 intake, PR7 execution,
PR8 review approval, PR9 publication and PR10 explicit completion.

Two unchanged full-unit failures were reproduced, matching prior PR8/PR9 reports:

```text
tests/unit/test_checking_handoff_fixture.py::test_checking_handoff_v1_fixture_schema_and_invariants
tests/unit/test_checking_handoff_fixture.py:29
    assert normalize_answer(item["answer_format"], item["raw_answer"]) == item["normalized_answer"]
E   AssertionError: assert {'text': 'Café\nX'} == {'text': 'Café\nX'}

tests/unit/test_classroom_notes.py::test_privacy_admin_rules_and_cas
tests/unit/test_classroom_notes.py:46
    with pytest.raises(ClassroomNotesError) as x: await s.update("class",c,None,n.id,a,False,"stale",n.updated_at)
E   Failed: DID NOT RAISE <class 'app.application.classroom_notes.ClassroomNotesError'>
```

The first is the decomposed-vs-NFC fixture mismatch in unchanged
`student_assessments.normalize_answer`; the second is the previously documented
intermittent fake-repository timestamp collision. Neither failing test nor its
production module was changed. The unit run used `-p pr8_pytest_ids`, the existing
local helper shortening byte parameter IDs to avoid Windows's environment
variable length limit. The actual failing tests were still executed.

No additional production defect was found. Initial new-test failures concerned
missing failure_code in seeded failed runs and using HTTP response shapes for
repository return values; these test setup mistakes were corrected. The accepted
PR7 aggregate assertions now expect checking_completed after the final success,
including a batch containing a successful paper and a terminally failed paper;
the PR9 fresh-migration assertion now uses repository_head(). Touched Python
files also received Ruff import/type cleanup and equivalent context-manager
flattening where required by the installed checker.

## Exact changed files

- `backend/alembic/versions/20261011_01_paper_batch_orchestration.py`
- `backend/app/application/paper_batch_orchestration.py`
- `backend/app/application/paper_checking_execution.py`
- `backend/app/application/scan_checking_contracts.py`
- `backend/app/infrastructure/checking_repository.py`
- `backend/app/infrastructure/paper_batch_orchestration_repository.py`
- `backend/app/infrastructure/paper_checking_execution_repository.py`
- `backend/app/infrastructure/paper_checking_repository.py`
- `backend/app/infrastructure/scan_checking_models.py`
- `backend/app/presentation/scan_intake_routes.py`
- `backend/tests/integration/test_paper_batch_orchestration_database.py`
- `backend/tests/integration/test_paper_batch_orchestration_migration.py`
- `backend/tests/integration/test_paper_checking_execution_database.py`
- `backend/tests/integration/test_paper_result_publication_migration.py`
- `backend/tests/unit/test_paper_batch_orchestration.py`
- `docs/pr10-paper-batch-orchestration-acceptance.md`
