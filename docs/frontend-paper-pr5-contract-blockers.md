# Frontend PR5: blocked contract inspection

Inspected 2026-10-14. Required base and starting HEAD:
`e343d509146f5abc6462b4b53c322490fff6b025` (`main`).
Branch: `codex/paper-student-result-remediation-ui`.

PR5 is **not complete**. No functional frontend changes have been made. The
required student assignment discovery/title path and manual-source remediation
HTTP path are stopped under the request's explicit backend-contract blocker rule.
No backend fix, alternative student application, invented association, fake
CheckRun, or teacher API access from student UI has been introduced.

## Blocker 1: no student-safe paper-to-assignment discovery contract

`PaperResultPublicationRepository.student_list()` returns active publication
projections plus normalized page descriptors. `project()` returns only `id`,
`paper_submission_id`, `publication_number`, `approved_review_revision_id`,
`published_at`, `active`, and `result`. Its immutable `PublicationSnapshot`
contains summary/items/findings/grade and paper/revision identities, but no
assignment ID or assignment title. `PublishedItem` also has no task title or
canonical position field.

The existing `StudentAssignmentSummary` / `StudentAssignmentDetail` contracts
provide assignment identity and title but no paper/result identity or link. The
current local OpenAPI has no student assignment-to-paper-result lookup route.
The student paper list's page descriptors do not supply this association either.

Consequently an assignment page cannot identify its paper result, and the result
list cannot label results with assignment titles. Fetching teacher metadata,
guessing from dates/order, requiring the student to enter an ID, or substituting
a generic paper label would not satisfy the requested academic navigation and
assignment-title requirements. A standalone result route could render a known
paper ID, but that does not solve the mandatory normal-navigation path.

Required contract decision before this path can finish: expose an authorized,
student-safe assignment association and title (through assignment projections,
paper projections, or an assignment-scoped result lookup). Confirm canonical
item labels/order if those are not intentionally the snapshot array order.
This document does not prescribe a backend implementation or change frozen
publication snapshots.

Sources:

- `backend/app/infrastructure/paper_result_publication_repository.py`: `project`,
  `student_result`, `student_list`.
- `backend/app/application/paper_result_publication.py`: `PublicationSnapshot`,
  `PublishedItem`, `PublishedFinding`, `PublishedGrade`.
- `backend/app/presentation/student_assessment_schemas.py`: student assignment DTOs.
- `backend/app/presentation/paper_result_publication_routes.py`: student routes.

## Blocker 2: manual-source plan cannot pass the HTTP response schema

`PaperRemediationRepository.create()` legitimately sets `source_check_run_id` to
`None` when the publication's approved revision is manual. The shared repository
`view()` returns that value unchanged. However,
`RemediationPlanResponse.source_check_run_id` is declared as required `UUID`,
not nullable. Current generated OpenAPI confirms `type: string, format: uuid`.

A representative otherwise-valid manual-source plan fails the actual response
model with exactly `source_check_run_id: uuid_type`. An isolated FastAPI route
using that same response model returns HTTP 500. Giving the control payload a
non-null CheckRun UUID validates. That control is solely a reproduction; a fake
CheckRun must never be used for a manual plan.

The affected real routes include paper draft creation, shared draft read,
assignment, and teacher history, all using `RemediationPlanResponse` directly or
inside `RemediationPage`. Paper creation commits before returning its response,
so a response-validation failure may occur after the draft is persisted. Do not
treat such an HTTP failure as proof that nothing was created.

Required contract correction before manual-source UI acceptance: the shared
response must represent a legitimate null CheckRun. No backend files were changed.

Sources:

- `backend/app/infrastructure/paper_remediation_repository.py`: `create`.
- `backend/app/infrastructure/remediation_repository.py`: `view`.
- `backend/app/presentation/remediation_schemas.py`: `RemediationPlanResponse`.
- `backend/app/presentation/remediation_routes.py`: create/get/assign/history.

## Reproduction and limits

From the repository's `backend` directory in PowerShell:

```powershell
$env:PYTHONPATH = (Get-Location).Path
..\.venv\Scripts\python.exe ..\docs\pr5_contract_probe.py
..\.venv\Scripts\alembic.exe heads
```

Successful probe output includes:

```text
MANUAL_DTO: [(['source_check_run_id'], 'uuid_type')]
ISOLATED_HTTP_SAME_RESPONSE_MODEL: 500
NON_NULL_CHECK_RUN_CONTROL: valid
ASSIGNMENT_DISCOVERY_LINK: absent in both contracts
OPENAPI_CHECK_RUN_SCHEMA: {'type': 'string', 'format': 'uuid', 'title': 'Source Check Run Id'}
PASS: both blocking contract mismatches reproduced
20261013_01 (head)
```

The probe runs real local projection/schema code and generated application
OpenAPI. Its HTTP reproduction uses an isolated route with the production
response model; it does **not** claim a real PostgreSQL/backend/browser E2E.
It opens no database connection and makes no academic data mutations or provider
calls. This is the requested PR5 contract inspection, not the final audit.

## Available contracts and reusable frontend architecture

These endpoints were inspected, **not newly integrated**. Braces denote route
parameters. Student identity is resolved by the backend principal.

| Purpose | Actual method/path |
| --- | --- |
| Student active paper list | GET `/api/assessment-core/student/paper-results?offset=0&limit=20` |
| Student current paper result | GET `/api/assessment-core/student/paper-submissions/{paper_submission_id}/result` |
| Student normalized page bytes | GET `/api/assessment-core/student/paper-submissions/{paper_submission_id}/pages/{scan_page_id}` |
| Teacher eligibility | GET `/api/assessment-core/paper-submissions/{paper_submission_id}/remediation-eligibility` |
| Teacher candidates | POST `/api/assessment-core/paper-submissions/{paper_submission_id}/remediations/candidates/search` |
| Teacher explicit draft create | POST `/api/assessment-core/paper-submissions/{paper_submission_id}/remediations` |
| Teacher read / edit | GET / PATCH `/api/remediations/{id}` |
| Teacher assign | POST `/api/remediations/{id}/assign` |
| Teacher plan history | GET `/api/classrooms/{class_id}/students/{student_id}/remediations` |
| Teacher execution result | GET `/api/remediations/{id}/result` |
| Student plan list/read | GET `/api/student/remediations`, GET `/api/student/remediations/{id}` |
| Student execution / results | GET `/api/student/remediations/{id}/execution` |
| Student start | POST `/api/student/remediations/{id}/start` |
| Student answer | PUT / DELETE `/api/student/remediations/{id}/answers/{item_id}` |
| Student submit | POST `/api/student/remediations/{id}/submit` |

Eligibility exposes current `active_publication_id`, `eligible`, eligible item
IDs/count, and `reason` (`no_active_publication` / `no_weak_items` when ineligible).
Candidate search/create bind `source_paper_publication_id`, selected source item
IDs, and publication finding IDs. Candidate reasons come from the API. Tasks are
selected catalog versions, not a new frontend generation engine. Draft PATCH
supports signals and ordered items with `expected_updated_at`; no invented task
editor is needed.

Stale create/assign uses `remediation_source_publication_stale`. Invalid source,
item, finding, and duplicate signals use
`remediation_source_publication_invalid`, `remediation_paper_item_ineligible`,
`remediation_publication_finding_not_found`, and `remediation_duplicate_signal`.
`assign()` returns an already assigned plan before validating the current source;
an assigned historical plan must not be gated on current publication activity.
New creation binds the new active publication explicitly. Eligibility/source
authorization uses class access, so an assigned co-teacher can create their own
plan; shared read/assign also applies existing plan-owner authorization.

Student no-active/foreign-paper/page failures are bounded by
`paper_result_not_found` (404). Student page responses use `private, no-store` and
serve validated normalized PNG bytes. Current page implementation inspected here
uses 404 for missing/integrity failures; a frontend should also bound unexpected
410 without a source-PDF fallback. The student projection contains internal IDs
that must be kept out of rendered content, despite being needed internally.

Frontend uses `main.tsx` capability-gated routes and `api.request` with session
cookies; these pages use component-local state/effects rather than a shared
query-cache library. Existing `StudentAssessmentPages` is the natural assignment
entry. `StudentRemediationsPage` / `StudentRemediationDetailPage` and
`remediationApi.ts` already provide the shared execution/checking/result flow.
`PaperPublicationPage` provides the teacher entry location. `ScanPageViewer`
currently hardcodes teacher page loading; student reuse must explicitly inject
or otherwise separate the authorized student byte loader. `regionStyle` already
handles the accepted point/box geometry and fails closed for invalid geometry.
No student viewer has been wired to the teacher loader.

## Requested completion report

Not-run entries below are intentional and must not be interpreted as passes.

| # | Requested item | Current status |
| --- | --- | --- |
| 1 | Branch | `codex/paper-student-result-remediation-ui` |
| 2 | Commit | Blocker report/probe commit; full SHA in chat completion message |
| 3 | Exact changed files | `docs/frontend-paper-pr5-contract-blockers.md`, `docs/pr5_contract_probe.py` |
| 4 | Student result endpoints used | Inspected only; listed above |
| 5 | Student page endpoint used | Inspected only; listed above |
| 6 | Teacher remediation endpoints used | Inspected only; listed above |
| 7 | Student remediation endpoints used | Existing integration inspected; listed above |
| 8 | Routes/navigation | No changes; mandatory assignment discovery is blocked |
| 9 | Active-only behavior | Backend inspected; no new UI implemented/tested |
| 10 | Withdrawal behavior | Backend no-active semantics inspected; no new UI test |
| 11 | Republish behavior | Current pointer semantics inspected; no new UI test |
| 12 | Score/grade | Published immutable fields confirmed; rendering not implemented |
| 13 | Item statuses | Four published statuses confirmed; rendering not implemented |
| 14 | Findings | Snapshot contract confirmed; rendering not implemented |
| 15 | Viewer reuse | Existing viewer inspected; student loader integration not implemented |
| 16 | Regions | Shared point/box helper inspected; no model expansion |
| 17 | Student privacy | No new UI, raw teacher requests, or persistence added |
| 18 | Teacher entry | Not implemented |
| 19 | Eligibility | Actual projection inspected; UI not implemented |
| 20 | Candidates/signals | Actual API contracts inspected; UI not implemented |
| 21 | Draft creation | Manual-source HTTP response blocked; no new UI |
| 22 | Assignment | Manual-source HTTP response blocked; no new UI |
| 23 | Stale draft | Actual stale code/assign order inspected; UI not implemented |
| 24 | Assigned old source | Backend assign ordering inspected; browser execution not tested |
| 25 | New B source | Exact source identity contract inspected; UI not implemented |
| 26 | Manual publication | Response-model blocker reproduced |
| 27 | Student remediation discovery | Existing general remediation navigation retained; no paper integration |
| 28 | Execution/checking | Existing shared frontend/API inspected; not modified |
| 29 | Completion | Existing semantics retained; no PR5 completion test |
| 30 | Co-teacher | Source/owner authorization inspected; no browser/DB claim |
| 31 | Negative authorization | No new browser/DB authorization tests run |
| 32 | Student 1440 | Not run; no PR5 screen |
| 33 | Student 760 | Not run; no PR5 screen |
| 34 | Teacher responsive | Not run; no PR5 screen |
| 35 | Performance | Not run; no new frontend requests |
| 36 | Focused tests | Contract probe passed; no PR5 frontend tests implemented |
| 37 | Full frontend suite | Not rerun; no frontend changes; 608 is supplied baseline, not this run |
| 38 | Typecheck | Not rerun; no TypeScript changes |
| 39 | Production build | Not rerun; no frontend changes |
| 40 | Vite advisory | No frontend bundle changes; prior PR4 report records JS 581.29 kB / gzip 155.78 kB |
| 41 | Student result E2E | Not run; discovery/title path blocked |
| 42 | Withdrawal E2E | Not run |
| 43 | Republish E2E | Not run |
| 44 | Teacher remediation E2E | Not run |
| 45 | Stale draft E2E | Not run |
| 46 | Assigned survives replacement E2E | Not run |
| 47 | Manual-source E2E | Not run; response mismatch independently reproduced |
| 48 | Authorization E2E | Not run |
| 49 | Backend diff | Empty relative to required base |
| 50 | Alembic | Single `20261013_01 (head)` verified |
| 51 | Backend blockers | Two documented above |
| 52 | Final audit | Not started |
| 53 | Push/merge | Nothing pushed or merged |

All pre-existing unrelated untracked files are preserved. This is a local
blocked-start report, not a functional PR5 candidate or a production-readiness
approval. AI-origin teacher remediation and known-ID result rendering have usable
contracts, but are not shipped as a partial replacement for the requested final
functional slice.
