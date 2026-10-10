# PR 7 PostgreSQL acceptance

PR title remains: **Add paper vision checking execution and immutable AI results**.
No PR 8 orchestration or production changes are included.

## Dedicated module

`backend/tests/integration/test_paper_checking_execution_database.py` adds **41 PostgreSQL cases**.
Every case uses `rolled_back_connection()` and sessions bound to that connection with
`expire_on_commit=False` and `join_transaction_mode="create_savepoint"`. Repository
commits release savepoints; the outer transaction rolls back every inserted row.
Rejected statements use nested savepoints. There is no fixture TRUNCATE, global DELETE,
committed setup, private fixture engine, or global result-table count.

The fixture reuses grade 7 in `active`/`provisional` status without mutation, or inserts
it inside the outer rollback transaction. Other globally unique names use a UUID suffix.
Two participants, variants, confirmed papers and page memberships share one batch.
Policies are frozen and CheckRuns created through the PR 6 repositories/service.
Digital and remediation attempts exercise `CheckingRepository.claim_model_attempt`
and its guarded finalization path with real source rows.

`FilesystemArtifactStorage` stores an actual deterministic 68-byte, 1x1 PNG for each
paper in pytest's temporary directory. Size, SHA-256, artifact metadata, ScanPage
fingerprint and dimensions agree. Pillow verified its chunk integrity and decoded it.
The source is a deterministic two-page PDF with valid xref data, also stored through
that adapter and verified with PDFium. Negative cases insert separate immutable
artifacts, repoint ScanPages before intake, or corrupt the stored bytes after intake;
InputArtifact rows are never updated. Wrong MIME and metadata disagreement are rejected
at the existing PR 6 handoff boundary; corrupt bytes and non-PNG bytes fail execution.

The injected deterministic fake implements the actual `check(request, content)`
contract, captures requests and bytes, uses stable provider/model IDs, and returns
bounded telemetry. It supports success, transport failure, unknown item/page tokens,
invalid region and maximum-score mismatch. Anthropic is never called.

## Coverage and outcomes

All PostgreSQL outcomes below are **skipped here**, awaiting the user's local database.
The assertions remain real PostgreSQL/service acceptance assertions.

| Proof | Assertions retained |
| --- | --- |
| ModelRun XOR | Valid paper source persists; assessment+paper, remediation+paper, all three and no source reject with `ck_model_runs_execution_item_xor`. |
| Digital/remediation regression | Each existing repository source can claim and finalize an attempt with its sole source FK and null paper FK. |
| Attempt history | Duplicate identity rejects with `uq_model_runs_paper_attempt`; transport attempts persist as contiguous 1,2,3 with the same logical request fingerprint. |
| Successful execution | Pending/running/review-required transitions; one successful attempt, revision and per-item result; `paper_vision`, mandatory review, frozen policy provenance and telemetry. |
| Status persistence | Six distinct statuses; correct=max, incorrect=0, partial strictly between, and null scores for not-attempted/unreadable/insufficient-evidence. Invalid unreadable score rejects at the DB constraint. |
| Findings/regions | info/info, warning/minor, error/major; exact result, revision, paper/page membership, finding token, normalized coordinates and coordinate space. |
| Immutability | UPDATE and DELETE reject for revisions, regions, results and findings. Nullable ModelRun sources and run identity cannot change even alongside a legal terminal update; legitimate repository finalization succeeds. |
| Uniqueness/replay | Second revision rejects with `uq_paper_ai_revisions_run`; exact scoped IDs for revisions/results/findings/regions/successful attempts/cost events remain unchanged; provider is not called twice. |
| Invalid output atomicity | Four invalid modes leave no aggregate; one invalid attempt and `failed_terminal`; paper B stays pending, then succeeds independently without A's results/page provenance. |
| Retry budget | Exactly three failed transport attempts, no sleeps, `failed_retryable`, no aggregate and no fourth call on terminal replay. Fingerprint reuse across attempts is intentional; attempt number distinguishes them. |
| Batch boundary | One success, mixed failure/success and both papers succeeding leave the batch exactly `checking`; no publication or batch completion. |
| Privacy/raw bytes | Recursive key/value inspection, including embedded JSON strings, across provider request, snapshot, ModelRun metadata/output, revision and events; private identities/storage paths/source reference and PNG/base64 absent. |
| Migration schema | Actual head, enum labels and partial unique index checked against PostgreSQL catalogs. |

PR 7 currently creates no CostEvent in this execution path; replay asserts the exact
scoped cost-event set is unchanged without inventing a pricing contract.

## Checks run in Codex on 2026-10-10

No reachable PostgreSQL database was available; `TEST_DATABASE_URL` was unset.
No credentials were invented. No real DB or migration-cycle acceptance is claimed.

| Command, from backend | Result |
| --- | --- |
| `pytest -q tests/integration/test_paper_checking_execution_database.py` | 41 skipped |
| Intake then execution | 53 skipped |
| Execution then intake | 53 skipped |
| Digital provider then execution | 54 skipped |
| Execution then digital provider | 54 skipped |
| Grouping then execution | 50 skipped |
| `pytest -q tests/integration -k "checking or scan"` | 39 passed, 145 skipped, 140 deselected |
| `pytest -q tests/unit` | 1015 passed, 2 failed, 4 errors, 1 warning |
| `pytest -q tests/unit/test_paper_checking_execution.py` | 15 passed |
| Ruff, new module | Passed |
| Requested Ruff check across six production files plus new module | 33 findings, all in unchanged production files |
| `python -m compileall -q app alembic/versions tests` | Passed |
| `git diff --check` | Passed |
| `alembic heads` | Single head: `20261005_01` |

The unit run used the existing root `.env` DATABASE_URL only for configuration loading;
it did not supply TEST_DATABASE_URL or claim a database connection. Its two assertion
failures are `test_checking_handoff_v1_fixture_schema_and_invariants` (Unicode
normalization mismatch) and `test_privacy_admin_rules_and_cas` (stale CAS rejection
expected). The four errors are setup/teardown errors for two existing oversized
input-artifact parameter IDs: Windows rejects PYTEST_CURRENT_TEST values over 32767
characters. A focused rerun reproduced all six failure/error outcomes. These files
were not modified. Production Ruff findings include import/style rules and existing
broad-exception/argument-shadowing findings. No production defect was demonstrated
by the new PostgreSQL tests, since they could not run here.

## Local Docker Compose acceptance commands (PowerShell, repository root)

Set TEST_DATABASE_URL to the user's existing connection URL for a disposable `_test`
database reachable **inside the Compose backend network**. The runner deliberately
refuses an unset URL or any database name not ending in `_test`.

```powershell
function Invoke-Pr7Backend {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ToolArgs)
    if (-not $env:TEST_DATABASE_URL) { throw 'Set TEST_DATABASE_URL first' }
    $target = [Uri]($env:TEST_DATABASE_URL.Replace('postgresql+asyncpg://','postgresql://'))
    if (-not $target.AbsolutePath.TrimStart('/').EndsWith('_test')) { throw 'Disposable _test database required' }
    docker compose run --rm --no-deps -T -e "DATABASE_URL=$env:TEST_DATABASE_URL" -e "TEST_DATABASE_URL=$env:TEST_DATABASE_URL" backend @ToolArgs
    if ($LASTEXITCODE -ne 0) { throw "Acceptance command failed: $ToolArgs" }
}

docker compose build backend
if ($LASTEXITCODE -ne 0) { throw 'Backend build failed' }
docker compose up -d postgres
```

For a **fresh, empty** selected test database:

```powershell
Invoke-Pr7Backend alembic upgrade head
Invoke-Pr7Backend alembic current
Invoke-Pr7Backend python -c 'import asyncio,os; from sqlalchemy import text; from sqlalchemy.ext.asyncio import create_async_engine; exec("async def main():\n e=create_async_engine(os.environ[\"TEST_DATABASE_URL\"]);\n async with e.connect() as c:\n  value=await c.scalar(text(\"SELECT version_num FROM alembic_version\")); print(value); assert value==\"20261005_01\"\n await e.dispose()\n"); asyncio.run(main())'
```

For the roundtrip, select a **separate fresh, empty** `_test` database URL in
TEST_DATABASE_URL before starting. An upgrade to an ancestor cannot start from head.
Do not run destructive migration acceptance on a development or production database.

```powershell
Invoke-Pr7Backend alembic upgrade 20260923_01
Invoke-Pr7Backend alembic upgrade 20261005_01
Invoke-Pr7Backend alembic downgrade 20260923_01
Invoke-Pr7Backend alembic upgrade 20261005_01
Invoke-Pr7Backend alembic current
Invoke-Pr7Backend python -c 'import asyncio,os; from sqlalchemy import text; from sqlalchemy.ext.asyncio import create_async_engine; exec("async def main():\n e=create_async_engine(os.environ[\"TEST_DATABASE_URL\"]);\n async with e.connect() as c:\n  value=await c.scalar(text(\"SELECT version_num FROM alembic_version\")); print(value); assert value==\"20261005_01\"\n await e.dispose()\n"); asyncio.run(main())'
```

The inspected downgrade drops the new immutable tables, removes paper evidence under
explicit trigger management, restores the previous score/source constraints,
reconstructs both PostgreSQL enums and restores the model-run guard. This inspection
is not a PostgreSQL roundtrip result. Migration execution stays outside pytest fixtures.

After the final upgrade, run the required order matrix:

```powershell
Invoke-Pr7Backend pytest -q tests/integration/test_paper_checking_execution_database.py
Invoke-Pr7Backend pytest -q tests/integration/test_paper_checking_intake_database.py tests/integration/test_paper_checking_execution_database.py
Invoke-Pr7Backend pytest -q tests/integration/test_paper_checking_execution_database.py tests/integration/test_paper_checking_intake_database.py
Invoke-Pr7Backend pytest -q tests/integration/test_checking_provider_database.py tests/integration/test_paper_checking_execution_database.py
Invoke-Pr7Backend pytest -q tests/integration/test_paper_checking_execution_database.py tests/integration/test_checking_provider_database.py
Invoke-Pr7Backend pytest -q tests/integration/test_scan_grouping_database.py tests/integration/test_paper_checking_execution_database.py
Invoke-Pr7Backend pytest -q tests/integration -k 'checking or scan'
Invoke-Pr7Backend pytest -q tests/unit
Invoke-Pr7Backend python -m compileall -q app alembic/versions tests
Invoke-Pr7Backend alembic heads
git diff --check
```

Ruff is not installed by the current backend requirements. With Ruff available in the
backend environment, the requested command is:

```powershell
Invoke-Pr7Backend ruff check app/application/paper_checking_execution.py app/application/scan_checking_contracts.py app/application/checking.py app/infrastructure/paper_checking_execution_repository.py app/infrastructure/paper_checking_providers.py app/infrastructure/checking_models.py tests/integration/test_paper_checking_execution_database.py
```
