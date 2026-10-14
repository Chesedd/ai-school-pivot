# Frontend PR3 contract audit — implementation blocked

Inspected on 2026-10-14 at required local `main`
`3b09b0324b7e0ddf236db58c63ac0a6d1c65796f`.
Branch: `codex/paper-checking-review-ui`.

This commit records discovered contract gaps. It does not implement Frontend PR3.
The request explicitly requires inspecting actual contracts before coding,
reporting missing endpoints instead of inventing them, and stopping affected
feature paths without patching the frozen backend. The gaps below prevent the
complete checking/review workspace, so the PR2 placeholder remains unchanged.

## Blocking gaps

### 1. Effective paper state and AI review discovery cannot be read

`GET /api/assessment-core/scan-batches/{batch_id}/checking` returns
`PaperBatchProgress` as a dictionary. It has aggregate counts, lifecycle flags,
publication count and blocking reasons. It has no per-paper entries, effective
CheckRun IDs, AI review state/pointers or legal action projection.

`ScanGroupingRepository.read()` returns confirmed paper IDs, participants,
variants and submission status, and group display names/page order. Its
`paper_submissions` projection has no CheckRun or review information. Submission
status is not the effective checking state.

Every AI-result/review read requires both a paper ID and CheckRun ID. The only
checking route returning a discoverable CheckRun ID is the creation command.
There is no GET run list/current-run/paper-workspace route in the generated
application OpenAPI. Creation replay is a mutation requiring the original request
key and policy version inputs; it is not a read/discovery contract.

Consequences: a teacher entering normally, a second teacher, or a browser reload
cannot discover an existing AI review. Aggregate counts cannot identify which
paper is running, failed, reviewable or approved. Retaining IDs in browser memory
would not satisfy reload or co-teacher access, and academic local persistence is
explicitly forbidden.

Evidence: `backend/app/application/paper_batch_orchestration.py`
(`PaperBatchProgress`, `derive_progress`),
`backend/app/infrastructure/paper_batch_orchestration_repository.py` (`progress`),
`backend/app/infrastructure/scan_grouping_repository.py` (`read`), and
`backend/app/presentation/scan_intake_routes.py`.

Required capability: an authorized read projection that exposes each confirmed
paper's server-selected effective run, review source/current state and approved
pointer, plus legal actions or enough documented server state to render them.
The server must preserve its existing attempt-number/manual-lineage precedence.
No replacement route name is proposed here.

### 2. Batch start does not schedule or execute checking

`POST /api/assessment-core/scan-batches/{batch_id}/checking/start` calls
`PaperBatchOrchestrationService.start()`. It locks the batch, validates frozen
policies/confirmed grouping, transitions to `checking`, and returns progress.
An already-checking batch returns progress idempotently. It creates no CheckRun,
enqueues no work, and invokes no provider execution. Reconciliation only derives
progress and optionally advances lifecycle state.

The actual execution entry point is a separate per-paper synchronous
`POST .../check-runs/{check_run_id}/execute`. Production references to
`PaperCheckingIntakeService` and `PaperCheckingExecutionService` are in the
explicit per-paper routes; inspection found no batch dispatcher/worker that
consumes this start transition.

Capacity limits correctly reject execution claims with
`paper_check_capacity_unavailable` before creating a provider attempt. They are
admission controls, not a durable queue. Neither a per-paper waiting projection
nor automatic resumption is exposed. The execute response is not a polling API.

Consequences: labeling the lifecycle command as starting AI checks would promise
work that it does not launch. Implementing browser loops to create/execute papers,
manage capacity retries or retain execution requests would introduce the client
scheduler forbidden by the request and would not support safely leaving the page.

Required capability: accepted server orchestration that durably starts/resumes
batch checks and handles capacity pressure, with readable execution progress.
If another orchestration service is intended, its actual contract is needed.

Evidence: `PaperBatchOrchestrationService.start/reconcile`,
`PaperBatchOrchestrationRepository.transition`, per-paper intake/execute routes,
and `SQLAlchemyPaperCheckingExecutionRepository.prepare`.

### 3. Review findings cannot be associated with canonical items

`PaperReviewFinding` has finding/source IDs, page, category, severity, explanation
and region; it has no assessment-item or source-result association. Review seeding
flattens findings across items. The teacher-safe AI result associates findings
with `check_result_id`, but does not expose the CheckFinding ID matching
`source_check_finding_id` in the seeded review. There is no reliable identity join
between these two finding representations.

Consequences: the required per-item findings and teacher-added item findings
cannot be represented reliably. Joining by explanation, page or geometry could
misassign identical findings and is not an accepted contract.

Required capability: a stable, teacher-safe item association in review findings
and its edit validation, or an existing documented equivalent. No backend change
was made.

Evidence: `PaperReviewFinding` in
`backend/app/application/paper_checking_review.py`, seeding in
`backend/app/infrastructure/paper_checking_review_repository.py`, and `_result`
in `backend/app/infrastructure/paper_checking_execution_repository.py`.

## Actual available contracts

All paths below have prefix `/api/assessment-core`. None was called to mutate
academic data during this audit.

| Operation | Actual path and method | Notes |
| --- | --- | --- |
| Batch progress | GET `/scan-batches/{batch_id}/checking` | Aggregate only |
| Batch lifecycle start | POST `/scan-batches/{batch_id}/checking/start` | No dispatch |
| Reconcile | POST `/scan-batches/{batch_id}/checking/reconcile` | No dispatch |
| Create paper run | POST `/paper-submissions/{paper_submission_id}/check-runs` | Requires request key and four policy/routing versions; optional superseded run |
| Execute known run | POST `/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/execute` | Per-paper execution; capacity admission |
| AI result | GET `/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/ai-result` | Requires known run; structured items/suggestions/findings |
| AI review | POST/GET/PUT `/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review` | Start/read/whole-draft edit |
| AI approval | POST same review path + `/approve` | CAS, optional grade override |
| AI history | GET same review path + `/history` | Immutable revisions |
| Manual review | POST/GET/PUT `/paper-submissions/{paper_submission_id}/manual-review` | Persisted shared review contract |
| Manual approval/history | POST manual path + `/approve`; GET manual path + `/history` | Shared grading/CAS |
| Page content | GET `/scan-pages/{page_id}/content` | Existing authorized normalized image route |
| Frozen policies | GET `/scan-batches/{batch_id}/grading-policies` | Existing policies |
| Freeze policy | POST `/scan-batches/{batch_id}/grading-policies/{variant_id}` | Must use accepted policy contract, never fabricate policy |

No explicit paper retry route is exposed. Execute rejects a failed run as
`paper_check_run_not_executable`; run creation supports `supersedes_run_id`.
Retry UX needs the effective-run discovery above and the documented accepted
new-attempt contract. Capacity wait must not be labeled provider failure.

## Review behavior confirmed for subsequent implementation

- AI review start is explicit and idempotent. It requires a completed reviewable
  run and immutable AI evidence; it seeds the persisted draft on the server.
- Manual start permits no run or failed retryable/terminal runs, rejects in-flight
  runs and successful AI lineage, and creates no artificial AI evidence. New
  lineage is blocked in completed batches; existing reviews/corrections remain
  accessible. Eligibility is validated by the server.
- Both sources use `PaperReviewResponse`, including `version`, current draft,
  state and exact latest approved revision. Any currently authorized class
  teacher can act; creator ownership is not required.
- PUT replaces the whole draft with `expected_version`. Approval also requires
  `expected_version`. Conflict is `paper_review_version_conflict` (409); the UI
  must refetch and discard speculative edits with the requested conflict notice.
- Manual items may have null status/score. Final statuses are correct, partially
  correct, incorrect, not attempted, unreadable and insufficient evidence.
- Approval requires complete decisions and a nonempty summary. Correct requires
  the maximum; partial requires strictly between zero and maximum; incorrect and
  explicitly accepted not attempted require zero. Null/unreadable/insufficient
  evidence must be resolved. Do not silently change decisions.
- Approval calculates total, maximum, percentage and calculated/final grade on
  the backend. Optional `grade_override` contains `override_grade` and a required
  reason. Grade labels are bounded strings, not a fixed numeric scale.
- Editing after approval returns the workspace to reviewing while retaining its
  old approved pointer. Reapproval creates a new immutable approved revision.
- Finding edits/removals affect review representation only. Teacher additions
  have null source IDs. The current finding contract requires exactly one region
  and one member page per finding; regionless and multi-region findings are not
  supported. A UI must not fabricate geometry to add a regionless comment.
- Geometry is `normalized_upright_v1`: point `{kind, x, y}` or box
  `{kind, x, y, width, height}`, with coordinates in [0, 1] and bounded page extent.
  Use a shared image/overlay surface that scales with actual image dimensions.

## Existing frontend architecture inspected

`PaperScanPages.tsx` loads authorized assignment/assessment context and dispatches
the existing checking deep-link into `PaperMatchingPage.tsx`. That page currently
renders the PR2 checking placeholder. The matching CTA already navigates to the
checking route.

`paperScanApi.ts` uses the shared `request`/`ApiError` abstraction. There is no
third-party query library: `usePaperMatching.ts` and `usePaperScanBatch.ts` provide
abortable reads, mutation guards and non-overlapping timeout polling. Existing
error mapping is in `paperScanState.ts`. `ScanPageViewer.tsx` lazily loads the
selected blob, revokes object URLs, provides fit/zoom and bounded unavailable or
denied states. Existing fixtures/tests and matching CSS should be extended when
contracts are unblocked; no new API/component/state-management library is needed.

## Validation and completion status

Generated OpenAPI from the actual FastAPI application without connecting to a
database. The initial import needed `DATABASE_URL`; a process-local dummy asyncpg
URL enabled schema generation without using credentials or performing DB writes.
The complete route inventory confirmed the absence of run discovery and batch
dispatch contracts. Source inspection supplies response shapes where routes do
not declare OpenAPI response models.

- Existing batch projection unit tests: **22 passed** via
  `python -m pytest -q tests/unit/test_paper_batch_orchestration.py`.
- `alembic heads`: single **20261013_01**.
- Changed file: only this audit document.
- Backend diff against required base: empty (both committed and working tree).
- `git diff --check`: passed.
- Frontend focused/full suite, typecheck, production build and Vite advisory
  comparison: not run; no frontend implementation changed.
- Co-teacher browser navigation, AI/manual/concurrency/capacity/retry acceptance,
  negative authorization, 1440/760 visual checks and 20–30-paper performance:
  not performed; a functioning PR3 workspace is blocked. These are outstanding
  implementation acceptance gates, not passed results.
- No route changes, page viewer changes, polling UI, save/approval UI or grade
  override UI were implemented. Supported independent review contracts were
  recorded without shipping an incomplete manual-only workspace.
- No backend/migration changes, push or merge. Publication, student visibility,
  withdrawal, finalization and Frontend PR4 remain unimplemented by this branch.

Resume frontend implementation after the accepted backend supplies the missing
capabilities or an existing contract satisfying them is identified. The backend
freeze remains in force; this audit does not authorize backend edits.
