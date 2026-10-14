# Paper checking/review contracts hotfix acceptance

Implemented from local `main` `3b09b0324b7e0ddf236db58c63ac0a6d1c65796f`.
Authoritative audit: `docs/frontend-paper-pr3-contract-audit.md` at
`732d962cc3fbb6817a97bcd9363f35017f7b9a65`. No frontend implementation.

1. **Branch:** `codex/paper-checking-review-contracts`.
2. **Commit:** the local commit containing this report; exact SHA is returned in
   the chat completion report. Nothing is pushed or merged.
3. **Exact changed files:**
   - `backend/app/application/paper_batch_orchestration.py`
   - `backend/app/application/paper_checking_review.py`
   - `backend/app/application/paper_result_publication.py`
   - `backend/app/infrastructure/paper_batch_orchestration_repository.py`
   - `backend/app/infrastructure/paper_checking_review_repository.py`
   - `backend/app/infrastructure/paper_result_publication_repository.py`
   - `backend/app/presentation/scan_intake_routes.py`
   - `backend/tests/integration/test_manual_paper_review_database.py`
   - `backend/tests/integration/test_paper_batch_orchestration_database.py`
   - `backend/tests/integration/test_paper_checking_review_contract_database.py`
   - `backend/tests/integration/test_paper_checking_review_database.py`
   - `backend/tests/integration/test_paper_operational_limits_database.py`
   - `backend/tests/integration/test_paper_teacher_context_routes.py`
   - `backend/tests/unit/test_paper_checking_review.py`
   - `docs/paper-checking-review-contracts-hotfix.md`
4. **Migration:** none. The accepted teacher overlay already persists drafts and
   immutable revision snapshots as JSONB. Adding an item key to those documents
   requires no physical schema change. Existing AI finding/result/item FKs remain
   authoritative; membership of teacher-added JSON findings is enforced in the
   existing review validation boundary. Historical migrations are unchanged.
5. **Alembic head:** single `20261013_01`.
6. **Effective run:** extracted the existing PR10 maximum canonical `attempt_no`
   resolver into `effective_runs`; both aggregate lifecycle derivation and paper
   projection use it. The database enforces unique attempts per paper, so equal
   attempts with different identities cannot exist. Creation times, ModelRun
   attempts and caller-supplied ordering have no precedence. Manual lineage
   overrides AI checking state, retaining PR12 behavior.
7. **Projection:** existing GET `/api/assessment-core/scan-batches/{batch_id}/checking`
   and start/reconcile responses retain aggregate fields and add `papers`.
   It returns one current entry per confirmed paper, deterministically ordered by
   paper UUID. No historical-run list, payload, prompt, provider metadata,
   storage reference or student-account identity is exposed.
8. **Exact paper fields:** `paper_submission_id`, `assignment_participant_id`,
   `display_name`, `assigned_variant_id`, `checking_state`,
   `effective_check_run_id`, `ai_review_available`, `manual_review_available`,
   `review_id`, `review_state`, `latest_approved_revision_id`, `can_retry`,
   `can_execute`. Checking states retain accepted domain names. AI approval is
   explicitly distinguished by `review_state=approved` and the immutable approved
   revision pointer; it does not change AI evidence status. When editing an
   approved review, the old approved pointer remains available. Manual state
   hides the historical AI pointer. Capabilities reflect historical AI successes
   and reviews that forbid manual takeover, not merely the latest failure.
9. **Reload:** refetch checking, take the server's `effective_check_run_id` when
   `ai_review_available`, and POST/GET the existing
   `/paper-submissions/{paper_id}/check-runs/{run_id}/review`. Review creation stays
   explicit, persisted, idempotent and shared between assigned teachers.
10. **Previous start:** transitioned batch lifecycle only; created zero runs.
11. **New start:** locks the batch, validates prerequisites, transitions when
    needed, and atomically creates initial CheckRuns for papers with no AI or
    manual lineage. Uses existing locked handoff, frozen snapshot compiler,
    methodology loading and `CheckingRepository.create_paper_run`; no provider
    calls. Invalid frozen input returns bounded `paper_checking_input_missing`
    and rolls back the command. Initial run request keys are deterministic
    `batch-initial:{paper_uuid}`. Server version markers are
    `checking_routing_contract_v1`, `paper_vision_v1`, `paper_grading_policy.v1`,
    and prompt policy `1.0.0`.
12. **Execution model:** production invoked `PaperCheckingExecutionService` only
    through explicit per-paper HTTP execution; no worker, CLI dispatcher or
    background queue existed. That mechanism remains. New POST
    `/api/assessment-core/scan-batches/{batch_id}/checking/execute` selects pending
    effective runs server-side and advances at most the configured per-batch
    execution window (default three), sequentially. It releases its authorization
    read transaction before invoking the existing short claim/provider/finalize
    service. Returns bounded `outcomes` (run ID plus status or error code) and
    fresh `progress`. Call again while pending work remains. Scheduling is durable;
    leaving after start does not automatically execute pending work. No new
    worker or background infrastructure is implied. A lost execution response is
    recovered by refetching; execution claims and successful replay remain governed
    by the existing service. One paper's failure does not abort other papers.
13. **Repeated start:** discovers existing lineage under the batch lock; no new
    initial runs, provider attempts or duplicate lifecycle events. Repeated start
    can fill genuinely missing initial work in an already-checking batch.
14. **Concurrency:** real separate PostgreSQL connections and two assigned
    teachers produced exactly two initial logical runs for two eligible papers,
    identical progress and valid checking lifecycle. The test proves contention
    with `pg_blocking_pids` before committing the winner; no timing sleeps or
    process-local mutex. Existing batch/paper locks and unique indexes remain.
15. **Mixed state:** five-paper test preserved running, retryable-failed,
    successful-reviewable and manual-reviewing papers; created only the missing
    initial run, including after replay. Separate cases preserve terminal failures
    and manual approval. Completed batches still reject start/new lineage.
    Failed runs use the existing superseding-run retry contract, never silent
    initial recreation; `can_retry` and effective identity describe that action.
16. **Capacity:** durable pending work is independent of execution capacity;
    thirty papers create thirty pending runs. The unchanged execution claim owns
    batch/global admission. Batch execution stops on
    `paper_check_capacity_unavailable`, leaves work pending and creates no
    provider attempt or retry-budget consumption for that denial. Updated PR13
    acceptance discovers scheduled runs and still proves three simultaneous
    provider calls, seven denied calls, zero denial retries and eventual checking,
    approval and publication of all ten papers.
17. **Finding root cause:** immutable `CheckFinding.check_result_id` already
    identifies a `CheckResult.assessment_item_id`; review seeding flattened the
    findings and dropped that canonical identity.
18. **Canonical identity:** `assessment_item_id`, the actual assessment item in
    the frozen grading policy and review item. No new item namespace or task-label
    association.
19. **Persistence:** seed/edit draft JSON and every new immutable approved
    revision snapshot persist `assessment_item_id` on each finding. Existing
    immutable evidence is not rewritten. JSON teacher overlays retain their
    accepted architecture and CAS behavior.
20. **Provider mapping:** unchanged opaque item token -> app-side item/task IDs
    mapping from PR7. Provider membership is validated before persistence; unknown
    item/page tokens are rejected. No DB UUIDs are added to provider requests.
    The application recovers AI finding association from CheckFinding ->
    CheckResult, then checks source correspondence during review edits.
21. **Teacher additions:** source CheckFinding stays nullable; the addition must
    supply a canonical `assessment_item_id` belonging to this review's frozen
    policy. Null/new foreign associations are rejected with
    `paper_review_finding_item_invalid`. Both manual and AI review paths enforce
    this. AI-derived findings cannot be reassigned even to a different valid item
    in the same policy. Existing cross-paper/page/provenance checks remain.
22. **Review DTO:** flat `draft.findings` gains `assessment_item_id`; approved
    revision/history findings carry the same key. Frontend joins only that key to
    `draft.items[].assessment_item_id`. No raw provider token is exposed.
23. **Historical rows:** optional field supports parsing old persisted JSON.
    Legacy AI links are deterministically enriched on reads from exact source FKs,
    constrained to the source run; no database rewrite. Missing AI keys submitted
    in old-shaped edits can likewise be recovered from that immutable source.
    Legacy teacher additions lacking source lineage remain explicitly null,
    meaning unknown legacy association, not a paper-global finding. They must be
    assigned by the teacher or removed before a new edit/approval. Existing
    approved legacy snapshots can still be published without fabricating a link.
    Old publication documents parse with a nullable key. No destructive backfill
    or historical evidence changes. Current paper-checking semantics require
    item-bound findings; no new global-finding feature.
24. **Publication/remediation:** published finding DTO carries the canonical key;
    publication snapshots preserve teacher-surviving findings only. Legacy source
    linkage is enriched for newly created publications when exact FKs allow it.
    Existing publication snapshots remain immutable. Remediation still consumes
    published structured signals, including teacher removals; no source-finding
    resurrection. Publication/remediation regressions pass.
25. **Authorization/co-teacher:** existing paper-management capability and
    assigned-class-teacher/Admin boundary reused. A second assigned teacher reads
    progress, starts the batch, executes/discovers a successful paper and opens
    review. Existing normal assignment/class context tests pass.
26. **Negative authorization:** unrelated/revoked teachers and students denied;
    existing context test additionally verifies co-teacher GET checking, start and
    batch execute deny a real foreign-class batch. Existing cross-paper review
    and provenance regressions pass. No roster/state response to denied callers.
27. **Focused unit suites:** 121 passed across batch lifecycle, checking execution,
    review and publication contracts, including canonical attempt ordering,
    unknown-state fail-closed behavior, review grading and immutable snapshots.
28. **Dedicated PostgreSQL module:** 35 passed in
    `test_paper_checking_review_contract_database.py`; zero skipped.
29. **Reload integration:** passed real PostgreSQL ready -> durable start ->
    execution -> fresh projection -> discovered run -> review -> approval ->
    publication, with stable item-linked findings.
30. **Batch orchestration integration:** passed five mixed papers and repeated
    start. Thirty-paper scheduling/projection and two-teacher race also passed.
31. **Affected regressions:** 295 unique PostgreSQL cases pass across execution,
    batch lifecycle, manual review, teacher review, publication, remediation,
    operational limits and co-teacher context. Initial combined run: 294 passed
    plus one obsolete PR13 zero-run expectation; updated PR13 suite passes in
    full. Final targeted run: 73 passed (35 dedicated + 27 operational + 11
    co-teacher). Existing Alembic config deprecation warning is unchanged.
32. **Query sanity:** thirty-paper progress uses exactly one joined SQL query
    after batch loading (two queries for the service-level read). It selects only
    scalar state/identity/display fields; the existing canonical attempt history
    feeds the shared resolver internally. One current entry per paper is returned;
    no query per paper/run/review, no provider evidence loading.
33. **Migration acceptance:** fresh disposable PostgreSQL 17 database successfully
    upgraded base -> single existing head `20261013_01`. No new revision means
    the requested new-revision roundtrip is not applicable. Dedicated legacy
    tests prove deterministic AI enrichment and explicit unknown teacher linkage
    without rewriting persisted evidence.
34. **Static checks:** changed Python files Ruff-clean; compileall for app,
    Alembic versions and tests passed; `git diff --check` passed; `alembic heads`
    confirms one head; `docker compose config --quiet` passed.
35. **Remaining PR3 blockers:** the three audited backend gaps are addressed.
    Runtime deliberately requires explicit bounded HTTP execution; it has no
    autonomous worker. Legacy unassociated teacher findings require correction
    before new approval, as documented above. Retry continues through the
    accepted per-paper superseding creation contract.
36. **Frontend protection:** `codex/paper-checking-review-ui` still points to
    `732d962cc3fbb6817a97bcd9363f35017f7b9a65`; frontend diff from main is empty.
    No PR3/PR4 frontend implementation, push or merge.

Acceptance used an isolated local Docker PostgreSQL 17 instance on port 55432
and rollback-isolated fixtures; concurrency fixtures clone disposable test
databases. Tests were never directed at the application's normal database.
