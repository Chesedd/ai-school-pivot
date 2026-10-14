# Backend contract hotfix for Frontend PR5

Base: `e343d509146f5abc6462b4b53c322490fff6b025`.
Branch: `codex/paper-student-remediation-contracts`.
The PR5 audit and probe were read directly from
`c4a6c894b2526642e1dfbccab26dc23a1a2bd918` before implementation.

## Student discovery contract

Existing navigation lists `/api/assessment-core/student/assignments` and opens
`/api/assessment-core/student/assignments/{assignment_id}`. Digital attempts are
linked by `submitted_attempts[].id`; paper results previously required a known
PaperSubmission ID, or a separate list with no assignment association/title.
PaperSubmission already stores assignment and participant identities, but the
publication projection and frozen snapshot omit assignment navigation context.

Added:

`GET /api/assessment-core/student/assignments/{assignment_id}/paper-result`

The `StudentAssignmentPaperResult` response contains `assignment_id`,
`assignment_title`, publication `id`, `paper_submission_id`, `publication_number`,
`published_at`, `active: true`, `result`, and normalized `pages` descriptors.
The title is read from Assignment → Assessment, exactly like existing assignment
navigation; no mutable title is copied into publication persistence.

Authorization uses existing student capability/role and linked identity checks.
`Principal.student_id` scopes AssignmentParticipant, whose assignment and student
must also match PaperSubmission. The active state pointer must resolve to a
publication of that same paper. No student ID parameter is accepted.

The endpoint returns `404 {"detail":"paper_result_not_found"}` for an unknown,
foreign, unpublished, approval-only, or withdrawn result; unlinked principals
receive the existing 403. It never reads historical publications as fallback.
If multiple papers for the same participant have active results, the most recently
published active result wins, with publication UUID as deterministic tie-breaker.
After withdrawal and republish, the same URL resolves the new publication.

The new DTO omits approved review revision identities at both levels, participant
IDs, history, withdrawal reasons, storage references, and CheckRun/ModelRun data.
Existing known-paper/list routes retain their contracts. Published item order is
the immutable snapshot array order; this hotfix does not add canonical task labels.
Pages use the existing ownership-scoped, validated normalized PNG loader.

The path performs three SELECTs: joined assignment/participant/paper/current-state
resolution, current owned publication validation, and ordered page descriptors.
The test excludes fixture SAVEPOINT/ROLLBACK statements from that query count.
No assignment list projection changed and no per-assignment query loop was added.

## Manual remediation response

The persisted manual paper plan legitimately has `source_check_run_id = NULL`.
`RemediationPlanResponse` incorrectly required `UUID`, causing response validation
failure after draft creation had committed. Its field is now required but nullable:
`UUID | None`. A response validator still rejects a digital plan without a CheckRun.
Digital create/candidate inputs remain UUID-only; database provenance XOR and
immutability constraints are unchanged.

Teacher draft create/read/assign/history all reuse the corrected shared model
(history through `RemediationPage`). The response still identifies paper source,
exact publication, paper submission, assignment and participant without dereferencing
a CheckRun. Manual plans create no CheckRun, ModelRun, or PaperAIRevision. AI plans
retain their real CheckRun UUID.

Student list/detail/execution/result schemas already avoid source CheckRun
requirements, so they need no change. Real routes serialize an assigned manual
plan after withdrawal and after republish; execution can start after replacement.
Its exact old publication provenance remains unchanged. An unassigned old draft
still fails assignment with `remediation_source_publication_stale` (409).

Existing teacher ownership/class access rules remain intact. An assigned co-teacher
can inspect eligibility and create/assign their own manual-source plan regardless
of who created the publication. Unrelated teachers and revoked plan owners fail
safely; other students cannot read or start the assigned plan.

OpenAPI exposes UUID-or-null for `RemediationPlanResponse.source_check_run_id`
and a typed `StudentAssignmentPaperResult` for the new lookup.

## Verification

Dedicated module: `backend/tests/integration/test_paper_student_contracts_database.py`.
**20 passed** on the local disposable PostgreSQL `_test` database, using the real
routers, dependencies for identity/session, production repositories, response
models, and persisted manual/AI publications. No provider calls are needed.
HTTP tests inject authenticated Principals at the normal authentication dependency;
they do not claim browser login or a deployed-server acceptance run.

Coverage includes normal assignment list → detail → result without paper/publication
IDs, withdrawal → 404 → republish, safe DTO/page bytes, foreign assignment/paper/
publication/page/source-PDF substitutions, unlinked identity, no-active states,
three independent assignments for one student, three-query bound, generated
OpenAPI, manual/AI teacher create/read/assign/history/result, student list/detail/
execution/start, source replacement, immutable exact publication provenance,
stale drafts, co-teacher/unrelated/revoked authorization, and digital missing-run
database rejection. Existing digital HTTP and lifecycle suites are also run.

Static checks: changed Python files Ruff-clean; `python -m compileall -q app tests`,
`git diff --check`, `docker compose config --quiet` pass. No migration added;
`alembic heads` remains the single `20261013_01 (head)`.

Affected regressions: **226 passed** across student paper publication/teacher
authorization, withdrawal, paper remediation, manual review, digital remediation,
student execution/results, student assignment HTTP, presentation security and
authenticated student route suites. The combined run returned 225 passes and
one fixture setup connection timeout (`WinError 121`) for
`test_teacher_routes_authorization[history-teacher]`; its targeted retry passed.
No application assertion failed in that run. A separate affected unit run passed
**115 tests** for publication, paper remediation, withdrawal, publication commands,
remediation execution/results and student assessments. Total: **361 passing cases**
across the focused module, affected regressions and separate unit run.

Regression modules (under `backend/tests`):

- `integration/test_paper_result_publication_database.py`
- `integration/test_paper_publication_withdrawal_database.py`
- `integration/test_paper_remediation_database.py`
- `integration/test_manual_paper_review_database.py`
- `integration/test_remediation_database.py`
- `integration/test_remediation_execution_database.py`
- `integration/test_remediation_results_database.py`
- `integration/test_student_assessment_api.py`
- `unit/test_remediation_presentation_security.py`
- `unit/test_authenticated_student_routes.py`
- `unit/test_paper_result_publication.py`
- `unit/test_paper_remediation.py`
- `unit/test_paper_publication_withdrawal.py`
- `unit/test_paper_publication_commands.py`
- `unit/test_remediation_execution.py`
- `unit/test_remediation_results.py`
- `unit/test_student_assessments.py`

The first focused test invocation hit a Windows pytest temp-directory permission
error before database setup. Subsequent runs used unique workspace `--basetemp`
directories. The final focused run was 20/20 passing with no skips.

Exact changed files:

- `backend/app/infrastructure/paper_result_publication_repository.py`
- `backend/app/presentation/paper_result_publication_routes.py`
- `backend/app/presentation/remediation_schemas.py`
- `backend/app/presentation/student_paper_result_schemas.py`
- `backend/tests/integration/test_paper_student_contracts_database.py`
- `docs/paper-student-remediation-contracts.md`

Frontend PR5 remains at `c4a6c894b2526642e1dfbccab26dc23a1a2bd918`.
No frontend files, probe artifacts, or migration files changed. No push, merge,
or final audit performed. Both verified backend blockers are corrected; PR5 UI
implementation and its own acceptance checks remain separate work.

## Requested completion checklist

| # | Item | Result |
| --- | --- | --- |
| 1 | Branch | `codex/paper-student-remediation-contracts` |
| 2 | Commit | See local commit SHA in chat completion; this report is part of that commit. |
| 3 | Files | Six exact paths listed above. |
| 4 | Migration | None. |
| 5 | Alembic | Repository and test DB both `20261013_01`. |
| 6 | Discovery root cause | Paper projection omitted assignment association/title; assignment APIs had no paper lookup. |
| 7 | Chosen contract | Assignment-scoped current result read. |
| 8 | Endpoint/DTO | New GET route and `StudentAssignmentPaperResult`, described above. |
| 9 | Assignment context | Authoritative assignment ID and Assessment title. |
| 10 | Identity | Principal linked student → owned participant → same-assignment paper → active publication. |
| 11 | Active only | No history/review fallback. |
| 12 | Withdrawal | Same lookup and page become 404. |
| 13 | Republish | Same assignment URL returns B, not A. |
| 14 | Assignment privacy | X and Z resolve independently; Y has no result. |
| 15 | Student privacy | Foreign assignment/paper/publication/page fail safely. |
| 16 | Queries | Three bounded SELECTs; existing list untouched. |
| 17 | 500 root cause | UUID-only teacher response rejected valid persisted manual NULL. |
| 18 | Changed response | Shared `RemediationPlanResponse`. |
| 19 | Nullability | Required UUID-or-null; digital response validator requires non-null. |
| 20 | Manual provenance | Exact paper/publication; no fabricated AI evidence. |
| 21 | AI provenance | Actual CheckRun UUID retained and tested. |
| 22 | Digital provenance | Inputs/domain/DB constraints retained; invalid missing run rejected. |
| 23 | Teacher HTTP | Create 201; read/assign/history/result 200. |
| 24 | Student HTTP | List/detail/execution 200; start 201. |
| 25 | Source replacement | Assigned manual plan readable and executable after withdrawal/replacement. |
| 26 | Stale draft | Assignment rejected with stale publication 409. |
| 27 | OpenAPI | UUID-or-null and typed discovery response tested. |
| 28 | Co-teacher | Eligibility/create/assign pass for another assigned teacher. |
| 29 | Negative authorization | Unrelated/revoked teacher and foreign student fail safely. |
| 30 | Focused tests | 20 cases in dedicated module. |
| 31 | PostgreSQL | 20/20 passing against disposable `_test` DB. |
| 32 | Discovery HTTP proof | Real assignment list → detail → current paper route; linked principal injected at auth dependency. |
| 33 | Manual HTTP proof | Real create → read → assign → student list/detail/execution. |
| 34 | Regressions | 226 passes including one infrastructure-error retry; 115 separate unit passes. |
| 35 | Static | Ruff, compileall, diff check, heads, Compose config pass. |
| 36 | Frontend PR5 | Exact branch tip unchanged; no frontend/probe edits. |
| 37 | Remaining blocker | Neither verified backend blocker remains; PR5 implementation/acceptance still separate. |
