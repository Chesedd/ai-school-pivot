# Frontend PR5 implementation and acceptance status

Date: 2026-10-15 (Europe/Moscow). Required main:
`23964ff2f2a151ab3b2ac59be460785ac234707e`.
Branch: `codex/paper-student-result-remediation-ui`.
Rebased historical blocker/probe commit:
`7c56228bf6e1bb9db69310cac57e2af64fca253f`.

The functional implementation is local. **PR5 acceptance is incomplete**:
the in-app browser stopped delivering input after the old native remediation
submit confirmation. Its dialog API returned no dialog, and documented keyboard,
fresh-tab, and originating-tab recovery did not restore input. The user has been
asked to dismiss the old confirmation. Backend HTTP evidence is reported separately
and does not replace the mandatory remaining browser scenarios. This is not the
final full-flow audit or an accepted final PR5 candidate.

## Exact changes relative to required main

- `docs/frontend-paper-pr5-contract-blockers.md`
- `docs/frontend-paper-pr5-validation.md`
- `docs/pr5_contract_probe.py` (retained historical rebased commit, unchanged)
- `frontend/src/PaperPlanSource.tsx`
- `frontend/src/PaperPublicationPage.tsx`
- `frontend/src/PaperRemediationPage.test.tsx`
- `frontend/src/PaperRemediationPage.tsx`
- `frontend/src/ScanPageViewer.tsx`
- `frontend/src/StudentAssessmentPages.test.tsx`
- `frontend/src/StudentAssessmentPages.tsx`
- `frontend/src/StudentPaperResult.test.tsx`
- `frontend/src/StudentPaperResult.tsx`
- `frontend/src/StudentRemediationDetailPage.test.tsx`
- `frontend/src/StudentRemediationsPage.tsx`
- `frontend/src/TeacherRemediationDetailPage.tsx`
- `frontend/src/errors.ts`
- `frontend/src/main.tsx`
- `frontend/src/paperRemediationApi.ts`
- `frontend/src/paperStudent.css`
- `frontend/src/remediationApi.ts`
- `frontend/src/studentPaperApi.ts`

## Merged endpoints actually used

| Purpose | Method/path |
| --- | --- |
| Student assignment result | GET `/api/assessment-core/student/assignments/{assignment_id}/paper-result` |
| Student normalized page | GET `/api/assessment-core/student/paper-submissions/{paper_submission_id}/pages/{scan_page_id}` |
| Teacher paper eligibility | GET `/api/assessment-core/paper-submissions/{paper_submission_id}/remediation-eligibility` |
| Teacher immutable source signals | GET `/api/assessment-core/paper-submissions/{paper_submission_id}/publications`, exact eligibility/plan publication selected internally |
| Teacher candidates | POST `/api/assessment-core/paper-submissions/{paper_submission_id}/remediations/candidates/search` |
| Teacher explicit draft creation | POST `/api/assessment-core/paper-submissions/{paper_submission_id}/remediations` |
| Teacher plan read | GET `/api/remediations/{id}` |
| Teacher explicit assignment | POST `/api/remediations/{id}/assign` |
| Existing teacher plan discovery | GET `/api/classrooms/{class_id}/students/{student_id}/remediations` on existing student detail |
| Existing teacher execution/result | GET `/api/remediations/{id}/result` |
| Student assigned plan discovery | GET `/api/student/remediations?offset={offset}&limit=20` |
| Existing student plan detail | GET `/api/student/remediations/{id}` (available shared binding; execution screen uses aggregate execution DTO) |
| Student execution/results | GET `/api/student/remediations/{id}/execution` |
| Student start | POST `/api/student/remediations/{id}/start` |
| Student answer | PUT/DELETE `/api/student/remediations/{id}/answers/{item_id}` |
| Student submit | POST `/api/student/remediations/{id}/submit` |

Context authorization uses existing teacher assignment, batch, and checking
projections. Student result/page calls never use those routes. Student identity is
always resolved by the authenticated principal; no selectable student parameter.

## Requested completion checklist

| # | Item | Evidence/status |
| --- | --- | --- |
| 1 | Branch | `codex/paper-student-result-remediation-ui` |
| 2 | Rebased blocker commit | Exact SHA above; actual old merge base `e343d509146f5abc6462b4b53c322490fff6b025`; conflict-free rebase |
| 3 | Final PR5 SHA | Implementation commit HEAD is reported in chat; **not yet an accepted final candidate** because browser acceptance remains incomplete |
| 4 | Exact changed files | 21 paths above; unrelated untracked files preserved |
| 5 | Blocker document | Historical report/probe preserved; narrow merged-resolution section added |
| 6 | Assignment-scoped endpoint | First endpoint table row; actual OpenAPI DTO independently checked |
| 7 | Student page endpoint | Second endpoint table row; cache `no-store`, authenticated cookies; no content URL fetch fallback |
| 8 | Teacher endpoints | Eligibility, immutable source history, candidates, create, shared plan/assign/result; table above |
| 9 | Student remediation endpoints | Existing list/start/answers/submit/execution; table above |
| 10 | Navigation | Student assignments → assignment → embedded current result → existing remediation list. Teacher class → paper batches → publications → secondary Additional tasks → explicit draft → shared detail. Existing teacher student detail lists durable drafts/assigned plans |
| 11 | Active-only result | One assignment-scoped response; no client history or merged snapshots |
| 12 | Withdrawal | Clear snapshot/image at refetch start, including focus; 404 no-active state never retains old result. Real browser refetch/reload passed; student page denied 404 via real HTTP |
| 13 | Republish | New response replaces old result and remounts page by publication identity. Real browser saw `Replacement B result` and no original summary |
| 14 | Score/grade | Server total/max/percentage/final grade, without client recalculation; real browser 10/20, 50%, server final grade |
| 15 | Items/findings | Snapshot item order and four canonical statuses; safe comments and immutable findings. Real five-item/five-finding browser fixture |
| 16 | Viewer reuse | PR2/PR3 `ScanPageViewer` reused with explicit student byte loader; existing teacher default retained |
| 17 | Regions | Shared `regionStyle`, actual one point/box per finding. Browser verified selected box style and page image; two-page switching/selection tested in focused suite |
| 18 | Student privacy | Allowlisted rendering only; IDs remain keys/API parameters. No raw CheckFinding/PaperAIRevision/review/history/source-PDF calls; no withdrawal reason rendered; adversarial extra-field test |
| 19 | Eligibility | Backend boolean/weak item IDs/reason, never frontend thresholds. UI states follow actual endpoint, which does not expose existing-plan/stale-plan metadata |
| 20 | Candidates/signals | Exact immutable publication signals and candidate API reasons/skill name; no AI enrichment. No-results/manual-search bounded state |
| 21 | Draft creation | Explicit action only; selected catalog versions. Frozen body/source/key retained on uncertain failure, unlocked after definitive 4xx; no automatic assignment |
| 22 | Assignment | Explicit shared detail action; plan/result refetched after success; manual co-teacher browser assignment passed |
| 23 | Stale draft | Exact backend 409 `remediation_source_publication_stale`; bounded message, disable assign, refetch plan and current eligibility; source never retargeted. Focused UI and real PostgreSQL HTTP passed; browser stale-action scenario pending |
| 24 | Assigned old source | Assigned plans are never gated on current eligibility; original immutable source used for teacher signals. Real PostgreSQL completion after withdrawal/replacement passed; browser opened and persisted answers before submit blockage |
| 25 | New B source | Fresh builder binds current eligibility publication; no migration of old plan. Focused test and real PostgreSQL new-source creation passed |
| 26 | Manual null CheckRun | Discriminated shared frontend type permits null for paper. OpenAPI/HTTP response proof passed; real co-teacher browser create/read/reload/assign passed |
| 27 | AI source | Paper type retains real CheckRun internally, hidden in UI. Focused UI and real PostgreSQL create/assign/execute/check/complete passed; browser AI-plan flow pending |
| 28 | Digital compatibility | Digital branch still requires non-null CheckRun/submission; backend validator rejects digital null. Existing builder retained; full suite passes. Shared answer editor/checking/result reused |
| 29 | Student discovery | Existing list with keyboard-accessible title buttons, bounded errors, 20-item pagination. Browser discovered manual assigned plan through result CTA |
| 30 | Execution/checking | Existing aggregate execution DTO, shared answer editor and save/delete/start/submit calls. Shared native submit confirmation replaced with existing focus-trapped application `Dialog`; dirty answers block submission |
| 31 | Completion | Existing checked/result cards and durable saved answers; backend HTTP manual and AI completion passed and original paper result unchanged. Remaining browser submit/completion pending |
| 32 | Co-teacher | Actual co-teacher normal navigation, publish, eligibility, candidate selection, draft creation, reload, assign passed. Backend plan-owner rule retained |
| 33 | Negative authorization | Real PostgreSQL HTTP owning principal result, foreign assignment/result/page/plan/start/task substitutions, unrelated/revoked teachers denied. UI bounds failures by code/status; remaining negative browser navigation pending |
| 34 | Focused tests | 77 passed across four focused files: 40 new result/remediation lifecycle tests, two shared submit tests and retained shared execution/result tests. `focused-final.log` records the run |
| 35 | Full frontend suite | 650 passed across 53 files with `--maxWorkers=1`, increased from 608; final output in `full-suite-final.log` |
| 36 | Typecheck | `npx tsc -b` passed |
| 37 | Build | `npm run build` passed |
| 38 | Vite advisory | Same >500 kB advisory. JS 597.59 kB / gzip 160.09 kB vs PR4 581.29 / 155.78: +2.8% / +2.8%. No broad code splitting |
| 39 | Student result E2E | Real PostgreSQL browser passed assignment navigation, score/grade, items, manual comments, page, region selection, privacy |
| 40 | Withdrawal E2E | Real PostgreSQL browser passed refetch/reload/no-result; page 404 proven with real HTTP while student held result open |
| 41 | Republish E2E | Real PostgreSQL browser passed replacement-only rendering; no old summary/history |
| 42 | Teacher remediation E2E | Manual-source co-teacher browser passed navigation, eligibility, candidates, draft, reload, assign and student discovery |
| 43 | Stale draft E2E | Focused UI + real PostgreSQL HTTP pass; mandatory real browser stale-assignment interaction still pending |
| 44 | Assigned survives replacement E2E | Browser opened old assigned plan and persisted/reloaded answers after replacement. HTTP submit/check/complete passed in a separate rollback fixture. Browser submit/completion pending |
| 45 | Manual source E2E | Browser create/read/reload/assign/open/start/save passed; HTTP null provenance and completion passed. Browser completion pending |
| 46 | Authorization E2E | Real PostgreSQL HTTP denials passed; browser cross-ID/denied UI scenario pending |
| 47 | Student 1440 | Readable summary, items/comments and useful highlighted viewer; DOM width/scroll width both 1425 px; saved screenshot |
| 48 | Student 760 | Stacked result/items/pages/CTA; DOM width/scroll width both 745 px; saved screenshot |
| 49 | Teacher responsive | Builder 1440 and 760 screenshots saved, 760 no horizontal overflow (745/745). Final small checkbox alignment refinement and shared submission dialog await browser recheck |
| 50 | Performance | Selected page only; no request per finding; no student history/raw-review calls. Shared aggregate execution loads all tasks in one request. Identity-scoped component effects/aborts replace cache query keys in this repository's existing architecture; mutations refetch only the plan/execution/current paper scope, no full-app refresh |
| 51 | Backend diff | Empty relative to required main; no backend changes |
| 52 | Alembic | Single `20261013_01 (head)` verified |
| 53 | Backend blocker | None discovered in the implemented paths; remaining blocker is browser interaction/acceptance |
| 54 | Final audit | Not started |
| 55 | Push/merge | Nothing pushed or merged |

## Evidence and limits

Evidence remains in untracked `local-backups/frontend-pr5-acceptance/`:
`current-contract.json`, `requests.log`, `initial-browser-state.json`,
`http-state.json`, scripts, suite/build logs, and responsive/state screenshots.
The real PostgreSQL fixtures use the disposable `ai-school-pg-test` database and
rollback-isolated sessions. Only canonical session-to-principal resolution is
overridden; production capabilities, object authorization and mutation routes
remain active. The paper provider is deterministic/fake; remediation checks use
the existing real deterministic checking infrastructure and authored practice
answers. No production credentials, academic data or provider calls are used.

The initial browser fixture was restarted after an acceptance-only checker helper
referenced a nonexistent `check_runs.created_at` column and poisoned its outer
transaction. The corrected separate HTTP fixture verifies completion/provenance,
but is not represented as browser completion evidence. Its source-change commands
use official manual-review edit/approve/publication routes. The old historical
probe was not rewritten or run as though its pre-hotfix assertions still apply.

Remaining acceptance before designating a final PR5 candidate: dismiss the stuck
native browser confirmation; recheck the shared application submit dialog and
teacher responsive refinement; finish real browser stale draft, old-source manual
completion, AI-source flow, multi-page switching and negative authorization.
