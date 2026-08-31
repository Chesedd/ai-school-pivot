# Phase B1 — Account / Authentication / RBAC Design Audit

Repository state audited on 2026-08-31. This document is design-only: it deliberately adds no runtime code or migration.

## A. Current Identity/Auth Audit

### Runtime and composition

- `backend/app/config.py` requires two UUID settings, `content_bank_dev_actor_id` and `assessment_dev_student_id`. `.env.example` and `docker-compose.yml` provide fixed pilot values. There are no password, session, cookie, or authentication settings.
- `backend/app/main.py` creates one FastAPI application, registers the Content Bank, teacher Assessment, student Assessment, attachment, authoring, Image Solving, and artifact routers, and applies CORS. CORS is currently an origin allow-list with `allow_credentials=False`; it permits an `Authorization` header even though no authentication consumes it.
- No `current_user`, authenticated `Principal`, auth middleware/dependency, JWT validation, login/logout route, cookie handling, user/role/session model, or permission implementation exists. `ActorContext` in `backend/app/application/content_bank.py` is only a frozen `actor_id` carrier, not an authenticated identity.

### Exact temporary identity seams

- `backend/app/presentation/routes.py` reads `content_bank_dev_actor_id` for tag administration and version tags; task/methodology creation and lifecycle; folder create/rename/move/delete and task relocation. Its admin tag routes explicitly describe themselves as trusted-pilot routes requiring production RBAC.
- `backend/app/presentation/assessment_routes.py` creates an `ActorContext` from `content_bank_dev_actor_id` for every teacher endpoint: class groups, assessments and variants/items, publication/assignment, and closing assignments.
- `backend/app/presentation/authoring_routes.py` uses `content_bank_dev_actor_id` as owner for every authoring workspace, quality/review, rejection, acceptance, and promotion operation.
- `backend/app/presentation/image_artifact_routes.py` assigns the setting as artifact `owner_id` and uses it for metadata lookup. It commendably rejects unknown multipart fields, so a submitted `owner_id` is not accepted.
- `backend/app/presentation/image_solving_routes.py` passes the same setting through session create/run/state/result/attempts/recommendation/promotion calls.
- `backend/app/presentation/student_assessment_routes.py` wraps `assessment_dev_student_id` in `PilotStudentContext` and uses it for all seven student operations: list/detail assignments, start/get attempt, save/delete answer, and submit.
- `backend/app/presentation/attachment_routes.py` independently reads `assessment_dev_student_id` for student answer attachment upload/list. This is an important secondary seam outside the student router. Task attachment upload has no actor at all.
- Environment-dependent unit modules set these values as import prerequisites (including `test_contents_routes.py`, `test_import_workflow_removed.py`, `test_image_artifact_routes.py`, and `test_image_solving_runtime.py`), and integration fixtures seed records with the same conceptual actor/student identities. Those fixtures must eventually create accounts/links or override the principal dependency rather than preserve production dev IDs.

### Persistence and identity-shaped fields

- The project consistently uses PostgreSQL native UUIDs (`Uuid(as_uuid=True)`) with `gen_random_uuid()` defaults and timezone-aware `clock_timestamp()` timestamps (`backend/app/infrastructure/models.py`, `assessment_models.py`, `authoring_models.py`, and `image_solving_models.py`).
- Content Bank `tasks.created_by`, `task_versions.created_by`, `task_versions.approved_by`, folder `created_by`/`updated_by`, managed-tag actor fields, and append-only `audit_log.actor_id` are UUID values but currently have no account FK. `ActorContext.actor_id` feeds creation, lifecycle, folder/tag operations, and audit writing.
- Assessment `class_groups.created_by`, `assessments.created_by`/`published_by`, `assignments.created_by`/`closed_by`, and teacher audit actors are UUID actor identities. `students.id` is instead a domain identity. `assignment_participants.student_id` is a restrictive FK to `students`; student audit events currently store this domain Student ID with `actor_type='student'`.
- Input artifacts, authoring sessions/reviews, and Image Solving sessions have `owner_id`. The authoring schema also has composite owner integrity between authoring sessions and input artifacts. These are account ownership fields in the future, not Assessment Student IDs.
- Current sole Alembic head is `20260831_01` (`backend/alembic/versions/20260831_01_local_image_metadata.py`). C1 should add a new revision that **revises `20260831_01`**; it must not alter historical revisions.

### Frontend and tests

- `frontend/src/api.ts` has a shared `request()` wrapper around `fetch`, but does not set `credentials: "include"`, has no auth types/endpoints, and retains idempotency keys in `sessionStorage` only for student start/submit. `frontend/src/api/imageSolving.ts` correctly reuses this wrapper.
- `frontend/src/main.tsx` is a client-side `history.pushState`/`popstate` route switch and a static navigation shell, not React Router. Current routes expose: Content Bank task browsing/creation, tag administration, teacher assessments, student work, and Image Solving. There is no login route, auth bootstrap, protected route gate, user state, logout, or capability-aware navigation.
- Backend tests are split into pure/unit tests and PostgreSQL integration tests. `backend/tests/conftest.py` supplies a minimal coroutine runner; integration modules use `pytest-asyncio`, `TEST_DATABASE_URL`, real SQLAlchemy sessions, and migration resets. `backend/tests/integration/test_migrations.py` asserts a single repository head and clean upgrades. Frontend uses Vitest/Testing Library (scripts in `frontend/package.json`).

## B. Existing Ownership Guarantees

1. **Image artifacts and Image Solving.** `ArtifactOwnershipService` requires both artifact ID and owner ID and deliberately maps mismatch/missing to not-found. `ImageSolvingService` checks the input artifact belongs to the requested owner before session creation, and the SQL repository/service operations select or validate sessions by `id + owner_id`. State, run/resume, result, attempts, recommendations, and promotion therefore receive the server-selected owner. The composite `(input_artifact_id, owner_id)` authoring FK and owner indexes reinforce isolation. Preserve these checks and merely replace the server setting with `principal.user_id`.
2. **Authoring workspace.** `SQLAlchemyAuthoringRepository.get()` scopes sessions and reviews by owner. Review, quality, acceptance, and promotion services receive the owner, retaining private workspaces and promotion idempotency. Authentication must not collapse owner scoping into a coarse `image_solving.use`/authoring capability.
3. **Student Assessment.** `SQLAlchemyStudentAssessmentService` scopes lists by `AssignmentParticipant.student_id`; assignment detail/start require an owned participant; attempt read/write/delete/submit joins submission through participant to the same Student ID. Locking versions repeat the ownership predicate. Missing or foreign objects become `assessment_not_found`/`attempt_not_found` 404s. Unique draft, participant-attempt, and participant-idempotency constraints plus row locks preserve concurrency and retries.
4. **Lifecycle and immutability.** Content Bank transitions enforce draft/review/approved rules, one approved version, completeness, cloning instead of mutating approved content, CAS/locking, and append-only audit. Assessment `_require_draft`, publication readiness, Content Bank version locks, participant snapshots, assignment close rules, and audit are likewise domain-state guarantees. Authorization is an additional precondition, never a substitute or admin bypass.

## C. Authorization Gaps

### Global gaps

- Every registered business router is callable anonymously. The fixed server identities prevent direct actor-ID parameters, but make all callers one shared owner/actor and provide no accountability.
- No capability gates exist. Admin tag mutation/usage, folder/catalog-like management, approval/archive, authoring, teacher Assessment, student workflow, raw attachments, and attachment content are open.
- `GET /api/attachments/{attachment_id}/content` checks only attachment existence. Anyone knowing an ID can retrieve task or student-answer content; this obsolete-style shared route must resolve the attachment's parent and enforce the corresponding visibility/ownership.

### Content Bank gaps

- List/card/audit/attachment reads expose draft/review content without creator filtering. Search/folder contents similarly accept lifecycle filters but have no visibility policy.
- All mutations lack owner checks: methodology, version tags, task attachments, submit review, return to draft, approve, archive, and folder/task moves. Approval uses the same actor as creation. `created_by` is recorded but not used for authorization.
- Tag `/admin/*` endpoints, folder mutations, and likely global catalog semantics are naming conventions only; no admin restriction exists.

### Assessment teacher gaps (exact repository seams)

- `AssessmentService.list`, `get`, `list_assignments`, and every mutation accept `ActorContext` but never compare it with `Assessment.created_by` or `Assignment.created_by`.
- `SQLAlchemyAssessmentRepository.list()` filters only status; `get()`/`lock()` filter only ID; `list_assignments()` filters only assessment ID; `get_assignment()`/`lock_assignment()` filter only assignment ID. `list_class_groups()` is global. These methods need explicit owner-aware variants/predicates rather than relying on a route-only pre-read.
- Consequently any teacher can enumerate, read, edit, publish, or mutate any assessment and get/close any assignment once real teacher identities differ. Teacher results/checking read surfaces must be traced through assignment ownership when exposed; no general teacher-results authorization currently exists.

### Student gap

- The repository-level isolation is strong, but all students currently resolve to one configured Student. Answer attachment routes duplicate that resolution. The link boundary must cover every student and attachment endpoint, and no future request body/query/path `student_id` may override it.

## D. Proposed Account Model

Add a small authentication bounded context without merging `User` and Assessment `Student`:

| Table | Proposed columns and constraints |
|---|---|
| `users` | `id UUID PK DEFAULT gen_random_uuid()`; `login VARCHAR(254) NOT NULL`; `normalized_login VARCHAR(254) NOT NULL UNIQUE`; `display_name VARCHAR(200) NOT NULL`; `password_hash TEXT NOT NULL`; `is_active BOOLEAN NOT NULL DEFAULT true`; `created_at/updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()`. Normalize login in the application (trim + Unicode-aware case normalization) and persist the canonical comparison value. Index the unique normalized login; optionally index active users for admin listing. |
| `user_roles` | `user_id UUID NOT NULL FK users(id) ON DELETE RESTRICT ON UPDATE RESTRICT`; `role VARCHAR(32) NOT NULL`; composite PK `(user_id, role)`; check role in the initial bounded vocabulary (`admin`,`teacher`,`student`). A row table, rather than one `users.role`, permits multiple roles and future additions. Keep role-to-capability policy in code/config initially; adding a future role is a deliberate schema/policy release, not scattered conditionals. |
| `auth_sessions` | `id UUID PK`; `user_id UUID NOT NULL FK ... RESTRICT`; `token_hash BYTEA NOT NULL UNIQUE` (SHA-256 of a high-entropy token); `created_at`, `expires_at`, nullable `revoked_at`, and optionally `last_seen_at`; checks `expires_at > created_at` and `revoked_at IS NULL OR revoked_at >= created_at`; indexes on unique hash, `(user_id, expires_at)`, and active-expiry cleanup. Never persist the bearer token. |
| `student_user_links` | `user_id UUID PK/FK users RESTRICT`; `student_id UUID NOT NULL UNIQUE/FK students RESTRICT`; `created_at`, optionally `linked_by UUID FK users RESTRICT`. The MVP is exactly 0-or-1 Student per User and 0-or-1 User per Student. This matches one learner login per domain learner and avoids ambiguous principal resolution. Change cardinality only for a demonstrated guardian/multi-profile requirement. |

Use restrictive deletion everywhere: actor/audit/history and domain links make hard user deletion unsafe. Admin management disables accounts; it does not delete them. A user with `student` role should have exactly one link as an application-level invariant (enforced transactionally by management/bootstrap operations because cross-table role/link checks are unsuitable as simple SQL checks). A link on a non-student account is invalid and user management must reject it. Multi-role accounts including `student` may have a link, but UI mode ambiguity is an open product choice; MVP should normally create single-role students.

Disabling a user must both set `is_active=false` and revoke all active sessions in one transaction. Session resolution must nevertheless join/check `users.is_active` on every request, so an unrevoked/racing old session fails closed. Role/link changes should revoke sessions (recommended) so `/me` capability snapshots cannot remain stale.

Existing historical actor UUID columns should not receive retroactive FKs in C1 unless all deployed data is reconciled. New users can become the source of future values without rewriting history. A later data-governance decision may introduce deferrable/nullable actor references or a system-actor registry.

## E. Authentication Design

### Mechanism and endpoints

Prefer an opaque server-side session in an HttpOnly cookie:

1. `POST /api/auth/login` accepts login/password, performs a constant-behavior lookup and password verification, rejects inactive users, creates a random token (at least 256 bits from `secrets.token_urlsafe(32)`), stores only SHA-256(token) with expiry, and returns the same safe principal DTO as `/me`. Rate limiting belongs at the edge plus an application login throttle/audit plan.
2. `GET /api/auth/me` resolves the cookie, hashes it, selects an unrevoked/unexpired session joined to an active user, loads roles/link, computes capabilities, and returns `user_id`, display/login, roles, capabilities, and `student_id` (only when valid). It never returns token/hash/password data.
3. `POST /api/auth/logout` revokes the matching session if present and always expires the cookie. It is idempotent; an absent/expired cookie need not disclose state.

Use **Argon2id** via an explicitly added, pinned password library (`argon2-cffi` or `pwdlib[argon2]`) with parameters benchmarked for deployment; store the self-describing encoded hash, use the library verifier, and rehash after successful login when parameters are obsolete. Do not invent crypto or log credentials.

Use an absolute session lifetime (recommended MVP: 8–12 hours for privileged school/admin use; exact policy configurable), UTC database time for validation, logout revocation, periodic expired/revoked cleanup, and an optional shorter idle limit only if `last_seen_at` updates are throttled. Password reset/change, role change, link change, and disabling revoke all sessions.

### Cookie, CORS, and CSRF

- Cookie: narrowly named (for example `__Host-ai_school_session` in HTTPS production), `HttpOnly`, `Secure=true` in production, `Path=/`, no `Domain`, and `SameSite=Lax` for the intended same-site SPA/API deployment. Local HTTP needs a separately named/configured non-Secure development cookie; production must fail startup if secure-cookie policy is disabled.
- The frontend shared request wrapper must use `credentials: "include"`. Backend CORS must switch to `allow_credentials=True` with **explicit trusted origins only**; never combine credentials with `*`. Origin configuration should be parsed/validated once.
- SameSite=Lax substantially reduces cross-site form CSRF but is not the sole control. For every unsafe cookie-authenticated method, validate `Origin` against the trusted frontend origins and use a per-session CSRF token (random value returned by login/`me`, sent in `X-CSRF-Token`, with only its hash/binding stored) or a rigorously implemented signed double-submit token. Keep JSON content types for normal commands; include uploads and logout in protection. CORS is not CSRF protection.

### Why not JWT/localStorage

This is a same-organization browser application with a database already required for authorization and immediate revocation needs (disabled users, role changes, logout). Opaque sessions make revocation and active-user enforcement direct, keep bearer material out of JavaScript, and avoid stale role/capability claims. JWT in localStorage expands XSS token theft impact, makes revocation/claim freshness harder, and supplies no repository-driven advantage. A JWT cookie would still require CSRF protection and revocation checks, eliminating its claimed stateless benefit.

## F. Principal and Capability Model

Use an immutable application/presentation value such as:

```text
AuthenticatedPrincipal(user_id, roles, capabilities, student_id | null)
```

`user_id` is always the canonical account actor. `student_id` is resolved only through `student_user_links`, never from request data. Central FastAPI dependencies should be equivalent to `require_principal()` and `require_capability(Capability.X)`. Role names occur only in the centralized role-policy resolver and account management validation; routers ask for capabilities. A system-initiated job uses an explicit `SystemActor`, not a forged user principal.

Refine the suggested names slightly: split broad management where object scope differs, but keep the MVP comprehensible.

| Capability | Admin | Teacher | Student |
|---|:---:|:---:|:---:|
| `users.manage` | ✓ | — | — |
| `catalog.manage` (tags/folders/global catalogs) | ✓ | — | — |
| `diagnostics.read` | ✓ | — | — |
| `content.read` | ✓ all | ✓ approved + own | — |
| `content.create` | ✓ | ✓ | — |
| `content.edit` | ✓ | ✓ own | — |
| `content.review.submit` | ✓ | ✓ own | — |
| `content.review.return` | ✓ | — (MVP) | — |
| `content.approve` | ✓ | — | — |
| `content.archive` | ✓ | — | — |
| `image_solving.use` | ✓ | ✓ | — |
| `assessment.create` | ✓ | ✓ | — |
| `assessment.manage` | ✓ all | ✓ own | — |
| `assessment.results.read` | ✓ all | ✓ own assignments | — |
| `student.assignments.read` | — | — | ✓ own |
| `student.attempts.submit` | — | — | ✓ own |
| `student.results.read` | — | — | ✓ own, when product exposes results |

`content.review` is renamed to directional `content.review.submit`/`content.review.return`; submitting one's work and acting as reviewer are materially different powers. If methodologists arrive, assign a new role a policy set (for example read + return + approve if governance permits) without adding role branches to services.

**Layer boundary:** presentation resolves cookie/session/link and enforces coarse capabilities. Application services receive a principal or minimal authorization context and perform resource visibility/ownership checks inside the same unit of work/lock as mutation. Repositories expose scoped queries and owner predicates. Domain rules continue to express lifecycle, immutability, validation, idempotency, and transitions without FastAPI, cookies, React, roles, or HTTP status knowledge.

## G. Object-Level Authorization Rules

### Content Bank

- **Create:** `content.create`; `Task.created_by` and initial `TaskVersion.created_by` are `principal.user_id`. A teacher owns the task created; admin is still recorded as creator when creating directly.
- **Read/list/search/card/audit/attachments:** admin sees all. Teacher sees approved shared content plus any task whose `Task.created_by == principal.user_id`, including own draft/review. Apply this predicate in repository queries, totals, folder contents, duplicate results, and direct card/audit/attachment lookup to avoid count/existence leakage. A teacher requesting another teacher's non-approved task gets 404. Students have no Content Bank capability; Assessment execution continues through its purpose-built safe projection, not `content.read`.
- **Edit methodology, tags, task attachments, relocation:** require `content.edit`, task ownership for teachers, and existing mutable-draft/CAS rules. Version ownership should be derived from its parent task for policy; `TaskVersion.created_by` remains the actor who created that version and is not by itself authority if an admin cloned it.
- **Submit review:** `content.review.submit`, ownership for teacher, current lifecycle validation unchanged.
- **Return:** `content.review.return`; MVP admin/reviewer only. Preserve mandatory reason and review-state rules.
- **Approve:** `content.approve`; set `approved_by=principal.user_id`; never allow capability to bypass completeness, review status, approved-version uniqueness, locks, or immutable version behavior.
- **Archive:** `content.archive`; MVP admin only; preserve in-use/domain conflicts and audit reason.
- **Folders, tag definitions, global catalog mutation:** `catalog.manage` (admin). Version tag assignment remains `content.edit` + task scope, not catalog management.
- **Audit actor:** authenticated user ID for interactive actions. Background actions use a documented system actor/type; never a Student ID. Historical actor UUIDs remain truthful historical values.

### Image Solving

All artifact upload/metadata and session create/run/state/result/attempt/recommendation/promote routes require `image_solving.use`; replace only `settings.content_bank_dev_actor_id` with `principal.user_id`. Keep application/repository owner checks: `principal.user_id == input_artifacts.owner_id == image_solving_sessions.owner_id` (and authoring session owner where promoted). Foreign/missing artifacts or sessions remain indistinguishable 404s. The upload contract must continue rejecting `owner_id`; promotion's creator/audit actor is the same authenticated User, while generated task `created_by` is that User. Promotion idempotency and transaction behavior remain untouched.

### Assessment Teacher

- Create writes `Assessment.created_by=principal.user_id`; publication/close/audit write `published_by`, assignment `created_by`, `closed_by`, and teacher audit actor as User IDs.
- Teacher list/get/mutation predicates require `assessment.created_by == principal.user_id`. Variants/items inherit assessment scope. Teacher publication also requires owned assessment; class-group access can initially be shared read-only, but assignment creation remains attributable to the publisher.
- Teacher assignment list/get/close/results require `assignment.created_by == principal.user_id` **and**, when reached below an assessment, consistent assessment scope. Admin bypasses owner scope but not state validation.
- Add owner-scoped repository methods or an explicit scope parameter to `list/get/lock/list_assignments/get_assignment/lock_assignment`; mutation ownership must be checked on the locked row in the transaction to prevent time-of-check/time-of-use gaps. Extend records to expose `created_by` internally even if public DTOs need not change.
- Results/checking joins must start from an authorized owned assignment, then traverse participants/submissions/results; knowing submission/run IDs is insufficient.

### Assessment Student

Resolution is session → User → roles/capabilities → unique link → Student. Every endpoint in `student_assessment_routes.py`, plus both student attachment endpoints and protected attachment content, uses only `principal.student_id`. The assignment/submission/item IDs remain resource selectors, never identity.

A student-capable principal without a link is authenticated but not provisioned: return 403 `student_link_required` from the student boundary (not 404, because the failure concerns the caller configuration), log the administrative defect, and expose no resource query. User management should normally prevent this state. A non-student principal with a link receives 403 for missing student capability; the link alone grants nothing. Cross-student assignment/attempt/answer/result/attachment access remains 404 through owned queries. Do not add `student_id` to any student request DTO.

Actor classification is deliberate: student workflow ownership remains the domain `Student.id`; current student audit `actor_id` may remain Student ID because its schema explicitly says `actor_type='student'`. For stronger account traceability, add a separate optional `user_id`/auth metadata in a future compatible audit migration rather than silently changing the meaning of existing `actor_id`. Teacher/user actor fields, Content Bank audit, `created_by`/`approved_by`, artifact/session owner, and promotion actor are User IDs. System jobs use system actor identity.

## H. HTTP Authorization Semantics

| Situation | Status/policy |
|---|---|
| Missing, malformed, revoked, expired session; inactive user's old session | **401** `authentication_required` (and clear cookie). Do not distinguish token failure modes. |
| Authenticated caller lacks coarse capability (teacher on users/tags; student on authoring; linked non-student on student API) | **403** `forbidden`. |
| Student A requests Student B's assignment, attempt, answer, result, or attachment | **404**, identical to absent. |
| Teacher A requests Teacher B's private assessment/assignment/result or private Content Bank task | **404**, including lists omitting it, to prevent existence disclosure. |
| Student-role account has no link | **403** `student_link_required`; it is a provisioning problem independent of a target object. |
| Valid authorization but stale CAS, immutable/published state, duplicate active transition, or other domain-state conflict | Existing **409** semantics (or existing 422 where the established contract treats readiness/input as validation). Authentication must not remap these. |

Perform capability denial before arbitrary object lookup when the caller lacks the entire feature; perform scoped lookup for a generally capable caller so foreign and absent objects both become 404. Preserve existing error envelope, but update the generic HTTP exception mapping so 401/403 do not misleadingly become `validation_error`.

## I. Bootstrap Strategy

Recommend an explicit backend CLI command, e.g. `python -m app.cli bootstrap-admin`, using login from an argument/prompt and password from a no-echo prompt or one-use secret-file/STDIN input. It should run the same account service as admin management, hash with Argon2id, and transactionally create an active admin only when no admin exists.

- **Idempotency:** if the normalized login already identifies the sole intended account with admin role, report success without changing the password; if any admin exists or login conflicts, fail safely unless an explicit separately audited recovery command is used.
- **Local development:** developers invoke the command after migrations; optional Compose documentation may pass a one-use secret interactively, but startup must not silently seed credentials.
- **Production:** deployment runs the command as an explicit one-off with secrets manager/TTY input. Never put a known password in migration, image, source, `.env.example`, logs, shell history, or automatic startup.
- **Continuity:** user management must prohibit disabling/removing the last active admin and should require recent password confirmation for high-risk account/role changes. Manual SQL and migration seeding are emergency procedures, not normal bootstrap.

## J. Frontend Integration Design

1. Extend the single `request()` wrapper to default to `credentials: "include"`, attach CSRF for unsafe methods, and translate 401 centrally. Preserve abort signals and idempotency headers.
2. Add `/login`, login/logout calls, and an auth provider/store whose initial state is `loading`. On application startup call `/api/auth/me` before rendering protected content. A 401 becomes anonymous; transient server/network failure is an error state, not false anonymity.
3. Route switching in `main.tsx` should classify anonymous-only and protected routes. Anonymous access redirects to login while preserving a validated internal return path; authenticated access to `/login` redirects to the capability-appropriate home. Prevent protected page fetches before bootstrap completes.
4. Build navigation from capabilities, not role-name JSX branches. Route visibility is UX only: **backend authorization remains authoritative**. A direct hidden URL must still fail server-side.
5. Display user name and logout. On logout clear only auth-sensitive client state; existing operation idempotency keys may be namespaced by `user_id` to prevent a shared-browser user inheriting another user's keys.

Navigation grounded in existing pages:

- **Student:** existing `Мои работы` (`/student/works`); add `Результаты` only when a results page/API is actually exposed.
- **Teacher:** existing `Решить по фото`, `Задания`, `Создать задание`, `Работы`; assessment results navigation when its surface exists. Do not show `Теги`.
- **Admin:** teacher functionality plus existing `Теги`; add `Каталоги`, `Пользователи`, and diagnostics only as those routes are implemented. Admin capability does not imply a Student workflow without a valid student role/link.

## K. Security Risks / Regression Risks

### Critical

1. **Anonymous/open privileged routes:** every current route lacks auth; partial rollout could leave an old router or attachment content path as a bypass. Inventory every `include_router` and add deny-by-default acceptance coverage.
2. **Teacher cross-resource access:** assessment repositories ignore `created_by`; Content Bank records it but do not scope queries/mutations. Enforce scoped repository lookup plus in-transaction lock checks.
3. **Attachment disclosure:** generic content lookup is parent-unaware; student answer data can leak by UUID. Authorize through the owning task/answer and return 404.
4. **Shared dev identity surviving rollout:** any overlooked setting use collapses accounts into one owner. CI should assert the two settings and `PilotStudentContext` no longer occur in runtime after E phases.

### High

5. **Student/user spoofing:** never accept `user_id`, `student_id`, actor, or owner from browser authority. The artifact route's current unknown-field rejection is a guarantee to preserve.
6. **Inactive/stale privilege sessions:** check active user and current role/link per request; revoke sessions transactionally on disable/password/role/link changes.
7. **Session theft/storage:** high-entropy opaque cookie, hash-at-rest, HttpOnly/Secure, no token logging, rotation after login/privilege changes, and cleanup. Never plaintext passwords or session tokens.
8. **CSRF/CORS:** credentials require exact origins, `allow_credentials=True`, Origin + CSRF-token protection on all mutations/uploads/logout, and secure production configuration.
9. **Privilege escalation/user management:** only `users.manage`; validate bounded roles server-side; prevent self-escalation policy mistakes and last-active-admin lockout; audit role/status/link changes.

### Medium

10. **Frontend hiding trusted as security:** navigation guards cannot authorize requests; acceptance tests must call APIs directly with each role.
11. **Existence leaks:** unscoped prefetch followed by 403, counts, duplicate candidates, audits, and empty attachment behavior can reveal foreign resources. Scope queries and standardize 404.
12. **Audit semantic corruption:** replacing Student audit actor IDs with User IDs would change meaning; retain typed semantics or migrate explicitly. Do not rewrite historical UUID actors.
13. **Domain regressions:** careless authorization preloads can break transaction/row-lock ordering, approved immutability, idempotency replay, canonical fingerprints, or promotion atomicity. Insert policy checks inside existing UoWs without changing DTO/pipeline inputs.
14. **Password/login abuse:** add uniform login errors, timing-safe verification path, rate limits, safe logging, and rehash policy. Avoid account enumeration.

## L. Recommended Migration / Implementation Sequence

Each item is one independently reviewable PR; do not mix frontend rollout or domain redesign into persistence/auth foundations.

### C1 — Account persistence

- **Goal:** add User/Role/AuthSession/StudentUserLink ORM models, repository ports, and one Alembic revision after `20260831_01` with the constraints/indexes above.
- **Likely files:** new `app/infrastructure/auth_models.py`, account application contracts/repository, `alembic/versions/<new>_account_persistence.py`, model metadata imports, migration/unit/integration fixtures.
- **Non-goals:** login/cookies, principal dependencies, route enforcement, seeding/default credentials, historical actor FK rewrites.
- **Tests:** clean upgrade/downgrade, sole-head assertion, uniqueness/FKs/checks, role/link cardinality, hashed-session shape, repository transactions.
- **Risk/PR boundary:** migration portability and unintended cascades; persistence only.

### D1 — Authentication service

- **Goal:** password hashing, opaque session creation/resolution/revocation, login/logout/me, secure settings and auth audit-safe logging.
- **Likely files:** new application auth service, infrastructure repository/hash adapter, `presentation/auth_routes.py` and schemas, `config.py`, `main.py`, requirements/tests.
- **Non-goals:** protecting existing business routers, role-based navigation, user-management API, dev seam removal.
- **Tests:** hash verify/rehash, no plaintext token, login enumeration resistance/error parity, expiry/revocation/disabled users, cookie flags, logout idempotency, `/me` data minimization.
- **Risk/PR boundary:** cookie/environment misconfiguration and secret leakage; endpoints/service only.

### D2 — Principal + capability boundary

- **Goal:** immutable principal, link resolution, centralized role policy, `require_principal`/`require_capability`, CSRF/Origin enforcement, consistent 401/403 envelopes.
- **Likely files:** new application authorization policy and presentation dependencies, `main.py` exception/CORS composition, config, dependency tests.
- **Non-goals:** per-object ownership or wholesale router migration.
- **Tests:** role matrix, multi-role union, missing link, inactive sessions, CSRF for every unsafe method class, exact-origin CORS, dependency overrides.
- **Risk/PR boundary:** policy accidentally granting through role/link; reusable boundary only.

### E1 — Content Bank + Image Solving identity migration

- **Goal:** replace `CONTENT_BANK_DEV_ACTOR_ID` with principal User ID across Content Bank, attachments, authoring, artifacts, Image Solving, recommendations and promotion; add coarse capabilities while retaining owner checks.
- **Likely files:** all corresponding presentation routes, application service signatures where authorization context is needed, repository scoped reads, settings/env/Compose, related tests.
- **Non-goals:** changing Image Solving ownership model, promotion/fingerprint/idempotency behavior, final Content Bank teacher object policy (except necessary private visibility), user UI.
- **Tests:** two-user artifact/session/authoring isolation, reject client owner, correct created/approved/audit actors, admin route denial, promotion replay, lifecycle/immutability suite unchanged.
- **Risk/PR boundary:** missed fixed-actor route or broken ownership/idempotency; one bounded identity-cutover PR with an explicit route inventory.

### E2 — Assessment student identity migration

- **Goal:** replace `ASSESSMENT_DEV_STUDENT_ID` with principal link resolution on all seven student operations, answer attachments, and attachment content; preserve 404 isolation.
- **Likely files:** student and attachment routes/dependencies, student application boundary, config/env/Compose, student API/integration fixtures.
- **Non-goals:** merging User/Student, teacher ownership, changing attempt/idempotency/domain DTOs.
- **Tests:** linked/unlinked/non-student/inactive cases; Student A vs B assignment/attempt/answer/attachment 404s; start/submit replay and locking regressions.
- **Risk/PR boundary:** accidental identity from request or changed retry semantics; student cutover only.

### F1 — Backend RBAC

- **Goal:** apply coarse capabilities to every registered endpoint and produce a reviewed route-to-capability manifest; secure generic attachment delivery.
- **Likely files:** every presentation router, authorization policy/tests, attachment resolver.
- **Non-goals:** detailed teacher owner filters (F2), admin CRUD, frontend navigation.
- **Tests:** anonymous 401 and each role's 403/allowed matrix for every route/method; no obsolete bypass; CORS/CSRF acceptance.
- **Risk/PR boundary:** missing route and over-broad admin bypass; coarse gates only.

### F2 — Teacher object-level ownership

- **Goal:** Content Bank own-private/shared-approved visibility and mutations; Assessment own assessment/assignment/results filtering, with admin all-scope and unchanged domain rules.
- **Likely files:** `content_bank.py`, `assessments.py`, their repositories/models/records and presentation adapters, attachment/result queries, tests.
- **Non-goals:** new lifecycle transitions, admin domain bypass, ownership reassignment/delegation, Student behavior.
- **Tests:** two teachers across list counts/direct reads/all mutations/results, 404 non-disclosure, admin access followed by normal 409/422 state rules, transaction/locking/CAS regression.
- **Risk/PR boundary:** list leaks and TOCTOU ownership checks; object authorization only.

### G1 — Admin user management

- **Goal:** capability-protected create/list/update/disable users, role and Student-link management, session revocation, security audit events, last-admin rule, and explicit bootstrap CLI.
- **Likely files:** account application/infrastructure modules, new admin routes/schemas/CLI, settings/docs/tests.
- **Non-goals:** password reset email, SSO, invitations, bulk sync, frontend admin page.
- **Tests:** normalization/duplicates, role/link invariant, privilege denial, disable/revoke, last-admin, bootstrap idempotency/no secret logs, concurrent admin changes.
- **Risk/PR boundary:** escalation and lockout; backend administration + CLI only.

### H1 — Frontend authentication / role-aware UX

- **Goal:** credentialed API client, CSRF handling, login/logout/me bootstrap, protected routing, user state, capability-built navigation, and user-management UI only if G1 contract is stable.
- **Likely files:** `frontend/src/api.ts`, new auth API/provider/login components, `main.tsx`, shell/styles and tests; existing page tests updated with auth fixtures.
- **Non-goals:** treating hidden routes as security, inventing unavailable results/catalog screens, storing bearer tokens.
- **Tests:** bootstrap loading/401/network error, login return path, logout, credentials on all calls, CSRF mutation headers, role navigation/direct-route denial, shared-browser state cleanup.
- **Risk/PR boundary:** request wrapper regressions and pre-bootstrap data fetch; frontend only, screenshot required because it is perceptible.

### I1 — Authorization/security acceptance suite

- **Goal:** end-to-end deny-by-default matrix and regression proof across accounts, resources, states, sessions, CSRF, CORS, attachments, and idempotent flows.
- **Likely files:** new backend integration/security tests, frontend acceptance tests, test account factories, security/runbook docs.
- **Non-goals:** new product behavior or policy expansion.
- **Tests:** route inventory; anonymous/disabled/expired/revoked; all role capabilities; two teachers/two students; 404 non-disclosure; owner spoof payloads; last admin; CSRF/origin; domain lifecycle, approved immutability, promotion and assessment idempotency/locks.
- **Risk/PR boundary:** false confidence from incomplete endpoint inventory; tests/docs only and release gate.

## M. Open Questions

1. Are class groups globally administered or teacher-owned? The schema records `ClassGroup.created_by`, but current APIs only list groups and no teacher/group membership concept exists. Until decided, teachers may read active groups for assignment while only admin manages them; do not infer group ownership from assessment ownership.
2. Should a teacher be allowed to archive their own tasks or return their own reviewed tasks to draft? The requested baseline only clearly grants submit/edit, while current lifecycle supports both operations. MVP above reserves archive/return to admin/reviewer pending governance confirmation.
3. Does the product require users with simultaneous teacher and student modes, guardian access, or multiple Student profiles? The repository provides no such requirement; MVP deliberately uses one-to-one links and single-role student accounts.
4. What exact production topology/domain relationship will host SPA and API? It determines whether `SameSite=Lax` is sufficient and whether a cross-site `SameSite=None; Secure` cookie is unavoidable. Exact-origin CORS and CSRF remain required either way.
5. What are the required session duration, idle timeout, password policy, recovery mechanism, and institutional identity-provider roadmap? No repository configuration or product contract answers these; they should be decided before D1 without changing the opaque-session direction.
6. When and to whom are Assessment results exposed? Student result capability/navigation and teacher result joins are designed, but the current frontend/API does not expose a general results page, so H1 must not fabricate one.
