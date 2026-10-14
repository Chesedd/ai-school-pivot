# Frontend PR3 checking/review workspace acceptance

Verified locally on 2026-10-14 against accepted backend/main
`e7b4cc2034fda380a3d58c5a2757330adfdcc9a0`. PR3 ends at approved teacher review.
The original contract audit remains in its rebased commit and document.

The supported checking, AI/manual review, CAS and approval paths are implemented
and validated. Regionless and multiple regions for a **single finding** remain
unavailable in the frozen backend contract. Those specific paths were stopped;
no backend endpoint, payload or geometry was invented. Item comments may have no
region. Multiple distinct findings, each with its own region, remain supported.

## Requested completion report

| # | Requested result | Evidence / behavior |
| --- | --- | --- |
| 1 | Branch | `codex/paper-checking-review-ui` |
| 2 | Rebased audit commit | `cdb6a8905dc65a60a707c0b392e99cf0462c62e0`; actual old base determined by Git as `3b09b0324b7e0ddf236db58c63ac0a6d1c65796f`; rebase clean |
| 3 | Final PR3 SHA | Supplied by final `git rev-parse HEAD` in delivery; this report is included in the candidate commit |
| 4 | Changed files from main | Exact inventory below; frontend and two documentation files only |
| 5 | Audit update | Resolution section identifies hotfix SHA, new actual contracts and remaining region limitation; original three blocker sections and original inspection evidence retained |
| 6 | Checking endpoints | GET `/scan-batches/{batch}/checking`; POST same path + `/start`, `/execute`, `/reconcile`; prefix `/api/assessment-core` throughout |
| 7 | Scheduling/execution contract | Start durably schedules missing initial runs; execute selects pending effective runs on server and advances at most its configured capacity window sequentially; outcome failures use actual `code` field |
| 8 | Effective run projection | `papers[].effective_check_run_id`, `checking_state`, review identity/state/approved pointer and capability flags; no frontend historical-run precedence |
| 9 | Route changes | Existing `/classrooms/{class}/assignments/{assignment}/paper/{batch}/checking` now dispatches to `PaperCheckingPage`; placeholder removed; PR2 CTA reused |
| 10 | State mapping | Not started → Не проверялась; pending → Ожидает очереди; running → Проверяется; successful reviewable → Нужно проверить; retryable failure → Можно повторить; terminal failure → Проверка не удалась; persisted review → Черновик/Подтверждено; manual source explicitly marked |
| 11 | Batch start | One guarded high-level POST; refetch scheduled progress before one bounded execution call; no browser loop creating runs |
| 12 | Bounded execution | Explicit Продолжить проверку advances another server-owned window. Durable pending work survives leaving/reloading. No autonomous worker is promised and no client queue/capacity calculation is introduced |
| 13 | Polling | Non-overlapping abortable GET checking every 3 seconds only while pending/running; pauses while hidden; focus revalidates; stops when stable. Commands refetch relevant progress. Shared review identity/lifecycle changes load the review without discarding local edits |
| 14 | Capacity/backpressure | Real three-claim PostgreSQL admission fixture returned capacity denial; UI showed waiting. Pending provider-attempt count and retry-count sum stayed zero; no provider calls occurred for the denied window |
| 15 | Retry | POST `/paper-submissions/{paper}/check-runs` with retained request key, effective `supersedes_run_id`, and accepted routing/checker/threshold/prompt versions; shown only for server-capable `failed_retryable`; no terminal retry CTA |
| 16 | Manual fallback | Uses server `manual_review_available`; absent for pending/running/successful AI or forbidden new lineage; manual lineage hides AI actions. Real terminal/retryable fixture showed legal manual fallback |
| 17 | AI review/reload | GET `/ai-result` supplies immutable suggestions. POST explicit `/paper-submissions/{paper}/check-runs/{effective_run}/review` opens persisted review; GET reads an existing review. Real reload recovered the effective run from projection and opened AI review |
| 18 | Manual review | POST/GET/PUT `/paper-submissions/{paper}/manual-review`, shared editor, server-owned unresolved canonical skeleton, no fake AI suggestions or provenance |
| 19 | Viewer reuse | `ScanPageViewer` extended with semantic overlays and point selection; existing PR2 behavior retained; page switching, fit, zoom, load/error handling and object-URL cleanup reused |
| 20 | Image loading | Only selected member page blob loaded; no all-paper image prefetch, no findings-specific requests. Membership and page order come from confirmed grouping |
| 21 | Coordinates | `normalized_upright_v1`; normalized decimal point/box values converted to percentages on the actual upright image surface. Invalid coordinates/extents fail closed. Image dimensions/ResizeObserver provide aspect-correct fit at zoom 1. Unit-tested normalization |
| 22 | Canonical findings | Findings grouped solely by `assessment_item_id` against frozen review items; selecting a finding selects its item and member page and highlights its region. Coincident regions direct the teacher to the text list instead of guessing |
| 23 | Suggestions vs decisions | Separate immutable AI-result suggestions and editable teacher draft; real first-item change to correct/2 preserved suggested partial/1 |
| 24 | Score editing | Decimal-capable numeric inputs (`step=any`), visible max, bounds validation; server authoritative. No client totals/grades calculated |
| 25 | Finding mutations | Remove AI finding from teacher draft; add teacher finding under a canonical item with an explicitly chosen member-page point; edit teacher text/detail/severity/item and relocate its point while keeping identity. Raw AI evidence is never mutated. Historical unassociated findings stay visible for linking/removal |
| 26 | Persistence | Local whole draft until explicit Сохранить; PUT with version; dirty indicator, existing router confirmation and beforeunload guard. Transient progress failures retain unsaved editor; authorization/object denials clear actionable data. No localStorage/IndexedDB |
| 27 | CAS | Save and approval send `expected_version`. `paper_review_version_conflict` triggers authoritative GET and exact requested Russian notice, with no speculative merge |
| 28 | Multi-teacher result | Real PostgreSQL browser sessions: A saved a correction; assigned B's stale PUT returned 409; B refetched A's summary; no silent overwrite; B then approved the new revision |
| 29 | Approval | POST review `/approve` only, guarded against duplicate submission; unresolved items, inconsistent score/status, missing summary and override reason have visible disabled explanations; server validation errors bounded |
| 30 | Deterministic result | Approved snapshot displays server total/max/percentage/calculated/final grade and override reason. Real AI result `6.00/10`, `60.0000%`; manual result `20/20`, `100.0000%`; fixture grade label `graded` |
| 31 | Grade override | Supported by approval's optional `{override_grade, reason}`; mandatory reason. Calculated grade is read-only when returned in an approved revision; no unexposed live grade-preview API invented |
| 32 | Corrections | Approved review read-only; deliberate Изменить проверку enables local correction; PUT produces newer unapproved draft while retaining prior approved snapshot/grade; reapproval creates the new approved revision. Browser verified approved revision 2 remained during draft and revision 4 after reapproval |
| 33 | checking_completed | Review/approval remain usable; focused coverage |
| 34 | completed batch | Existing review/correction remains usable; not disabled globally by batch status; new checking actions still depend on backend capabilities |
| 35 | Co-teacher navigation | Real normal path passed: Мои классы → class → Проверить бумажные работы → batch → О сопоставлении → Перейти к проверке. No direct URL shortcut needed for that acceptance |
| 36 | Negative authorization | Real backend/browser: unrelated and genuinely revoked class teacher → bounded 404; student → backend 403 and UI Нет доступа; existing foreign-class page → 404; real run substituted into another paper's review → 404. No private paper/page data rendered |
| 37 | Focused tests | **71 passed**, three new PR3 test files; checking/review/manual/CAS/approval/corrections, regions, API paths, error safety, dirty-state preservation, shared-review discovery and pending-to-success suggestion loading |
| 38 | Full frontend suite | **543 passed**, 49 files, up from 473 baseline; 71 additions and one obsolete placeholder test removed. Stable worker configuration: `--maxWorkers=1 --no-file-parallelism` |
| 39 | Typecheck | `npx tsc -b` passed |
| 40 | Production build | `npm run build` passed |
| 41 | Vite advisory | Baseline JS 532.35 kB / gzip 145.07 kB; candidate 565.36 kB / gzip 152.63 kB. Increase 33.01 kB (~6.2%), not pathological. Existing >500 kB advisory retained; no code splitting introduced solely to suppress it |
| 42 | AI browser E2E | Real PostgreSQL, fake provider only: schedule → bounded execution → server progress → reload → effective run → review → canonical findings/page 2 highlight → accepted edit/save → approve → server grade; passed |
| 43 | Manual browser E2E | Real legal no-run paper → unresolved five-item skeleton → fill/save → approve; passed. SQL confirmed zero CheckRuns for the manual paper and no synthetic AI lineage |
| 44 | Concurrency browser E2E | Distinct primary/assigned teacher identities at command time, real versioned persistence and stale 409; passed. Version refetch kept A's edit and old approved revision |
| 45 | Queue/retry browser E2E | Durable pending queue, real capacity denial, legal superseding retry, terminal no-retry/manual fallback and in-flight manual blocking verified. Test-only capacity claims were allowed to expire; their release helper consequently rejected expired claims, as designed. The denied window's zero-attempt/zero-retry evidence was captured before expiry |
| 46 | 1440 visual | Three usable regions, aspect-correct readable page, selected blue point, visible approved grade and suggested/accepted distinction; document width 1425 at viewport 1440, no horizontal overflow |
| 47 | 760 visual | Stacked paper selector → viewer → editor → actions; document width 745 at viewport 760. Score fields/findings and save/approve reachable; screenshots of viewer, editor and actions retained |
| 48 | Performance sanity | Real 30-paper fixture with three pages per paper and five frozen items; one current-page image in DOM, no all-page image prefetch. Successful AI fixture rendered five linked findings. One bounded projection per poll; geometry counts memoized, no request per finding |
| 49 | Backend diff | Empty against authoritative hotfix, both source changes and candidate history; no backend patch |
| 50 | Alembic | Single head `20261013_01`; no migration |
| 51 | Remaining blocker | Single-page/single-region required finding DTO prevents regionless or multiple-region findings. These feature paths remain explicitly unsupported; remaining requested workflow is implemented |
| 52 | Publication | Remains unimplemented in PR3; no publication, student visibility, finalization, withdrawal or remediation commands added. SQL after AI/manual browser approval: zero publications and zero active publication pointers |
| 53 | PR4 | Not started. No push or merge |

## Exact candidate file inventory relative to main

- `docs/frontend-paper-pr3-contract-audit.md`
- `docs/frontend-paper-pr3-review-workspace.md`
- `frontend/src/PaperCheckingPage.test.tsx`
- `frontend/src/PaperCheckingPage.tsx`
- `frontend/src/PaperMatchingPage.test.tsx`
- `frontend/src/PaperMatchingPage.tsx`
- `frontend/src/PaperReviewPanel.tsx`
- `frontend/src/PaperScanPages.tsx`
- `frontend/src/ScanPageViewer.tsx`
- `frontend/src/main.tsx`
- `frontend/src/paperReview.css`
- `frontend/src/paperReviewApi.test.ts`
- `frontend/src/paperReviewApi.ts`
- `frontend/src/paperReviewState.test.ts`
- `frontend/src/paperReviewState.ts`
- `frontend/src/paperReviewTestFixtures.ts`
- `frontend/src/paperScanState.ts`
- `frontend/src/usePaperChecking.ts`

## Local evidence and validation boundaries

Evidence remains under untracked `local-backups/frontend-pr3-acceptance/`:
`focused.log`, `full-suite.log`, `build.log`, `ai-manual-requests.log`,
`requests.log`, `evidence.json`, `capacity-evidence.json`, `authorization.json`,
`workspace-1440.jpg`, `workspace-760.jpg`, `page-760.jpg`, `actions-760.jpg`,
and `capacity-wait.jpg`. The local fixture runner and standalone fixture-control
HTML are not production application files and are not committed. The controlled
authentication substitute supplies real teacher/student Principals; production
capability, class assignment, object-scope, grading and CAS boundaries are intact.
Provider output is deterministic test output, with no real provider traffic.

Browser fixtures used dedicated PostgreSQL on port 55432, outer rollback-isolated
transactions and real routes. No normal academic database was modified. Earlier
fixture setup failures were corrected before the successful recorded scenarios;
the original aggregate acceptance suites were not rerun and backend source stayed
unchanged. Final frontend verification was rerun after the last implementation
changes. Test services ended after interruption; disconnected outer transactions
roll back on PostgreSQL. The stored evidence supports results without a live
fixture service.

All unrelated original untracked files are preserved. No clean, stash, move or
delete was used. Final preservation verification excludes only PR3-created local
evidence/fixture-control artifacts and compares the original untracked inventory.
