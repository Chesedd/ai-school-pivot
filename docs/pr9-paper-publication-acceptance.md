# PR9 — Explicit paper publication and student projection

Branch: `codex/paper-result-publication`, based on accepted local main
`57ba470b3c460cda4c5cbd616bc33ec05ca09760`. The final commit is reported in the
completion message. No push or PR10 work is included.

## Persistence and transaction boundaries

Migration `20261010_02` has `down_revision = "20261010_01"`.

`paper_result_publications` stores immutable publication identity, paper,
review, exact approved review revision, publishing user, positive contiguous
publication number, optional superseded publication, student-safe JSON snapshot
and timestamp. PostgreSQL rejects UPDATE and DELETE. A revision composite FK
and INSERT guard require the matching paper/review/approved revision. The
supersedes composite FK requires the same paper. Publication numbers are unique
per paper; schema version and JSON-object checks bound the persisted contract.

`paper_result_publication_states` has one primary-key row per paper, a non-null
active publication, positive version and update timestamp. Its composite FK
guarantees that the publication belongs to the paper. Its trigger requires the
next version, matching publication number and predecessor, and rejects pointer
deletion. Publication is not encoded in CheckRun or PaperSubmission status.

Publication locks PaperSubmission rows in UUID order. The caller owns one
transaction covering validation, immutable snapshot creation, publication
insertion and pointer replacement. Real independent PostgreSQL sessions prove
that competing publishers wait on this lock. Identical concurrent revisions
produce one publication; different revisions serialize into consecutive
publication numbers with exactly one current pointer.

Publishing the same active revision returns `already_current`, without a new
row or version increment. Publishing a historical revision creates a new
temporal publication superseding the active record. Editing or approving B
after publishing A leaves A current until an explicit publish action. A remains
queryable in teacher history after replacement.

Selected publication validates every selected paper and approved revision
before any publication write. Every paper must belong to the authorized batch;
duplicates, foreign papers and invalid revisions reject the whole transaction.

Bulk publication is **strict all-or-nothing across the batch**. It selects the
latest immutable approved revision per paper. Any paper without one returns
409 `paper_publication_batch_has_ineligible_papers`, with no publication writes.
The teacher may use the selected endpoint to publish a smaller confirmed set.
Any malformed eligible revision also rejects the whole transaction. A successful
response labels every paper `published` or `already_current`; replay creates no
duplicates. An empty batch returns an empty list. Selections are limited to 500
papers per transaction, including bulk, which rejects larger nonempty sets.

## HTTP API

All paths below are relative to `/api/assessment-core`.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/paper-submissions/{paper_id}/publications` | Explicit exact-revision publication |
| GET | `/paper-submissions/{paper_id}/publications` | Teacher history by publication number, active marker |
| POST | `/scan-batches/{batch_id}/publications/selected` | Atomic selected paper/revision pairs |
| POST | `/scan-batches/{batch_id}/publications/bulk` | Strict batch publication of latest approvals |
| GET | `/student/paper-submissions/{paper_id}/result` | Owner's current publication only |
| GET | `/student/paper-results` | Owner's current results, offset/limit pagination |
| GET | `/student/paper-submissions/{paper_id}/pages/{scan_page_id}` | Confirmed owner's normalized page PNG |

Single publish accepts `approved_review_revision_id`. Selected publish accepts
`entries` of `paper_submission_id` and `approved_review_revision_id`. Unknown
request fields and duplicate selected papers are rejected. Teacher endpoints
reuse scan-management capability, trusted-Origin enforcement and the existing
batch/class teacher authorization. Students and unrelated teachers are denied.

Student endpoints require the existing `student.results.read` capability,
student role and non-null authoritative `Principal.student_id`. Queries compare
that identity directly to `PaperSubmission.student_id`; no login, display name,
email, user ID or second StudentUserLink lookup grants ownership. A teacher with
a student ID cannot masquerade as a student. Missing, foreign and unpublished
papers return the same 404 `paper_result_not_found`. The student list contains
only their current published papers, never replaced publication history.

## Frozen result material and artifacts

The publication snapshot is a strict, frozen `paper_result_publication.v1`
allowlist, created inside the publication transaction from the exact immutable
approved revision. It contains paper/revision identity, summary, item status,
accepted and maximum scores, optional teacher comment, semantic annotations,
and calculated total, maximum, percentage, calculated grade and final grade.
Items use the frozen checking input order; findings use stable UUID order.
Annotations retain the teacher-approved page, normalized region, category,
severity and explanations, excluding AI source lineage identifiers.

Publication reuses PR8 frozen policy validation, verifies review/run/AI/paper
provenance, compares grading policy revision and fingerprint, recomputes
deterministic grade fields and validates confirmed page membership and normalized
regions. Corrupted foreign pages, coordinate spaces, regions, grades, policy
provenance and snapshot run identity fail closed. Student reads validate and
project the stored snapshot; they do not reconstruct grades from current review
or assessment data.

The override reason stays internal to immutable teacher review history. The
student receives the final grade and calculated grade, with no internal override
reason or mechanism. Teacher-edited findings and teacher additions appear;
removed AI findings do not. AI evidence remains unchanged.

Page descriptors expose only scan page ID, confirmed page order, normalized
dimensions, coordinate-space version and the membership-scoped page URL. No
artifact IDs, storage keys, filesystem paths or original filenames are returned.
Page reads require paper ownership, active publication, confirmed
PaperSubmissionPage membership, a ready normalized page, and a derived PNG
artifact. Any artifact attached as a ScanBatchArtifact source is rejected. MIME,
PNG signature, byte length, artifact hash and normalized page fingerprint are
checked before returning bytes. Missing or corrupted content returns the same
bounded 404. Responses use `private, no-store` and `nosniff` headers.

The two-student HTTP fixture includes a real two-page batch source PDF. It proves
owner access only after publication, cross-student denial, guessed page denial,
source and arbitrary artifact-ID denial, and lack of source/private metadata in
student JSON. Deliberately redirecting a page to the source artifact is rejected.
Existing teacher scan-page and batch-artifact routes deny students; the existing
image artifact metadata route also denies students. The separate Attachment
download API operates on Attachment ownership/content relationships and does
not resolve scan InputArtifacts. No existing source-download bypass was found;
no storage redesign or unrelated security patch was needed.

Publication writes only the two new tables. Evidence snapshots prove no changes
to PaperAIRevision, CheckResult, CheckFinding, PaperCheckFindingRegion, ModelRun
or CheckRun. Review revision snapshots remain equal around publication actions.
PaperSubmission stays `ready_for_checking`, CheckRun stays
`completed_with_review_required`, and batch stays `checking`. No remediation,
notifications, automatic publication, revoke/unpublish or completion transition
is introduced.

## Verification

Real PostgreSQL + asyncpg runs use the separate local database
`paper_publication_pr9_test`. Ordinary fixtures use rollback-isolated outer
transactions and savepoint sessions. The two concurrency cases clone only that
disposable `_test` database, commit seed data for independent connections and
drop their clones in fixture cleanup. Those cases require CREATEDB privileges.
No development database was migrated.

| Check | Result |
| --- | --- |
| Publication + PR8 review focused units | 84 passed (16 publication, 68 review) |
| Dedicated publication PostgreSQL module | 39 passed |
| PR8 review → PR9 publication PostgreSQL | 87 passed |
| PR9 publication → PR8 review PostgreSQL | 87 passed |
| PR7 execution → PR9 publication PostgreSQL | 80 passed |
| PR9 publication → PR7 execution PostgreSQL | 80 passed |
| Integration `-k "checking or scan or publication"` | 283 passed, 281 deselected |
| Final full units with Windows-safe byte parameter IDs | 1,102 passed, 1 existing failure |
| Dedicated PR9 migration process gate | 1 passed |
| Ruff on every changed Python file | passed |
| `python -m compileall -q app alembic/versions tests` | passed |
| `git diff --check` | passed |
| `alembic heads` | sole head `20261010_02` |

The dedicated migration gate verified actual database revisions through
`20261010_01 -> 20261010_02 -> 20261010_01 -> 20261010_02`, then recreated the
disposable database's public schema and passed a fresh `alembic upgrade head`.
Downgrade and upgrade ran through PostgreSQL + asyncpg. Every migration
`op.execute()` contains exactly one top-level SQL command. The broader suite
also passed the PR8 and PR9 migration gates with the new repository head.

The final correction case changes the accepted score from 10 to 5 and proves
that A's frozen 10-point result remains student-current through B's approval.
Explicit B publication exposes 5 points and 50 percent; explicit historical A
republication restores its exact frozen result via a new publication record.
UPDATE and DELETE checks require the immutable-trigger error, preventing a
foreign-key rejection alone from being mistaken for append-only enforcement.

The full unit run used the existing local byte-parameter-ID helper to avoid the
Windows environment-variable size limit. The final run reported
**1,102 passed, 1 failed**: the unchanged Unicode NFC fixture failure. An earlier
run reported **1,101 passed, 2 failed**, also reproducing the previously documented
intermittent classroom-notes timestamp failure. That test passed in the final run.
Both limitations are documented in the PR8 acceptance report:

- `test_checking_handoff_fixture.py::test_checking_handoff_v1_fixture_schema_and_invariants`
  expects Unicode NFC normalization, while the existing normalizer preserves
  the decomposed input.
- `test_classroom_notes.py::test_privacy_admin_rules_and_cas` intermittently
  gets the same timestamp for successive fake repository updates, defeating its
  stale-timestamp assertion.

These production modules and tests are outside PR9 and were not modified.
The regression adjustment in PR8 tests replaces the assertion that publication
tables do not exist with proof that approval creates no publication rows, and
uses the repository's current head for migrated-schema/fresh-head assertions.

## Exact changed files

- `backend/alembic/versions/20261010_02_paper_result_publication.py`
- `backend/app/application/paper_result_publication.py`
- `backend/app/infrastructure/paper_result_publication_models.py`
- `backend/app/infrastructure/paper_result_publication_repository.py`
- `backend/app/infrastructure/model_registry.py`
- `backend/app/presentation/paper_result_publication_routes.py`
- `backend/app/main.py`
- `backend/tests/unit/test_paper_result_publication.py`
- `backend/tests/integration/test_paper_result_publication_database.py`
- `backend/tests/integration/test_paper_result_publication_migration.py`
- `backend/tests/integration/test_paper_checking_review_database.py`
- `backend/tests/integration/test_paper_checking_review_migration.py`
- `docs/pr9-paper-publication-acceptance.md`
