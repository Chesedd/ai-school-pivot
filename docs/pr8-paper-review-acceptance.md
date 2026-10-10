# PR8 — Teacher review revisions and deterministic paper grading

Implemented on `codex/teacher-review-grading`. The final commit SHA is reported in
the accompanying completion message. This change ends at an immutable approved
teacher revision. It adds no publication, student access, notifications, frontend,
provider calls, rechecking, or batch completion.

## Exact files changed

- `backend/app/application/paper_checking_review.py`
- `backend/app/application/scan_grading_policy.py`
- `backend/app/infrastructure/paper_checking_review_models.py`
- `backend/app/infrastructure/paper_checking_review_repository.py`
- `backend/app/infrastructure/checking_models.py`
- `backend/app/infrastructure/model_registry.py`
- `backend/app/presentation/scan_intake_routes.py`
- `backend/alembic/versions/20261010_01_paper_checking_review.py`
- `backend/tests/unit/test_paper_checking_review.py`
- `backend/tests/integration/test_paper_checking_review_database.py`
- `backend/tests/integration/test_paper_checking_review_routes.py`
- `backend/tests/integration/test_paper_checking_review_migration.py`
- `backend/tests/integration/test_paper_checking_execution_database.py`
- `docs/pr8-paper-review-acceptance.md`

The existing PR7 schema test now resolves the actual repository head. Its execution
architecture and evidence behavior are unchanged. The AI model mapping adds a
composite uniqueness constraint used to enforce review provenance with a foreign
key; no AI evidence row is rewritten.

## Persistence and revisions

Migration `20261010_01` has `down_revision = 20261005_01`.

`paper_check_reviews` stores `id`, `paper_submission_id`, `check_run_id`,
`ai_revision_id`, `state`, `draft`, `version`, `latest_approved_revision_id`,
`created_by_user_id`, `updated_by_user_id`, `created_at`, and `updated_at`.
It permits one workspace per CheckRun, positive versions, and only `reviewing`
or `approved`. Composite foreign keys bind its AI/paper/run provenance and bind
the approval pointer to the same review. A guard requires an approved target
revision and protects workspace provenance.

`paper_check_review_revisions` stores `id`, `review_id`, `paper_submission_id`,
`check_run_id`, `ai_revision_id`, `revision_number`, `revision_kind`, `snapshot`,
`actor_user_id`, `grading_policy_revision`, `grading_policy_fingerprint`,
`change_summary`, `calculated_total_score`, `total_max_score`,
`calculated_percentage`, `calculated_grade`, `final_grade`,
`grade_override_reason`, `approved_at`, and `created_at`.

Revision 0 is `ai_seed`; subsequent meaningful edits create `human_edit`
revisions. Approval creates a separate new `approved` revision. Review/revision
number is unique. PostgreSQL rejects UPDATE and DELETE with the existing generic
immutability function. SQL constraints enforce revision kinds, positive policy
provenance, approval grade-field completeness, score and percentage ranges,
bounded labels, and override consistency. Totals use unconstrained Numeric to
preserve accepted Decimal precision; display percentages use Numeric(7, 4).

## Human review behavior

Start serializes initialization on the exact CheckRun and is idempotent. It
requires a completed PR7 paper execution, a PaperAIRevision and complete canonical
results/findings/regions consistent with the validated response and model attempt.
The seed copies the AI summary and ordinary correct/partial/incorrect score
suggestions. `not_attempted`, `unreadable`, and `insufficient_evidence` remain NULL
until explicitly resolved by a teacher. Findings are copied with stable UUIDs,
semantic regions and source CheckFinding lineage.

Edits replace the complete bounded draft. They lock the workspace, validate the
expected version, and use a compare-and-swap UPDATE. Stale edits and approvals
return `paper_review_version_conflict` (409). Revision numbering follows accepted
workspace versions. Items are canonicalized by frozen input position and ID;
findings are canonicalized by finding UUID. Identical canonical replacements do
not persist meaningless edits. Change summaries contain only bounded technical
domains: summary, item score/status, findings, and approval override intent.

Teachers can retain, edit or remove copied findings and add findings whose source
is NULL. Each edit checks page membership in the exact paper, finding lineage in
the exact AI result, unique identities, bounded text/severity/category, normalized
geometry and `normalized_upright_v1`. The original CheckFinding and region remain
untouched, including when the human snapshot removes an AI finding.

After approval, a later edit sets state back to `reviewing` and retains the
previous approved revision pointer. Reapproval creates another immutable approved
revision and advances the pointer. Earlier approvals stay immutable and queryable,
so a future publication can reference an exact revision independently.

## Approval and grading

Approval holds the review lock, checks the expected version, loads the exact
immutable policy revision from the CheckRun snapshot, verifies revision,
fingerprint, variant, item coverage and maxima against PR6/PR7 provenance, validates
the complete draft, calculates grades, inserts the approved revision and advances
the workspace in one transaction. It never consults mutable assessment maxima or
a current/later policy. Missing or inconsistent policy provenance fails closed
with `paper_review_policy_mismatch`. A failed approval leaves the workspace and
previous approval pointer unchanged and persists no partial result.

| Approved status | Required explicit accepted score |
| --- | --- |
| correct | exactly the frozen maximum |
| partially_correct | strictly between zero and the frozen maximum |
| incorrect | exactly zero |
| not_attempted | exactly zero, explicitly accepted by the teacher |
| unreadable / insufficient_evidence | must be changed to a resolvable status |

All scores are finite Decimal values within the frozen maxima. Approval requires
exact item coverage. It constructs `AcceptedItemScore` values and calls the
existing `calculate_total()` and `calculate_grade()` functions. Neither the
client nor AI supplies an authoritative total or calculated grade.

`calculate_percentage()` produces display metadata using Decimal and four decimal
places with ROUND_HALF_UP, isolated from a caller's ambient precision. It does
not change PAPER_GRADING_POLICY_VERSION or the existing grade-selection semantics.
Tests cover a display percentage rounding to 70.0000 while the exact ratio remains
below a 70-percent grade threshold; the calculated grade stays below that threshold.

An optional approval `grade_override` contains only `override_grade` and `reason`.
The application computes the calculated grade first, then uses
`GradeOverrideCommand`. Labels are bounded strings, not a hardcoded numeric scale.
The reason must be non-empty, trimmed and at most 2,000 characters. The approved
revision preserves the calculated grade and the override reason; without an
override, final grade equals calculated grade.

The five existing-scan-style teacher routes implement start/get/PUT edit/history/
POST approve below `/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review`.
They retain scan-management capability and class/batch authorization. Any current
authorized class teacher can act; unrelated teachers and students are denied.
HTTP edits validate strict UUID/Decimal contracts in JSON mode. The response
includes the current workspace and exact latest approved snapshot/grade result,
without raw provider output, credentials, storage references or the original PDF.

## Verification

Real PostgreSQL + asyncpg verification uses the separate local database
`paper_review_pr8_test`, with existing local credentials. No development database
was migrated. Paper execution/review/route fixtures use rollback-isolated outer
transactions and savepoint-bound sessions, including the actual PR7 intake and
execution path with a fake provider.

| Check | Result |
| --- | --- |
| PR8 review unit module | 68 passed |
| Requested review + execution + contracts focused units | 94 passed |
| Review + execution + contracts + frozen grading focused units | 99 passed |
| Final full units with temporary Windows-safe byte parameter IDs | 1,086 passed, 1 failed |
| PR7 → PR8 PostgreSQL modules | 89 passed |
| PR8 → PR7 PostgreSQL modules | 89 passed |
| PR8 database module | 48 tests, all passed in both order runs |
| Review routes | 16 passed, including the final broad integration run |
| Dedicated migration process gate | 1 passed |
| Checking/scan integration regression | 238 passed, 286 deselected |
| Ruff: all 13 changed Python files | passed |
| `python -m compileall -q app alembic/versions tests` | passed |
| `git diff --check` | passed |
| `alembic heads` | single head `20261010_01` |

The migration gate verified
`20261005_01 -> 20261010_01 -> 20261005_01 -> 20261010_01`, checking the actual
database revision at each step. It also rebuilt the disposable database's public
schema and verified a fresh `alembic upgrade head`. Every op.execute issues one
top-level SQL command; downgrade passed through asyncpg.

Database evidence snapshots before and after review operations compare all rows
of PaperAIRevision, CheckResult, CheckFinding, PaperCheckFindingRegion, ModelRun
and CheckRun (including input snapshot). They remain equal. Tests separately prove
CheckRun remains `completed_with_review_required`, PaperSubmission remains
`ready_for_checking`, the batch remains `checking`, and no paper publication
tables/effects are introduced. They cover earlier approved revision preservation,
failed approval atomicity, foreign page/finding rejection, teacher additions and
removals, competing reader versions, unique workspaces, immutable revisions and
SQL grade constraints.

The full unit suite is not green. The final failure is the pre-existing
`tests/unit/test_checking_handoff_fixture.py::test_checking_handoff_v1_fixture_schema_and_invariants`
Unicode NFC expectation. The existing PR7 local failure report records the same
failure. An earlier run also reproduced the existing intermittent
`test_classroom_notes.py::test_privacy_admin_rules_and_cas` failure; it did not
recur in the final run. Neither production module was changed by PR8.

An initial Windows run additionally hit the 32,767-character environment-variable
limit because two existing input-artifact tests derive IDs from very large byte
parameters. The final full run used a temporary local pytest plugin to shorten
byte parameter IDs without changing test inputs or assertions. The plugin and
local execution logs are not part of this commit.

No PR7 production architecture defect was discovered. During PR8 verification,
HTTP JSON-mode parsing and SQL NULL-safe override consistency were addressed;
real route tests and invalid approved-row insertion tests cover those paths.
Pre-existing local untracked files and backups are excluded from the commit.

Executed regression commands (from `backend`, with the disposable test URL):

```text
pytest -q tests/unit/test_paper_checking_review.py
pytest -q tests/unit/test_paper_checking_review.py tests/unit/test_paper_checking_execution.py tests/unit/test_scan_checking_contracts.py
pytest -q tests/unit/test_paper_checking_review.py tests/unit/test_paper_checking_execution.py tests/unit/test_scan_checking_contracts.py tests/unit/test_scan_grading_policy.py
pytest -q tests/unit -p pr8_pytest_ids --tb=short
pytest -q tests/integration/test_paper_checking_review_database.py tests/integration/test_paper_checking_review_routes.py
pytest -q tests/integration/test_paper_checking_execution_database.py tests/integration/test_paper_checking_review_database.py
pytest -q tests/integration/test_paper_checking_review_database.py tests/integration/test_paper_checking_execution_database.py
pytest -q tests/integration/test_paper_checking_review_migration.py
pytest -q tests/integration -k "checking or scan"
ruff check <all 13 changed Python files>
python -m compileall -q app alembic/versions tests
git diff --check
alembic heads
```

The process-level migration module rebuilds a disposable database schema. It
must run separately from any concurrently executing database fixture suite.
