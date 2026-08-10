# Typed Methodology Foundation — verification and recovery

## Status and migration chain

Phase 4.1M is a Content Bank authoring foundation, not a checker. Historical
revision `20260810_02` has `down_revision = 20260810_01` and introduced the typed
answer and choice schema. PostgreSQL acceptance exposed two integrity gaps: weighted
rules had no authored correct/distractor role, and deleting the last membership
could bypass the original non-empty-set trigger. Its schema semantics remain
immutable; only the narrowly proven asyncpg execution packaging in `20260810_02`
was repaired by splitting top-level commands. Corrective revision `20260810_03` has
`down_revision = 20260810_02`; it adds `choice_option_rules.role` and deferred
version-wide integrity triggers. Revision `20260810_04` has
`down_revision = 20260810_03` and renames the generated scoring-policy unique
constraint to the ORM-canonical `uq_choice_scoring_policy_version` without rewriting
data or replacing its backing index. The expected head is therefore `20260810_04`.

## Asyncpg execution compatibility repair

A PostgreSQL 17 lifecycle run proved that asyncpg rejects a prepared statement
containing multiple completed top-level SQL commands. Historical revision
`20260810_02` previously sent two `CREATE FUNCTION` and two `CREATE TRIGGER`
commands in one `op.execute()`. Revision `20260810_03` likewise combined two
`CREATE FUNCTION` commands and one `DO` block. Each command is now issued by its
own Alembic execution call; PL/pgSQL semicolons remain inside their single
`$$ ... $$` function or `DO` body. Revision identity, DDL names, ordering,
transactional behavior, constraints, deferred triggers, and downgrade schema are
unchanged. A source-level regression parses every `execute` call and distinguishes
top-level separators from semicolons inside dollar-quoted bodies.

## Constraint-name parity correction

PostgreSQL created the column-level `unique=True` constraint from `20260810_02` as
`choice_scoring_policies_task_version_id_key`, while ORM metadata names the identical
constraint `uq_choice_scoring_policy_version`. Revision `20260810_04` performs one
transactional `ALTER TABLE ... RENAME CONSTRAINT` in each direction. It neither drops
uniqueness nor creates a second constraint, rewrites no rows, and retains the backing
unique index. At head, `pg_constraint` must expose exactly one unique constraint on
`choice_scoring_policies(task_version_id)`, named
`uq_choice_scoring_policy_version`; downgrade to `20260810_03` must restore the old
implicit name and preserve duplicate rejection.

## Schema inventory and invariants

| Area | Columns/tables | Database enforcement |
|---|---|---|
| Typed accepted answer | `accepted_answers.value_kind`, `canonical_text`, `canonical_decimal`, `absolute_tolerance`, `relative_tolerance`, `unit_code`, `normalization_policy_code`, `normalization_policy_version` | kind/shape check; finite, nonnegative tolerances; safe unit-code regex; policy code/version pair; allowlisted V1 typed shapes |
| Legacy compatibility | `answer_value`, `tolerance`, `unit`, `normalization_rule` remain | additive default/backfill is `legacy_untyped`; migration never parses or rewrites legacy values |
| Catalogue | `choice_options(id, task_version_id, option_key, content, order_index)` | bounded stable key, nonblank content, nonnegative order, unique version/key and version/order |
| Accepted sets | `accepted_answer_options` | composite PK prevents duplicates; composite FKs on `(id, task_version_id)` prevent cross-version membership; deferred triggers prevent empty sets, including deletion of the last member |
| Scoring | `choice_scoring_policies`, `choice_option_rules(role, weight)` | policy and option rule composite FKs share a version; V1 mode/version checks; deferred validation requires multiple-choice for weighted mode, complete roles, positive correct weights summing exactly `1.000000`, and negative distractor penalties |

The accepted-answer response exposes both authoring `option_keys` and canonical
`option_ids`. Option IDs, not labels or keys, are future Student Answer/Checking
identities. Multiple accepted-answer rows are OR alternatives.

## Payload examples

Legacy payload remains valid and opaque; no free-text rule is executed:

```json
{"expected_solution":null,"rubric":null,"accepted_answers":[{"answer_value":" 42 ","tolerance":"0.1","unit":" kg ","normalization_rule":"author note"}],"typical_errors":[],"hints":[]}
```

Typed decimal uses JSON strings for lossless Decimal input. Output is plain decimal
notation, `-0` is `0`, and omitted typed tolerances are persisted as zero:

```json
{"answer_value":"1e-21","value_kind":"decimal","canonical_decimal":"1e-21","absolute_tolerance":"0","relative_tolerance":"0","normalization_policy_code":"decimal_v1","normalization_policy_version":1}
```

Choice authoring and weighted policy:

```json
{
  "choice_options":[
    {"option_key":"a","content":"First","order_index":0},
    {"option_key":"x","content":"Distractor","order_index":1}
  ],
  "accepted_answers":[{"answer_value":"display only","value_kind":"choice_set","option_keys":["a"]}],
  "choice_scoring_policy":{"mode":"per_option","policy_version":1,"option_rules":[
    {"option_key":"a","role":"correct","weight":"1.000000"},
    {"option_key":"x","role":"distractor","weight":"-0.250000"}
  ]},
  "expected_solution":null,"rubric":null,"typical_errors":[],"hints":[]
}
```

A response catalogue item is
`{"id":"<canonical UUID>","option_key":"a","content":"First","order_index":0}`;
the accepted answer contains matching `option_keys` and `option_ids`.

## Automation readiness v1

`automation_readiness` is analysis of authored methodology only. It does not route a
student answer, create a run, call a provider, or change review/approval state.
Approved legacy methodology may legitimately report `ready=false`.

| Format | Candidate | Minimum ready methodology | Principal non-ready reasons |
|---|---|---|---|
| `short_text` | `exact` | typed text + `exact_text_v1` | `legacy_untyped_answer`, `missing_typed_accepted_answer` |
| `number` | `numeric` | typed Decimal, finite nonnegative tolerances, `decimal_v1`, no unsupported unit | `invalid_numeric_tolerance`, `unsupported_unit` |
| `single_choice` / `multiple_choice` | `multiple_choice` | catalogue, relational non-empty accepted sets, explicit scoring policy | `missing_choice_options`, `unknown_choice_option`, `missing_choice_scoring_policy`, `invalid_weighted_policy` |
| `expression` | `structured_expression` | canonical text + `expression_identity_v1` | `missing_typed_accepted_answer` |
| `long_text` | `llm_rubric` | expected solution and non-empty points rubric | `insufficient_rubric`, `unsupported_exact_long_text` |
| unsupported/manual capability | `manual_required` | no automatic path | versioned field-specific issues explain why |

The readiness contract is `methodology_readiness_v1`; every result includes `ready`,
`checker_candidate`, `reason_codes`, and field-specific `issues`.

## Replacement and cloning semantics

Methodology validation occurs before replacement. Repository writes run in the same
unit-of-work transaction, so unknown keys, invalid roles/weights, or database
constraint failures roll back options, answers, memberships, policies, and rules
together. Only the latest draft is mutable; review and approved versions remain
read-only. Cloning assigns new option/answer/policy UUIDs, preserves option
key/content/order and all typed and legacy fields, and rewires memberships and rules
only to cloned option IDs. The approved historical source remains readable.

## Gap-audit and acceptance matrix

| Requirement | Implemented | Tested | Gap/fix |
|---|---:|---:|---|
| Typed kinds/canonical fields/tolerances/unit/policies | yes | unit + collected PostgreSQL | finite/shape and allowlist validation completed |
| Legacy byte preservation/backfill | yes | collected PostgreSQL + lifecycle gate | never reinterpret legacy truth |
| Catalogue and relational sets | yes | unit + collected PostgreSQL | canonical IDs added to response |
| Cross-version protection | yes | collected direct-SQL PostgreSQL | composite FKs, not application-only |
| Weighted policy and roles | yes | unit + collected PostgreSQL | corrective `20260810_03` adds roles and deferred integrity |
| Scoring-policy constraint name parity | yes | source + collected PostgreSQL | `20260810_04` renames the existing constraint/index identity without data rewrite |
| Non-empty set after member deletion | yes | collected PostgreSQL | corrective deferred version validator |
| Atomic replacement/status immutability | yes | collected API PostgreSQL | transaction and latest-draft lock |
| Version cloning | yes | collected PostgreSQL | rekeys and rewires every relation |
| Automation readiness | yes | 38 new unit cases | `methodology_readiness_v1` matrix implemented |
| Response/frontend round-trip | yes | unit/frontend/build | frontend carries typed authoring data; no new UI |

## Migration lifecycle on a disposable database

Never run the downgrade against the development database. The local gate below:
upgrades to `20260810_01`, inserts representative legacy data, captures exact legacy
values, upgrades to head, checks backfill, runs focused tests, downgrades to
`20260810_01`, checks that legacy columns/data remain, and **always re-upgrades in a
`finally` block** before running the focused tests again plus `alembic check/current/heads`.

In the Codex environment used for this patch, Docker/PostgreSQL were unavailable.
Therefore PostgreSQL cases were collected, not claimed as passed. Unit tests,
frontend tests, compilation, and production build are separately reportable; the
PowerShell gate is locally required before Phase 4.1M acceptance.

## V1 limitations

No checker/router/intake, student-answer normalization, unit conversion, fuzzy text,
regex execution, `eval`, mathematical equivalence/CAS, provider/LLM invocation,
Teacher Review, or frontend authoring UI is introduced. A canonical unit is authored
metadata and yields manual readiness until input-unit support exists.

## PowerShell constraint-name continuation from `20260810_03`

The existing disposable database that completed the previous lifecycle can continue
from `20260810_03`: upgrade to head, inspect `pg_constraint`, test duplicate rejection,
downgrade and inspect the restored implicit name, then always recover to head in a
`finally` block before `alembic check/current/heads` and the focused PostgreSQL suite.
The complete command block is included in the corrective report; the broader fresh
`20260810_01` lifecycle remains below for full recovery.

## PowerShell continuation and Docker Hub TLS recovery

```powershell
$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
$dbName = 'ai_school_test'
$dbUser = $env:POSTGRES_USER
$dbPassword = $env:POSTGRES_PASSWORD
if (-not $dbUser) { $dbUser = 'content_bank' }
if (-not $dbPassword) { $dbPassword = 'change-me-for-local-development' }
$db = "postgresql+asyncpg://${dbUser}:${dbPassword}@postgres:5432/${dbName}"
$exists = docker compose exec -T postgres psql -U $dbUser -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$dbName'"
if ($exists -ne '1') { docker compose exec -T postgres createdb -U $dbUser $dbName }
$built = $false
try { docker compose build backend; $built = $true }
catch { Write-Warning 'Build unavailable (for example Docker Hub TLS timeout); the backend container will not be recreated from an old image.' }
if ($built) { docker compose up -d --no-deps backend }
else {
  $cid = docker compose ps -q backend
  if (-not $cid) { throw 'No existing backend container; refusing an unproven fallback.' }
  docker cp "$repo/backend/app/." "${cid}:/app/app/"
  docker cp "$repo/backend/alembic/." "${cid}:/app/alembic/"
  docker cp "$repo/backend/tests/." "${cid}:/app/tests/"
}
$cid = docker compose ps -q backend
$hostHash = (Get-FileHash "$repo/backend/app/application/content_bank.py" -Algorithm SHA256).Hash.ToLower()
$containerHash = (docker compose exec -T backend sha256sum /app/app/application/content_bank.py).Split(' ')[0]
if ($hostHash -ne $containerHash) { throw 'Container backend content does not match workspace.' }
docker compose exec -T backend test -f /app/tests/integration/test_typed_methodology_database.py

docker compose exec -T -e DATABASE_URL=$db backend alembic downgrade 20260810_01
@'
INSERT INTO subjects(id,code,name) VALUES ('10000000-0000-4000-8000-000000000001','lifecycle','Lifecycle');
INSERT INTO grades(id,number,name) VALUES ('10000000-0000-4000-8000-000000000002',7,'7');
INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES ('10000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','life','Lifecycle');
INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES ('10000000-0000-4000-8000-000000000004','10000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000005');
INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES ('10000000-0000-4000-8000-000000000006','10000000-0000-4000-8000-000000000004',1,'Legacy','calculation','number',10,'draft','10000000-0000-4000-8000-000000000005');
INSERT INTO accepted_answers(id,task_version_id,answer_value,tolerance,unit,normalization_rule) VALUES ('10000000-0000-4000-8000-000000000007','10000000-0000-4000-8000-000000000006',E'  A\r\nB  ',0.125,' kg ','opaque (.*)');
'@ | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName
try {
  docker compose exec -T -e DATABASE_URL=$db backend alembic upgrade head
  $upgraded = docker compose exec -T postgres psql -U $dbUser -d $dbName -Atc "SELECT value_kind||'|'||encode(convert_to(answer_value,'UTF8'),'hex')||'|'||tolerance::text||'|'||unit||'|'||normalization_rule FROM accepted_answers WHERE id='10000000-0000-4000-8000-000000000007'"
  if ($upgraded -ne 'legacy_untyped|2020410d0a422020|0.125| kg |opaque (.*)') { throw 'Legacy upgrade/backfill proof failed.' }
  docker compose exec -T -e TEST_DATABASE_URL=$db -e DATABASE_URL=$db backend pytest -q tests/integration/test_typed_methodology_database.py
  # Reinsert a downgrade sentinel after destructive focused fixtures.
  @'
INSERT INTO accepted_answers(id,task_version_id,answer_value,tolerance,unit,normalization_rule) SELECT '10000000-0000-4000-8000-000000000017',id,E'  D\r\nE  ',0.250,' m ','opaque downgrade' FROM task_versions LIMIT 1;
'@ | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName
  docker compose exec -T -e DATABASE_URL=$db backend alembic downgrade 20260810_01
  $down = docker compose exec -T postgres psql -U $dbUser -d $dbName -Atc "SELECT encode(convert_to(answer_value,'UTF8'),'hex')||'|'||tolerance::text||'|'||unit||'|'||normalization_rule FROM accepted_answers WHERE id='10000000-0000-4000-8000-000000000017'"
  if ($down -ne '2020440d0a452020|0.250| m |opaque downgrade') { throw 'Legacy downgrade preservation proof failed.' }
} finally {
  docker compose exec -T -e DATABASE_URL=$db backend alembic upgrade head
}
docker compose exec -T -e TEST_DATABASE_URL=$db -e DATABASE_URL=$db backend pytest -q tests/integration/test_typed_methodology_database.py
docker compose exec -T -e TEST_DATABASE_URL=$db -e DATABASE_URL=$db backend pytest -q tests/unit/test_typed_methodology.py tests/unit/test_save_methodology.py tests/unit/test_status_cycle.py
docker compose exec -T -e TEST_DATABASE_URL=$db -e DATABASE_URL=$db backend pytest -q tests/integration/test_checking_database.py tests/integration/test_checking_handoff.py tests/integration/test_phase3_vertical_acceptance.py
docker compose exec -T -e TEST_DATABASE_URL=$db -e DATABASE_URL=$db backend pytest -q
docker compose exec -T -e DATABASE_URL=$db backend sh -lc 'alembic check && alembic current && alembic heads'
npm --prefix frontend test -- --run
npm --prefix frontend run build
```
