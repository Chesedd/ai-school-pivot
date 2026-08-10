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
unique index. At head, `pg_constraint` must expose exactly one single-column
unique constraint on `choice_scoring_policies(task_version_id)`, named
`uq_choice_scoring_policy_version`, while retaining the intentional composite unique
constraint `uq_choice_scoring_policy_id_version` on `(id, task_version_id)`.
Downgrade to `20260810_03` must restore only the old implicit single-column name and
must preserve the composite constraint and duplicate rejection.

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

This gate uses only the existing disposable `ai_school_41m_gate_test` database. The
catalog query expands `pg_constraint.conkey` with ordinality, so assertions compare
ordered column sets rather than unspecified result order or all UNIQUE constraints
on the table. It proves the canonical single-column constraint and intentional
composite constraint independently in both migration states, preserves representative
data, checks duplicate rejection, and always recovers to `20260810_04`.

```powershell
$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
$dbName = 'ai_school_41m_gate_test'
$dbUser = $env:POSTGRES_USER
$dbPassword = $env:POSTGRES_PASSWORD
if (-not $dbUser) { $dbUser = 'content_bank' }
if (-not $dbPassword) { $dbPassword = 'change-me-for-local-development' }
if (-not $dbName.EndsWith('_test')) { throw 'Refusing a non-test database.' }
$db = "postgresql+asyncpg://${dbUser}:${dbPassword}@postgres:5432/${dbName}"

$built = $false
try { docker compose build backend; $built = $true }
catch { Write-Warning 'Build unavailable; refusing to recreate backend from a stale image.' }
if ($built) { docker compose up -d --no-deps backend }
else {
  $cid = docker compose ps -q backend
  if (-not $cid) { throw 'No running backend container for the verified docker-cp fallback.' }
  docker cp "$repo/backend/alembic/versions/20260810_04_typed_methodology_constraint_name.py" "${cid}:/app/alembic/versions/20260810_04_typed_methodology_constraint_name.py"
  docker cp "$repo/backend/tests/unit/test_typed_methodology_migration_sql.py" "${cid}:/app/tests/unit/test_typed_methodology_migration_sql.py"
  docker cp "$repo/backend/tests/integration/test_typed_methodology_database.py" "${cid}:/app/tests/integration/test_typed_methodology_database.py"
}
$cid = docker compose ps -q backend
$hostMigrationHash = (Get-FileHash "$repo/backend/alembic/versions/20260810_04_typed_methodology_constraint_name.py" -Algorithm SHA256).Hash.ToLower()
$containerMigrationHash = (docker compose exec -T backend sha256sum /app/alembic/versions/20260810_04_typed_methodology_constraint_name.py).Split(' ')[0]
if ($hostMigrationHash -ne $containerMigrationHash) { throw 'Container migration differs from the workspace.' }
$hostHash = (Get-FileHash "$repo/backend/tests/integration/test_typed_methodology_database.py" -Algorithm SHA256).Hash.ToLower()
$containerHash = (docker compose exec -T backend sha256sum /app/tests/integration/test_typed_methodology_database.py).Split(' ')[0]
if ($hostHash -ne $containerHash) { throw 'Container test differs from the workspace.' }

$current = docker compose exec -T -e DATABASE_URL=$db backend alembic current
if ($current -notmatch '20260810_03') { throw "Expected 20260810_03, got: $current" }

$policy = docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -Atc @'
WITH existing AS (
  SELECT id,task_version_id,mode,policy_version FROM choice_scoring_policies ORDER BY id LIMIT 1
), inserted AS (
  INSERT INTO choice_scoring_policies(task_version_id,mode,policy_version)
  SELECT v.id,'all_or_nothing',1 FROM task_versions v
  WHERE NOT EXISTS (SELECT 1 FROM existing)
  ORDER BY v.id LIMIT 1
  RETURNING id,task_version_id,mode,policy_version
)
SELECT id::text||'|'||task_version_id::text||'|'||mode||'|'||policy_version::text FROM existing
UNION ALL
SELECT id::text||'|'||task_version_id::text||'|'||mode||'|'||policy_version::text FROM inserted;
'@
if (-not $policy) { throw 'No representative policy and no task version available.' }
$policyParts = $policy.Split('|')
$policyId = $policyParts[0]
$taskVersionId = $policyParts[1]

$constraintSql = @'
SELECT c.conname||'|'||string_agg(a.attname,',' ORDER BY k.ordinality)
FROM pg_constraint c
JOIN pg_class r ON r.oid=c.conrelid
JOIN pg_namespace n ON n.oid=r.relnamespace
CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum,ordinality)
JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum
WHERE n.nspname=current_schema() AND r.relname='choice_scoring_policies' AND c.contype='u'
GROUP BY c.conname
ORDER BY c.conname;
'@
function Get-PolicyConstraints {
  return @(docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -Atc $constraintSql)
}
function Assert-ConstraintState([string[]]$rows,[string]$singleName) {
  $single = @($rows | Where-Object { $_ -eq "$singleName|task_version_id" })
  if ($single.Count -ne 1) { throw "Expected exactly one $singleName single-column constraint: $rows" }
  if (-not ($rows -contains 'uq_choice_scoring_policy_id_version|id,task_version_id')) { throw "Composite constraint missing: $rows" }
  $otherName = if ($singleName -eq 'uq_choice_scoring_policy_version') { 'choice_scoring_policies_task_version_id_key' } else { 'uq_choice_scoring_policy_version' }
  if ($rows | Where-Object { $_ -like "$otherName|*" }) { throw "Unexpected alternate single-column name: $rows" }
}
function Assert-DuplicateRejected {
  $sql = "DO `$`$ BEGIN BEGIN INSERT INTO choice_scoring_policies(task_version_id,mode,policy_version) VALUES ('$taskVersionId','all_or_nothing',1); RAISE EXCEPTION 'duplicate accepted'; EXCEPTION WHEN unique_violation THEN NULL; END; END `$`$;"
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -c $sql
}

try {
  docker compose exec -T -e DATABASE_URL=$db backend alembic upgrade 20260810_04
  Assert-ConstraintState (Get-PolicyConstraints) 'uq_choice_scoring_policy_version'
  $after = docker compose exec -T postgres psql -U $dbUser -d $dbName -Atc "SELECT id::text||'|'||task_version_id::text||'|'||mode||'|'||policy_version::text FROM choice_scoring_policies WHERE id='$policyId'"
  if ($after -ne $policy) { throw 'Representative policy changed during upgrade.' }
  Assert-DuplicateRejected

  docker compose exec -T -e DATABASE_URL=$db backend alembic downgrade 20260810_03
  Assert-ConstraintState (Get-PolicyConstraints) 'choice_scoring_policies_task_version_id_key'
  Assert-DuplicateRejected
}
finally {
  docker compose exec -T -e DATABASE_URL=$db backend alembic upgrade 20260810_04
}

Assert-ConstraintState (Get-PolicyConstraints) 'uq_choice_scoring_policy_version'
docker compose exec -T -e DATABASE_URL=$db backend alembic check
$finalCurrent = docker compose exec -T -e DATABASE_URL=$db backend alembic current
$finalHeads = docker compose exec -T -e DATABASE_URL=$db backend alembic heads
if ($finalCurrent -notmatch '20260810_04') { throw "Unexpected current: $finalCurrent" }
if ($finalHeads -notmatch '20260810_04') { throw "Unexpected heads: $finalHeads" }
docker compose exec -T -e TEST_DATABASE_URL=$db -e DATABASE_URL=$db backend pytest -q tests/integration/test_typed_methodology_database.py
```
