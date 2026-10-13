# Final scanned-paper backend production-readiness audit

Audit date: 2026-10-13 (Europe/Moscow). Repository: `Chesedd/ai-school-pivot`.

The original evidence below records the starting state at `d919bf1`. The final follow-up section documents current fixes and verification and supersedes the initial blocked decision. Accepted PR1-PR15 and the hardening commit remain in history.


## Initial audit at d919bf1 (historical evidence)

- Authoritative local base: `d9b33aae1ac4b5ff76a3b79992599e52b08c1638`; stale `origin/main` was not used.
- Starting hardening SHA: `d919bf1c64031899a05270a6eb5310a021101734` (local hardening commit; report remains an uncommitted local artifact). This report is a local artifact; its working-tree status is stated with the final delivery.
- Branch: `codex/paper-production-readiness-audit`.
- Production code changed: **yes**, narrowly scoped hardening and ORM declarations.
- No push, merge, frontend work, new product feature, or accepted migration rewrite.
- Alembic head remains `20261012_02`. ORM declarations were aligned with existing PostgreSQL schema, so no new migration was needed.

**Initial freeze decision: blocked.** Matching has no stale-claim recovery, and interrupted object creation has no durable orphan-discovery mechanism. Passing checking/publication tests do not eliminate these intake recovery gaps.

Data-bearing rollback also fails the requested safe-representation/refusal criterion: an active replacement permits PR15 downgrade to delete withdrawal and command history. Current forward operation retains that history; rollback does not. Accepted migration history was left unchanged.

## Initial findings, severity and fixes

| ID | Severity | Evidence and disposition |
|---|---|---|
| B1 | BLOCKER, unresolved | A matching run/chunk claimed one day ago still returns `matching_already_running`. There is no lease, owner fencing, recovery command, or supported resume. A crash after run creation also strands the batch before chunk execution. Cancellation/new intake is a workaround, not recovery of the existing run. A recovery/fencing contract needs a separate design decision; this audit does not invent one. |
| B2 | BLOCKER, fixed | Upload response-refresh failure deleted already-committed source bytes. Refresh now occurs outside compensation. Lost upload/extraction commit acknowledgements could also delete committed source/render bytes; compensation now preserves bytes when commit outcome is uncertain. |
| B3 | BLOCKER, fixed | Scan-batch create replay returned cached metadata before checking current class access. A removed teacher is now reauthorized before replay/conflict details are returned. |
| B4 | BLOCKER, fixed | Active-page protection depended on mutable grouping status. PostgreSQL permits a confirmed grouping to become superseded; retention could then select active-publication pages. The active pointer plus paper membership now protects pages regardless of grouping status. |
| B5 | BLOCKER, unresolved for data-bearing rollback | PR15 downgrade refuses a NULL active pointer, but after republish it drops immutable withdrawal and command ledgers. `test_downgrade_refuses_null_pointer_and_preserves_republish_history` executes this path on PostgreSQL, preserving the two publication rows/current pointer; the inspected downgrade explicitly drops the historical ledgers. Re-upgrade recreates empty ledgers, so original command replay/provenance is lost. This fails audit requirement 38. A reviewed rollback guard/archival strategy is required; accepted migrations were not rewritten. |
| H1 | HIGH, fixed | Controlled PostgreSQL connections reproduced retention page → ledger FK batch / publication batch → page deadlock (`40P01`). Retention now takes batch → target, with `SKIP LOCKED`, before its FK insert. |
| H2 | HIGH, fixed | Matching provider failure rolled back then accessed expired ORM primary keys, raising `MissingGreenlet` instead of recording failure. Scalar IDs are retained before rollback. |
| H3 | HIGH, fixed | Matching reads held a transaction across the provider call. It now closes first. Paper execute HTTP also releases its authorization transaction before independent execution sessions/AI work. |
| H4 | HIGH, fixed | Partial filesystem write failure left an object whose reference was never returned. The adapter now removes partial bytes on failure. |
| H5 | HIGH, fixed | Unexpected paper intake exceptions were serialized with `str(exc)` into HTTP 409, potentially exposing SQL/parameters. Only known domain exception families are translated; unexpected errors retain the bounded production `internal_error` 500. |
| H6 | HIGH, unresolved | A hard process exit after `store()` but before DB registration leaves bytes absent from `input_artifacts` and retention discovery. Preserving uncertain-commit bytes can also leave orphans. A durable object/staging registry or reviewed reconciliation mechanism is required; that extends the current lifecycle. |
| M1 | MEDIUM, fixed | ORM omitted existing paper AI review/hash checks, finding coordinate-space check and paper page-order check. Retention ORM advertised an ID server default absent in PostgreSQL. Declarations now match these accepted schema contracts. |
| M2 | MEDIUM, fixed | Matching finalization attempted to overwrite a cancelled batch. The DB cancellation constraint already prevented committing reactivation; the new guard produces a bounded conflict instead of an internal error. |
| M3 | MEDIUM, deferred | Paper FKs do not enforce all student/participant/assignment/batch/grouping correspondence or freeze paper/page identities. Direct SQL can change paper A's student to B; the ownership projection then trusts B. This is a DB-integrity gap, not a demonstrated client-ID substitution bypass with valid rows. Any corrective schema work must use a new migration after `20261012_02`. |
| M4 | MEDIUM, deferred | Teacher publication/review histories and assignment batch lists load all history, without pagination fields in their contracts. Teacher remediation history has pagination but lacks parameter bounds: negative values can reach PostgreSQL and very large limits can load excessive history. Define/reuse the history contract explicitly rather than silently truncating responses. |
| M5 | MEDIUM, deferred | A 30-paper fixture used 611 SQL statements for bulk publication and 189 for bulk withdrawal, including transaction/savepoint statements. This is bounded linear work with one command commit, but deserves query batching before substantially raising limits. |
| M6 | MEDIUM, deferred | Most scan/publication OpenAPI responses are untyped; manual multipart parsing leaves upload without a request-body schema. Runtime projections are allowlisted/privacy tested, but generated client contracts remain incomplete. |
| M7 | MEDIUM, deferred | Extraction holds its batch transaction/lock across bounded storage reads, PDF rendering and render writes. This preserves aggregate-limit atomicity; moving rendering outside it needs a claim/fencing design. |
| M8 | MEDIUM, deferred | Beyond B5, PR14 drops purge metadata without restoring bytes, and older checking downgrades explicitly delete paper evidence. These paths require an operational rollback decision; restoring external bytes is not demanded. This does not affect ordinary forward execution, but the historical chain must not be described as a lossless rollback. |
| L1 | LOW, corrected tests | PR11/PR12 environment-gated migration tests asserted obsolete heads. Fresh-head/refusal assertions now resolve `repository_head()`; boundary assertions remain unchanged. |
| L2 | LOW, deferred | Intake `BatchResponse`, `ArtifactResponse`, `PageResponse` DTOs are unused by their routes. Shared remediation check names also differ between ORM/DB without missing current enforcement. No broad cosmetic cleanup was performed. |

The unresolved BLOCKER and HIGH findings are **not** non-blocking debt. **All production defects fixed: no.**

## Architecture and dependency map

Paths below are relative to `backend/`. Inventory was established before production edits and recorded in `local-backups/paper-audit/architecture.json`.

| Component | Actual classes/files |
|---|---|
| Identity | `application/principal.py`; `assessment_models.Student`; `auth_models.User`, `StudentUserLink`; student self-access uses `Principal.student_id` |
| Scan aggregate | `application/scan_intake.py:ScanIntakeService`; `scan_intake_repository.SqlAlchemyScanIntakeRepository`; `scan_checking_models.AssessmentScanBatch` |
| Upload/extraction | `infrastructure/scan_page_pipeline.py:ScanPagePipeline`; `scan_page_rasterizer.PillowPdfiumPageRasterizer`; `ScanBatchArtifact`, `ScanPage`, immutable private `InputArtifact` |
| Matching | `ScanMatchingService` in `application/scan_matching_service.py`; `scan_matching.py`; `SqlAlchemyScanMatchingRepository`; `AnthropicScanPageMatchingProvider`; run/chunk/roster/page/proposal/candidate tables |
| Teacher grouping | `ScanGroupingService`; `SqlAlchemyScanGroupingRepository`; grouping revision/entry/page/unmatched models |
| Paper identity | `PaperSubmission`, `PaperSubmissionPage` in `scan_checking_models.py`; academic participant/student/assignment and frozen variant |
| Grading policy | `application/scan_grading_policy.py`; `PaperGradingPolicyRepository`; immutable `ScanGradingPolicyRevision` |
| Check intake | `PaperCheckingIntakeService`, `SQLAlchemyPaperCheckingIntakeUnitOfWork`; shared `CheckingRepository.create_paper_run`; `CheckRun` digital/paper source XOR |
| Paper AI | `PaperCheckingExecutionService`, `compile_paper_request`; `SQLAlchemyPaperCheckingExecutionRepository`; `AnthropicPaperCheckingProvider` |
| Evidence | `ModelRun`, `PaperAIRevision`, `CheckResult`, `CheckFinding`, `PaperCheckFindingRegion` in `checking_models.py`; immutability/identity triggers |
| AI/manual review | `application/paper_checking_review.py`; `PaperCheckingReviewRepository`; `PaperCheckReview`, immutable `PaperCheckReviewRevision` |
| Publication/ledger/withdrawal | `application/paper_result_publication.py`; `PaperResultPublicationRepository`; publication/state/command/command-item/withdrawal models |
| Batch orchestration | `PaperBatchOrchestrationService`, `derive_progress`; `PaperBatchOrchestrationRepository` |
| Remediation | `application/paper_remediation.py`; `PaperRemediationRepository`; shared `RemediationRepository`, execution/checking/results services; paper item/finding signal models |
| Limits/capacity | `PaperOperationalLimits`; `paper_operational_guards.py`; advisory global claim lock, batch locks and durable ModelRun leases |
| Retention | `ScanStorageRetentionService`, `PaperRetentionSettings`; `ScanStorageRetentionRepository`, `ScanStorageRetentionEntry`; operator command in `app/cli.py` |
| Student result/page | `paper_result_publication_routes.py`; `_owned_publication`, student result/list/page repository projections; strict `PublicationSnapshot` |
| Teacher reads | `scan_intake_routes.py`, review/history and publication-history handlers; `scan_storage_content.read_scan_content` |
| Storage | `artifact_storage.FilesystemArtifactStorage`; opaque references, bounded reads, idempotent delete |
| Authorization | `capabilities.py`, `auth_dependencies.py`, `_authorized_batch`; `SQLAlchemyClassroomAccessRepository.can_access_class` |
| Events/config/schema | `ScanCheckingEvent`, `CheckerEvent`, `RemediationEvent`; `app/config.py`; `alembic/versions` |

## Architectural invariants and enforcement

Account and academic identities remain separate. Papers never fabricate digital submissions. Original artifact metadata has a DB immutability guard; students receive only membership-scoped normalized PNGs. Logical page/paper identity consistency is enforced by workflow, but not completely frozen/related at DB level (M3).

Matching receives minimal assignment roster display names and opaque tokens. Academic checking maps opaque page/item/content tokens from the frozen paper, with no account/student/participant/page UUID or storage reference in its constructed provider DTO. Handwriting and teacher-authored text can themselves contain names; document-content anonymization is not promised. Teacher task/rubric text is serialized academic context, not executed code. Page prompt-injection instructions, strict token/score/region validation, immutable evidence, deterministic grading and separate teacher commands prevent provider outputs from authorizing publication/remediation or selecting arbitrary database targets. Tests use deterministic provider doubles; they do not prove live-model adversarial semantics.

Checking/review approval do not publish. Snapshots and withdrawals are immutable; visibility uses the current pointer. Old command replay has no pointer mutations. Manual review has NULL AI provenance and fabricates no evidence. Teacher edits append revisions; calculated grade remains distinct from reasoned override.

Settlement differs from finalization. A paper checking failure does not set the batch to `processing_failed`; completed batches remain completed after withdrawal. New remediation uses exact current publication provenance and selected published weak items; assigned plans execute after withdrawal/replacement. Retention deletes bytes, not academic rows; active pages and grace remain protected. New publication verifies actual required bytes and durable claim fencing.

Incomplete enforcement: matching crash recovery (B1), orphan reachability (H6), DB-wide correspondence/identity immutability (M3), lossless historical downgrades (M8), history bounds/pagination and OpenAPI completeness (M4/M6).

## Actual state graphs and bypass search

### Batch

Contract edges: `draft → uploading/cancelled`; `uploading → extracting/processing_failed/cancelled`; `extracting → matching/processing_failed/cancelled`; `matching → matching_review_required/processing_failed/cancelled`; `matching_review_required → grouping_confirmed/cancelled`; `grouping_confirmed → ready_for_checking/cancelled`; `ready_for_checking → checking/cancelled`; `checking → checking_completed/processing_failed/cancelled`; `checking_completed → completed/cancelled`, or explicitly `→ checking` for a new attempt; `processing_failed → cancelled`. Completed/cancelled are terminal.

Grouping confirmation crosses `grouping_confirmed` and `ready_for_checking` in one transaction. Current routes do not independently initiate `processing_failed`; failures live on artifact/matching/checking records. Application validators and row locks enforce edges; DB checks enforce status/cancellation/policy shapes, not the whole graph. Direct `SqlAlchemyScanIntakeRepository.transition_batch` can bypass the application graph; no generic HTTP transition route exposes it. Matching cancellation finalization is now guarded.

### Extraction and pages

Artifact: `pending → running → completed/failed_retryable/failed_terminal`; retryable → running. Pipeline running/render work is inside the aggregate transaction, so crashes roll DB work back; successful pages are skipped on resume. Foundation repository methods can persist running separately. Page: pending → ready/retryable/terminal; retryable → ready/terminal. Completed artifact/ready page/terminal failure are application-terminal. DB status/metadata shapes do not enforce every edge. Uncommitted files still have H6.

### Matching

Run: running → succeeded/retryable/terminal failure; retryable → running. Chunk: pending/retryable → running → succeeded/retryable/terminal. Revision/chunk/proposal uniqueness limits duplication. There is no TTL/owner fence/crash recovery. Running and terminal matching runs cannot resume through the current route; terminal retry/manual grouping requires a product decision. Finalization now requires the batch still be matching.

### CheckRun and ModelRun

Digital/paper share pending → running, running → completed/review-required/retryable/terminal failure, retryable → pending under retry rules; local terminal precondition rejection is also supported. DB source/item XOR, active/attempt uniqueness, timestamp shapes, identity guards and immutable evidence supplement application transitions. Paper target is a confirmed paper, not a digital attempt. Provider attempts are running → succeeded/failed/invalid; expired attempts fail with `paper_execution_lease_expired` and consume no provider retry. Atomic success stores one evidence lineage and settled run.

### Review

Reviewing → approved; a changed edit returns approved/reviewing to reviewing; later approval appends a revision. Seed 0 is AI/manual, then human-edit/approved revisions. CAS protects edits/approvals. Revision immutability, composite provenance/pointers and source-kind XOR guard history shapes. Old approved/publication pointers can remain while a new draft is edited. Arbitrary raw SQL insertion is not equivalent to deterministic application approval.

### Publication and commands

There is no mutable publication status: active is derived from the state pointer; old immutable rows are superseded history. Withdrawal adds immutable history and clears the pointer while preserving version. A new command can republish the same revision as a new row. Actor/key uniqueness, declared/resolved fingerprints, ordered referenced outcomes, once-only command finalization, FK/immutability/active/deferred completeness guards supplement repository locks.

### Retention

Pending/unclaimed → claimed pending → purged/retryable_failed; failed/expired pending → new token/attempt. Purged is terminal through the operator repository. Claim commits before deletion; finalization is token-fenced. DB validates target XOR, batch correspondence, claim shape and immutable target identity; repository enforces eligibility/retry/terminal rules. No HTTP operational bypass exists.

### Remediation

Draft → assigned/cancelled; assigned → cancelled; cancelled terminal. Assignment freezes versions/source; execution uses existing remediation submission states. Paper create/draft assignment requires current publication; assigned execution does not. DB source XOR and signal/publication constraints supplement application validation. Existing owner/admin rules are preserved.

## Referential integrity and object-ID substitution

Concrete DB guards cover publication review/revision provenance, active pointer/paper identity, withdrawal/publication identity, review source XOR/composite revisions, model attempt/run target correspondence, retention target/batch correspondence and remediation signal/publication relationships. Routes join artifact→batch, page→artifact→batch, paper→batch/class, run→paper, revision→review/paper, publication→paper and participant→assignment. Frozen policy/item coverage and finding page/region provenance are revalidated before approval/publication.

Gaps: paper fields have independent FKs; page membership does not independently prove its batch; AI revision/run/model and finding region/page correspondence partly depend on application; remediation paper/publication provenance is not a complete composite FK contract. Direct SQL student-owner mismatch was reproduced. Any DB correction must use a new post-head migration; none was invented in this audit.

Substitution tests cover foreign artifacts/pages/participants/batches, paper/run mismatch, foreign approval revision/publication/withdrawal targets, remediation publication/item/finding substitutions and foreign source assessment items. Provider tokens cannot inject arbitrary DB UUIDs.

## Authorization matrix

| Actor | Teacher scan/check/review/publication/history/content | Student result/page | Paper remediation |
|---|---|---|---|
| Assigned class teacher | Allowed for class | Linked student identity boundary | Class source; plan-owner/admin rules |
| Second assigned teacher | Same class access, including another teacher's review/publication | Same boundary | Can create own plans; no new rights to another owner's plans |
| Outside teacher | Concealed 404 after capability check | Student boundary | Source/plan denied by existing rules |
| Assessment creator outside class | No creator override | Student boundary | Same source boundary |
| Admin | Existing unrestricted class-management semantics | Paper self endpoints still require linked student role/identity | Existing admin semantics |
| Owning student | 403 capability denial | Current publication and own membership pages only | Own assigned execution |
| Other student, same/other class | 403 | Foreign paper/page 404 | Foreign plans denied |
| Student without Student link | 403 teacher surface | 403 `student_identity_required` | Linked identity required |

Retention is an operator CLI using deployment credentials, not an HTTP capability: `python -m app.cli scan-retention --dry-run/--execute`. Mutation routers retain Trusted Origin enforcement. Route tests inject principals but exercise real dependencies, memberships and PostgreSQL; they are not a separate external-session penetration test.

## Privacy, review, grading, batch and remediation findings

Student projections contain only validated publication snapshots and membership-page metadata. They omit ModelRun/prompt/raw output/storage references/roster tokens/provider model metadata and withdrawal reason/history. Unpublished/withdrawn/foreign result or page access is 404; source routes remain teacher-only. Grade override reason is deliberately part of the approved snapshot, unlike private withdrawal reason. Student lists are ownership scoped/paginated.

Matching sends title, roster display names/aliases, opaque tokens and previews; checking sends confirmed images, opaque tokens, bounded task/rubric context and policy/prompt fingerprints. Internal mappings stay private. Strict output/token/score/region checks precede evidence persistence; provider responses cannot publish or create remediation.

Approval rejects incomplete coverage, NULL/ungraded/unreadable/insufficient-evidence items, invalid accepted scores/maxima, foreign items/pages and invalid regions. Decimal totals and unrounded grade ratios remain authoritative; display percentage uses explicit four-place HALF_UP. Zero accepted total is valid, zero policy maximum invalid; full/fractional/threshold/override cases are unit tested. Overrides preserve calculation and reason.

Manual-vs-AI starts serialize through batch/paper locks/source guards. Failed AI history remains. CAS rejects stale edits/approval; later edits cannot rewrite publication snapshots. Latest batch run uses unique attempt number, not timestamps; manual source dominates its state. Settlement includes terminal failure; finalization requires all active publications and stays explicit. Withdrawal does not reverse completed state/events.

All single/selected/automatic bulk publication and withdrawal writes require durable identity. Actor/key conflict precedes state resolution; automatic bulk freezes the resolved selection. Reuse outcomes and ordered UUID/number/time snapshots survive recreation. Exact-ID old withdrawal/replay cannot affect newer pointers. Commands commit once with all-or-none rollback.

New paper remediation uses exact active publication/selected weak items. Removed raw AI findings do not return through signals, candidate ranking, reasons or plans. Current candidates use source-task skill fallback, not raw check findings. Manual publication works without CheckRun; digital source still requires its run. Assigned plans retain immutable provenance and execute after visibility changes.

## Storage, transaction boundaries and external recovery

| Side effect | Claim/effect/finalize/crash/replay |
|---|---|
| Source upload | Limits/signature validation; batch lock; store bytes; immutable artifact/attachment; commit. Known pre-commit failure compensates. Post-commit refresh and uncertain commit preserve referenced bytes. Hard exit before registration still leaves an undiscoverable object. Upload-position retry safely conflicts rather than being durable command replay. |
| Extraction | Aggregate lock/transaction cover preflight/render/store/rows. Ordinary partial-render failure rolls back pages/removes temporary objects and records accepted extraction failure. Uncertain commit preserves possibly committed bytes. Hard-exit orphan risk and long render transaction remain. |
| Matching | Durable run/chunk claim; construct/read request; close transaction before provider; validated proposals/finalization. Ordinary retryable failure now settles correctly. Crash after claim has no recovery/fence. Provider SDK timeout is not a durable matching lease. |
| Paper checker | Short advisory-global/batch/run claim transaction; bounded image reads; claim recheck; bounded provider call; atomic evidence settlement. Capacity denial creates no attempt/retry. Interrupted finalize can reclaim an expired claim and persist one result despite repeated external calls. Late expired writers are fenced. |
| Retention | Capped discovery; batch→target lock/eligibility recheck; durable claim; delete outside transaction; token-fenced finalize. Missing bytes after prior successful deletion count as success; expired claims reclaim and concurrent sweepers do not share a current claim. |

Publication/withdrawal lock command key → ordered batch → ordered paper → ordered page. Manual writes lock batch → paper → review. Execution claims lock advisory → batch → run; settle locks batch → attempt/run. Remediation locks plan/paper/task and does not wait back on a batch lock. Retention now locks batch → target; its implicit FK wait previously created the proven cycle. Tests use `pg_blocking_pids`, events and actual filesystem deletion.

Source purge is independent of active page protection. Active pages and withdrawal grace survive, including the new DB-permitted grouping-state mismatch case. At exact grace expiry ordinary eligibility resumes. Publish/republish/bulk creation checks actual PNG signature, length, hash, metadata and retention fencing; unavailable pages fail before a broken active row is made. Dry run mutates nothing, run cap holds, and shared/non-paper InputArtifacts are excluded. Cancelled/incomplete batch cleanup is outside the existing completed-batch policy and needs a policy decision.

## Query/performance, history, configuration, time and ordering

A confirmed 30-paper PostgreSQL fixture completed progress, manual approvals, bulk publication and bulk withdrawal. Progress used two business SELECTs/four statements including fixture savepoint/release; bulk publish used 611 statements and withdrawal 189. Neither bulk commits per item. Progress is one joined projection, candidates are paginated and retention selection capped. Bounded N+1 remains in publication/replay/student-list/grouping projections. Unbounded teacher histories/teacher remediation limit are M4, not claimed as safe pagination.

Settings instantiate/validate paper operational and retention settings at startup. Positive ceilings, 25 MiB/25 MP/200-page security bounds, artifact/batch relationships, provider/page relationship, matching concurrency 1, execution lease ≥ twice provider timeout, grace/windows/claim TTL/run cap are enforced. Defaults are batch 3/global 12; exact global-12 claims and existing ten-paper batch-3 races use real PostgreSQL. A smaller global cap is a tighter aggregate restriction, not an impossible configuration.

Lease/retention eligibility and completion anchors use DB time. Intake/grouping/matching audit timestamps still use application UTC; matching has no authoritative recovery. Paper timestamp guards preserve accepted clock-reversal chronology. Latest choices use unique revision/attempt numbers or timestamp plus secondary keys; publication/page/retention history is deterministic. No unrelated Unicode normalization was changed.

## OpenAPI, dead code, logging, secrets and errors

OpenAPI was generated for 49 paper/scan-specific routes. Auth dependencies and typed command schemas are present, but most scan/publication responses and manually parsed upload bodies lack complete OpenAPI schemas (M6). Runtime projections do not serialize persistence objects. Shared remediation has typed models. Selected/bulk withdrawal are intentional aliases; no obsolete ledger-bypassing publication route or pre-capacity execution route was found. Unused intake response DTOs were retained.

Provider/storage secrets remain environment/config sourced; they are absent from published snapshots, audit details and this report. Provider builders do not log bytes/raw payloads. Private provider evidence is stored behind teacher-only boundaries. Retention CLI emits IDs/counts/codes, not storage references. No new logging infrastructure was added.

Production `app.main` maps HTTP errors to `{error:{code,message,details,request_id}}` and unexpected errors to bounded `internal_error` 500. Lightweight test apps may expose FastAPI `detail`. Review/publication/batch/storage-read domain codes retain bounded handlers. Some checking-intake messages remain legacy prose, and matching string semantic codes can become `unexpected_internal_error`; this taxonomy is maintenance debt. Unexpected SQL/provider exception text is no longer converted into paper-intake HTTP 409.

The error literal/source inventory is `local-backups/paper-audit/error-codes.json`; dynamic transition/domain strings are not falsely claimed to be a complete fixed enum. It is regenerated at final validation.

## Migration chain, real PostgreSQL matrix and ORM drift

Linear scanned-paper chain:

`20260914_02 → 20260914_03 → 20260915_01 → 20260915_02 → 20260923_01 → 20261005_01 → 20261010_01 → 20261010_02 → 20261011_01 → 20261011_02 → 20261011_03 → 20261012_01 → 20261012_02`.

History/heads confirm one head/correct parents. DDL/function/DO bodies were inspected and executed on PostgreSQL; function-body semicolons are not asyncpg multi-command strings. Fresh base→head, explicit pre-scan boundary→head and `20261012_01 → 02 → 01 → 02` passed in disposable databases.

Schema inspection covered 35 checking/scan/paper/remediation tables: nullability, declared server defaults, indexes, FK targets/actions, enum labels and checks. Missing paper checks and retention-ID declaration were corrected in ORM only, retaining accepted PostgreSQL behavior. Shared remediation constraint names/anonymous declarations differ without missing enforcement. DB correspondence gaps remain M3, not migration drift.

PR15 intentionally refuses null active pointers; republish permits downgrade but loses command/withdrawal audit. PR12 refuses manual reviews and PR11 refuses paper remediation. PR14 drops purge metadata without restoring storage; earlier paper checking downgrades explicitly delete evidence. Populated refusal gates are recorded separately. The full historical downgrade chain is not claimed as lossless rollback.

## Validation results and baseline test debt

Evidence: `local-backups/paper-audit/` contains XML/logs, exact module lists, a snapshot of the requested SHA, OpenAPI, schema inspection, migration proofs and controlled characterizations. Connection settings were read privately, never printed. Test databases end in `_test`; providers are deterministic doubles, files are real.

| Gate | Passed | Skipped | Failed | Errors | Evidence |
|---|---:|---:|---:|---:|---|
| Dedicated scanned-paper PostgreSQL (22 modules) | 478 | 4 | 0 | 0 | `dedicated-stable.xml` / `.log` |
| Coupled PostgreSQL modules, forward order (11) | 400 | 0 | 0 | 0 | `forward-stable.xml` / `.log` |
| Same PostgreSQL modules, reverse order | 400 | 0 | 0 | 0 | `reverse-stable.xml` / `.log` |
| Requested relevant-integration selector | 604 | 4 | 0 | 0 | `selector-stable.xml` / `.log` |
| Final audit regressions | 18 | 0 | 0 | 0 | `focused-final.xml` / `.log` |
| PR11 migration gates, explicitly enabled | 2 | 0 | 0 | 0 | `pr11-migration.xml` / `.log` |
| PR12 migration gates, explicitly enabled | 2 | 0 | 0 | 0 | `pr12-migration.xml` / `.log` |
| Full current unit suite | 1317 | 1 | 2 | 4 | `units.xml` / `.log` |
| Exact accepted-base full units | 1317 | 1 | 2 | 4 | `baseline-units.xml` / `.log` |
| 30-paper performance sanity | 1 | 0 | 0 | 0 | `performance.xml` / `.log` |

The dedicated run includes four environment-gated migration skips; all four were run separately with their explicit PR11/PR12 gates and passed. The requested selector is `pytest -q tests/integration -k "scan or checking or paper or publication or withdrawal or batch or remediation or manual or retention or operational"`. Each final PostgreSQL gate used its own disposable database.

Exact-base negative comparisons: 11 regression failures in `baseline-final-regressions.xml` plus four in `baseline-latest.xml`; those 15 defect assertions pass in the final 18-case audit suite. The remaining three cases extend failure-isolation/capacity/crash evidence. The three current and exact-base gap characterizations each pass by asserting the observed unresolved matching/orphan/correspondence defects; they are not readiness-positive tests.

Fresh base→head, pre-scan `20260914_02`→head, and PR15 `20261012_01 → 02 → 01 → 02` migration scripts passed; evidence is in `migration.log` and `historical-*.log`. Populated withdrawal downgrade coverage is in the cross-order suite. No migration was added.


Forward cross-order sequence under `backend/tests/integration/`: `test_paper_checking_execution_database.py`, `test_paper_checking_review_database.py`, `test_manual_paper_review_database.py`, `test_paper_result_publication_database.py`, `test_paper_publication_withdrawal_database.py`, `test_paper_publication_withdrawal_races.py`, `test_paper_publication_commands_database.py`, `test_paper_batch_orchestration_database.py`, `test_paper_remediation_database.py`, `test_scan_storage_retention_database.py`, `test_paper_operational_limits_database.py`. Reverse runs exactly the same list in reverse order.

One final full-unit failure is `tests/unit/test_checking_handoff_fixture.py::test_checking_handoff_v1_fixture_schema_and_invariants`: NFC assertion compares decomposed `Café` with composed `Café`. The exact accepted-base full suite reproduces it; paper code is not involved and it does not block this workflow.

The four unit errors are setup/teardown errors for two `tests/unit/test_input_artifacts.py::test_upload_rejects_unsafe_input_before_storage` cases: `image/gif` with `GIF89a` + 65,530 `x` characters, and `image/png` with 26,214,401 `x` characters. Pytest setting `PYTEST_CURRENT_TEST` raises `ValueError: the environment variable is longer than 32767 characters`. Full exact node IDs and exceptions are retained in both XML reports; their enormous autogenerated parameter IDs are not pasted into this report. Both cases/all four errors reproduce on the exact base. These are generic harness errors, not failed bounded paper uploads.

The final current and exact accepted-base full units also reproduce `tests/unit/test_classroom_notes.py::test_privacy_admin_rules_and_cas` with `DID NOT RAISE ClassroomNotesError`. It passed in the earlier completed current run. This is evidenced intermittent unrelated CAS debt, left unchanged; the failing test exercises classroom-note optimistic concurrency, not paper workflow code.

Intermediate harness failures are retained and excluded from clean gates: inaccessible default temp directory, missing basetemp parent, Windows backslash splitting in `PYTEST_ADDOPTS`, and overlapping migration/fixture work on one database that caused DDL deadlock. Final suites use created parents, forward-slash paths and isolated databases/process sequencing. Empty-output interpreter exit `3221227274` also occurred in migration attempts; completed reruns/baseline comparisons are recorded rather than hiding failed attempts. The actual production retention/publication deadlock has a distinct controlled reproduction and fix.

Static checks: `python -m compileall -q app alembic/versions tests`, `git diff --check`, `alembic heads`, `alembic history`, and `docker compose config --quiet` pass. Alembic has one head, `20261012_02`. Full `ruff check app tests alembic/versions` still fails with 900 violations, exactly 900 on the accepted base; comparison by relative file/rule reports no added violation. The new audit test passes Ruff from the backend working directory. Raw full-project JSON and baseline outputs are retained. No unrelated style cleanup was performed.

## E2E evidence and practical limits

- Failure isolation: five confirmed papers in one class, genuine five-page source fixture and physical PNGs. A succeeds with AI; B transport-retries/succeeds; C terminal semantic failure switches to manual; D withdraws/republishes; E creates/assigns remediation; completed source cleanup preserves active results/pages. Existing actual-intake/grouping and full assigned-remediation execution E2Es also run. The combined five-paper fixture starts at confirmed grouping; it is not a live-provider five-paper intake test.
- Provider crash/retry: a provider response precedes injected finalize interruption; an aged claim is reclaimed via the existing PostgreSQL stale-attempt fixture pattern; two calls result in one AI revision and one item result. This proves persistence fencing, not provider billing exactly once.
- Retention crash/retry: `test_missing_storage_is_successful_idempotent_purge[after_claim]` deletes real bytes before finalize interruption/claim aging; replay treats absence as successful purge.
- Publication replay: `test_mandatory_single_http_e2e` plus both selected/automatic three-paper `test_mandatory_bulk_historical_e2e` prove K1→A, withdrawal, old replay without mutation, K2→B, old replay leaves B, K2 returns B. Durable reuse/restart/concurrency cases run too.
- Retention/publication: active protection, grace/exact expiry, both claim/publish orderings, real purge and safe post-purge republish rejection run. The new grouping-state mismatch proof independently preserves active bytes.
- Student security: two-student HTTP proofs cover foreign result/page, unpublished/withdrawn result, source denial, linked identity and snapshot allowlists. Teacher content distinguishes missing, purged 410 and unavailable storage.
- Performance: the 30-paper query count is a bounded sanity check, not a load benchmark. Capacity tests use PostgreSQL; provider semantics use doubles.

## Exact changed files

- `backend/app/application/scan_intake.py`
- `backend/app/application/scan_matching_service.py`
- `backend/app/infrastructure/artifact_storage.py`
- `backend/app/infrastructure/checking_models.py`
- `backend/app/infrastructure/scan_checking_models.py`
- `backend/app/infrastructure/scan_page_pipeline.py`
- `backend/app/infrastructure/scan_storage_retention_models.py`
- `backend/app/infrastructure/scan_storage_retention_repository.py`
- `backend/app/presentation/scan_intake_routes.py`
- `backend/tests/integration/test_manual_paper_review_migration.py`
- `backend/tests/integration/test_paper_checking_execution_database.py`
- `backend/tests/integration/test_paper_production_audit_database.py`
- `backend/tests/integration/test_paper_remediation_migration.py`
- `docs/paper-production-readiness-audit.md`

## Route inventory

All scan/publication paths below use `/api/assessment-core`. Paper remediation is on the shared `/api` router. Teacher routes use capability plus authoritative class membership/admin semantics; student routes use linked academic identity/current active ownership. Internal repository ports do not substitute for HTTP authorization.

| Method/path | Handler | Actor / object check | Write identity or retry | Provider / bytes |
|---|---|---|---|---|
| `GET /api/assessment-core/scan-batches/{batch_id}/checking` | `paper_batch_progress` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/checking/start` | `start_batch_checking` | Teacher capability; authoritative paper/batch/class join | Locked state / accepted transition | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/checking/reconcile` | `reconcile_batch_checking` | Teacher capability; authoritative paper/batch/class join | Locked state / accepted transition | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/complete` | `complete_paper_batch` | Teacher capability; authoritative paper/batch/class join | Locked state / accepted transition | None |
| `POST /api/assessment-core/assignments/{assignment_id}/scan-batches` | `create_batch` | Teacher capability; assignment → class access (replay reauthorized) | Actor + request key + payload hash | None |
| `GET /api/assessment-core/assignments/{assignment_id}/scan-batches` | `list_batches` | Teacher capability; assignment → class access (replay reauthorized) | Read | None |
| `GET /api/assessment-core/scan-batches/{batch_id}` | `get_batch` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/cancel` | `cancel` | Teacher capability; authoritative paper/batch/class join | Locked state / accepted transition | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/artifacts` | `upload` | Teacher capability; authoritative paper/batch/class join | Unique batch upload position / locked ceilings | Private source write |
| `GET /api/assessment-core/scan-batches/{batch_id}/artifacts` | `artifacts` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `GET /api/assessment-core/scan-batches/{batch_id}/pages` | `pages` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `GET /api/assessment-core/scan-batches/{batch_id}/artifacts/{artifact_id}/content` | `artifact_content` | Teacher capability; authoritative paper/batch/class join | Read | Authorized bytes; student normalized page only |
| `POST /api/assessment-core/scan-batches/{batch_id}/extract` | `extract` | Teacher capability; authoritative paper/batch/class join | Locked state / accepted transition | Private source read / normalized PNG write |
| `GET /api/assessment-core/scan-pages/{page_id}/content` | `page_content` | Teacher capability; authoritative paper/batch/class join | Read | Authorized bytes; student normalized page only |
| `POST /api/assessment-core/scan-batches/{batch_id}/match` | `match_pages` | Teacher capability; authoritative paper/batch/class join | Run/chunk claims; stale recovery missing (B1) | Provider: opaque page tokens + minimal roster |
| `GET /api/assessment-core/scan-batches/{batch_id}/matching` | `matching_results` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/grouping` | `initialize_grouping` | Teacher capability; authoritative paper/batch/class join | Revision / expected version; confirm paper uniqueness | None |
| `GET /api/assessment-core/scan-batches/{batch_id}/grouping` | `get_grouping` | Teacher capability; authoritative paper/batch/class join | Revision / expected version; confirm paper uniqueness | None |
| `PATCH /api/assessment-core/scan-batches/{batch_id}/grouping/pages/{page_id}` | `assign_grouping_page` | Teacher capability; authoritative paper/batch/class join | Revision / expected version; confirm paper uniqueness | None |
| `PUT /api/assessment-core/scan-batches/{batch_id}/grouping/groups/{participant_id}/page-order` | `reorder_grouping_pages` | Teacher capability; authoritative paper/batch/class join | Revision / expected version; confirm paper uniqueness | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/grouping/confirm` | `confirm_grouping` | Teacher capability; authoritative paper/batch/class join | Revision / expected version; confirm paper uniqueness | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/grading-policies/{variant_id}` | `freeze_grading_policy` | Teacher capability; authoritative paper/batch/class join | Locked state / accepted transition | None |
| `GET /api/assessment-core/scan-batches/{batch_id}/grading-policies` | `list_grading_policies` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs` | `create_paper_check_run` | Teacher capability; authoritative paper/batch/class join | Paper + request key + frozen input fingerprint | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/execute` | `execute_paper_check` | Teacher capability; authoritative paper/batch/class join | Durable attempt claim / lease fence | Provider: frozen academic context + opaque page/item tokens |
| `GET /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/ai-result` | `get_paper_ai_result` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review` | `start_paper_review` | Teacher capability; authoritative paper/batch/class join | Immutable revisions / source lock / expected version | None |
| `GET /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review` | `get_paper_review` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `PUT /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review` | `edit_paper_review` | Teacher capability; authoritative paper/batch/class join | Immutable revisions / source lock / expected version | None |
| `GET /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review/history` | `paper_review_history` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/check-runs/{check_run_id}/review/approve` | `approve_paper_review` | Teacher capability; authoritative paper/batch/class join | Immutable revisions / source lock / expected version | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/manual-review` | `start_manual_paper_review` | Teacher capability; authoritative paper/batch/class join | Immutable revisions / source lock / expected version | None |
| `GET /api/assessment-core/paper-submissions/{paper_submission_id}/manual-review` | `get_manual_paper_review` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `PUT /api/assessment-core/paper-submissions/{paper_submission_id}/manual-review` | `edit_manual_paper_review` | Teacher capability; authoritative paper/batch/class join | Immutable revisions / source lock / expected version | None |
| `GET /api/assessment-core/paper-submissions/{paper_submission_id}/manual-review/history` | `manual_paper_review_history` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/manual-review/approve` | `approve_manual_paper_review` | Teacher capability; authoritative paper/batch/class join | Immutable revisions / source lock / expected version | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/publications` | `publish_paper` | Teacher capability; authoritative paper/batch/class join | Actor + request key + command kind + fingerprint ledger | Required page bytes verified before publication |
| `GET /api/assessment-core/paper-submissions/{paper_submission_id}/publications` | `publication_history` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/publications/selected` | `publish_selected` | Teacher capability; authoritative paper/batch/class join | Actor + request key + command kind + fingerprint ledger | Required page bytes verified before publication |
| `POST /api/assessment-core/scan-batches/{batch_id}/publications/bulk` | `publish_bulk` | Teacher capability; authoritative paper/batch/class join | Actor + request key + command kind + fingerprint ledger | Required page bytes verified before publication |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/publications/{publication_id}/withdraw` | `withdraw_publication` | Teacher capability; authoritative paper/batch/class join | Actor + request key + command kind + fingerprint ledger | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/publications/withdraw/selected` | `withdraw_selected` | Teacher capability; authoritative paper/batch/class join | Actor + request key + command kind + fingerprint ledger | None |
| `POST /api/assessment-core/scan-batches/{batch_id}/publications/withdraw/bulk` | `withdraw_selected` | Teacher capability; authoritative paper/batch/class join | Actor + request key + command kind + fingerprint ledger | None |
| `GET /api/assessment-core/student/paper-submissions/{paper_submission_id}/result` | `student_result` | Student; linked identity + current active publication membership | Read | None |
| `GET /api/assessment-core/student/paper-results` | `student_results` | Student; linked identity + current active publication membership | Read | None |
| `GET /api/assessment-core/student/paper-submissions/{paper_submission_id}/pages/{scan_page_id}` | `student_page` | Student; linked identity + current active publication membership | Read | Authorized bytes; student normalized page only |
| `GET /api/assessment-core/paper-submissions/{paper_submission_id}/remediation-eligibility` | `paper_eligibility` | Teacher capability; authoritative paper/batch/class join | Read | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/remediations/candidates/search` | `paper_candidates` | Teacher capability; authoritative paper/batch/class join | Locked state / accepted transition | None |
| `POST /api/assessment-core/paper-submissions/{paper_submission_id}/remediations` | `paper_create` | Teacher capability; authoritative paper/batch/class join | Publication provenance + owner / plan request identity | None |

Shared continuation routes preserve existing plan-owner/admin or student-owner rules: `/api/remediations/{id}` read/edit, assign, cancel, result; classroom/student remediation histories; `/api/student/remediations/{id}` detail/start/answers/submit/execution; shared checking/result surfaces for assigned remediation submissions. Retention has no HTTP route.

Shared continuation routes consuming paper-origin plans (all also require trusted origin):

| Method/path | Handler | Authorization | State / response |
|---|---|---|---|
| `GET /api/remediations/{id}` | `get` | Teacher remediation capability + plan owner/admin | Read; explicit response schema |
| `GET /api/remediations/{id}/result` | `remediation_result` | Teacher remediation capability + plan owner/admin | Read; explicit response schema |
| `PATCH /api/remediations/{id}` | `update` | Teacher remediation capability + plan owner/admin | Locked accepted lifecycle; explicit response schema |
| `POST /api/remediations/{id}/assign` | `assign` | Teacher remediation capability + plan owner/admin | Locked accepted lifecycle; explicit response schema |
| `POST /api/remediations/{id}/cancel` | `cancel` | Teacher remediation capability + plan owner/admin | Locked accepted lifecycle; explicit response schema |
| `GET /api/classrooms/{class_id}/students/{student_id}/remediations` | `history` | Teacher remediation capability + classroom/student access | Read; explicit response schema |
| `GET /api/student/remediations` | `student_list` | Linked student identity + plan ownership + read capability | Read; explicit response schema |
| `GET /api/student/remediations/{id}` | `student_detail` | Linked student identity + plan ownership + read capability | Read; explicit response schema |
| `GET /api/student/remediations/{id}/execution` | `execution` | Linked student identity + plan ownership + execute capability | Read; explicit response schema |
| `POST /api/student/remediations/{id}/start` | `start_execution` | Linked student identity + plan ownership + execute capability | Locked accepted lifecycle; explicit response schema |
| `PUT /api/student/remediations/{id}/answers/{item_id}` | `save_execution_answer` | Linked student identity + plan ownership + execute capability | Owner + plan item + active attempt; answer update/delete |
| `DELETE /api/student/remediations/{id}/answers/{item_id}` | `delete_execution_answer` | Linked student identity + plan ownership + execute capability | Owner + plan item + active attempt; answer update/delete |
| `POST /api/student/remediations/{id}/submit` | `submit_execution` | Linked student identity + plan ownership + execute capability | Locked accepted lifecycle; explicit response schema |

## Dedicated PostgreSQL module inventory

- `backend/tests/integration/test_manual_paper_review_database.py`
- `backend/tests/integration/test_manual_paper_review_migration.py`
- `backend/tests/integration/test_paper_batch_orchestration_database.py`
- `backend/tests/integration/test_paper_batch_orchestration_migration.py`
- `backend/tests/integration/test_paper_checking_clock_regression_database.py`
- `backend/tests/integration/test_paper_checking_execution_database.py`
- `backend/tests/integration/test_paper_checking_intake_database.py`
- `backend/tests/integration/test_paper_checking_review_database.py`
- `backend/tests/integration/test_paper_checking_review_migration.py`
- `backend/tests/integration/test_paper_checking_review_routes.py`
- `backend/tests/integration/test_paper_operational_limits_database.py`
- `backend/tests/integration/test_paper_production_audit_database.py`
- `backend/tests/integration/test_paper_publication_commands_database.py`
- `backend/tests/integration/test_paper_publication_withdrawal_database.py`
- `backend/tests/integration/test_paper_publication_withdrawal_races.py`
- `backend/tests/integration/test_paper_remediation_database.py`
- `backend/tests/integration/test_paper_remediation_migration.py`
- `backend/tests/integration/test_paper_result_publication_database.py`
- `backend/tests/integration/test_paper_result_publication_migration.py`
- `backend/tests/integration/test_scan_grouping_database.py`
- `backend/tests/integration/test_scan_intake_database.py`
- `backend/tests/integration/test_scan_storage_retention_database.py`

## Initial deferred work and freeze decision

Resolve B1 with a reviewed matching recovery/fencing contract and H6 with a durable orphan lifecycle before declaring readiness. Resolve B5 before approving data-bearing rollback. Keep the reproduced M3 integrity gap and design new post-head constraints/immutability guards. Explicitly decide history/OpenAPI contracts, extraction claims, cancelled/incomplete storage policy and permissible historical downgrade behavior. Keep unrelated NFC/CAS/Windows/Ruff debt separately evidenced.

All production defects fixed: **no**. Remaining non-blocking debt for ordinary forward operation: M3–M8 and L2, with the rationales above; B1 and H6 block production readiness, and B5 blocks safe data-bearing rollback. A local hardening commit and passing subsystem cases do not authorize push, merge, frontend work or backend freeze.

PAPER BACKEND PRODUCTION-READINESS BLOCKED



## Final blocker follow-up — implementation and focused proof

Branch: `codex/paper-production-readiness-audit`. Starting HEAD: `d919bf1c64031899a05270a6eb5310a021101734`. `git merge-base --is-ancestor d919bf1 HEAD` succeeded. Accepted-base SHA remains `d9b33aae1ac4b5ff76a3b79992599e52b08c1638`. No push, merge or frontend work is authorized or performed.

### Matching crash recovery — RESOLVED

B1's exact reproduced defect was a durable `running` run/chunk with no lease or owner identity: `_begin_or_resume` rejected even a day-old running run, and `claim_chunk` accepted only pending/retryable rows. A crash after creating the run, after claiming a chunk, during the provider call, or before finalization therefore had no supported path back.

`ScanMatchingRun` now holds a UUID claim token and a database-clock lease of fifteen minutes. Batch then run locks serialize claim/reclaim with a prior finalizer. Claim checks and renewal use PostgreSQL `clock_timestamp()`, never process-local wall time. Legacy active rows with no token are recoverable. Recovery reuses the same run/revision and frozen roster/page/request identity; only unfinished running chunks return to pending. Completed chunks, proposals, candidates and successful evidence remain unchanged.

`scan_matching_attempts` persists an attempt immediately before external invocation; interrupted attempts settle with `matching_lease_expired`. Validated response, provider request/usage metadata and prompt metadata settle with the canonical proposals in one transaction. Run locks, the owner token and non-expired lease fence success and failure persistence; an old worker cannot mark its replacement failed. Existing proposal uniqueness limits one logical result per run/page, and new PostgreSQL triggers forbid replacing settled attempts or proposals/candidates. A database failure after validated provider success is retryable; a lost success-commit acknowledgement observes the committed chunk and preserves it. Finalization remains separate from provider-result persistence, is fenced, and still requires the batch to be matching. Replay after run finalization reconstructs persisted results.

The accepted matching implementation had no numeric provider retry budget. This change introduces none and preserves its retryable-provider classification and SDK retry policy. A pre-provider claim consumes no provider attempt. An interrupted/unknown external call may execute again: exactly-once external provider execution or billing is not promised. Canonical database persistence is fenced and unique. No fake grading ModelRun or CheckRun is created for matching.

Focused PostgreSQL proof is in `test_paper_blocker_recovery_database.py`: five boundaries in `test_matching_crash_recovery_and_post_finalize_replay`; `test_concurrent_recovery_fences_old_provider_and_one_finalizer`; `test_provider_success_database_finalize_failure_is_retryable`; and `test_matching_success_evidence_is_database_immutable`. Concurrency uses independent committed PostgreSQL connections and event barriers, with SQL-controlled lease expiry and no sleeps. The stale owner and a second recovery contender are refused; one replacement finishes and one canonical proposal exists. The provider-success E2E makes two external calls, keeps interrupted→successful attempt lineage, preserves one run and proposal, requires teacher review, refuses to seed unmatched proposals into groups, and then advances through explicit teacher assignment/confirmation to `ready_for_checking`.

### Orphan-file cleanup — RESOLVED

H6's exact leak paths were `ScanPagePipeline.upload` (source bytes before InputArtifact/ScanBatchArtifact registration) and `_persist_page` inside `_extract_artifact` (normalized PNG bytes before derived InputArtifact/ScanPage registration). A hard exit before the reference returned/registered, a rollback, an uncertain commit, or failed compensating deletion left bytes invisible to PR14 retention. No Content Bank or student-attachment creation path was added to cleanup.

The filesystem adapter allocates an exclusive private `scan-<UUID>` namespace. Before its `content` file is written, it creates and fsyncs an ownership marker at `.scan-pending/<batch UUID>/<reference>`. This is a durable scan-only staging journal on the same storage volume, not academic retention history. A collision cannot replace or journal a pre-existing namespace. Generic `store()` objects never enter this journal. Bytes and markers must be retained together in storage backups; this proves process-death recovery, not recovery from loss of the storage volume itself. Empty reservation/journal directories contain no artifact bytes.

Source and page writes hold the existing batch transaction lock from before journaling through database registration/commit. Immediate rollback compensation reacquires that lock and checks registration for this exact journaled reference. A registered object is preserved, including uncertain/lost commit acknowledgement; an unregistered owned allocation is deleted immediately. Successful commits acknowledge/remove only their marker. The row-missing check is applied solely to an exclusive allocation with its durable ownership marker, never to arbitrary shared storage references. Successful/committed/shared generic objects remain intact in regression tests.

If a write is interrupted before returning its reference, the adapter compensates locally; if compensation fails, its pre-existing journal marker survives. If deletion/database reconciliation/acknowledgement fails after rollback, the marker likewise stays discoverable. `python -m app.cli scan-orphans --dry-run` previews this journal; `--execute` retries it under the batch lock, with no completed-batch or 30-day requirement. Deletion returning already absent is success, as is replay after a delete-before-ack crash. Failures remain retryable rather than invisible garbage. Once artifact bytes are absent, failure to remove an empty namespace directory is bounded housekeeping and does not strand the journal; `test_absent_scan_bytes_are_success_even_if_directory_cleanup_fails` proves that boundary. Unknown batch identity is preserved for investigation. PR14 eligibility and purge-history semantics remain unchanged.

`test_scan_storage_failure_compensation_and_retry` covers source/render × write/insert/commit × successful/missing/transient cleanup (18 cases). `test_scan_partial_write_before_registration_is_discoverable` covers partial writes with successful and failed compensation, then recovery. `test_committed_and_unrelated_blobs_are_never_orphan_deleted` covers committed marker replay and unrelated storage. The storage E2Es assert no failed-operation academic row references its blob, any remaining bytes have a durable marker, operator retry removes those bytes/markers, and normal committed source bytes survive. Prior lost-commit and response-refresh regressions remain part of the unchanged 18-case hardening suite.

Temporary multipart files already close through `scan_upload_form`: parser rejection closes spools; disconnect/cancellation closes partial spools; the form's `finally` closes successful/validation/error paths. Rasterization uses in-memory BytesIO/PDFium rather than durable extraction temp files. Extraction now explicitly closes its render generator in `finally`, ensuring PDF document/page/bitmap cleanup on an interrupted persistence loop, as well as success. No database ledger was added for ephemeral resources.

### Audit-history loss during data-bearing rollback — RESOLVED

B5 was specifically Alembic PR15 downgrade, not application transaction rollback. Withdrawal A followed by republishing B restores a non-NULL active pointer, so the old guard permitted dropping withdrawal, command and command-item tables. Re-upgrade recreated empty tables, losing provenance and replay outcomes despite preserving two publication rows and pointer B.

Accepted migration bodies remain unchanged. Instead, `alembic/env.py` preflights the entire requested online downgrade plan inside the same PostgreSQL transaction, before the first migration step. `paper_downgrade_guard.py` locks protected tables against concurrent writers and refuses incompatible populated history with an actionable `Paper downgrade blocked` error. Boundaries cover matching attempts/claims; PR15 withdrawal/commands; PR14 retention; manual review and remediation provenance; publication and review revisions; paper AI/ModelRun/CheckRun evidence; grading policy; grouping/matching/intake history. Representable compatible/empty transitions still run. Offline downgrade is refused because it cannot inspect data; offline upgrades retain their supported path.

This preserves linear migration history and applies safe refusal even to already-applied PR15 installations when using the updated repository's Alembic entry point. Running copied old release migrations or arbitrary privileged DDL bypasses that entry point and is outside this guarantee; deploy the new environment guard with the migrations. Downgrade never recreates externally purged bytes or fabricated legacy/AI provenance.

`test_downgrade_refuses_null_pointer_and_preserves_republish_history` now proves BOTH withdrawn visibility and active replacement refuse. It asserts the actual `Paper downgrade blocked:` refusal message for both attempts, then verifies both publication rows, current pointer B, the original withdrawal, at least three command ledgers, original head revision and the newer matching claim column remain after refusal: no partly downgraded schema. Existing PR11/PR12 data-bearing refusal tests retain their semantic assertions and recognize the earlier whole-plan refusal message. `tests/paper_recovery_migration_acceptance.py` proves real PostgreSQL `20261012_02 → 20261013_01 → 20261012_02 → 20261013_01` with empty/compatible history and fresh `base → head`. The new migration supports only matching recovery state/evidence; the filesystem orphan journal requires no database migration.

### Final verification

Focused PostgreSQL: **48 passed**, comprising the preserved **18** audit regressions and **30** new blocker cases. Dedicated PostgreSQL: **508 passed, 4 skipped**, zero failures/errors across the 23 modules inventoried in the final evidence below; the four gated cases passed separately. Final full units: **1,317 passed, 1 skipped, 2 failures, 4 errors** current; **1,318 passed, 1 skipped, 1 failure, 4 errors** in the latest accepted-base full run. The intermittent classroom-note CAS case passed that base run but failed immediately in the separate unmodified accepted-base focused run (`baseline-cas-1.xml`, 1 failed), with the exact current `DID NOT RAISE ClassroomNotesError` exception. An earlier complete current/base pair both reproduced 1,317/2/4/1. Every current failed/error node and all exception messages (including setup and teardown) compare exactly equal to the union of the latest base full run and focused CAS reproduction in `local-backups/paper-blockers/unit-baseline-comparison.json`; full raw node IDs are retained in XML. There is no new unit regression. The two failures are the NFC handoff fixture assertion and classroom-note CAS `DID NOT RAISE ClassroomNotesError`. Two oversized generic artifact parameter IDs cause four Windows `ValueError: the environment variable is longer than 32767 characters` errors; the mathematical parameter definitions and full nodes are recorded in the initial evidence above and the raw XML.

Full Ruff: **895 current / 900 accepted-base violations**, with no new relative-path/rule/message violation. The five removed violations are import formatting in audit-changed files; no unrelated functional cleanup was done. Every Python file changed anywhere on this audit branch passes the separate changed-file Ruff gate. Compileall, diff whitespace and Compose validation have passed. Raw lint comparison is `local-backups/paper-blockers/ruff-comparison.json`.

Completed migration gates: PR11 two tests, PR12 two tests, PR14/PR15 empty roundtrips and fresh-head scripts, and the new recovery boundary/fresh-head script. Forward and reverse stress each completed with **430 passed**, zero failures/errors, in the exact 12-module orders below. The final selector completed with **634 passed, 4 skipped, 255 deselected**, zero failures/errors, on its separate disposable PostgreSQL database. Earlier exploratory/host-failed runs are excluded from final evidence. A Windows fatal exception in CPython `_wmi` during SQLAlchemy startup required the test-only supported platform fallback (`sys.modules['_wmi']=None`, via local sitecustomize); no application, database, clock or provider behavior is patched. Final PostgreSQL runs use installed CPython 3.12.10 with the project's existing 3.12 dependencies and this local compatibility shim.

A superseded selector attempt finished with 633 passed, four skipped and one setup error at `tests/integration/test_paper_checking_review_routes.py::test_unrelated_teacher_and_student_denied_all_routes[GET--student]`: native Windows `OSError [WinError 121]` during `engine.connect()` / asyncio Proactor `finish_connect`, before application SQL or the test body. Logs/XML are retained as `selector-native-timeout.*`. The exact node subsequently passed on both the unmodified accepted-base checkout and the current branch (`baseline-selector-node.xml`, `current-selector-node.xml`). The transport exception did not reproduce in that comparison; it is not claimed as reproduced baseline debt or used to waive a final error. The full selector rerun completed with 634 passed and zero failures/errors, without source changes or retry wrappers. Only this complete clean rerun is counted as final selector evidence.

Current implementation blocker count: **0**. Unresolved HIGH findings: **0**. Remaining M3–M7/L2 are the original non-blocking forward-operation debt; unsafe historical downgrade loss M8 is now guarded by explicit refusal. All required final readiness gates are now satisfied. The final decision and evidence matrix below supersede the explicitly historical initial BLOCKED decision.


### Final gate inventory and delivery scope

Final dedicated suite (23 modules; supersedes the initial 22-module inventory):

- `backend/tests/integration/test_manual_paper_review_database.py`
- `backend/tests/integration/test_manual_paper_review_migration.py`
- `backend/tests/integration/test_paper_batch_orchestration_database.py`
- `backend/tests/integration/test_paper_batch_orchestration_migration.py`
- `backend/tests/integration/test_paper_blocker_recovery_database.py`
- `backend/tests/integration/test_paper_checking_clock_regression_database.py`
- `backend/tests/integration/test_paper_checking_execution_database.py`
- `backend/tests/integration/test_paper_checking_intake_database.py`
- `backend/tests/integration/test_paper_checking_review_database.py`
- `backend/tests/integration/test_paper_checking_review_migration.py`
- `backend/tests/integration/test_paper_checking_review_routes.py`
- `backend/tests/integration/test_paper_operational_limits_database.py`
- `backend/tests/integration/test_paper_production_audit_database.py`
- `backend/tests/integration/test_paper_publication_commands_database.py`
- `backend/tests/integration/test_paper_publication_withdrawal_database.py`
- `backend/tests/integration/test_paper_publication_withdrawal_races.py`
- `backend/tests/integration/test_paper_remediation_database.py`
- `backend/tests/integration/test_paper_remediation_migration.py`
- `backend/tests/integration/test_paper_result_publication_database.py`
- `backend/tests/integration/test_paper_result_publication_migration.py`
- `backend/tests/integration/test_scan_grouping_database.py`
- `backend/tests/integration/test_scan_intake_database.py`
- `backend/tests/integration/test_scan_storage_retention_database.py`

Forward stress executes these 12 modules in this exact order; reverse stress uses the exact reversed order:

1. `test_paper_checking_execution_database.py`
2. `test_paper_checking_review_database.py`
3. `test_manual_paper_review_database.py`
4. `test_paper_result_publication_database.py`
5. `test_paper_publication_withdrawal_database.py`
6. `test_paper_publication_withdrawal_races.py`
7. `test_paper_publication_commands_database.py`
8. `test_paper_batch_orchestration_database.py`
9. `test_paper_remediation_database.py`
10. `test_scan_storage_retention_database.py`
11. `test_paper_operational_limits_database.py`
12. `test_paper_blocker_recovery_database.py`

Follow-up commit scope (19 files; the starting audit commit remains intact):

- `backend/alembic/env.py`
- `backend/alembic/versions/20261013_01_matching_recovery.py`
- `backend/app/application/scan_intake.py`
- `backend/app/application/scan_matching_service.py`
- `backend/app/cli.py`
- `backend/app/infrastructure/artifact_storage.py`
- `backend/app/infrastructure/paper_downgrade_guard.py`
- `backend/app/infrastructure/scan_checking_models.py`
- `backend/app/infrastructure/scan_matching_repository.py`
- `backend/app/infrastructure/scan_orphan_cleanup.py`
- `backend/app/infrastructure/scan_page_pipeline.py`
- `backend/tests/integration/test_manual_paper_review_migration.py`
- `backend/tests/integration/test_paper_blocker_recovery_database.py`
- `backend/tests/integration/test_paper_checking_execution_database.py`
- `backend/tests/integration/test_paper_operational_limits_database.py`
- `backend/tests/integration/test_paper_publication_withdrawal_races.py`
- `backend/tests/integration/test_paper_remediation_migration.py`
- `backend/tests/paper_recovery_migration_acceptance.py`
- `docs/paper-production-readiness-audit.md`

The delivery commit contains this report together with the fixes and tests; its exact SHA is returned in the completion response (embedding a commit's own SHA in its contents would be circular). Unrelated pre-existing untracked files and local evidence are excluded.


### Final evidence matrix and readiness decision

| Gate | Final result |
| --- | --- |
| Existing audit regressions | 18 passed (preserved) |
| New blocker regressions | 30 passed |
| Combined focused PostgreSQL | 48 passed |
| Dedicated PostgreSQL, 23 modules | 508 passed, 4 gated skips; zero failures/errors |
| Forward stress, 12 modules | 430 passed; zero failures/errors |
| Reverse stress, exact reversed order | 430 passed; zero failures/errors |
| Exact requested integration selector | 634 passed, 4 gated skips, 255 deselected; zero failures/errors |
| Separately enabled gated migrations | PR11 2 passed; PR12 2 passed |
| PR14 / PR15 migration acceptance | Each empty boundary roundtrip and fresh base-to-head passed |
| New recovery migration | Real PostgreSQL old head → new head → old head → new head, and fresh base → head passed |
| Historical pre-scan upgrade | 20260914_02 → head passed |
| Offline migration behavior | New boundary upgrade SQL succeeds; downgrade refuses before DDL |
| Alembic heads | Exactly one: 20261013_01 |
| Full units | 1,317 passed, 2 failed, 4 errors, 1 skipped; exact accepted-base equivalence as detailed above; no new regression |
| Full Ruff | 895 current / 900 accepted-base; no added path/rule/message violation |
| All branch-changed Python files Ruff | Passed |
| Compileall / diff whitespace / Compose config | Passed |

The matching crash E2E preserves one logical run and one canonical proposal across provider-success interruption/replay, records interrupted and successful attempts, and requires explicit teacher grouping review before reaching `ready_for_checking`. The source/render storage E2Es retain no undiscoverable bytes after failure, preserve committed/shared content, and recover transient deletion failure through the durable ownership journal. The rollback E2E retains withdrawal A, republished B, both publications, pointer B, command history, original Alembic revision and the new matching schema after explicit preflight refusal.

Matching crash recovery: **RESOLVED**. Orphan-file cleanup: **RESOLVED**. Audit-history loss during data-bearing rollback: **RESOLVED**. Unresolved BLOCKER findings: **0**. Unresolved HIGH findings preventing normal operation: **0**. Original non-blocking forward-operation findings and independently reproduced unrelated unit/Ruff debt remain documented; they were not hidden or broadened into this fix scope.

One coherent follow-up commit contains the exact 19-file scope above and this completed report. `d919bf1` is preserved in ancestry. No push, merge or frontend work was performed.

PAPER BACKEND PRODUCTION-READINESS ACCEPTED
