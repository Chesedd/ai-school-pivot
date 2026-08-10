# Checking Engine v1 — DB Foundation verification

## Migration and owned schema

Revision `20260810_01` has `down_revision = 20260808_02`. It creates, in dependency
order, `check_runs`, `check_results`, `check_findings`, `checker_events`,
`prompt_versions`, `model_runs`, and `cost_events`, plus Checking-scoped enums.
Downgrade drops triggers/functions, tables in reverse order, and then enums. It
does not backfill submissions, mutate Assessment/Content Bank rows, copy student
identity, or attach triggers to pre-existing tables.

FKs are `ON DELETE RESTRICT ON UPDATE RESTRICT`: run → submission; result → run,
assessment item and task version; finding → result; event → run and optional
result/item; model attempt → run, item, prompt and optional result; cost → model
attempt; self-supersession → run. Rubric item, typical error, and skill IDs are
deliberately snapshot provenance without Content Bank FKs.

## Constraints, indexes, and states

The schema checks lowercase SHA-256 values, bounded/nonblank versions and text,
positive attempt/version/score fields, confidence and score ranges, review/failure
consistency, model output/error/timestamp consistency, currency and nonnegative
cost/usage. Result score is tied to result status. Run status is one of `pending`,
`running`, `completed`, `completed_with_review_required`, `failed_retryable`, or
`failed_terminal`. A retryable failure is terminal for that execution interval:
it has `started_at`, `finished_at`, and a failure code; its controlled retry
transition clears/re-establishes lifecycle fields when Phase 4.2 scheduling uses it.

Unique indexes enforce request-key idempotency, per-submission attempt numbers,
one active (`pending`/`running`) run, one result per run/item, model attempt number,
prompt identity, and cost pricing event. History, active, worker, supersession,
review, provenance, event, and provider lookups have explicit indexes.

## Application and transaction semantics

`CreateRunCommand` accepts a prebuilt synthetic snapshot and externally computed
request hash/fingerprint; it does no intake or canonicalization. The repository
locks the source submission, allocates `max(attempt_no)+1`, returns an existing row
for equal key/hash, and reports typed key/hash, active-run, missing-source, invalid,
or concurrency errors. Creation and its initial event use the caller's transaction.
Run CAS transition and transition event, result/findings/result event, model
finalization, and cost insertion likewise have no hidden commit. The application
validators prove snapshot item/task-version/frozen-points consistency, finding
provenance allowlists, model result run/item membership, and an allowlist for safe
event detail keys.

Database triggers reject direct UPDATE/DELETE of results, findings, events and
costs. Run identity/snapshot fields and deletion are rejected; prompt content is
immutable with one-way retirement; a model attempt permits only its first terminal
finalization. Repository APIs provide no result/finding/event/cost update or delete.

## PostgreSQL proofs

`tests/integration/test_checking_database.py` exercises catalog presence and PII
column absence, replay/conflicting hash, concurrent equal and different keys,
source RESTRICT, result FKs/uniqueness/score checks, immutable SQL guards, synthetic
failed model attempts, unique cost events, and prompt/model finalization guards.
Pure tests cover commands, transition matrix, frozen snapshot validation, finding
allowlists, model-result consistency, and event redaction. PostgreSQL tests must
run with `TEST_DATABASE_URL` naming a disposable database ending `_test`; a skip is
not acceptance.

Phase 4.1M still owns methodology evolution (typed accepted answers, tolerance,
units and choice catalogue). Phase 4.2 still owns real handoff/methodology intake,
snapshot construction and materialization. This foundation implements neither.

## Local PowerShell gate (repository root)

The block is restartable: database recreation is restricted to `ai_school_test`,
and re-upgrade is performed immediately after the lifecycle downgrade.

```powershell
$ErrorActionPreference = 'Stop'
git rev-parse HEAD
git status --short
if (-not (Test-Path .env)) { throw '.env is required; copy the repository example and use local credentials.' }
docker compose config
docker compose up -d postgres backend
docker compose ps

$Db = 'ai_school_test'
if (-not $Db.EndsWith('_test')) { throw 'Refusing a non-test database name.' }
$PgUser = docker compose exec -T postgres sh -lc 'printf %s "$POSTGRES_USER"'
$PgPassword = docker compose exec -T postgres sh -lc 'printf %s "$POSTGRES_PASSWORD"'
docker compose exec -T postgres psql -U $PgUser -d postgres -v ON_ERROR_STOP=1 -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$Db' AND pid<>pg_backend_pid();"
docker compose exec -T postgres dropdb -U $PgUser --if-exists $Db
docker compose exec -T postgres createdb -U $PgUser $Db
$env:TEST_DATABASE_URL = "postgresql+asyncpg://${PgUser}:${PgPassword}@postgres:5432/$Db"

docker compose exec -T -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic upgrade 20260808_02
docker compose exec -T -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic upgrade head
docker compose exec -T -e TEST_DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q tests/integration/test_checking_database.py
docker compose exec -T -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic downgrade 20260808_02
docker compose exec -T -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic upgrade head

docker compose exec -T backend python -m compileall -q app tests
docker compose exec -T backend pytest -q tests/unit/test_checking_persistence.py
docker compose exec -T -e TEST_DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q tests/integration/test_checking_database.py
docker compose exec -T -e TEST_DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q tests/integration/test_checking_handoff.py tests/integration/test_phase3_vertical_acceptance.py
docker compose exec -T -e TEST_DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q
$collect = docker compose exec -T -e TEST_DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q tests/integration/test_checking_database.py -rs
if ($collect -match 'skipped') { throw 'Skipped PostgreSQL Checking tests are not accepted.' }
docker compose exec -T -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic check
docker compose exec -T -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic current
docker compose exec -T -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic heads
git diff --check
git status --short
git diff --stat
git diff --name-only
```

After a failure, fix the cause and rerun from `docker compose up -d postgres
backend`; recreation remains confined to the suffix-checked disposable database.
