# Teacher batch active publication contract hotfix

Base: local `main` at `e67242e1ae1e0f50a8052e4d936c4515d9db6107`.
Branch: `codex/paper-publication-projection-contract`. No push or merge.
The resulting local commit SHA is reported with delivery (rather than embedded
in the commit's own contents).

The authoritative PR4 report was read directly from commit
`9d644704085fc95ad8c8b0a27d499130075ab23f` before implementation.
Current backend source and generated OpenAPI were then inspected. The checking
route currently has an untyped OpenAPI success schema (`{}`); its actual response
is `asdict(PaperBatchProgress)`, whose `papers` entries are repository dictionaries.
This patch preserves that existing representation and adds two response fields.

## Additive contract

`GET /api/assessment-core/scan-batches/{batch_id}/checking`, each `papers` entry:

| Field | JSON type | Authoritative source |
| --- | --- | --- |
| `paper_submission_id` (existing) | UUID string | `PaperSubmission.id` |
| `latest_approved_revision_id` (existing) | UUID string or null | Effective `PaperCheckReview.latest_approved_revision_id` |
| `review_state` (existing) | string or null | Effective `PaperCheckReview.state` |
| `active_publication_id` (new) | UUID string or null | `PaperResultPublication.id` joined through the active state pointer |
| `active_publication_approved_review_revision_id` (new) | UUID string or null | That same publication row's `approved_review_revision_id` |

The existing effective review selection is unchanged: manual review takes
precedence; otherwise the canonical highest CheckRun attempt supplies the review.
Its latest approved pointer is preserved while corrections are drafts and advances
when a new approval succeeds. No review history discovery is needed.

The publication join follows
`PaperResultPublicationState.active_publication_id = PaperResultPublication.id`,
also matching `paper_submission_id`. The same authoritative pointer is used by
student visibility, publication writes and withdrawal. No timestamps, historical
row ordering or withdrawal-history inference determine activity. The database's
composite foreign key guarantees the active row belongs to the paper.

Both new values are selected from that publication row in the existing single
batch projection SELECT. No active pointer (including after withdrawal) produces
`null/null`. No current approval produces a null existing approval identity.
No publication snapshot, storage references, provider data, withdrawal reason
or publication history is selected or returned by this addition.

## Completion report

| Requested item | Result |
| --- | --- |
| 1. Branch | `codex/paper-publication-projection-contract`, from the exact specified local main |
| 2. Commit | Local commit SHA provided in the delivery message |
| 3. Changed files | `backend/app/infrastructure/paper_batch_orchestration_repository.py`; `backend/tests/integration/test_paper_publication_projection_database.py`; this document |
| 4. Endpoint / DTO | Checking GET above; existing `PaperBatchProgress.papers` entries. Other actions returning progress inherit the additive entries. No route redesign or DTO field removal |
| 5. Added fields | Two nullable UUIDs listed above |
| 6. Current approval | Existing effective review's `latest_approved_revision_id`, unchanged |
| 7. Active publication | Actual publication `id`, via the authoritative state pointer |
| 8. Publication source revision | Active publication row's `approved_review_revision_id` |
| 9. Active semantics | Existing publication state pointer; same identity as student visibility and publication/withdrawal services |
| 10. Null behavior | No publication and withdrawn publication: null/null; current approval remains independent |
| 11. Corrected R1/A + R2 | Current approval R2, active A, active source R1; tested after actual edit and reapproval |
| 12. Withdrawal | Active pair null/null; checking/review identity and academic state remain intact |
| 13. Same revision republish | New B ID with R1; withdrawn A remains historical |
| 14. New revision publish | New B ID with R2; current approval R2; superseded A is absent from active projection |
| 15. Completed batch | A, null/null after withdrawal, then B; batch remains completed throughout |
| 16. Bulk | Official bulk HTTP endpoint followed by checking GET returns distinct IDs and exact revisions for both papers |
| 17. Concurrency | Two real PostgreSQL connection cases read during an uncommitted publish/withdraw, then after commit. A/R1 before; B/R2 or null/null after. Single-statement MVCC selects both values from one row |
| 18. Withdrawal ID | Owner, second assigned teacher and Admin each pass the exact projected ID to the existing withdrawal HTTP endpoint successfully; subsequent checking GET is null/null |
| 19. Co-teacher | Same active/current/source identities; exact-ID withdrawal succeeds without publisher ownership restriction. Existing normal-navigation context regressions also run |
| 20. Negative authorization | Unrelated and revoked teachers denied (404); student denied (403). Teacher assigned to another class cannot read substituted batch/paper identities or withdraw; no publication IDs in denied bodies |
| 21. Queries / N+1 | Exactly one repository progress SELECT for 30 papers with active, withdrawn and unpublished mix. No history calls; no snapshot column selected. Existing bounded authorization/load queries remain unchanged |
| 22. Focused tests | 19 cases in the new module; initial dedicated run: 19 passed |
| 23. Dedicated PostgreSQL | All 19 cases use PostgreSQL; two concurrency cases use disposable database clones and separate connections |
| 24. Relevant regressions | 425 passed, including 19 new cases and 406 existing affected cases; 1 warning, 400.19 seconds |
| 25. Static checks | Ruff on both changed Python files; compileall of app/tests; diff check; Compose config: passed |
| 26. Alembic head | `20261013_01` (single head) |
| 27. Migration | None; persistence already contains both identities |
| 28. Write contracts | Publication endpoints, command ledger, Idempotency-Key, fingerprints, replay, single/bulk publication and withdrawal contracts unchanged |
| 29. Frontend PR4 | Branch still at `9d644704085fc95ad8c8b0a27d499130075ab23f`; no frontend diff, rebase, merge or continuation; PR5 not started |
| 30. Remaining contract limitations | Requested identity blocker resolved. The PR4 audit additionally requests page-availability eligibility/blockers and last-publication/withdrawal metadata; these remain absent from this projection and are outside this explicitly identity-only hotfix. No full PR4 UI acceptance is claimed |

## Verification

Focused invocation:

```text
python -m pytest tests/integration/test_paper_publication_projection_database.py -q
19 passed in 34.94s
```

Affected regression selection (no full historical backend run):

- Unit: batch orchestration, checking/review, result publication, withdrawal,
  publication commands.
- PostgreSQL/HTTP: batch orchestration (including completion), checking/review
  contracts/database/routes, result publication, withdrawal database/races,
  teacher normal-navigation context, and the new publication projection module.

Final run after selecting both new identities from the active publication row:

```text
425 passed, 1 warning in 400.19s (0:06:40)
```

Exact selected modules under `backend/tests`:

```text
unit/test_paper_batch_orchestration.py
unit/test_paper_checking_review.py
unit/test_paper_result_publication.py
unit/test_paper_publication_withdrawal.py
unit/test_paper_publication_commands.py
integration/test_paper_batch_orchestration_database.py
integration/test_paper_checking_review_contract_database.py
integration/test_paper_checking_review_database.py
integration/test_paper_checking_review_routes.py
integration/test_paper_result_publication_database.py
integration/test_paper_publication_withdrawal_database.py
integration/test_paper_publication_withdrawal_races.py
integration/test_paper_teacher_context_routes.py
integration/test_paper_publication_projection_database.py
```

Static invocations (from `backend` unless noted):

```text
ruff check app/infrastructure/paper_batch_orchestration_repository.py tests/integration/test_paper_publication_projection_database.py
python -m compileall -q app tests
git diff --check
alembic heads
docker compose config --quiet  (repository root)
```

Tests use the existing disposable `ai-school-pg-test` PostgreSQL container;
ordinary fixtures roll back their writes and concurrency fixtures clean up clones.
An explicit workspace `--basetemp` is used because Windows denies access to the
default pytest temporary directory. No production database is used.
