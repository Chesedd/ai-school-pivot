# PR11: published paper results to existing remediation

Branch: `codex/paper-remediation-integration`, based on accepted local `main`
`f961d1b07861e7a7d30af4a6e04140fe61f98a3f`. The final completion message records
the commit SHA. No push or merge.

## Persistence and source invariants

Migration **20261011_02**, down revision **20261011_01**, generalizes the existing
`remediation_plans`. Digital sources require `source_submission_id` and null paper
fields; paper sources require `source_paper_submission_id` and
`source_paper_publication_id`, with a null digital submission. The DB check
`ck_remediation_plans_source_xor` rejects all mixed/empty states. API `source_type`
is derived, without a persisted enum. Both sources retain assignment, participant,
CheckRun, student and class provenance.

`fk_remediation_plan_paper_publication` binds the exact publication to its paper.
The plan never references merely "current publication". Separate normalized
`remediation_plan_paper_item_signals` and `remediation_plan_paper_finding_signals`
store the motivating original assessment items and published finding IDs. Each has
plan/signal uniqueness and a composite FK to the same plan/publication. JSON
membership/eligibility is validated in the application. Assigned exercises remain
`RemediationPlanItem`; digital CheckFinding signals remain `RemediationPlanSignal`.

PR9 already supplies stable publication finding IDs, including teacher-added
findings. Its snapshot omits CheckFinding identity and taxonomy. No publication
redesign or new finding identifiers are required. Teacher-added findings are valid
contextual provenance; PR11 never invents a CheckFinding, skill or typical-error ID.

Historical rows remain digital without backfill. Downgrade checks for paper plans
before any DDL and refuses a lossy downgrade. Digital data survives the roundtrip.

## Teacher and student behavior

Paper endpoints use the accepted paper URL context under `/api/assessment-core`:

* `GET /paper-submissions/{paper_submission_id}/remediation-eligibility`
* `POST /paper-submissions/{paper_submission_id}/remediations/candidates/search`
* `POST /paper-submissions/{paper_submission_id}/remediations`

Creation accepts a creation key, exact publication ID, selected original assessment
item IDs, optional publication finding IDs, title, instructions, due date and normal
Content Bank exercises. Student/class/assignment/participant/CheckRun are derived
server-side; extra client identity fields are rejected. The existing digital create
contract still requires all original digital UUIDs. Its endpoint, source resolver,
creator/class authorization and review-ack rules remain compatible. Paper source
authorization uses the accepted scan class-access policy; another assigned class
teacher can create their own teacher-owned paper plan.

The fingerprint includes paper identity, exact publication and both signal lists.
Identical retries return the original plan; altered publication, signals or intent
conflicts. Historical identical retries after replacement return the original plan,
without rebuilding or assigning it. Changing publication requires a new plan/key.

Eligibility requires the active publication and a structurally valid snapshot from
that paper's exact approved revision and authoritative assigned variant. At least
one item must have `accepted_score < max_score`. Correct/full-credit items are
excluded; incorrect and not-attempted zero scores qualify. Final grade, grade
override and confidence have no influence. Foreign/duplicate items, unresolved
statuses, inconsistent scores/totals or broken provenance fail closed. No active
pointer, including historical-only publications, is ineligible.

Both candidate modes require a nonempty valid weak-item selection. Canonical
TaskVersions are resolved server-side from assessment items. Suggested candidates
use those tasks' skills, subject, grade and difficulty through the shared Content
Bank mechanism. Original assessment task versions are excluded across all variants;
manual query/difficulty/type filters remain available. Ordering is deterministic.
Published findings are provenance only: no taxonomy inference from teacher text and
no typical-error recommendation without structured provenance. The paper path
never selects all raw CheckFindings. The removed-finding regression rejects removed
AI findings in selection, recommendation and persistence.

Existing draft updates support paper item/finding signals and ordinary title,
instructions, due date and exercises. Source-signal updates and draft assignment
require the exact publication to remain active. Assignment revalidates weak items,
coherent plan provenance and available approved exercises. It needs no digital
review acknowledgement. Paper locks serialize creation, candidate search, source
updates and assignment with PR9 replacement. Replaced-publication drafts report
`remediation_source_publication_stale`; they never silently switch to a new source.
Approval of an unpublished revision has zero effect on paper remediation.

Assigned plans remain historical teacher intent. Republication does not mutate or
cancel them. Existing student list/detail, start, save, submit, Checking intake and
result projections are reused, including persisted deterministic Checking results. Unrelated students cannot execute a plan. Student
responses expose normal assigned exercises, without scans, provider output,
unpublished review or paper source provenance. No fake original-paper
StudentSubmission is created. The shared integration fixture includes one unrelated
pre-existing digital submission; the tests exclude that baseline when proving no
new digital source submission was fabricated. The complete grouping-to-remediation
end-to-end test removes that baseline first, proving a paper-only original source.

Remediation does not change, reopen or block batch lifecycle and remains available
after completion. AI completion, review approval, publication and batch completion
create zero remediation plans. Teacher creation remains explicit.

## Acceptance results

* Focused new units: **22 passed**.
* Dedicated paper PostgreSQL module: **28 passed** before the order matrix.
  Final strengthened grouping-to-remediation case: **1 passed** in 21.69 seconds,
  with the unrelated digital fixture row removed before paper grouping.
* Existing remediation: **51 passed**, all eight existing files (table below).
* Real PostgreSQL migration gate: **2 passed**. Digital rows survived
  `20261011_01 -> 20261011_02 -> 20261011_01 -> 20261011_02`; fresh schema upgraded
  to head; paper-source downgrade refusal preserved head and publication provenance.
* PR9 publication then PR11: **67 passed**, exit 0 and zero JUnit errors/failures.
* PR11 then PR9 publication: **67 passed**.
* PR10 batch orchestration then PR11: **71 passed**.
* PR11 then PR10 batch orchestration: **71 passed**.
* Existing digital database suites then PR11: **56 passed**.
* PR11 then existing digital database suites: **56 passed**.
* Relevant integration selection: **381 passed, 2 skipped, 255 deselected**, six warnings,
  in 1,498.33 seconds. The two migration-gate tests intentionally skip outside their
  separate dedicated migration database; they passed in the standalone gate.
* Full units: **1,145 passed, 2 failed**, one existing deprecation warning.
* Ruff: passed for all changed Python files.
* Compileall, diff check and Alembic heads: passed; single head **20261011_02**.

| Existing file under `backend` | Tests |
| --- | ---: |
| `tests/integration/test_remediation_database.py` | 10 |
| `tests/integration/test_remediation_execution_database.py` | 7 |
| `tests/integration/test_remediation_checking_database.py` | 4 |
| `tests/integration/test_remediation_results_database.py` | 7 |
| `tests/unit/test_remediation_checking.py` | 2 |
| `tests/unit/test_remediation_execution.py` | 7 |
| `tests/unit/test_remediation_presentation_security.py` | 3 |
| `tests/unit/test_remediation_results.py` | 11 |

The initial full unit run had four Windows setup/teardown errors from oversized
byte-valued pytest IDs exceeding the environment-variable length limit. The local
acceptance plugin hashes IDs longer than 1,024 characters, changing no test case,
input or assertion. The full rerun above has no such errors. The first combined
publication/remediation run also printed a Windows WMI platform-detection diagnostic
(`0x8007000e`); all 67 tests executed successfully, with exit 0 and clean JUnit.

The two remaining unit failures are the failures identified in the request. Their
production files were not modified. Exact short traces:

```text
tests/unit/test_checking_handoff_fixture.py:29
assert normalize_answer(item["answer_format"], item["raw_answer"]) == item["normalized_answer"]
AssertionError: assert {'text': 'Café\nX'} == {'text': 'Café\nX'}

tests/unit/test_classroom_notes.py:46
with pytest.raises(ClassroomNotesError) as x:
    await s.update("class", c, None, n.id, a, False, "stale", n.updated_at)
Failed: DID NOT RAISE <class 'app.application.classroom_notes.ClassroomNotesError'>
```

An early accidental overlap of test processes caused database locks. Those runs
are not acceptance evidence; the recorded matrix executes sequentially. No
unrelated production defect was masked or fixed. The existing PR10 migration test
now resolves the current head after its fresh upgrade and still checks the original
event constraint at each historical revision, accommodating PR11's head without
removing assertions. An AST comparison also confirms that the existing digital
source, create, ownership, cancellation, history and student list/detail methods
remain unchanged apart from unused variable names and formatting.

## Exact changed files

1. `backend/alembic/versions/20261011_02_paper_remediation.py`
2. `backend/app/application/paper_remediation.py`
3. `backend/app/infrastructure/paper_remediation_repository.py`
4. `backend/app/infrastructure/remediation_models.py`
5. `backend/app/infrastructure/remediation_repository.py`
6. `backend/app/presentation/remediation_routes.py`
7. `backend/app/presentation/remediation_schemas.py`
8. `backend/tests/integration/test_paper_remediation_database.py`
9. `backend/tests/integration/test_paper_remediation_migration.py`
10. `backend/tests/integration/test_paper_batch_orchestration_migration.py`
11. `backend/tests/unit/test_paper_remediation.py`
12. `backend/tests/pr11_acceptance.py`
13. `backend/tests/pr11_windows_pytest_ids.py`
14. `docs/pr11-paper-remediation-acceptance.md`

Run `python backend/tests/pr11_acceptance.py matrix` for sequential acceptance on
the dedicated `paper_remediation_pr11_test` database. Set
`PR11_TEST_DB=paper_remediation_pr11_migration_test` for the destructive standalone
migration gate. The runner derives credentials from the existing root `.env`, never
prints credentials, permits only these two test database names, adds venv entry
points to the test PATH and disables JIT only on these test databases to avoid slow
asyncpg catalog introspection. It does not modify `.env`, `.test.env`, existing
backups or other databases. Logs/JUnit files are preserved as local acceptance artifacts in
`local-backups/pr11-acceptance-20261011`; they are not committed.
