# PR13: scanned-paper operational safeguards

Branch: `codex/paper-batch-operational-guards`, based on accepted local
`2510fb1d0e9fcca5f4edeac5cbbfb5b9a3f9f8e3`. Local implementation only.

## Configuration and defaults

`backend/app/application/paper_operational_limits.py` owns the immutable,
validated `PaperOperationalLimits`. `Settings` constructs it at application
startup; invalid values fail startup. Environment names are `PAPER_` followed
by the uppercase field name. `.env.example` and Compose expose these options.

| Field | Default |
| --- | ---: |
| max_artifacts_per_batch | 25 |
| max_total_upload_bytes_per_batch | 262144000 bytes (250 MiB) |
| max_single_artifact_bytes | 26214400 bytes (25 MiB) |
| max_pages_per_artifact | 100 |
| max_pages_per_batch | 150 |
| max_pixels_per_page | 25000000 |
| max_paper_submissions_per_batch | 60 |
| max_pages_per_paper | 20 |
| max_parallel_matching_runs | 1 per batch |
| max_parallel_paper_checks_per_batch | 3 |
| max_parallel_paper_checks_global | 12 |
| max_provider_pages_per_paper | 20 |
| max_provider_image_bytes_per_paper | 26214400 bytes (25 MiB) |
| execution_lease_seconds | 120 |

Every field must be positive. Matching remains restricted to one active run.
Single-file bytes and pixels cannot exceed the previously accepted security
ceilings; artifact pages cannot exceed the existing 200-page PDF security
ceiling. Single-file bytes must fit batch bytes; artifact pages must fit batch
pages; provider pages must fit the per-paper storage limit. Configuration is
never silently corrected. No database-managed settings were added.

## Intake and extraction

The existing single-file HTTP API remains atomic. The Starlette multipart
parser now measures file bytes as chunks arrive and rejects the first excess
chunk, before spooling it. It closes spools on rejection, invalid form,
disconnect, cancellation and successful completion. Readback is also bounded.
Multi-file requests are rejected without attaching any file.

Upload and existing-artifact attachment lock the batch row before counting
memberships and summing source `InputArtifact.size_bytes`. Source-byte limits
never use derived renders. Upload creates storage and metadata only after
these aggregate checks. Rejected writes roll back and remove new storage.

Extraction holds the batch lock across one bounded artifact operation.
PDFium page counts are checked before rendering; the prospective batch total
subtracts already persisted pages of the current artifact before adding the
complete source page count. Source pixels are checked before decoding/rendering
and normalized pixels before persistence. Existing signature, MIME, dimensions,
decompression-bomb, normalization and PNG-byte protections remain active.

Pages and render metadata are flushed together and committed only after the
whole artifact succeeds. Rejection rolls back all pages of that artifact and
deletes its new renders. A fallback per-page guard also protects custom
rasterizers and the lower-level intake repository. Concurrent extraction and
duplicate extraction cannot exceed authoritative batch page counts.

## Grouping and provider preconditions

Grouping confirmation validates the current revision under its existing batch
lock. It checks paper count and all per-paper page counts before confirming the
revision or creating any paper. Failure creates zero partial papers. A replay of
an already confirmed revision remains readable regardless of new limits.

Paper execution checks snapshot page count before constructing page descriptors.
It then sums authoritative normalized-artifact sizes before acquiring an AI slot
or reading binaries. It rejects the entire paper rather than omitting pages.
Filesystem reads use the remaining payload budget plus one rejection byte;
actual byte totals and hashes are checked before the provider call.

A provider-size precondition failure is committed as `failed_terminal` with its
application error code, creating zero provider attempts and leaving retry count
unchanged. This permits explicit PR12 manual takeover. It is never classified as
transport failure and does not automatically start a manual review.

## Execution claims, leases and retries

No migration or new semaphore table is necessary. Existing `ModelRun` attempt
identity, status, database-generated `started_at`, and persisted `timeout_ms`
provide the durable claim. For paper attempts, `timeout_ms` holds the execution
lease duration. The actual asynchronous provider call is bounded to 30 seconds;
the lease defaults to 120 seconds and cannot be configured below 60 seconds.

Paper claims first acquire a PostgreSQL transaction advisory lock in a dedicated
paper namespace, then acquire the batch row lock. Finalizations acquire the batch
row lock. Claim transactions verify eligibility and same-run ownership, count
unexpired running paper attempts globally and in that batch, and atomically insert
the winning attempt. The global lock is released at transaction end, before
reading images or calling AI; it never gates digital/remediation provider work.
`pending` becomes `running` only when a claim is obtained. Other processes use
the same PostgreSQL authority. No process-local semaphore is used.

At capacity, `paper_check_capacity_unavailable` returns HTTP 409. The transaction
rolls back: no attempt, failure, retry increment, prompt insertion or denial audit
event is committed. Pending work remains eligible, and the batch remains checking.
The caller retries explicitly when capacity frees. No persistent queue or worker
system was introduced. Capacity projection fields were not added to the progress
contract; enforcement is independent of that read contract.

The existing duplicate code `paper_check_in_progress` is retained (HTTP 409).
Exactly one same-run request reaches the provider. Completed results replay even
if configuration has subsequently become more restrictive.

Lease expiration is calculated with PostgreSQL `clock_timestamp()` and persisted
claim timestamps. An expired attempt stops occupying capacity. On a later execution
request for its run, the old attempt is finalized as failed with
`paper_execution_lease_expired`, and a new attempt identity fences the old process.
No academic failure is assigned just because a process disappeared. Expired leases
do not count toward the three-attempt provider transport budget or increase
`CheckRun.retry_count`. Late success/failure cannot write through the old claim.
Claims are rechecked after binary reads, with enough remaining time for the bounded
provider call. No automatic lease-recovery worker was added.

The guarantees are **three live claims per batch and twelve globally** by default,
shared by API processes using the same PostgreSQL database and deployment config.
Independent batches have independent per-batch slots and share the global budget.
A global denial uses the same non-terminal capacity code and consumes no attempt
or retry. Existing generic model status/time indexing and paper foreign keys are
reused; no table, global worker queue or process-local semaphore was needed.
Global matching concurrency is not capped. The semaphore bounds live database
claims and application execution; it cannot cancel work already received by a
remote provider after a client/process crash.

Matching already had a batch claim and per-chunk claim. Both now refresh rows
after waiting on locks, including rows preloaded by an authorization read. Matching
also counts batch pages before loading descriptors. Two clicks cannot create two
concurrent matching provider calls for a batch.

## Lifecycle compatibility and errors

PR10 start/reconcile/finalize semantics remain intact. Ten simultaneous starts
produce one transition event and no implicit CheckRuns. Provider finalization,
manual approval, reconciliation and explicit completion serialize on the batch
row. Capacity pressure never counts as settlement or publication.

PR12 manual review and approval consume no AI capacity. The existing rule preventing
manual takeover of an active AI run is preserved. A different paper can be manually
reviewed and approved while all AI slots are occupied. An oversized AI paper can
finish through explicit manual review, publication and batch finalization.

PR11 remediation, digital checks, and digital provider capacity are untouched.
Historical batches remain readable. Only new operations are constrained; no pages,
papers or results are deleted to fit new defaults. No academic matching, grouping,
grading, approval, publication or remediation policy was changed.

Intake errors include `scan_artifact_too_large`,
`scan_batch_artifact_limit_exceeded`, `scan_batch_byte_limit_exceeded`,
`scan_artifact_page_limit_exceeded`, `scan_batch_page_limit_exceeded`, and existing
`image_too_large`/normalization errors. Upload limits use HTTP 413. Extraction
retains its existing per-artifact failure summary contract with bounded codes.
Grouping uses `scan_batch_paper_limit_exceeded` and
`paper_submission_page_limit_exceeded` (HTTP 422). Provider preconditions use
`paper_check_provider_page_limit_exceeded` and
`paper_check_provider_payload_too_large` (HTTP 422).
Expired execution ownership uses `paper_check_execution_claim_expired`.

## Acceptance

`tests/integration/test_paper_operational_limits_database.py` uses real PostgreSQL,
rollback-isolated fixtures and disposable database clones for independent
connections. Event-gated fake providers prove a maximum of three active calls
across ten simultaneous attempts, seven capacity denials, unchanged retries,
eventual completion of all ten papers, teacher approvals, publications and explicit
completion. The flow includes actual batch creation, ten uploads, rasterization,
matching and grouping before checking.

Other proofs cover concurrent artifact counts and source bytes, atomic PDF/page
rejection and concurrent extraction, grouping atomicity, provider page/byte guards,
zero calls on precondition rejection, historical reads/replays, stale recovery,
same-run races, independent batches sharing global capacity, preserved transport/semantic failure behavior,
manual review during saturation, matching clicks with stale session objects, and
provider-finalization/manual-approval/reconcile/finalize races. Barriers and events
control provider overlap; sleeps do not determine correctness. The final global-slot
race uses independent sessions in independent batches, proving one winner and safe
retry of the capacity-denied loser.

`tests/unit/test_paper_operational_limits.py` covers every default and exact/+1
boundary, every nonpositive field, invalid relationships and security ceiling
overrides, environment/startup validation, provider pre-materialization rejection,
source pixels, PDF preflight and incremental multipart rejection/spool closure.

No migration was added. Alembic has the single head `20261011_03`; no PR13
roundtrip is applicable. A fresh PostgreSQL upgrade to that head was also run.

Validation used disposable PostgreSQL 17 databases and fake providers, with no
external provider calls. The final full unit run used the existing Linux backend
Docker image because the managed Windows Python runtime has separate failures.

| Acceptance run | Passed | Skipped | Deselected | Failed | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full Linux unit suite (final configuration) | 1231 | 0 | 0 | 0 | 0 |
| Dedicated real PostgreSQL PR13, before the final independent-connection global race was added | 26 | 0 | 0 | 0 | 0 |
| Final PR13-first order: PR13, PR7 execution, PR10, PR12, scan intake/grouping, input artifacts, image-solving end-to-end | 205 | 0 | 0 | 0 | 0 |
| Final reverse order, with the same eight modules reversed | 205 | 0 | 0 | 0 | 0 |
| Earlier forward/reverse orders, before global-cap additions | 203 each | 0 | 0 | 0 | 0 |
| Requested broad integration selector | 453 | 4 | 253 | 0 | 14 |
| Same broad selector on accepted baseline `2510fb1` | 434 | 4 | 253 | 0 | 7 |

Both final orders include all **27 dedicated PR13 cases**, including a
separate-session, separate-batch race for the last global slot: exactly one claim
wins; the other has pending status, zero attempts and zero retries, then can claim
once the winner settles. The ten-paper provider stress observed three simultaneous
calls, seven capacity denials, and all ten eventually approved and published.
The provider page and byte rejection paths both completed through explicit manual
review and publication, without making a rejected provider call.

The broad selector was run exactly as requested:
`pytest -q tests/integration -k "scan or checking or batch or manual or publication or remediation or operational or limit or concurrency"`.
It loaded the module before the final independent-connection race was added.
Its 14 errors occur before test bodies, while cloning the disposable source DB:
`asyncpg.exceptions.ObjectInUseError: source database "paper_operational_pr13_docker_test" is being accessed by other users`;
`DETAIL: There is 1 other session using the database.` The affected fixtures are
manual start races (3), batch transition races (2), PR13 races (7), and publication
races (2). Both final forward/reverse module runs use a separate disposable source
DB and do not have that pooled-connection interference. Full logs are preserved
locally in `local-backups/pr13-selector-results.txt`.
The same selector on the unmodified accepted baseline reproduces all seven
existing clone errors (manual 3, batch 2, publication 2), with the same one-open-
session `ObjectInUseError`. PR13 adds seven cases using that existing fixture,
which encounter the same setup interference in the broad run. All these old and
new paths pass in the isolated regression orders. Baseline results are in
`local-backups/pr13-baseline-selector-results.txt`. This broad-suite harness
limitation remains; the broad selector is not reported as fully green.

An additional baseline-only targeted diagnostic of the generic checking/provider
and image-solving database modules reported 23 passed and 1 failed:
`test_nonretryable_response_or_failure_has_one_attempt[value0-schema_invalid]`
violated existing `ck_model_runs_time` with `finished_at` earlier than `started_at`.
That generic provider code was not changed. The later full baseline selector had
zero test-body failures; its seven errors were the clone-setup errors above.

The latest Windows full-unit attempt (before the global field was added) reported
**1224 passed, 2 failed, 4 setup/teardown errors**. Exact bounded traces are in
`local-backups/pr13-windows-unit-failures.txt`; the full UTF-8 log is
`local-backups/pr13-5e9bf1cd1433.log`. Distinct failures were:

- `test_privacy_admin_rules_and_cas`, `tests/unit/test_classroom_notes.py:46`:
  `Failed: DID NOT RAISE <class 'app.application.classroom_notes.ClassroomNotesError'>`.
- `test_checking_repository_registers_complete_fk_graph_in_fresh_interpreter`,
  `tests/unit/test_model_registry.py:84`: child return code `3221227274`, empty
  stdout/stderr.
- Two oversized-byte parameter cases in `test_input_artifacts.py`: setup and
  teardown fail in `_pytest/runner.py:209` / `<frozen os>:720` when assigning the
  oversized `PYTEST_CURRENT_TEST` environment variable:
  `ValueError: the environment variable is longer than 32767 characters`.

A later Windows integration collection attempt also emitted
`Windows fatal exception: code 0x8007000e` in `platform.py::_wmi_query`, reached from
SQLAlchemy imports. It was stopped and replaced with Docker verification. No
unrelated production change was made to hide these failures; all 1231 Linux unit
cases pass. No NFC fixture failure was observed in the final Linux run.

Ruff on all 16 changed Python files, compileall on `app`, `alembic` and `tests`,
`git diff --check`, and `docker compose config --quiet` pass. Alembic reports only
`20261011_03 (head)`. A fresh disposable database was upgraded from empty to that
head successfully. There is no PR13 migration ID/down revision or PR13 migration
roundtrip because no migration was introduced.

## Local continuation verification (2026-10-12)

The continuation found the implementation already committed as
`c080f5eb7168e8b5494c2963a8e806591a12027e`; no production changes were required.
Fresh Docker verification of that exact implementation reported:

- Dedicated PostgreSQL PR13: **27 passed**.
- Full Linux units: **1231 passed**, zero failures or errors.
- PR13/PR7/PR10/PR12, scan intake/grouping and image-solving regression
  modules: **201 passed in each order**. Input-artifact database tests ran
  separately: **4 passed**. The earlier eight-module orders above included
  these four cases in their 205-test totals.
- Exact requested broad selector: **453 passed, 4 skipped, 253 deselected,
  0 failed, 15 errors**. All errors are the documented source-database clone
  conflict during fixture setup. The final independent-global-slot race adds
  the fifteenth setup error compared with the earlier broad run. That case
  passes in the dedicated and both regression-order runs.
- Ruff on all changed Python files (run from `backend`), compileall,
  baseline-to-HEAD diff whitespace checking, Compose config validation and
  Alembic heads pass; the sole head remains **20261011_03**.

Fresh local logs are `local-backups/pr13-reverification-selector.txt`,
`local-backups/pr13-reverification-forward.txt`, and
`local-backups/pr13-reverification-reverse.txt`. The Windows issues and prior
fresh-database upgrade evidence above remain unchanged. The broad selector
is still not fully green; no unrelated production change was made to hide
the harness errors. No push or merge was performed.

## Defects addressed

- Unbounded aggregate uploads and partial page persistence are replaced by
  locked authoritative counts and atomic per-artifact persistence.
- Grouping and provider materialization now reject excess inputs before partial
  papers, binary reads, provider calls or provider attempts.
- Concurrent matching requests previously could trust stale preloaded ORM state
  after acquiring a lock; batch/run/chunk rows now refresh after locking.
  Intake and grouping locked reads also refresh cached aggregate/revision state.
- Paper claims now enforce per-batch and global capacity durably; duplicate calls
  preserve the original in-progress code instead of being mistaken for capacity.
- Expired paper attempts previously had no bounded recovery/fencing path. A later
  explicit execution replaces the stale identity without consuming retries, and
  late owners cannot persist a result.
- Provider finalization now uses the same aggregate lock order as batch/manual
  mutations, preserving concurrent reconciliation and completion semantics.
- The extraction rollback path initially touched expired ORM attributes and
  raised `MissingGreenlet`; stable IDs captured before rollback now drive cleanup
  and failure persistence. Rejection/cleanup tests cover this path.

No remote push, merge, PR creation, queue system or PR14 work is included.

## Changed files

- `.env.example`
- `docker-compose.yml`
- `backend/app/application/paper_operational_limits.py`
- `backend/app/application/paper_checking_execution.py`
- `backend/app/application/scan_matching_service.py`
- `backend/app/config.py`
- `backend/app/infrastructure/artifact_storage.py`
- `backend/app/infrastructure/paper_operational_guards.py`
- `backend/app/infrastructure/paper_checking_execution_repository.py`
- `backend/app/infrastructure/scan_grouping_repository.py`
- `backend/app/infrastructure/scan_intake_repository.py`
- `backend/app/infrastructure/scan_matching_repository.py`
- `backend/app/infrastructure/scan_page_pipeline.py`
- `backend/app/infrastructure/scan_page_rasterizer.py`
- `backend/app/presentation/scan_intake_routes.py`
- `backend/app/presentation/scan_upload_form.py`
- `backend/tests/unit/test_paper_operational_limits.py`
- `backend/tests/integration/test_paper_operational_limits_database.py`
- `docs/pr13-paper-operational-safeguards.md`
