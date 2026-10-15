# Final scanned paper production readiness audit

Audit date: 2026-10-15, Europe/Moscow. Repository: `Chesedd/ai-school-pivot`.

## 1. Executive verdict

**SCANNED-PAPER WORKFLOW PRODUCTION-READINESS NOT ACCEPTED.**

The fresh teacher browser flow stops after grouping: checking requires frozen,
batch-specific grading policies, but the teacher application provides no way to
create them. A real browser request returned HTTP 409
`paper_grading_policy_incomplete` and the bounded instruction to contact an
administrator. This is B1, an unresolved BLOCKER under the requested core-flow
acceptance standard. Supplying policies through the production API enables the
rest of the flow; it does not make the unassisted teacher flow pass.

The audit also found H1: that policy API rejected valid JSON with HTTP 422.
It is narrowly fixed, with a failing-before/passing-after PostgreSQL HTTP test.
The broad backend baseline is also not green: two inherited test assertions
fail identically on the exact base SHA. Neither is a scanned-paper product
regression; they are disclosed rather than waived as a green full-suite gate.
No policy authoring feature, grading defaults, automatic publication, or other
product scope has been added. Defining a teacher/admin policy provisioning
workflow needs a separate product decision.

The results below distinguish browser execution, real PostgreSQL HTTP tests,
source inspection, earlier evidence, and verification gaps. Passing component
tests is not represented as an integrated production acceptance.

## 2. Audited base SHA

- Required and verified `main` / starting `HEAD`:
  `bfb4a52ebfb895a04ccca2b936eba90290c9c5d1`.
- Audit branch: `codex/paper-full-flow-audit`, created directly from that local
  `main`. The stale remote was not used as the base.
- Original tracked working tree was clean. Preserved original untracked entries:
  `-U`, `.test.env`, `backend/pr11-units.log`, `backups/`,
  `frontend/pr3-acceptance.html`, and `local-backups/`.
- No push, merge, clean, stash, or deletion/move of unrelated files.
- Current schema head: single `20261013_01`.
- The audit commit identity and final tracked status are recorded in delivery;
  a commit cannot contain its own hash.

## 3. Architecture map

The inventory was generated from current route decorators **before tests** into
`local-backups/full-flow-audit-20261015/architecture.json`: 66 route registrations
across scan, publication, and shared remediation routers. The complete inventory
is appended below. Paths in this report are repository-relative.

| Stage | Current implementation and boundary |
|---|---|
| Account/academic identity | `application/principal.py`; `auth_models.User`, `StudentUserLink`; `assessment_models.Student`, `AssignmentParticipant`. `Principal.student_id` controls student self-access. |
| Assignment and class access | `assessment_routes.py`, `student_assessment_routes.py`; `SQLAlchemyClassroomAccessRepository.can_access_class`; capability plus class membership, including assigned co-teachers. |
| Batch/intake | `scan_intake_routes.py`; `ScanIntakeService`, `SqlAlchemyScanIntakeRepository`; `AssessmentScanBatch`, `ScanBatchArtifact`, private immutable `InputArtifact`. |
| Extraction/content | `ScanPagePipeline`, `PillowPdfiumPageRasterizer`, `ScanPage`; teacher source download and normalized page routes; filesystem bytes remain outside the database. |
| Matching | `ScanMatchingService`, matching repository/provider; run/chunk/attempt, roster/page entries, proposals/candidates. Minimal roster names and opaque matching tokens are a separate provider contract. |
| Grouping | `ScanGroupingService`, grouping repository; immutable confirmed grouping revision, group entries/pages and paper membership. Expected revision/row version protect writes. |
| Academic paper | `PaperSubmission`, `PaperSubmissionPage`. Separate from digital `StudentSubmission`; one non-superseded paper per participant/batch; variant from participant. |
| Grading prerequisite | `ScanGradingPolicy`, item/rubric/grade-scale snapshots; `PaperGradingPolicyRepository.freeze`; immutable batch/variant policies. This is the missing browser transition, B1. |
| Checking | `PaperCheckingIntakeService`, `PaperCheckingExecutionService`, checking execution repository; `CheckRun`, `ModelRun`, immutable `PaperAIRevision`, check results/findings/regions. |
| Teacher review | `PaperCheckingReviewRepository`; `PaperReviewDraft/Edit/Approval`, review and immutable review revisions. Manual provenance has null CheckRun/AI revision. |
| Publication | `paper_result_publication_routes.py`; `PublicationSnapshot`, `PublicationEntry`, `PublishedItem/Finding/Grade`; immutable publication plus authoritative active state pointer. |
| Commands/withdrawal | Durable publication command/item ledger; immutable withdrawal row; exact publication IDs and common reason in official selected/bulk withdrawal. |
| Student result | `student_paper_result_schemas.py`: `StudentAssignmentPaperResult`, `StudentPublishedResult/Page`; assignment lookup uses authenticated academic identity, active pointer and paper ownership. |
| Remediation | `paper_remediation.py`, `paper_remediation_repository.py`; shared `RemediationPlan`, item/finding signals, execution/checking/results. Source publication fixed at draft creation and rechecked at assignment. |
| Recovery/retention | Matching lease/claim fencing; `scan_orphan_cleanup.py` and filesystem scan-object journal; `ScanStorageRetentionRepository`; whole-plan downgrade preflight in `paper_downgrade_guard.py`. |

Frontend route map:

| Route | Components / API adapters |
|---|---|
| `/classrooms/:class/assignments/:assignment/paper` | `PaperScanPages`, batch list/create; `paperScanApi`. |
| Same + `/:batch` | `PaperScanIntake`, upload/extraction and saved file/page reconstruction. |
| Same + `/:batch/matching` | `PaperMatchingPage`, `PaperGroupPanel`, `usePaperMatching`; explicit confirmation. |
| Same + `/:batch/checking` | `PaperCheckingPage`, `PaperReviewPanel`, `usePaperChecking`, `paperReviewApi`; optional exact paper query parameter. |
| Same + `/:batch/publications` | `PaperPublicationPage`, `paperPublicationApi/State`; main checking projection and lazy history. |
| Same + `/:batch/papers/:paper/remediation` | `PaperRemediationPage`, `paperRemediationApi`; exact active source and shared teacher plan detail. |
| `/classrooms/:class/students/:student/remediations/:plan` | `TeacherRemediationDetailPage`; read/update/assign existing plan. |
| `/student/assignments/:assignment` | `StudentAssignmentDetailPage`, `StudentPaperResult`, `studentPaperApi`; no frontend-selected student ID. |
| `/student/remediations`, `/student/remediations/:plan` | Student discovery/execution, saved answers, submit confirmation and checked result. |

Asynchronous boundaries are explicit HTTP execution windows, not an autonomous
worker: extraction runs in the request; matching is chunked with durable leases;
checking schedules runs then executes a bounded window, with explicit continuation
and lease recovery; remediation submission uses the shared checking pipeline.
The browser harness pumps the real deterministic remediation checker after submit.
Pending/running state is persisted; browser state is not the academic record.

Migration chain inspected from source and migrated PostgreSQL:

`20260914_02 → 20260914_03` intake `→ 20260915_01` matching
`→ 20260915_02` grouping/papers `→ 20260923_01` grading/intake
`→ 20261005_01` execution `→ 20261010_01` review
`→ 20261010_02` publication `→ 20261011_01` orchestration
`→ 20261011_02` remediation `→ 20261011_03` manual
`→ 20261012_01` retention `→ 20261012_02` withdrawal
`→ 20261013_01` matching recovery.

The live constraints/indexes/triggers and nullable provenance were exported to
`schema.json`. They include paper uniqueness, immutable evidence/revision guards,
publication identity/number and command-key uniqueness, active-pointer composite
FK, withdrawal uniqueness and remediation source XOR. Manual CheckRun/AI revision
and inactive publication pointer nullability match the current contracts. Digital
remediation still requires a CheckRun. Whole-plan online preflight refuses lossy
paper-history downgrades; external purged bytes are not restored by downgrades.
No migration was added or edited. Existing direct-SQL cross-aggregate identity
hardening debt is not claimed to be eliminated.

## 4. Test matrix

Evidence directory: `local-backups/full-flow-audit-20261015/` (untracked).

| Gate | Result / evidence |
|---|---|
| Focused H1 regression, before fix | **1 failed, 3 passed**; valid JSON rejected with tuple/Decimal errors, `policy-red.log`. |
| Focused H1 regression, after fix | **4 passed**, `policy-green.xml`. Valid freeze/readback/replay reaches two scheduled papers; malformed/missing/extra-field contracts remain rejected. |
| Affected PostgreSQL suite | **138 passed**, zero failures/skips, `affected.xml`; policy HTTP, teacher review contracts, student contracts, remediation, withdrawal races, blocker recovery. |
| Final policy/OpenAPI regression | **4 passed**, `policy-final.xml`; includes preserving the named OpenAPI request-body schema. |
| Separately enabled migration gates | **4 passed**, `migration-gates.xml`; the four full-suite skips run under explicit PR11/PR12 migration flags in their own disposable database. |
| Broad backend baseline | **2311 passed, 4 skipped, 2 failed, 0 errors / 2317 total**; `backend-linux.xml`, existing Linux acceptance image, PostgreSQL, all `tests`. |
| Full frontend, stable run | **650 passed / 53 files**, zero failures/skips; `frontend-stable.log`, `npm run test:run -- --maxWorkers=2`. |
| Initial frontend attempt | 639 passed / 11 failed, mostly parallel-load timeouts. Superseded by the complete clean constrained-worker run; no frontend source changes. |
| TypeScript | `npx tsc -b --pretty false`, exit 0, `typecheck.log`. |
| Production build | `npm run build`, exit 0; 597.59 kB JS / 160.09 kB gzip; existing >500 kB advisory, `build.log`. |
| Alembic | `heads`, `current`, `history`: one head and current `20261013_01`; fresh dedicated databases upgraded to head. |
| Changed-file lint / whitespace | Ruff and `git diff --check` passed; no broad cleanup. |

The initial Windows broad attempts were aborted/superseded: migration tests reset
the test schema, the default shared Windows pytest temp directory denied access,
and local subprocess/runtime overhead prevented a useful stable baseline. These
are not counted as passing suites or product regressions. The final broad run uses
the existing `ai-school-pr14-acceptance:latest` Linux runtime and a separate
`full_flow_audit_linux_20261015_test` database. Browser, affected suite and broad
suite databases are separate. Credentials were obtained without printing them.
Windows affected runs use the existing short-test-ID plugin, a unique basetemp,
and supported non-WMI platform fallback; no application/clock/auth gate is patched.

## 5. Full-flow scenarios

All fresh evidence uses real PostgreSQL, production routes/services/storage, a
headless Edge browser, deterministic provider doubles, and disposable synthetic
academic fixtures. The fixture principal override selects actors; the production
capability/class/student authorization code still runs. This is not live-model
quality evidence or a production login/session acceptance.

| Scenario | Outcome and exact boundary evidence |
|---|---|
| Clean unassisted teacher path | **BLOCKED B1** after explicit grouping confirmation. Browser start → 409 `paper_grading_policy_incomplete`; no grading-policy action. `policy-gap.json`, `missing-policy-browser-blocker-760.png`. |
| Happy path with explicit policy provisioning | PASS: browser batch create 201 → PDF upload 201 → extraction 200 → two ready pages → matching 200 → grouping confirm 200 → policy POST 200 per variant → checking start/execute 200 → per-paper review start/approve 200 → official selected publication 200 → complete 200 → student assignment/result/page 200 and selected finding region. `fresh-results.json`, `fresh-traffic.json`, `requests.log`. |
| Manual production-route path | **PASS**, `manual-results.json`; browser manual start/edit/approval/publication, student visibility, nullable-provenance remediation creation/assignment and browser submission/check/result. No paper provider call or fake paper checking lineage. |
| Withdrawal / same-revision republish | PASS: exact active publication withdraw 200 → student result/page 404 → browser refetch removes result/image → new key creates a distinct publication from the same approved revision → old key returns historical command result without changing the current pointer. `lifecycle-results.json`, `lifecycle-traffic.json`. |
| Corrected review | PASS: R2 approval leaves R1 publication unchanged in HTTP and student view; teacher publication workspace shows previous version active; explicit R2 publish replaces pointer; old revision remains history. |
| Explicit completion | PASS: completion before final publication 409 and disabled UI; selected publish enables explicit completion; later withdrawal/republish retain completed status and original checking run IDs. |
| Stale remediation | PASS: draft source A; publication replacement; assign → 409 `remediation_source_publication_stale`; source remains A, no rebound. Browser bounded stale message and disabled assign verified in the 30-paper run. |
| Assigned source survives | PASS: A-derived assigned plan executes/checks after B replaces A; plan source remains A, completion persists, paper result unchanged. Fresh lifecycle and manual/AI browser runs. |
| Removed finding | PASS: real review edit removes AI X; publication omits X; student omits X; selecting removed X for remediation → 422 `remediation_publication_finding_not_found`. |
| Representative statuses | `partially_correct`, `not_attempted` with zero, and explicit `incorrect` survive publication. `unreadable` and `insufficient_evidence` save as review states but approval rejects with 422 `paper_review_unresolved_item`; teacher must explicitly resolve them. They are never silently converted to incorrect. |
| Large batch | 30-paper publication/remediation browser fixture, scoped filters/selections, lazy history and selected-page loading; 1440/760 screenshots and traffic. Intake/matching fresh browser fixture has two papers; a 30-paper intake/matching browser rerun was not performed. |

The fixture helper originally assumed one assignment and integer score text.
Those audit-only selectors were corrected for the added fresh assignment and
server-formatted `5.00`; no product change was made to accommodate them. Retained
partial logs are not counted as completed scenarios. `browser-fixed.log` ends
`FINAL BROWSER PASS`; `lifecycle-last.log` ends lifecycle PASS.

## 6. Authorization/security matrix

`authorization.json` contains **72 fresh route probes**. Negative requests use real
existing IDs, not absent placeholder IDs. All returned safe 403/404 responses.
Teacher/co-teacher mutations are additionally exercised by the browser and the
138 passing PostgreSQL affected tests.

| Action/resource | Original teacher | Assigned co-teacher | Unrelated / revoked teacher | Owning student | Other student | Anonymous |
|---|---|---|---|---|---|---|
| Batch, teacher page/source | allowed | allowed | denied | denied | denied | require principal |
| Match, grouping/confirm | allowed | allowed | denied | denied | denied | require principal |
| Checking, review/approve | allowed | allowed | denied | denied | denied | require principal |
| Publish/history/withdraw | allowed | allowed | denied | denied | denied | require principal |
| Paper remediation create/assign | allowed | allowed | denied | denied | denied | require principal |
| Student active result/page | teacher role denied | teacher role denied | denied | allowed while active | denied | require principal |
| Student plan/start/answer/submit | teacher role denied | teacher role denied | denied | own assigned plan allowed | denied | require principal |

Anonymous gates were inspected and remain covered by existing auth tests; the
principal-overridden browser fixture cannot establish a real anonymous-session
matrix. It is not reported as a separately executed seven-actor browser matrix.
No creator/reviewer ownership shortcut was found. Revoked class membership is
checked again even on idempotent requests. Student self-access never trusts a
query/body student ID. Teacher route lookup is followed by authorized batch/class
access; student joins require both paper ownership and active publication.

## 7. Concurrency/idempotency

- Durable command keys are scoped to actor and operation/fingerprint. Same payload
  replays do not mutate academic state; different payload conflicts. Single,
  selected and bulk routes use the official command ledger, not UI loops.
- Browser command objects freeze body/key at user intent, retain on uncertain
  transport/5xx, and generate a new key for later intent. Definitive replays can
  retain historical response flags; refetched active pointer is authoritative.
- Fresh competing teacher scenario: stale withdrawal of A after B publication
  returned conflict; B remained active. No "withdraw current" retargeting.
- Grouping uses expected matching revision and row version; explicit confirm and
  paper uniqueness prevent stale overwrite/duplicate active papers. Review uses
  expected version. Publication validates the exact latest approved revision.
- Remediation assignment locks the paper and validates exact source against the
  current pointer; assigned plans do not rebind or acquire current-source gates.
- Completion serializes on the batch and derives eligibility from settled papers
  and active publications. It is explicit and remains terminal after corrections.
- The affected suite includes independent PostgreSQL connections for review
  contract, withdrawal/remediation, retention and recovery races. Wider matching,
  checking capacity/duplicate scheduling and command races are in the broad suite;
  final baseline failures are listed separately, not waived as architecture proof.

## 8. Storage/retention

The actual retention query excludes pages belonging to the active publication
pointer irrespective of mutable grouping status. Withdrawal grace joins immutable
withdrawals and remains **30 days**. Shared references are excluded; academic
artifact rows/history remain immutable while eligible bytes can be purged.
Student routes stream only authorized normalized PNG pages with
`Cache-Control: private, no-store`; there is no student source-PDF route/fallback.
New publication verifies required page bytes. A missing render fails boundedly
instead of publishing an unusable snapshot.

Batch-before-page lock ordering, retention claims/fencing, orphan scan-object
journaling and uncertain-commit preservation remain in current code. The affected
withdrawal/recovery tests passed. Originals remain academic evidence after byte
purge. Restore from migration downgrade does not restore external bytes.

## 9. Student privacy

Fresh assignment DTO/network response was recursively checked for forbidden
identity/roster/storage/raw AI/teacher-reason keys (`student-response.json`).
The current student UI uses only active publication snapshot items/findings,
server-calculated score/percentage/final grade and authorized page metadata.
Publication history, withdrawal reason, source PDF, provider/model/prompt, raw AI
finding provenance and grade-override reason are absent. Cross-student page/result
substitution returned 404. Browser visible text contains no UUIDs.

Opaque publication/paper/page/finding/item IDs are authorization-scoped navigation
or selection identifiers. Older student result/list routes retain approved-revision
IDs; shared remediation execution contains an own execution `check_run_id`.
These are identifiers, not CheckRun/ModelRun objects or provider data; they are not
displayed and confer no access. This is L1 minimization debt, not proof of a
cross-student privacy leak. The normal assignment result removes the revision ID.

Checking provider construction was inspected in `compile_paper_request` and
`AnthropicPaperCheckingProvider.check`: only frozen task/rubric context, anonymous
item/page/content tokens and page image blocks cross that boundary. One call
checks one confirmed paper. No roster, student/account/participant ID or storage
path is added. Matching has its separate minimal roster contract. Names written
on the scanned page can remain visible; image anonymization is not claimed.
Provider doubles cannot prove real-model prompt-injection/quality behavior.

## 10. Remediation provenance

Published grades come from the frozen deterministic policy and approved accepted
item scores. `build_publication_snapshot` recalculates and compares totals,
maxima, percentage and calculated grade against the approved result before
publication. An explicit grade override preserves the calculated grade and keeps
its reason teacher-only; student final grade comes from the immutable snapshot.
AI free-form totals are not authoritative. Browser scores use the server's
decimal formatting and do not recalculate academic truth.

`PaperRemediationRepository.source/selected` reads the exact active snapshot,
validates publication/review/paper/assignment/participant/variant correspondence,
and derives weak items from published accepted scores/findings. Removed raw AI
findings cannot reappear as signals. Manual source has null CheckRun, AI source
has a real run, and digital provenance remains non-null and strict.

Assigned plan execution remains owned by the student and its original immutable
source. Fresh browser and real HTTP tests completed manual/AI plans after source
replacement without changing the original publication/grade. No migration from A
to B is performed. Student plan screens do not expose internal source lineage.

## 11. Performance/query sanity

The publication workspace reads one main checking projection after scope checks;
it does not fetch each paper's history at mount. History is loaded on demand.
The projection builds the paper/review/publication join as a set; selected page
bytes are fetched once per selected URL, with no request per finding. StrictMode
may repeat a development request; unique page URL/request strategy is the metric.
Student traffic does not fetch teacher history or raw AI routes. Remediation
student-list item counts use a grouped outer join, not task-by-task queries.

Bulk writes still do bounded linear per-paper policy/review/page validation and
ledger writes inside one transaction. The earlier audit measured 611 publication /
189 withdrawal SQL statements for 30 papers; those numerical SQL counts were not
remeasured here. Current source still contains the per-paper work. M1 remains a
non-blocking write-efficiency issue at current bounds. No claim of load/capacity
production benchmarking is made.

## 12. Reload/recovery

| Location | Fresh evidence |
|---|---|
| Intake/extraction completed | browser reload reconstructed files/pages before matching. |
| Extraction pending | source/polling and existing tests; no fresh process-kill browser gate. |
| Matching/grouping | reload before confirmation; confirmed state derived from backend. |
| Checking/review | exact deep link per paper, server projection and immutable review reads; affected stale-version tests. |
| Publication/completed | browser reload retained active result and completed batch; withdrawal/republish did not restart checking. |
| Student result | reload, exact page/highlight selection, withdrawal refetch clears result/image. |
| Teacher draft | browser draft reload and stale-source assignment failure. |
| Assigned student plan | saved answers survive reload; checked execution persists. |
| Actual backend process restart | PASS over HTTP: production policy/manual-review/edit/approve/publish routes were committed on a separate disposable database; the source process exited, a new application process read the identical student result and byte-identical authorized PNG. `restart-proof.json`. Browser fixtures separately remain rollback isolated. |

Matching crash boundaries, orphan byte reconciliation, partial writes, uncertain
commit acknowledgements and downgrade-history refusal are covered by the affected
recovery tests. The separate committed restart proof supplements those tests;
browser harness setup restarts are not used as durability evidence. No paper/remediation academic
truth is stored in localStorage/IndexedDB. In-memory retry keys are transient.

## 13. Frontend UX/accessibility/responsive

1440 px and 760 px screenshots and DOM width assertions cover fresh matching,
review/publication/student result and manual/AI remediation builder/submission/
completion. No horizontal document overflow or clipped primary action was found
in those states. Actual screenshots were inspected, including 760 px matching,
publication and submission dialog. Selection/status has text/pressed semantics,
not color alone. Manual browser score controls/labels and review confirmation are
reachable; remediation confirmation has keyboard/cancel/focus tests in the real
browser harness. Stale source errors are bounded and source IDs are not retargeted.

This is limited sanity, not a full accessibility certification. Keyboard traversal
of every matching/review/publication/withdrawal control and 30-paper intake at both
widths was not exhaustively rerun. Error mapping uses structured status/code,
not message substring matching. No raw stack/error object was observed in UI.

Auth logout/401 removes the authenticated application tree; paper components use
local state with abort/cleanup, not a shared query-client cache. The code and
existing auth/component tests cover this mechanism. Real logout/login as different
users in one browser was not run because fixture identity is dependency-injected.
M4 records that acceptance gap instead of claiming a real-session cache gate.

## 14. Findings by severity

| ID | Severity | Finding / current disposition |
|---|---|---|
| B1 | **BLOCKER, OPEN** | Fresh teacher UI has no transition to provision required batch/variant grading policies. Create/upload/extract/match/confirm succeeds; start checking returns 409 and directs teacher to an administrator, with no policy screen/action. A clean mandatory teacher flow cannot complete. API-assisted continuation is a prerequisite workaround, not an accepted fix. |
| H1 | **HIGH, FIXED** | Existing grading-policy POST always rejected its valid serialized strict contract (arrays/Decimal strings) before service execution. JSON-mode HTTP adapter now accepts the contract, preserves strict domain checks and schema; 4 new focused tests and 138 affected tests pass. |
| M1 | MEDIUM, non-blocking | Bounded linear bulk write SQL remains; set-based projections and one command transaction are correct. Revisit before increasing batch bounds. |
| M2 | MEDIUM, non-blocking | Teacher publication/review/batch histories remain unbounded; remediation teacher-history pagination lacks complete parameter bounds. Existing debt, no observed privacy/correctness bypass. |
| M3 | MEDIUM, non-blocking | Several scan/publication/manual-review routes have incomplete OpenAPI request/response models. Handwritten frontend contracts were checked against actual responses; H1 alone was changed. |
| M4 | MEDIUM, verification gap | Complete anonymous browser matrix and real same-browser logout/login cache gate not freshly executed; current guards/source/tests are documented, not substituted as E2E evidence. Persisted result/page access across a new backend process was separately verified. |
| L1 | LOW, non-blocking | Legacy student result/list and shared remediation execution expose scoped opaque internal revision/execution IDs. No raw metadata/object access or visible UUID; minimize separately if contract compatibility permits. |
| L2 | LOW, coverage gap | Accessibility/responsive sanity is representative; exhaustive keyboard coverage and 30-paper intake browser exercise remain unperformed. |
| L3 | LOW, inherited test debt | Broad baseline failures are recorded below. The obsolete expected head and incorrect overlong-name exception assertion both reproduce on the exact base SHA; the actual database rejects the invalid name correctly. They are not hidden as passing gates. |

Any broad-suite failures are additionally classified in the final baseline addendum;
they must not be silently converted into product acceptance.

## 15. Fixes made during audit

Only `backend/app/presentation/scan_intake_routes.py` product code changed:
a `BeforeValidator` on the grading-policy HTTP body revalidates JSON dictionaries
with `ScanGradingPolicy.model_validate_json`. This preserves the original domain
model, Decimal precision, UUID/tuple semantics, forbidden extra fields and OpenAPI
model reference. The repository's policy coverage/instruction/version checks,
authorization and locking remain authoritative.

Added `backend/tests/integration/test_paper_grading_policy_http_database.py`:
valid freeze/readback/replay → batch schedule, plus malformed/NaN/extra-field
rejection. The original valid-input 422 is recorded before the fix. No frontend,
database schema, grading policy content or product workflow feature changed.

## 16. Known accepted debt

Existing Vite bundle warning; unbounded teacher histories; bounded per-paper bulk
write cost; partial OpenAPI coverage; extraction holds its aggregate transaction
while doing bounded render/storage work; incomplete direct-SQL cross-aggregate
identity freezing. Provider quality/security semantics with a live model remain
separate. These debts do not waive B1 or any failing mandatory acceptance gate.

The test system has known PostgreSQL template clone/connection timing and Windows
runtime/temp-directory debt. Only clean complete reruns count as final passing
evidence. Initial harness mistakes and aborted attempts are retained separately.

## 17. Final production-readiness decision

**NOT ACCEPTED.** B1 remains open, and the full baseline retains the two
independently reproduced inherited test failures. The policy API fix enables an explicit
administrator/API provisioning prerequisite and the downstream integrated flow,
but the requested fresh end-user workflow is incomplete. No new policy authoring
feature or arbitrary grading defaults were invented to produce a green audit.

Do not merge or push this audit automatically. Review the narrow fix and decide
the supported grading-policy provisioning workflow before a new acceptance run.

### Complete route inventory

The following table is generated from the audited base's actual decorators.

| Router | Method | Path (including router prefix) | Handler |
|---|---|---|---|
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}/checking` | `paper_batch_progress` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/checking/start` | `start_batch_checking` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/checking/reconcile` | `reconcile_batch_checking` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/checking/execute` | `execute_batch_checking` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/complete` | `complete_paper_batch` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/assignments/{assignment_id}/scan-batches` | `create_batch` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/assignments/{assignment_id}/scan-batches` | `list_batches` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}` | `get_batch` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/cancel` | `cancel` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/artifacts` | `upload` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}/artifacts` | `artifacts` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}/pages` | `pages` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}/artifacts/{artifact_id}/content` | `artifact_content` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/extract` | `extract` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-pages/{page_id}/content` | `page_content` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/match` | `match_pages` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}/matching` | `matching_results` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/grouping` | `initialize_grouping` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}/grouping` | `get_grouping` |
| `scan_intake_routes.py` | PATCH | `/api/assessment-core/scan-batches/{batch_id}/grouping/pages/{page_id}` | `assign_grouping_page` |
| `scan_intake_routes.py` | PUT | `/api/assessment-core/scan-batches/{batch_id}/grouping/groups/{participant_id}/page-order` | `reorder_grouping_pages` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/grouping/confirm` | `confirm_grouping` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/grading-policies/{variant_id}` | `freeze_grading_policy` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/scan-batches/{batch_id}/grading-policies` | `list_grading_policies` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs` | `create_paper_check_run` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/execute` | `execute_paper_check` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/ai-result` | `get_paper_ai_result` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review` | `start_paper_review` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review` | `get_paper_review` |
| `scan_intake_routes.py` | PUT | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review` | `edit_paper_review` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review/history` | `paper_review_history` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review/approve` | `approve_paper_review` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/manual-review` | `start_manual_paper_review` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/paper-submissions/{paper_submission_id}/manual-review` | `get_manual_paper_review` |
| `scan_intake_routes.py` | PUT | `/api/assessment-core/paper-submissions/{paper_submission_id}/manual-review` | `edit_manual_paper_review` |
| `scan_intake_routes.py` | GET | `/api/assessment-core/paper-submissions/{paper_submission_id}/manual-review/history` | `manual_paper_review_history` |
| `scan_intake_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/manual-review/approve` | `approve_manual_paper_review` |
| `paper_result_publication_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/publications` | `publish_paper` |
| `paper_result_publication_routes.py` | GET | `/api/assessment-core/paper-submissions/{paper_submission_id}/publications` | `publication_history` |
| `paper_result_publication_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/publications/selected` | `publish_selected` |
| `paper_result_publication_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/publications/bulk` | `publish_bulk` |
| `paper_result_publication_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/publications/{publication_id}/withdraw` | `withdraw_publication` |
| `paper_result_publication_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/publications/withdraw/selected` | `withdraw_selected` |
| `paper_result_publication_routes.py` | POST | `/api/assessment-core/scan-batches/{batch_id}/publications/withdraw/bulk` | `withdraw_selected` |
| `paper_result_publication_routes.py` | GET | `/api/assessment-core/student/paper-submissions/{paper_submission_id}/result` | `student_result` |
| `paper_result_publication_routes.py` | GET | `/api/assessment-core/student/paper-results` | `student_results` |
| `paper_result_publication_routes.py` | GET | `/api/assessment-core/student/assignments/{assignment_id}/paper-result` | `student_assignment_result` |
| `paper_result_publication_routes.py` | GET | `/api/assessment-core/student/paper-submissions/{paper_submission_id}/pages/{scan_page_id}` | `student_page` |
| `remediation_routes.py` | POST | `/api/remediations/candidates/search` | `candidates` |
| `remediation_routes.py` | POST | `/api/remediations` | `create` |
| `remediation_routes.py` | GET | `/api/remediations/{id}` | `get` |
| `remediation_routes.py` | GET | `/api/remediations/{id}/result` | `remediation_result` |
| `remediation_routes.py` | PATCH | `/api/remediations/{id}` | `update` |
| `remediation_routes.py` | POST | `/api/remediations/{id}/assign` | `assign` |
| `remediation_routes.py` | POST | `/api/remediations/{id}/cancel` | `cancel` |
| `remediation_routes.py` | GET | `/api/classrooms/{class_id}/students/{student_id}/remediations` | `history` |
| `remediation_routes.py` | GET | `/api/student/remediations` | `student_list` |
| `remediation_routes.py` | GET | `/api/student/remediations/{id}` | `student_detail` |
| `remediation_routes.py` | GET | `/api/student/remediations/{id}/execution` | `execution` |
| `remediation_routes.py` | POST | `/api/student/remediations/{id}/start` | `start_execution` |
| `remediation_routes.py` | PUT | `/api/student/remediations/{id}/answers/{item_id}` | `save_execution_answer` |
| `remediation_routes.py` | DELETE | `/api/student/remediations/{id}/answers/{item_id}` | `delete_execution_answer` |
| `remediation_routes.py` | POST | `/api/student/remediations/{id}/submit` | `submit_execution` |
| `remediation_routes.py` | GET | `/api/assessment-core/paper-submissions/{paper_submission_id}/remediation-eligibility` | `paper_eligibility` |
| `remediation_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/remediations/candidates/search` | `paper_candidates` |
| `remediation_routes.py` | POST | `/api/assessment-core/paper-submissions/{paper_submission_id}/remediations` | `paper_create` |

### HTTP/error, provider and contract evidence

Captured fixture traffic: **1,349 responses, zero HTTP 500s** at final capture.
Statuses: 1,110 HTTP 200, 34 HTTP 201, 13 HTTP 409, 123 HTTP 404,
13 HTTP 422 and 56 HTTP 403. This includes expected negative probes and
audit-harness precondition attempts; it is not a production traffic sample.
Teacher/private test snapshots in evidence are synthetic audit artifacts,
not application logging. Source inspection found no normal logging of page
bytes, full rosters, prompts, auth tokens or publication snapshots. The paper
reconciliation exception logger uses a fixed message; database echo defaults off.

Contract checks include manual nullable CheckRun, student result allowlist,
publication projection active revision identity and H1 policy JSON/OpenAPI.
The current policy adapter preserves its named schema reference. No enum or
required/nullable mismatch was found that required another product change.
The legacy/internal identifier minimization and incomplete OpenAPI surfaces
are recorded as debt, rather than represented as complete schema generation.

Focused coverage already present and rerun includes
`test_thirty_paper_projection_one_query_and_bulk_pending`: a 30-paper projection
executes exactly **one SQL statement** after loading the authorized batch.
This is fresh automated query-count evidence, separate from the earlier bulk
write counts quoted in section 11.

Audit-only backend/Vite browser services were stopped after evidence collection;
their synthetic browser transactions rolled back. Existing unrelated services,
untracked evidence and `local-backups/` were preserved. Disposable audit databases
and raw logs remain available locally for inspection.

Separate actual restart proof: production-route manual review/publication was
committed, its creating process exited, and a new application process returned
the identical active student JSON and byte-identical authorized PNG
(`restart-proof.json`). That server was also stopped after verification.

### Broad backend baseline addendum

**2311 passed, 4 skipped, 2 failed, 0 errors / 2317 total**.

| Failed/error node | Captured failure |
|---|---|
| `tests.integration.test_execution_target_generalization_database.test_schema_metadata_remains_explicit_and_at_expected_head` | AssertionError: assert '20261013_01' == '20260911_01'      - 20260911_01   ?      - ^   + 20261013_01   ?     +  ^ |
| `tests.integration.test_migrations.test_user_structured_names_migration_preserves_legacy_rows` | sqlalchemy.exc.DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.StringDataRightTruncationError'>: value too long for type character varying(100) [SQL: UPDATE users SET last_name=$1 WHERE id=$2] [parameters: ('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx', UUID('4447f58b-ba33-41d5-ae62-90bfd768ef8e'))] (Ba |

The first two integration failures also reproduced on the exact unmodified base SHA in `baseline-failures.xml`: obsolete hard-coded head `20260911_01` and expecting `IntegrityError` instead of `DBAPIError` for PostgreSQL `StringDataRightTruncationError`. The overlong input is correctly rejected. No migration or product fix was made for these test assertions.

All **1,322 unit tests passed**. Integration totals: **989 passed, 4 skipped, 2 failed**. The four gated migration skips separately completed with **4 passed**. The final Linux full run needed **zero infrastructure retries**; aborted Windows setup attempts are separately disclosed and not counted.

### Requested 46-field completion ledger

| # | Field | Result |
|---|---|---|
| 1 | Branch | `codex/paper-full-flow-audit` |
| 2 | Audit commit SHA | Reported in final delivery; cannot embed a commit's own hash in itself. |
| 3 | Base SHA | `bfb4a52ebfb895a04ccca2b936eba90290c9c5d1` |
| 4 | Exact changed files | `backend/app/presentation/scan_intake_routes.py`; `backend/tests/integration/test_paper_grading_policy_http_database.py`; this report. |
| 5 | Architecture map | 66 actual route registrations, frontend routes, models/DTOs, migrations, commands, auth/storage/retry boundaries above. |
| 6 | Backend totals | **2311 passed, 4 skipped, 2 failed, 0 errors / 2317 total** |
| 7 | Frontend totals | 650 passed, 53 files, stable full run; initial parallel-load failures retained separately. |
| 8 | Typecheck | PASS, `npx tsc -b --pretty false`. |
| 9 | Build | PASS, existing bundle advisory; 597.59 kB JS / 160.09 kB gzip. |
| 10 | Alembic head | Single `20261013_01`; dedicated DB current matches. |
| 11 | Happy-path E2E | BLOCKED as clean browser flow, B1. PASS downstream with explicit production-API policy provisioning. |
| 12 | Manual-path E2E | PASS through production routes and browser; null CheckRun/AI lineage, no extra paper provider calls. |
| 13 | Withdrawal/republish | PASS; distinct new publication, student page/result denied after withdrawal, old replay cannot restore visibility. |
| 14 | Corrected revision | PASS; approval alone preserves A; explicit publish activates B. |
| 15 | Completion | PASS; reject before publication; remains completed after withdrawal/republish; no new checking lineage. |
| 16 | Stale remediation | PASS; HTTP 409, bounded browser message, unchanged source. |
| 17 | Assigned remediation survival | PASS; old-source plan executes/checks and preserves paper result. |
| 18 | Authorization matrix | 72 route probes plus affected HTTP tests/browser; no bypass. Anonymous fresh-browser gap explicitly recorded. |
| 19 | Privacy | Normal assignment DTO recursively checked; scoped opaque legacy IDs documented; no raw AI/history/source PDF/private reason. |
| 20 | Provider payload | Anonymous one-paper checking contract; separate minimal matching roster; visible writing is not anonymized. |
| 21 | Idempotency | Durable same-key replay/different-payload conflict; frozen frontend command key/body; official single/selected/bulk commands. |
| 22 | Concurrency | Stale exact withdrawal cannot target replacement; review/grouping CAS, source assignment lock; real PostgreSQL races in affected/full evidence. |
| 23 | Crash/recovery | 138 affected tests; committed result and page verified from a new backend process; lease/orphan/downgrade safeguards retained. |
| 24 | Retention | Active-pointer protection, 30-day withdrawal grace, eligible-byte purge, missing-page safe failure; no student source PDF. |
| 25 | Result calculation | Deterministic frozen policy; calculated/final grade separation; teacher-only override reason. |
| 26 | Publication snapshot | Immutable; later review/approval does not change current student result until explicit publication. |
| 27 | Removed finding | PASS; omitted from student snapshot; rejected as remediation source. |
| 28 | Error/500 audit | Zero HTTP 500 in captured fixture traffic; bounded 403/404/409/422; invalid policy JSON fixed. |
| 29 | Contract drift | H1 fixed with preserved OpenAPI reference; nullable manual and digital provenance tests pass; incomplete schemas remain debt. |
| 30 | Performance | 30-paper progress exactly one SQL statement in passing affected test; one main browser projection, lazy history/page, aggregate plan item count. |
| 31 | Reload/deep links | Representative browser matrix and committed new-process proof pass; extraction-pending process-kill not rerun. |
| 32 | Role-switch cache | Logout unmount/abort/local state inspected and component-tested; actual real-login same-browser scenario not freshly executed. |
| 33 | Accessibility | Critical labels/status/focus/keyboard submission sanity; exhaustive keyboard path not claimed. |
| 34 | Responsive | 1440/760 screenshots and DOM width checks pass for representative required screens. |
| 35 | BLOCKER findings | 1 OPEN: B1 grading-policy provisioning missing in fresh teacher browser flow. |
| 36 | HIGH findings | 0 open; H1 fixed. |
| 37 | MEDIUM findings | 4: bounded linear writes, histories, OpenAPI completeness, remaining session verification gaps. |
| 38 | LOW findings | 3: opaque ID minimization, representative UX coverage gaps, inherited baseline test debt. |
| 39 | Fixes | Only strict grading-policy HTTP JSON boundary plus four focused regression cases. |
| 40 | Accepted debt | Existing Vite advisory and explicitly listed bounded/history/schema/DB hardening debt; B1 is not accepted debt. |
| 41 | Product code changed | YES: one backend route module. |
| 42 | Backend changed | YES: HTTP boundary and integration regression test. |
| 43 | Frontend changed | NO. Not a tests/docs-only change. |
| 44 | Tracked status | Final delivery verifies clean tracked tree after explicit three-file audit commit. |
| 45 | Untracked preservation | All six original untracked entries preserved; audit evidence added only under existing untracked local-backups/. |
| 46 | Final verdict | SCANNED-PAPER WORKFLOW PRODUCTION-READINESS NOT ACCEPTED. No merge; no push. |
