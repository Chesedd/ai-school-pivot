# PR14: scanned-paper storage retention

Local branch: `codex/paper-storage-retention`, based on accepted
`98dc8394d4fe4bcff356735e52d68d27466cee27`. The final local commit SHA is
reported with delivery. No push, merge, remote PR creation, or PR15 work.

## Operator commands

Run from `backend` with the application's normal database and storage settings:

```sh
python -m app.cli scan-retention --dry-run
python -m app.cli scan-retention --execute
```

A mode flag is required. There is no scheduler or global teacher/student API.
JSON reports contain bounded selected target IDs/kinds/batch IDs and counts of
purged, retryable-failed and skipped targets. They never contain storage
references, filesystem paths, source filenames or backend error payloads.
Selected counts are the bounded window, not an unbounded database-wide total.

## Configuration, anchor and policy

`Settings.paper_retention` constructs immutable `PaperRetentionSettings` at
startup, following PR13's `PAPER_` settings convention. Every environment value
is validated, and both `.env.example` and Compose expose it.

| Environment variable | Default | Allowed range |
| --- | ---: | ---: |
| `PAPER_SOURCE_RETENTION_DAYS` | 30 | 1–3650 |
| `PAPER_UNPUBLISHED_PAGE_RETENTION_DAYS` | 30 | 1–3650 |
| `PAPER_RETENTION_MAX_TARGETS_PER_RUN` | 100 | 1–1000 |
| `PAPER_RETENTION_CLAIM_TTL_SECONDS` | 300 | 30–3600 |

The anchor is the earliest explicit `batch.completed` event's `occurred_at`,
with matching batch aggregate identity. PR10 writes this event in the completion
transaction. PR14 adds an index and an update/delete guard for these events;
post-completion edits and republishes cannot alter the clock. Generic
`updated_at` is never used. A historical completed batch without a completion
event is skipped rather than given an invented completion timestamp. No new
`completed_at` column or historical storage backfill is needed.

The ledger preserves `paper_storage_retention.v1`, original eligibility time,
attempt count and final purge time. Changing deployment settings never rewrites
existing ledger identity, policy version or recorded eligibility time.

## Storage inventory

| Artifact | Actual persistence | PR14 treatment |
| --- | --- | --- |
| Uploaded PDF/image | Filesystem object and `InputArtifact` metadata, referenced by `ScanBatchArtifact` | Eligible source bytes only |
| Normalized page PNG | Separate filesystem object and `InputArtifact`, referenced by `ScanPage` | Eligible unprotected bytes only |
| Provider request images | Read into bounded in-memory byte maps; provider base64 assembled in memory | No durable request-image blob to purge |
| Provider response blobs | No separate storage-backed scan response object | No invented retention kind |
| Paper `ModelRun` payload | `raw_output` is serialized validated response JSON; `validated_output` and `PaperAIRevision` preserve structured evidence | Preserve unchanged |
| Matching previews | Generated/read in memory, including preview bytes and provider maps | No durable preview blob |
| Matching provider output | Structured `ScanMatchingChunk.validated_output`, proposals, candidates, frozen roster/page metadata and telemetry in PostgreSQL | Preserve unchanged |
| Extraction intermediates | In-process Pillow/PDFium image/bitmap/document resources | Native resources close in renderer `finally` blocks |
| Newly written extraction renders on rollback | Temporary uncommitted filesystem objects | Existing rollback deletes them immediately; PR13 regression verifies rollback/rejection behavior |
| Multipart upload spools | Starlette transient spool files | Existing form context closes on success/body errors; parser closes rejected spools, and cancellation/disconnect cleanup closes partial spools |

PR13's spool boundary tests prove immediate closure on successful and oversized
uploads. No additional durable provider/intermediate storage kind was found.
No provider output, raw database evidence, or immutable AI provenance is mutated.

## Eligibility and protection

Both kinds require current batch status `completed`, an authoritative completion
event, and completion plus the configured window less than or equal to database
`clock_timestamp()`. Already-purged targets and unexpired claims are excluded.
Only actual scanned-paper ownership/membership creates candidates; generic
`InputArtifact` rows alone cannot create retention work.

Source candidates reference exactly the original `ScanBatchArtifact` input.
They cannot also be another batch's source or a page render. Page candidates
reference the page's actual derived input, cannot also be a source or another
page's render, and must have no protecting active publication. Both kinds exclude
inputs referenced by authoring or image-solving sessions. Shared inputs are
conservatively retained even if every known scanned-paper consumer is old.
Content Bank and answer attachments use their separate storage boundary.

Protection is an SQL `NOT EXISTS` over `PaperSubmissionPage` →
`PaperSubmission` → confirmed grouping → `PaperResultPublicationState` active
pointer → matching `PaperResultPublication`. No publication snapshots are
loaded into Python. Superseded publication rows cannot negate protection of
the same page in the current active publication; historical rows without an
active pointer do not independently protect pages.

Original source purge is independent from normalized page storage. PR14 never
deletes batches, artifact/page metadata, grouping history, PaperSubmissions,
CheckRuns, CheckResults, CheckFindings, ModelRuns, PaperAIRevisions, review
revisions, publication snapshots/pointers, grading policy, remediation plans or
provenance. It never changes batch workflow status or creates publication
withdrawal, expiration or manual-review fallback.

## Ledger and transactions

Migration `20261012_01` has `down_revision = "20261011_03"`.
`scan_storage_retention_entries` contains:

- UUID identity and batch FK;
- `target_kind` (`source_artifact` or `page_render`), nullable artifact/page FKs;
- kind-matching exact-XOR target constraint, separate unique target constraints;
- `pending`, `retryable_failed`, or `purged` state;
- policy version and eligible time;
- claim time, expiry and UUID fencing token;
- nonnegative attempts, nullable bounded 64-character error code;
- purge time, created time, updated time;
- state/expiry/eligible index, plus the completion-event index.

A database trigger checks the target's actual batch and prevents changes to
recorded identity, policy and eligible time. The table is populated lazily;
migration performs no storage deletion or eager target backfill.

Discovery orders by eligibility time, target kind and UUID and applies the
configured limit. Claim locks the actual artifact/page with `FOR UPDATE SKIP
LOCKED`, rechecks the full authoritative SQL policy, then creates/reclaims the
unique ledger row. The short transaction commits before external deletion.
Finalization uses the claim token, so an old worker cannot overwrite a newer
claim. Database time controls all eligibility and lease decisions.

Unexpired claims are not stolen. Expired claims can be reclaimed, incrementing
attempts on the same row. Concurrent workers cannot acquire the same active
claim. A lease-expired worker's external replay may be harmlessly idempotent;
its obsolete token cannot finalize another worker's lease.

Filesystem delete accepts one bounded opaque reference, rejects traversal,
drive/path syntax and symlink targets, and unlinks one object. It returns `True`
for a deletion and `False` for confirmed absence. Confirmed absence or
`FileNotFoundError` finalizes as successful purge, including crash-after-delete
recovery. Unknown backend exceptions become `storage_delete_failed`, release
the claim as `retryable_failed`, and permit retry; raw exception text is never
persisted. No failed delete marks a batch failed or changes academic data.

Dry-run only discovers and reports; it never claims, writes a lifecycle row,
finalizes a row or calls delete. Retention has its own table and never consumes
PR13 paper AI execution slots or uses a process-local mutex.

## Publication and read races

Publication locks papers then page IDs in deterministic UUID order. Retention
locks the same page before the final eligibility check. Publication checks for
retention attempts under those locks in one bounded query. If publication wins,
retention skips the locked/protected page; if the retention claim commits first,
publication cannot promise those page bytes. Ordinary completed batches already
have active publications for all papers, so their pages never get a ledger row.

The teacher page endpoint now checks finalized purge state before reading. It
reads all bytes before creating a response; if deletion wins the race, a fresh
query recognizes the committed retention attempt and returns `410` with
`{"detail":"scan_page_content_purged"}`. Missing content without a retention
attempt is bounded `404`; filesystem/invalid-reference failures are bounded
`503`, with no path/backend details.

The accepted local code had no original-source content-download endpoint.
PR14 adds the teacher-only, class-authorized route:

```text
GET /api/assessment-core/scan-batches/{batch_id}/artifacts/{artifact_id}/content
```

It checks exact artifact/batch ownership and uses the same read-race boundary,
returning `410` with `{"detail":"scan_artifact_content_purged"}` after purge.
The source remains inaccessible to students. Artifact metadata remains readable.
Active-publication student page routes continue serving the preserved renders.

Existing AI/manual corrections, approval and explicit republishing work after
source cleanup. An earlier active publication remains active until replacement
is explicitly published. New manual review remains prohibited after completion;
existing manual review start remains idempotent. Remediation eligibility,
candidate selection, creation, assignment, answer submission, checking and
student/teacher results continue from publication/database provenance.

## Verification

Testing uses real PostgreSQL 17, savepoint-isolated fixtures and disposable clone
databases, plus actual local filesystem objects. The cached acceptance image
lacked Pillow/PDFium; the local verification image adds the already-declared
`Pillow==11.3.0` and `pypdfium2==4.30.0`. This is verification-environment setup,
not a production dependency or renderer change.

Final acceptance results:

- Focused units plus dedicated PostgreSQL: **74 passed**.
- Dedicated PostgreSQL module: **36 passed**.
- Full unit suite: **1269 passed**, one dependency deprecation warning, no failures/errors.
- Forward regression order: **345 passed**, two dependency/configuration deprecation warnings.
- Reverse regression order: **345 passed**, two dependency/configuration deprecation warnings.
- Final model-registry and retention unit regression: **41 passed**.
- Exact requested integration selector: **502 passed, 4 skipped, 255 deselected, 0 failed, 0 setup errors**, six Alembic configuration deprecation warnings. The four skips are environment-gated dedicated PR11/PR12 migration cases; PR14 migration roundtrip and fresh upgrade were run separately against real PostgreSQL. The known clone-fixture errors did not recur on this fresh disposable database.
- Real PostgreSQL `20261011_03 → 20261012_01 → 20261011_03 → 20261012_01`: **pass**.
- Fresh empty database → `alembic upgrade head`: **pass**.
- One Alembic head: **20261012_01**.
- Ruff on all 17 changed Python files, compileall, staged/unstaged diff whitespace and Compose validation: **pass**.

**Storage purge is irreversible across schema downgrade.** Downgrade removes
PR14 ledger/index/trigger metadata and never recreates or fabricates deleted
bytes. Operator recovery of bytes would require an independently retained backup.

Acceptance logs are local files under `local-backups/pr14-*`; generated test
environments, logs, backups and cached images are not included in the commit.

The forward regression order uses these modules under `tests/integration`;
the reverse run uses precisely the opposite order:

1. `test_scan_intake_database.py`
2. `test_scan_grouping_database.py`
3. `test_paper_checking_execution_database.py`
4. `test_paper_checking_review_database.py`
5. `test_paper_result_publication_database.py`
6. `test_paper_batch_orchestration_database.py`
7. `test_paper_remediation_database.py`
8. `test_manual_paper_review_database.py`
9. `test_paper_operational_limits_database.py`
10. `test_scan_storage_retention_database.py`

The broad selector is run exactly as requested, from `backend`:

```sh
pytest -q tests/integration -k "scan or retention or publication or batch or remediation or manual or operational or checking"
```

Full units use `pytest -q tests/unit`; diagnostic runs add `--tb=short` without
changing test selection. Migration acceptance is reproducible with
`python -m tests.pr14_migration_acceptance` against an explicitly disposable
`TEST_DATABASE_URL` ending in `_test`.

The dedicated module proves the full production-service chain: create a new
batch, upload a two-student PDF, real extraction, deterministic matching-provider
output, grouping confirmation, check/review/approve, publish both papers,
explicit completion, historical clock seed, and source cleanup. It checks
physical source absence, unchanged AI evidence/result JSON/grades and working
student page bytes/remediation creation. A separate HTTP acceptance case proves
source `410`, preserved source/page metadata, orphan PNG deletion/page `410`,
student source denial and intact active pages in the same batch. Retry acceptance
proves physical bytes remain on failure, bounded ledger errors, unchanged batch,
then successful second deletion with attempt count two. Independent PostgreSQL
connections prove exclusive claims/two sweepers, read/delete bounded responses
and publication page locking. PR11's full assign/answer/submit/check/results flow
is also executed after actual source deletion.

## Production gaps addressed

- Added durable, crash-recoverable purge decisions instead of unexplained missing
  storage content, with exact target integrity and fenced retry handling.
- Protected active publication pages through authoritative references and shared
  page locks during publication/retention decisions.
- Bounded teacher page storage-read failures and deletion races; added the missing
  authorized teacher source-content route with explicit retention responses.
- Hardened the opaque filesystem boundary against path syntax and symlink targets.
- Enforced immutability of the existing completion-event anchor in PostgreSQL.

No unrelated application behavior was changed to hide test-fixture instability.
The initial broad run reported **501 passed, 4 skipped, 255 deselected, 1 failed**,
with six Alembic configuration deprecation warnings and **zero fixture setup
errors**. Its only failure was PR10's obsolete explicit head list in the migration
acceptance assertion. That assertion now checks preserved batch events for all
exercised revisions after the pre-PR10 target, including the current head; it does
not change production code or relax the event-preservation invariant.

## Exact changed files

- `.env.example`
- `docker-compose.yml`
- `backend/alembic/versions/20261012_01_scan_storage_retention.py`
- `backend/app/application/input_artifacts.py`
- `backend/app/application/scan_storage_retention.py`
- `backend/app/cli.py`
- `backend/app/config.py`
- `backend/app/infrastructure/artifact_storage.py`
- `backend/app/infrastructure/model_registry.py`
- `backend/app/infrastructure/paper_result_publication_repository.py`
- `backend/app/infrastructure/scan_checking_models.py`
- `backend/app/infrastructure/scan_storage_content.py`
- `backend/app/infrastructure/scan_storage_retention_models.py`
- `backend/app/infrastructure/scan_storage_retention_repository.py`
- `backend/app/presentation/scan_intake_routes.py`
- `backend/tests/integration/test_scan_storage_retention_database.py`
- `backend/tests/integration/test_paper_batch_orchestration_migration.py`
- `backend/tests/pr14_migration_acceptance.py`
- `backend/tests/unit/test_scan_storage_retention.py`
- `docs/pr14-paper-storage-retention.md`
