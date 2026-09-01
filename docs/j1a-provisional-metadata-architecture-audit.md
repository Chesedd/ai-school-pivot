# J1A — Provisional metadata architecture audit and contract

This document records repository state at Alembic head `20260831_02`. It is an
architecture decision record only: J1A changes no runtime, schema, provider
budget, capability, or test.

## A. Current catalog schema

### Curriculum hierarchy

The enforced graph is **Subject + Grade → Topic → Subtopic → Skill**. Grade is
global, not a child of Subject. Skill has only `subtopic_id`; it is neither
directly subject- nor topic-scoped. Subject/grade compatibility is inherited
through Topic. This is schema evidence, not a UI inference.

| Entity | Table/model and UUID PK | Fields, state and metadata | Hierarchy, constraints and indexes | Repository, routes and capability |
|---|---|---|---|---|
| Subject | `subjects` / `Subject`; generated PostgreSQL UUID | `code varchar(64)`, `name text`, `created_at`; no status, update timestamp, or actor | unique `code`; no name uniqueness/index | `SQLAlchemyContentBankRepository.catalog`; `GET /api/content-bank/catalog/subjects`; `content.read`. No CRUD service/route exists. Seed/migration SQL is its only current mutation path. |
| Grade | `grades` / `Grade`; generated UUID | `number smallint`, `name text`, `created_at`; no state/actor | unique `number`; check 1..11; global rather than subject-owned | Same catalog repository and `GET .../grades`, `content.read`; no HTTP mutation. |
| Topic | `topics` / `Topic`; generated UUID | `subject_id`, `grade_id`, `code`, `name`, `created_at`; no state/actor | RESTRICT FKs to Subject and Grade; unique `(subject_id, grade_id, code)`; indexes on both FKs | Same catalog repository and `GET .../topics`, `content.read`; no HTTP mutation. |
| Subtopic | `subtopics` / `Subtopic`; generated UUID | `topic_id`, `code`, `name`, `created_at`; no state/actor | RESTRICT Topic FK; unique `(topic_id, code)`; topic index | Same catalog repository and `GET .../subtopics`, `content.read`; no HTTP mutation. |
| Skill | `skills` / `Skill`; generated UUID | `subtopic_id`, `code`, `name`, `created_at`; no state/actor | RESTRICT Subtopic FK; unique `(subtopic_id, code)`; subtopic index | Same catalog repository and `GET .../skills`, `content.read`; no HTTP mutation. |

The catalog list query returns `CatalogRecord(id, name, ...)`; topics include
subject/grade, subtopics include topic, and skills are expanded through joins to
include topic and subtopic. The loader independently verifies all parent links.
There is no curriculum application service for writes and `catalog.manage` does
not imply a hidden curriculum API.

### Managed tags (a separate lifecycle)

`tag_categories` (`TagCategory`) uses string `code` as PK, with display name,
unique sort order and code/sort checks. `tags` (`Tag`) uses generated UUID and
has category FK, optional Subject FK (global or subject-scoped), `name`, globally
unique `normalized_name`, `active|deprecated` status, optional self-FK
replacement, created/updated timestamps and actors. It has catalog/scope/status,
replacement and trigram indexes. `task_version_tags` is the immutable-version
association (composite PK), with `attached_at/by`; `tag_audit_log` captures actor,
action, before/after JSON and time.

`ManagedTagService` supports list/get/similarity/create/patch/deprecate/usage and
translates uniqueness races. Reads (`/tag-categories`, `/tags`, `/tags/similar`,
`/tags/{id}`) need `content.read`; `/admin/tags...` mutation/usage routes need
`catalog.manage`; version assignment needs `content.edit`. Tags therefore must
not lend their lifecycle wholesale to curriculum entities: they are version
metadata, globally name-unique, optionally subject-scoped, and already have a
replacement/audit design.

### Folder (organization, not semantic catalog)

`task_folders` / `TaskFolder` has generated UUID, mandatory Subject, optional
same-subject parent, name, created/updated actors and timestamps. Composite FK
and uniqueness enforce same-subject ancestry; case-insensitive unique indexes
apply within a root subject or parent. `FolderService` and repository implement
tree/read/create/rename/move/delete/task move; reads need `content.read`, writes
need `content.edit`. Folder is optional Task organization, resolver output is
always `None`, and proposal support is out of scope.

## B. Current Image Solving metadata flow

The exact chain is:

1. `AnthropicImageExtractor.extract` makes provider call 1 using the extraction
   tool schema. `_tool_input` parses it into `ExtractionResultV1` containing
   `ExtractionMetadataV1`.
2. `ExtractionMetadataV1` requires title, subject text, grade integer, topic
   text, 1–5 skill texts, task type/answer format/difficulty; subtopic and up to
   eight tags are optional. It rejects surrounding whitespace and normalized
   duplicates. It has no catalog IDs, per-field confidence, or source field.
   Extraction-level confidence is retained outside metadata.
3. The extraction checkpoint persists the complete `ExtractionResultV1` as
   JSONB in `image_solving_checkpoints.payload`, with fingerprint and provider
   telemetry. Result serialization exposes all semantic strings through
   `ExtractionMetadataResponse`; frontend `ExtractionMetadata` retains them.
4. Provider call 2 produces `SolverResultV1`. The only conditional extra call
   remains the bounded structural repair after `end_turn`, no `record_solution`,
   and nonempty prose. Local deterministic validation is not a provider call.
5. `MetadataRecommendationService.generate` loads the validated owned session,
   returns a cached recommendation if present, otherwise loads the local
   catalog and calls `resolve_metadata`. It adds no provider call.
6. `resolve_metadata` emits `ImageTaskMetadataRecommendationV1`. Existing values
   carry UUID/confidence/reason; unmatched curriculum values carry
   `kind="new"`, `proposed_name`, optional resolved parent UUID, zero confidence
   and reason. New tags similarly retain name/category placeholder/scope.
7. The recommendation is stored as JSONB in
   `image_solving_metadata_recommendations.payload` with catalog fingerprint and
   returned verbatim by `POST/GET .../recommendations`.
8. Frontend `MetadataRecommendation` can decode both variants. `ReviewForm`
   copies only existing IDs into UUID-backed form state. Promotion sends only
   UUIDs via `ImagePromotionRequest`; `PromoteImageSolvingRequest` cannot carry
   free text in FK fields.

Field retention summary: title/type/format/difficulty remain values throughout;
subject/grade/topic/subtopic/skills/tags remain human-readable in extraction and
checkpoint/result DTOs. Resolution adds IDs only on a match and retains labels
on every `new` recommendation. Resolver confidence and reason are persisted;
they are deterministic annotations, not provider confidence/source. No explicit
AI/manual source is stored on recommendation fields.

## C. Exact unresolved-metadata loss point

There is **no backend loss of recognized labels in the current head**. For the
hypothesis `Математика / 8 / Квадратные уравнения / Теорема Виета /
Применять теорему Виета` with empty catalogs:

* `ExtractionMetadataV1` holds those exact strings in
  `ImageSolvingSession.extraction_checkpoint.metadata`; checkpoint JSONB and
  `ImageSolvingResultResponse.extraction.metadata` preserve them.
* `MetadataRecommendationService.generate` invokes `resolve_metadata` after
  `SqlAlchemyMetadataCatalogLoader.load()` returns empty tuples.
* `_unique_match`/grade lookup return no rows. `selection` emits `new` objects:
  subject/topic/subtopic/skill each retain `proposed_name`; grade retains `"8"`.
  Parent IDs are null because no ancestor UUID exists.
* `ImageSolvingMetadataRecommendationRow.payload` and the recommendation HTTP
  response preserve those objects. Frontend `MetadataRecommendation` receives
  them and `recommendation` React state retains them.
* The effective UI loss is `ImageSolvingPages.tsx`, `ReviewForm.applyRecommendation`:
  every `new` hierarchy item becomes `""` and every new skill/tag is filtered
  out of selected ID arrays. The component renders only UUID-backed `<select>`/
  `<select multiple>` controls and a generic unresolved alert; it never renders
  `proposed_name`/new tag `name`. Thus recognizable labels are present in memory
  but invisible and unusable in the editor. `valid` then fails because required
  IDs/skills are empty. This is “resolved entity or empty form selection” at the
  editable/promotion boundary, not “resolver returned None.”

## D. Current resolver behavior

There is one resolver, `resolve_metadata(session, catalog)`, plus helpers
`_normalized` and `_unique_match`.

* Normalization is Unicode NFKC, casefold, `ё→е`, non-word runs to one space,
  then trim. It performs no fuzzy match. Codes are never compared.
* A match must be unique; zero or multiple normalized-name matches produce
  `new`, avoiding arbitrary selection.
* Resolution is ordered. Subject is global name match. Grade uniquely matches
  integer `grade_number`. Topic candidates require both resolved Subject and
  Grade. Subtopic candidates require resolved Topic.
* Skills require a resolved Subtopic when semantic subtopic exists. Only when
  semantic subtopic is absent are rows with the resolved Topic considered.
  Because persisted Skill always has Subtopic, loader-derived `topic_id` provides
  this fallback. An unresolved named subtopic deliberately prevents a skill from
  matching under some other subtopic.
* Tags consider active rows only (loader filter), and only global or resolved-
  Subject scoped tags. Ambiguity/no match yields `kind=new`; its category is the
  non-persistable placeholder `unresolved`.
* Folder is loaded/validated but never resolved. Results are persisted once per
  session and cached; they are not recomputed after catalog changes because the
  service returns cache before loading the catalog. The fingerprint documents
  the snapshot but does not invalidate it.

## E. Current Content Bank metadata requirements

| Metadata | Storage and nullability | Validation |
|---|---|---|
| Subject | `tasks.subject_id`, non-null RESTRICT FK | create repository loads ID; topic must have same subject |
| Grade | `tasks.grade_id`, non-null RESTRICT FK | loads ID; topic must have same grade |
| Topic | `tasks.topic_id`, non-null RESTRICT FK | loads ID and validates subject+grade |
| Subtopic | `tasks.subtopic_id`, nullable RESTRICT FK | if supplied, loads it and requires selected topic |
| Skills | `task_skill_links` per TaskVersion, non-null skill FK; at least one application-level, one primary, unique, positive weights summing to one | each skill is loaded and its Subtopic→Topic must equal selected topic; if task subtopic exists, skill must belong to it |
| Tags | `task_version_tags` per TaskVersion; zero allowed; composite unique, RESTRICT tag FK | active status, global/selected-subject scope, count/duplicate checks |
| Folder | `tasks.folder_id`, nullable composite FK with Subject | same subject, depth/access policies |

Curriculum IDs live on mutable Task aggregate identity, not TaskVersion; skill
and tag links are version-specific. TaskVersion does not snapshot curriculum
names. Assessments reference an **approved TaskVersion UUID**, not curriculum
IDs, and checking snapshots content/methodology rather than catalog rows.
Promotion locks the owned validated Image Solving session, enforces checkpoint
fingerprints and human confirmation, invokes canonical `CreateTaskOperation`,
saves methodology, enriches the ordinary `task_created` audit event with source
provenance, commits once, and finds an existing task by session provenance for
idempotency. The created version is always `draft`.

Current status transitions are `draft→review` via `StatusCycleService.submit_review`
(`content.review.submit`) and `review→approved` via `approve`
(`content.approve`). Application service checks state and ownership; repository
performs a locked transition, approval timestamp/actor, one-approved-version
constraint, and atomic audit. Catalog compatibility is validated on create, not
revalidated on approval.

The future provisional gate belongs in the **application status-cycle service**
immediately before its transactional repository transition: it is a
cross-resource query/invariant across Task metadata and version links. Route
keeps coarse capability; domain transition semantics remain explicit; repository
persists/locks. The check and transition must share the same UoW/transaction,
with relevant catalog rows locked or protected by CAS so status cannot change
between check and approval.

## F. Current Admin catalog surfaces

Admin has no Subject/Grade/Topic/Subtopic/Skill management UI or mutation API.
Those catalogs have read-only selectors and demo/dev seed support. Admin Tags is
one dedicated UI and API supporting create, rename/category/scope/replacement
patch, deprecate, similarity, usage counts and subject filters. It has optimistic
`expected_updated_at`, audit snapshots and replacement validation, but no true
merge that rewrites links.

Reusable ideas: normalized exact lookup, bounded similarity as a human hint,
status/replacement presentation, usage endpoint, actor audit, CAS, conflict
translation and accessible admin patterns. Not reusable as-is: global tag-name
uniqueness, category/scope model, tag association/link rewriting, lifecycle
rules, and single-screen hierarchy. Curriculum review needs hierarchy-specific
forms and integrity checks; it must not be mounted under Admin Tags.

## G. Design A vs Design B

| Concern | A: status on curriculum rows | B: separate proposals |
|---|---|---|
| Draft FKs/promotion | Existing FK shape works unchanged once accepted | Requires dual real/proposal references or parallel draft metadata |
| Provisional parent | Natural FK; provisional Topic/Subtopic can parent children | Polymorphic parent references and conversion graph required |
| Migration | Add status/proposer/update/audit and normalized uniqueness to five tables | New proposal tables plus draft link representation and conversion machinery |
| Existing repositories | Must default ordinary lists to active and add policy-aware proposal reads | Existing lists stable, but every promotion/editor path becomes dual-mode |
| Uniqueness/races | Per-kind hierarchy-aware unique index can cover active+provisional | Proposal uniqueness does not race-proof Admin creating a real row without cross-table locking |
| Confirmation | Status flip preserves UUID and draft links | Must create real hierarchy then atomically remap all draft proposal references |
| Merge/reject | Keep source row, reassign eligible live refs, deprecate | Similar remap plus deletion/retention of proposal references |
| Exposure risk | High unless every catalog query explicitly defaults to active | Lower ordinary-selector risk |
| Audit/history | Normal UUID remains stable; explicit audit/replacement needed | Proposal history is explicit but disconnected from canonical IDs |
| Overall repository fit | Narrow extension to canonical FK model | Creates the parallel metadata system the current schema avoids |

## H. Recommended persistence design

**Choose A.** The decisive evidence is that required Task metadata consists of
non-null canonical FKs, provisional parents must be referentially sound, and
promotion deliberately delegates to ordinary Content Bank creation. B would
force either nullable dual identifiers on Task/link records or a parallel free-
text/proposal metadata store, then transactional graph conversion. A lets an
accepted human proposal receive a real UUID and participate in existing FK and
compatibility checks without changing Content Bank representation.

A is safe only if J1B makes active-only the default for every ordinary catalog
read/loader, adds actor/audit/status uniformly, and adds normalized,
hierarchy-aware uniqueness. Do not add `provisional` to managed tags in this
phase: unresolved extracted tags can continue to require selection/admin tag
creation because tag lifecycle and global uniqueness differ.

## I. Recommended lifecycle

Use exactly `provisional`, `active`, `deprecated` and an optional
`replacement_id` on each curriculum kind. Creation of seeded/admin canonical
rows is active; Teacher acceptance creates provisional; Admin confirmation is
`provisional→active`; merge/rejection is `provisional→deprecated` with replacement
for merge. No “pending/rejected/merged” duplicate workflow states are needed;
audit action expresses why deprecated.

| State | Authority/use and visibility | Content lifecycle |
|---|---|---|
| provisional | `catalog.propose` creates; proposer may select/use own, Admin sees all; only Admin `catalog.manage` confirms/merges/rejects | draft and review allowed; approval forbidden |
| active | Admin/seed creates or confirms; all `content.read` selectors see/use | draft, review and approval allowed |
| deprecated | Admin only transitions; hidden from new selections; retained/replacement displayed for repair/history | existing draft/review FK remains valid but approval forbidden until replaced; no new use |

An author may clear/replace any recommendation before acceptance. Accepting is
explicit per value and ordered by hierarchy; AI never writes catalog rows.

## J. Recommended visibility policy

Choose **Option 2: proposing Teacher + Admin** until confirmation. Option 1
pollutes ordinary selectors without status context; Option 3 makes advisory
values look quasi-canonical. Option 2 follows current owner-private draft model
while global active catalog remains curated. Duplicate prevention must be
server-global even when the reused provisional is not disclosed: on exact
collision, attach the second proposer as an authorized proposer/observer (a
small `catalog_proposal_actors` association) or return an opaque “proposal
already pending” result and allow use in that teacher's Image Solving flow.
Admins see all. Active values become visible to all immediately.

## K. Recommended capability/API boundary

Add the distinct coarse capability `catalog.propose`: Admin and Teacher receive
it; Student and no-role receive neither; only Admin retains `catalog.manage`.
`content.create` is unsuitable because proposal abuse/global namespace policy is
independent of task creation and future rate controls need a distinct boundary.

Use one `POST /api/catalog/proposals` command with a strict discriminated body
and response containing the canonical catalog entity. It is one application
operation, but validate an exact allowlist per kind (reject extra fields):

```json
{"kind":"subject","name":"Математика"}
{"kind":"grade","number":8,"name":"8"}
{"kind":"topic","name":"Квадратные уравнения","subject_id":"...","grade_id":"..."}
{"kind":"subtopic","name":"Теорема Виета","topic_id":"..."}
{"kind":"skill","name":"Применять теорему Виета","subtopic_id":"..."}
```

Grade proposal is justified only for an empty catalog because Grade is required,
globally numbered 1..11, and extraction already yields a bounded integer. Server
generates deterministic administrative `code` candidates; client neither
supplies codes nor UUIDs. Parent IDs must be active or actor-visible provisional
and compatible. Response reports `created` or idempotent `reused`; it never
confirms. Separate Admin confirm/merge/deprecate commands may be kind-specific
internally even if routed under `/api/catalog/admin/{kind}/{id}`.

Creation algorithm: canonicalize NFKC/whitespace/casefold/`ё→е`; validate shape;
within hierarchy lookup exact active, then exact provisional; return existing
active or reuse/associate provisional; otherwise insert provisional. Add stored
normalized name and a database unique key across lifecycle-relevant rows:
Subject normalized name; Grade number; Topic `(subject_id,grade_id,normalized)`;
Subtopic `(topic_id,normalized)`; Skill `(subtopic_id,normalized)`. Prefer a
partial unique index for `status IN ('active','provisional')`; deprecated rows
may repeat. On `IntegrityError`, rollback to savepoint, re-read winner, validate
visibility/hierarchy, return reused/conflict. This handles two Teachers and
Admin-create races without serializable isolation.

## L. Recommended resolution DTO contract

Evolve the existing discriminated contract rather than add parallel fields:

```text
CatalogCandidateV2 =
  { kind: "matched", recognized_label, entity: {id,name,status:"active"}, confidence, reason }
| { kind: "unresolved", recognized_label, parent_candidate_key?, confidence, reason }

Accepted selection (editor state only) =
  { entity_id, entity_name, entity_status:"active"|"provisional" }
```

Grade additionally carries `recognized_number`. Skills use an ordered candidate
array. `recognized_label` always survives, including on a match, so UI can
explain recognition versus canonical label. `unresolved` has no invented ID.
After acceptance, refetch/re-resolve to an entity; provisional is the entity's
database status, not a duplicated recommendation state. Preserve confidence and
reason; add `source: "ai"|"manual"` only to transient candidate/editor events
and proposal audit if product analytics needs attribution. Authorization and
catalog authority are identical for AI and manual candidates.

Frontend state should separate `candidate` from `selection`:

```ts
type Candidate = {label:string; source:"ai"|"manual"; resolution: Matched|null}
type Selection = {id:string; name:string; status:"active"|"provisional"}|null
type MetadataField = {candidate:Candidate|null; selection:Selection}
```

Use UUID-backed comboboxes for selection plus an explicit “Propose” action for
unresolved text; skills are an array/multi-combobox. Subject/grade changes clear
topic/subtopic/skills, topic clears subtopic/skills, subtopic clears skills.
Unresolved display: neutral `НОВОЕ` badge, “Нет в каталоге”, aria-label
“Новое значение, отсутствует в каталоге”. Accepted provisional: `ПРЕДЛОЖЕНО`,
aria-label “Предложенное значение ожидает подтверждения администратора”. Never
use color alone or error red merely for newness.

## M. Recommended empty-catalog flow

1. Extraction uses call 1 and returns all example strings; solver uses call 2.
   Resolver returns five unresolved hierarchy candidates plus two unresolved
   skills, each with its recognized label and dependency key (not UUID).
2. API returns V2 candidates. UI shows each neutral `НОВОЕ / Нет в каталоге`;
   none is silently selected and promotion remains disabled.
3. Teacher explicitly accepts in dependency order: Subject → Grade → Topic →
   Subtopic → each Skill. Each local proposal command performs exact active and
   provisional lookup before insert. Server UUIDs become available at each step,
   so Topic references provisional Subject+Grade, Subtopic references provisional
   Topic, and Skills reference provisional Subtopic.
4. UI replaces candidates with entity selections marked `ПРЕДЛОЖЕНО`. Tags must
   be omitted (allowed) or selected from managed tags; no automatic tag proposal.
5. Promotion sends those UUIDs, calls the unchanged canonical create operation,
   stores Subject/Grade/Topic/Subtopic on Task and Skill links on initial
   TaskVersion, enriches provenance audit, and returns an ordinary draft.
6. Review submission is allowed. Approval queries the complete hierarchy/skill/
   tag state and returns bounded conflict while any selection is provisional or
   deprecated.
7. Admin confirms nodes in parent-first order or merges them. Confirmation keeps
   UUIDs; merge repairs eligible live draft/review references. Once the task's
   complete metadata graph is active, normal approval succeeds.

No metadata, deduplication, or proposal LLM call is introduced.

## N. Recommended partial-catalog flow

Existing Subject and Grade resolve to active UUIDs. Missing Topic is unresolved
under those UUIDs; missing Subtopic carries a dependency on the Topic candidate;
Skill candidates depend on Subtopic. Teacher accepts Topic first, receiving its
provisional UUID, then Subtopic with that parent, then Skills. Each child uses a
normal FK to its provisional parent. Promotion is otherwise identical. If an
Admin creates/activates an exact Topic meanwhile, proposal creation returns the
active winner and the UI substitutes its UUID before creating children.

## O. Recommended approval/review behavior

* **Draft:** may reference active or explicitly accepted actor-visible
  provisional entities; never raw unresolved text. Promotion always creates it.
* **Review:** allow `draft→review` with provisional references (Option A). This
  gives Admin a concrete task and proposal context and matches Teacher's current
  submit authority. UI prominently lists pending catalog decisions.
* **Approved:** block when any direct Task hierarchy row, transitive parent, or
  version Skill/Tag is non-active. Application error should follow the existing
  bounded code style: `provisional_metadata_unresolved`, HTTP 409, details only
  `{kind,id,display_name}`. Deprecated references use the same repair-required
  family (or `catalog_metadata_inactive`) but never leak proposer/ownership/DB
  internals. Recheck inside approval transaction; presentation alone is unsafe.

## P. Recommended merge/rejection semantics

For provisional `Квадратные уравнения` merged into active `Квадратное уравнение`:

1. Admin command locks source and target in stable UUID order, verifies source
   provisional, target active/same kind and compatible hierarchy, and uses CAS
   (`expected_updated_at` or status/version).
2. Reassign **live mutable** Task aggregate FKs only for tasks whose latest
   version is draft/review and whose hierarchy remains valid. Topic merge may
   require choosing/reusing compatible active Subtopic/Skill descendants; do not
   blindly change Topic while leaving incompatible child/skill IDs.
3. For Skill, insert target `task_skill_links` then resolve duplicate/primary/
   weight semantics before deleting source links. For curriculum Subject/Grade/
   Topic/Subtopic update `tasks`; tags are outside this merge.
4. Do not rewrite approved TaskVersions, Assessment item references, published
   Assessment snapshots, checking snapshots, audit log, Image Solving checkpoint,
   recommendation payload, or provenance. Those are historical evidence. Since
   Task hierarchy is not versioned today, any Task with approved history is a
   blocker for automatic curriculum FK rewriting; require a new Content Bank
   version/explicit repair rather than falsify the approved version's displayed
   catalog context.
5. Mark source deprecated with replacement ID and append immutable catalog audit
   recording source/target, actor and affected live IDs/counts. Keep its row and
   FKs valid. Historical recommendation can be interpreted through replacement
   without mutation.

Potential references to inspect per kind are `topics`, `subtopics`, `skills`,
`tasks`, `task_skill_links`, `typical_errors.skill_id`, `task_folders.subject_id`
and `tags.subject_id`; TaskVersions/methodologies reference skills through links,
and Assessment items indirectly reference TaskVersion. Parent merge is therefore
not a generic “update every FK.” Initial narrow J1 should prohibit merge when
descendants, folders/scoped tags, typical errors, or approved-history Tasks make
safe deterministic reassignment impossible, returning usage details for manual
resolution.

Rejection means deprecate-without-replacement, never delete. If referenced,
retain FK validity, disallow new selection and approval, return affected draft/
review usage to Admin, and require authors to replace/remove where optional.
Hard-delete and silent broken/stale references are forbidden. Optionally prohibit
final rejection until Admin acknowledges affected references, but do not prohibit
the durable deprecated state: it is the mechanism that safely flags repair.

## Q. Security and concurrency risks

### Blockers before release

1. **Approval bypass/stale state:** application gate and transition in one UoW;
   lock referenced catalog rows during approval. Promotion validates all IDs and
   status/visibility in its transaction. A stale deprecated/replaced ID yields a
   bounded conflict with replacement hint.
2. **Hierarchy substitution:** server derives ancestry from DB; never trust
   client kind/parent labels. Proposal and promotion validate every transitive
   parent, subject/grade and Skill relationship.
3. **Authority:** `catalog.propose` only Teacher/Admin; object policy restricts
   provisional read/use/edit to proposer associations and Admin; only
   `catalog.manage` confirms/merges/deprecates. Student/no-role denied. Teacher
   cannot mutate even own proposal after creation; propose corrected value or
   ask Admin, avoiding shared-row tampering.
4. **Duplicate/race integrity:** normalized partial unique indexes plus savepoint
   conflict translation. Teacher-vs-Admin creation converges on one winner.
5. **Admin races:** stable row locks and CAS make confirm-vs-merge and two-Admin
   merge yield one success/one stale conflict. Merge target is locked, active,
   same kind and hierarchy-compatible.
6. **Merge/promotion and reject/reference races:** lock source catalog rows and
   affected Task rows in deterministic order. Promotion locks/validates selected
   rows before task insert; merge/reject takes the same locks before usage query.
   Ordinary READ COMMITTED transactions suffice with constraints and locks.

### Important non-blockers / operational controls

Rate-limit proposals per actor/tenant/day, cap outstanding proposals and name
length, log denials/volume, and give Admin usage dashboards. Proposal POST may
accept an idempotency key, though canonical uniqueness is the correctness guard.
Do not disclose a foreign proposer's identity on duplicate reuse. Cached Image
Solving recommendations may be stale; acceptance always performs fresh local
lookup. No broad table/advisory locks or SERIALIZABLE isolation are justified.

## R. Proposed J1 implementation slices

* **J1B — persistence foundation:** forward migration and ORM for curriculum
  state, normalized keys, proposer/audit/replacement, uniqueness and repository
  active defaults; real PostgreSQL persistence/race tests only.
* **J1C — proposal authority/API:** `catalog.propose`, role grants, object policy,
  discriminated command, hierarchy validation, idempotent reuse and Admin read
  queue. No confirm/merge yet unless separately feature-gated.
* **J1D — resolution contract:** V2 DTO/persistence/API preserving labels and
  candidate dependencies; cache-version strategy; resolver unit/PostgreSQL tests.
  Provider prompts/call budget unchanged.
* **J1E — Image Solving editor/promotion:** accessible unresolved/provisional UI,
  explicit ordered acceptance, active/provisional selector policy, invalidation,
  and canonical UUID-only draft promotion.
* **J1F — review/approval/Admin decisions:** allow submit, transactional approval
  gate, confirm and safe rejection, admin proposal queue/usage/audit.
* **J1G — bounded merge/hardening:** hierarchy-safe live-reference reassignment,
  replacement semantics, concurrency/rate controls, operational metrics and full
  end-to-end regression. Do not broaden merge beyond proven safe cases.

## S. Migration implications

Alembic has one head, `20260831_02`; do not rewrite it. J1B is one forward child.
It should create a shared PostgreSQL enum (or consistent checks) for curriculum
status; add non-null `status` defaulting existing rows to active, normalized name,
`proposed_by` nullable, created/updated actor/time and replacement FK/version;
backfill normalized values; then create hierarchy-aware partial unique indexes.
Add immutable catalog audit and (for Option 2/reuse) proposal-actor association.
Validate replacement kind/hierarchy in application and add feasible self/not-
self checks. Use staged backfill before NOT NULL/index validation to keep existing
catalogs compatible. Downgrade may remove new structures but historical migration
files remain untouched.

Before choosing names, verify existing data has no normalized collisions that
current code-based uniqueness permits. A collision report/explicit remediation
is a migration gate, not automatic fuzzy merging.

## T. Acceptance matrix

All DB integration gates below run against real PostgreSQL.

| Area | Required gates |
|---|---|
| Persistence | provisional/active create; actor/time/audit attribution; Grade range; each parent FK; normalized duplicates in same hierarchy collapse/conflict; same label in compatible different hierarchies allowed; deprecated-key reuse policy; two-transaction race |
| Authorization | Teacher proposes and sees/uses own; cannot confirm/merge/manage foreign; Admin proposes/manages/sees all; Student/no-role denied; foreign Teacher gets non-disclosing response |
| Resolution | exact existing; NFKC/case/`ё`; no-match label preserved; ambiguous unresolved; wrong subject/grade/subtopic unresolved; partial and empty catalogs; stale cache version; no fuzzy matching |
| Image Solving | extraction and recommendation preserve every recognized label/confidence/reason; normal exactly two provider calls; structural repair anomaly maximum three; no calls from resolver/proposal |
| Promotion | active IDs→ordinary draft; actor-visible provisional IDs→draft; raw/unaccepted candidate cannot promote; wrong hierarchy/foreign/deprecated IDs conflict; ownership/provenance/audit/idempotency unchanged |
| Lifecycle | provisional draft→review allowed; approval with any direct/transitive provisional/deprecated value returns safe 409; all-active approves normally; race with status transition cannot bypass |
| Merge/reject | compatible source→target; no duplicate skill links/primary violation; live draft repair; approved/assessment/checking/history and checkpoints unchanged; replacement/audit retained; referenced rejection preserves FK and blocks approval |
| Frontend | existing selection; AI unresolved label; manual unresolved label; explicit proposal acceptance; replacement with existing; parent invalidates children; foreign provisional hidden; `НОВОЕ`/`ПРЕДЛОЖЕНО` text and aria labels; keyboard/focus/error behavior |
| Migration | sole old head before, sole new head after; upgrade seeded and empty DB; existing rows active; downgrade smoke if supported; collision preflight; ORM/schema parity |

Minimum command gates per slice should include backend unit tests, frontend Vitest/
typecheck/build, migration tests, and the repository's PostgreSQL integration
suite. Provider spies must assert exact call counts, not merely upper bounds.

## U. Open architecture questions

Repository evidence cannot determine only these product/operations choices:

1. Is Grade truly Teacher-proposable, or will operations guarantee all 1–11 rows
   are administrator-seeded? Empty-catalog support implies proposal unless that
   operational prerequisite is formalized.
2. Should a second proposer gain direct visibility/use of an identical pending
   row, or receive an opaque pending response until Admin acts? Option 2 needs one
   policy choice and likely a proposer association.
3. What per-actor outstanding/rate limits fit deployment scale, and is there a
   tenant boundary not represented in the current schema?
4. Must approved historical Task cards display the catalog label as of approval?
   Current Task-level hierarchy cannot do so. This audit conservatively blocks
   merge for approved history; true historical catalog snapshots are a separate
   product decision, not J1.
5. Which safe merge cases are required in the first release versus confirmation
   and deprecate-with-replacement only? Descendant/approved-history merges should
   remain out until explicit scope is accepted.
