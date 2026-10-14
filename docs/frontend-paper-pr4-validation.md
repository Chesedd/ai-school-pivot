# Frontend PR4 teacher publication candidate

Validated on 2026-10-14 against main `d5915d245405f8d74385fadcf1149f9da521a7a5`.
Branch: `codex/paper-publication-ui`. The final candidate is this branch's HEAD;
its full SHA is reported in the completion message. Nothing was pushed or merged.

## Exact changes relative to required main

- `docs/frontend-paper-pr4-publication-contract.md`
- `docs/frontend-paper-pr4-validation.md`
- `frontend/src/PaperCheckingPage.tsx`
- `frontend/src/PaperPublicationPage.test.tsx`
- `frontend/src/PaperPublicationPage.tsx`
- `frontend/src/PaperScanPages.tsx`
- `frontend/src/main.tsx`
- `frontend/src/paperPublication.css`
- `frontend/src/paperPublicationApi.test.ts`
- `frontend/src/paperPublicationApi.ts`
- `frontend/src/paperPublicationState.ts`
- `frontend/src/paperScanState.ts`

## Current endpoints

All paths below have prefix `/api/assessment-core`. Publication and withdrawal
POSTs use one `Idempotency-Key` per intentional command. There is no automatic
HTTP retry in `api.request`; the command object's serialized body and key survive
explicit retries. Completion does not require that header.

| Operation | Method/path and body |
| --- | --- |
| Validate batch/assignment scope | GET `/scan-batches/{batch_id}` |
| Teacher projection/readiness | GET `/scan-batches/{batch_id}/checking` |
| Single publish | POST `/paper-submissions/{paper_id}/publications`, `{approved_review_revision_id}` |
| Selected bulk publish used by UI | POST `/scan-batches/{batch_id}/publications/selected`, `{entries: [{paper_submission_id, approved_review_revision_id}]}` |
| Automatic bulk binding retained | POST `/scan-batches/{batch_id}/publications/bulk`, no body |
| Lazy teacher history | GET `/paper-submissions/{paper_id}/publications` |
| Single exact-ID withdrawal | POST `/paper-submissions/{paper_id}/publications/{publication_id}/withdraw`, `{reason}` |
| Selected bulk withdrawal used by UI | POST `/scan-batches/{batch_id}/publications/withdraw/selected`, `{entries: [{paper_submission_id, publication_id}], reason}` |
| Backend bulk-withdraw alias | POST `/scan-batches/{batch_id}/publications/withdraw/bulk`, same selected payload |
| Explicit completion | POST `/scan-batches/{batch_id}/complete`, no body |

Sources reinspected: `backend/app/presentation/paper_result_publication_routes.py`,
`scan_intake_routes.py`, `application/paper_batch_orchestration.py`,
`infrastructure/paper_batch_orchestration_repository.py`,
`paper_result_publication_repository.py`, and `application/paper_result_publication.py`.
The runtime responses in acceptance agree with these current contracts. No
student result endpoint was added to frontend bindings.

## Requested completion report

| Item | Result |
| --- | --- |
| 1 Branch | `codex/paper-publication-ui` |
| 2 Rebased partial SHA | `2dc48cd3155425ddf4fed5452db7d903945f4159`; original `9d644704085fc95ad8c8b0a27d499130075ab23f`, actual old merge base `e67242e1ae1e0f50a8052e4d936c4515d9db6107`; clean mechanical rebase, no conflicts |
| 3 Final PR4 SHA | Branch HEAD, full SHA in completion message; coherent follow-up commit preserves partial audit commit |
| 4 Changed files | Exact 12-file list above |
| 5 Contract document | Original blocker/audit/report preserved as historical; appended hotfix resolution and actual consumed fields |
| 6 Publication endpoints | Single publish route above; current approved revision supplied explicitly |
| 7 Withdrawal endpoints | Exact active publication route above; no history-based target discovery |
| 8 Bulk endpoints | Official selected publish and selected withdraw routes; one command, one key, one request, no single-call loops; automatic bulk publish binding retained |
| 9 Completion endpoint | Actual `/scan-batches/{batch_id}/complete` |
| 10 Workspace design | Teacher-only dense batch list, compact counts, search, five filters, labeled checkboxes, selected actions, secondary lazy history, contextual PR3 review navigation; batch-list and checking-page entry points |
| 11 State derivation | No approved revision → no confirmed review; approved/no active → ready; same approved/active revision → published; distinct current/active revision → previous version; draft state shown separately |
| 12 Active vs current approved | Compare current approved UUID to active publication source UUID; never compare timestamps or infer ownership; direct new-version CTA |
| 13 Single publish | Prepared explicit command; authoritative projection refetched after success/error; exact server active ID rendered |
| 14 Key lifecycle | Key created once at user-action boundary; synchronous UI lock prevents double click; uncertain command retained in memory; later intentional publish/withdraw/republish creates a new object/key; no browser persistence |
| 15 Retry preservation | Real fetch-boundary network/503 tests prove identical key/body across retries; UI test proves retry remains R1 even after approved projection advances to R2; generic request has no automatic retry |
| 16 Same revision republish | Real browser A withdrawn then B created from identical R1; distinct IDs and preserved withdrawn history; command tests prove new key; no unwithdraw |
| 17 Corrected revision publish | R1 active → real review edit/reapprove via existing HTTP routes → R2 projected; browser offers direct new-version publication; old publication superseded |
| 18 Bulk publish | Browser selected 29 approved papers; one official selected command; all 30 active after refetch. Mixed invalid payload sent by fixture HTTP harness rejected with `paper_publication_revision_mismatch` (422), database counts unchanged; component test verifies bounded failure/refetch/selection clearing |
| 19 History | On-demand per paper only; publication number/time, approved revision UUID, active/superseded/withdrawn, withdrawal time/actor/reason. DTO has no approved-review revision number, so exact revision UUID is displayed. Snapshot JSON never rendered |
| 20 Withdrawal | Browser confirms exact active ID; mandatory teacher-only reason; projection becomes inactive and history retained |
| 21 Reason validation | Trim, nonempty, ≤500 Unicode code points, shared API validation and disabled confirmation; backend authoritative; tested whitespace/501/500 emoji boundary |
| 22 Bulk withdrawal | Browser selected Private A/B; one selected withdrawal with common reason; count 30→28; selected republish restored 30. A one-row selected action also uses selected endpoint, tested |
| 23 Stale concurrency | Browser co-teacher held A; second teacher HTTP session loaded same projection, corrected/reapproved and published B; browser attempted exact stale A; backend rejected, UI refetched B with state-change notice, no automatic retarget. Bulk stale-ID safety also tested |
| 24 Pages unavailable | Actual `paper_publication_pages_unavailable` mapped to bounded explanatory text; UI refetches without silently retrying/new key. Focused API/UI tests; no extra eligibility metadata requested |
| 25 Completion readiness | Server `can_finalize` governs CTA, including false even when count appears complete; visible disabled explanation; real incomplete completion rejected with 409 |
| 26 Explicit completion | Browser confirmation then official complete command; no finalization on last publication |
| 27 Completed after withdrawal | Real browser withdrawal after completion left `Партия завершена`; projection status remained completed |
| 28 Completed after republish | Real browser republish and reload remained completed; no checking restart |
| 29 Co-teacher | Real assigned co-teacher normal navigation: My classes → assigned class → paper batches → publication workspace; publish/bulk/history/withdraw/republish/complete all accepted. No publisher/reviewer ownership gate |
| 30 Negative authorization | Real routes deny unrelated/revoked/student actors for projection/history/publish/withdraw/complete (scope hiding 404 or capability 403); foreign batch denied, publication ID substituted into another paper rejected. Safe UI 403/404 and batch/assignment substitution tests |
| 31 Focused tests | 65 passed: 25 retained API tests + 40 workspace/projection tests. `npm run test:run -- src/PaperPublicationPage.test.tsx src/paperPublicationApi.test.ts --maxWorkers=1` |
| 32 Full frontend suite | 608 passed across 51 files (`npm run test:run -- --maxWorkers=1`), final run 345.69 s; up from partial PR4 568 and merged PR3 543 |
| 33 Typecheck | `npx tsc -b` passed |
| 34 Production build | `npm run build` passed |
| 35 Vite advisory | Existing >500 kB chunk advisory remains. JS 581.29 kB / gzip 155.78 kB vs partial 567.54 / 153.04 (~2.4% / 1.8% increase) and PR3 565.36 / 152.63 (~2.8% / 2.1%). Same advisory category; no bundle optimization introduced |
| 36 Standard publication E2E | Real PostgreSQL browser publish/reload: active ID retained, no duplicate on reload |
| 37 Withdrawal/republish E2E | A `eb39ca8e-f494-4a5b-8758-4c168345cc39` withdrawn; same R1 B `0be8b2f1-b5be-4d90-a948-55b7006eb4b0` active; A persisted in history |
| 38 Corrected revision E2E | R2 `5d444f8c-2f8a-419f-8d17-f0b503366f05` published as `15232151-80ad-4299-be2f-2f187780f037`; prior R1 historical |
| 39 Bulk E2E | Valid UI selected publish/withdraw/republish passed; invalid all-or-none check used HTTP harness as described in item 18 |
| 40 Completion E2E | Initially incomplete/disabled and real backend 409; publish all → explicit complete → withdraw → republish → reload, status stable |
| 41 Concurrent teacher E2E | Browser stale co-teacher + real second-teacher HTTP session; B `738e56ea-18ef-48ca-b90a-627411b17396` remained active after stale A rejection |
| 42 1440 visual | 30 rows, compact summary/actions, active/version distinction and completion state visible; DOM width equals scroll width 1425 px; screenshot saved |
| 43 760 visual | Stacked rows, wrapped controls, reason and completion dialogs usable; caught/fixed shared-dialog child overflow; DOM width equals scroll width 745 px; screenshots saved |
| 44 Performance | One main checking projection at mount after scope validation, zero history reads at mount; selected history only; no polling/mutation effects; 30-row filter/selection test. Request log and `performance.json` evidence |
| 45 Backend diff | Empty relative to `d5915d245405f8d74385fadcf1149f9da521a7a5`; no backend files changed |
| 46 Alembic | Single `20261013_01 (head)` verified with `.venv/Scripts/alembic.exe heads` from backend |
| 47 Remaining blocker | None discovered in required teacher workflow; convenience eligibility/last-withdrawal metadata not required |
| 48 Student UI | Student paper result UI not implemented; withdrawal reason remains inside teacher-only component/DTO |
| 49 PR5 | Not started. No remediation, retention, notifications, automatic publication, unwithdraw, checking/review redesign, push, or merge |

## Local acceptance evidence

Evidence lives in untracked `local-backups/frontend-pr4-acceptance/`, preserving
all pre-existing unrelated untracked files. `live-fixture.py` uses the disposable
PostgreSQL container, real application routes/services/repositories, real retained
page bytes and 30 manually approved reviews, in a rollback-isolated transaction.
Test principal dependency override selects assigned co-teacher/teacher/denied
actors; it does not replace authorization or mutation implementations. No
production user credentials or data are used. Corrected reviews use real edit
and approval routes. The concurrent competitor and malformed commands use HTTP
fixture requests while the browser executes the user-facing workflow.

Artifacts: `requests.log`, `http-acceptance.json`, `concurrency.json`,
`final-evidence.json`, `performance.json`, `focused-final.log`,
`full-suite-final.log`, `build-final.log`, `workspace-1440.png`,
`workspace-760.png`, `withdraw-760.png`. Final DB-backed evidence: completed
batch, 30 active pointers, 36 total publications, six historical publications for
Private A, zero new check runs/provider calls. The fixture's superseded/withdrawn
states agree with the browser. Runtime fixture servers are stopped after acceptance.

The real source-backed history endpoint returns immutable result data in addition
to teacher metadata; frontend types deliberately omit that data and the component
does not render it or expose it to student UI.
