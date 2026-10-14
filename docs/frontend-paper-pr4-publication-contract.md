# Frontend PR4: contract blocker and API preparation

PR4 is **not complete**. Base: `main` at `e67242e1ae1e0f50a8052e4d936c4515d9db6107`.
Branch: `codex/paper-publication-ui`. No push or merge.

## Blocking missing read contract

`GET /api/assessment-core/scan-batches/{batch_id}/checking` returns server completion
readiness (`can_finalize`, `blocking_reasons`, `active_publication_count`) and effective
review metadata per paper (`latest_approved_revision_id`, `review_state`). However,
its `papers` entries omit the active publication ID and its approved revision identity.
The repository selects the active pointer internally, but uses it only to calculate
the aggregate count. See `PaperBatchOrchestrationRepository.progress` in
`backend/app/infrastructure/paper_batch_orchestration_repository.py`.

The sole teacher publication read endpoint is per-paper history. It returns `active`,
`is_active`, `is_withdrawn`, publication and approved revision IDs, timestamps and
teacher-only withdrawal reason, alongside the immutable snapshot. There is no
teacher batch publication projection. Remediation eligibility is a per-paper,
remediation-specific endpoint and cannot replace the missing batch contract.

Consequences: the workspace cannot distinguish published R1 from approved R2,
know which rows permit republishing, or capture exact publication identities for
bulk withdrawal from a single batch read. An aggregate count cannot identify rows.
Fetching all 30 histories at mount violates the explicit performance requirement
and fetches all immutable snapshots unnecessarily. Pages-unavailable is currently
a write-time error, not a publication eligibility field in that batch projection.

Following the requested backend freeze and STOP instruction, no workspace route,
row state inference, or history-request fan-out was introduced. A separate backend
change needs an authorized teacher batch read contract exposing per-paper active
publication ID, its approved revision identity, and publication eligibility/blockers
(including known unavailable pages). Last-publication/withdrawal metadata would
allow the withdrawn filter and republish label without eager history loading.
Full history can then stay lazy. No backend patch is included here.

## Inspected write and history contracts

All paths below use `/api/assessment-core` as prefix. All publication and withdrawal
POSTs require `Idempotency-Key` (1–128 characters).

| Method | Path | Payload |
| --- | --- | --- |
| GET | `/paper-submissions/{paper_id}/publications` | Teacher history, `{items}` |
| POST | `/paper-submissions/{paper_id}/publications` | `{approved_review_revision_id}` |
| POST | `/scan-batches/{batch_id}/publications/selected` | `{entries: [{paper_submission_id, approved_review_revision_id}]}` |
| POST | `/scan-batches/{batch_id}/publications/bulk` | No body; backend resolves revisions |
| POST | `/paper-submissions/{paper_id}/publications/{publication_id}/withdraw` | `{reason}` |
| POST | `/scan-batches/{batch_id}/publications/withdraw/selected` | `{entries: [{paper_submission_id, publication_id}], reason}` |
| POST | `/scan-batches/{batch_id}/publications/withdraw/bulk` | Alias for selected withdrawal, same payload |
| GET | `/scan-batches/{batch_id}/checking` | Authoritative progress/readiness |
| POST | `/scan-batches/{batch_id}/complete` | No body, no command header required |

Sources: `paper_result_publication_routes.py`, `paper_result_publication.py`,
`paper_result_publication_repository.py`, `scan_intake_routes.py` and
`paper_batch_orchestration.py`. Single/selected publication explicitly requires
the approved revision UUID. Publication outcome is `published` or `already_current`.
Selected commands validate all entries before writes and commit once at the route.
Withdrawal targets the exact publication, with no implicit current substitution.
Completed finalization replays return progress. Later withdrawal does not transition
the batch out of completed. Corrections and publication remain separate from new
checking. These are source-inspected semantics, not newly executed browser proofs.

The student result/page endpoints exist, but are neither bound nor rendered here.
Publication writes and history reads do not require frontend remediation behavior.
Review approval identity comes from the effective review pointer in checking progress;
review correction preserves the previous approved pointer until another approval.

## Prepared frontend behavior

`paperPublicationApi.ts` reuses the credentialed API client. It binds the existing
teacher commands and history/progress reads; no new library or persistence is added.
Types deliberately exclude immutable snapshot contents and student result rendering.
The server response is not interpreted as a new active state locally.

Prepare one command at the user action boundary. Preparation serializes the payload
and generates `paper:<crypto.randomUUID()>`. Concurrent `run()` calls share one
promise. Network and 5xx failures leave that same command retryable with exactly
the same body and key. Definitive successes and 4xx responses retain their promise,
preventing duplicate transport calls on that object. A subsequent intentional action
must prepare a new object, including same-revision republish after withdrawal.
The eventual UI must retain unresolved commands in ephemeral mutation state and
refetch server projection after success/error; those UI integrations are blocked.
No automatic HTTP retry exists in `api.request`; key generation never occurs inside
`run()`. No keys or academic state are stored in browser storage.

Withdrawal preparation trims the reason, rejects empty text and more than 500
Unicode characters, and freezes explicit publication IDs. Selected publish and
withdrawal each invoke one official endpoint, not per-paper loops. Central scanned
paper errors now cover publication/revision conflicts, unavailable pages, reason
validation, selection failures and incomplete batch completion. Existing bounded
authorization mapping remains in effect. Stale publication mapping says
`Публикация изменилась. Загружены актуальные данные.`; the future UI must refetch
before displaying this message. No claim is made that this API layer refetches.

## Requested completion report

| Request items | Status |
| --- | --- |
| 1–2 Branch / commit | Branch above; accompanying local commit SHA reported in chat |
| 3 Changed files | `frontend/src/paperPublicationApi.ts`, `frontend/src/paperPublicationApi.test.ts`, `frontend/src/paperScanState.ts`, this document |
| 4–7 Publication / withdrawal / bulk / completion endpoints | Exact contracts listed above |
| 8 Route changes | None; blocked workspace is not exposed |
| 9–11 Workspace / state mapping / approved vs active | Blocked by absent per-paper batch publication projection |
| 12–14 Single publish / key lifecycle / HTTP retries | API implemented, frozen key/body, duplicate suppression; 25 focused tests |
| 15 Same-revision republish | New prepared command gets new key; focused coverage, no browser UI yet |
| 16 Bulk publish | Selected and automatic official endpoint bindings; no local optimistic row updates |
| 17 History | Teacher read binding available, UI/lazy display blocked |
| 18–20 Withdrawal / reason / bulk withdrawal | Exact IDs; common reason; trim/nonempty/500 validation; endpoint tests |
| 21 Stale concurrency | Exact old ID cannot become a newer ID; error mapping tested; UI refetch blocked |
| 22 Pages unavailable | Actual error centrally mapped; no recovery or local active state |
| 23 Corrected approval | Exact approved revision accepted by API; comparison/CTA blocked |
| 24–26 Readiness / explicit completion / completed after withdrawal | Server DTO and completion binding; UI blocked; no automatic completion or reopening |
| 27 Co-teacher | Source-inspected class access, no publisher ownership added; normal-navigation acceptance not run |
| 28 Negative authorization | API mappings tested; real unrelated/revoked/student/cross-class publication acceptance not run |
| 29–31 1440 / 760 / performance | Not run: no workspace rendered; no N-history mount introduced |
| 32 Focused tests | 25 passed, `npm run test:run -- src/paperPublicationApi.test.ts --maxWorkers=1` |
| 33 Full suite | **568 passed**, 50 files, up from 543; `npm run test:run -- --maxWorkers=1` (204.32 s) |
| 34 Typecheck | Passed, `npx tsc -b` |
| 35 Build | Passed, `npm run build` |
| 36 Vite advisory | Retained >500 kB warning; PR3 recorded 565.36 kB / gzip 152.63 kB; current 567.54 kB / gzip 153.04 kB (~0.4% increase). API module unused by routes, error text adds small bundle size |
| 37–42 Six browser E2E scenarios | Not run, blocked workspace: standard, withdrawal/republish, corrected revision, bulk, completion, concurrent teachers |
| 43 Backend diff | Empty against required base, verified before and after commit |
| 44 Alembic | Single head `20261013_01`, verified with `.venv/Scripts/alembic.exe heads` from backend |
| 45 Blocker | Missing teacher batch publication projection, detailed above |
| 46–47 Student UI / PR5 | Student result UI remains unimplemented; PR5 not started |

PR4 UI acceptance and requested publication-state component tests cannot be claimed.
The added tests cover only the actual API and command boundary, including old
historical replay, new republish keys, transport retry, exact stale identity,
bulk endpoint/body/header, reason validation, safe errors and explicit completion.
