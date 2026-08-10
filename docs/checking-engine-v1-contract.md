# Checking Engine v1 — технический контракт и gap audit

> **Статус:** Phase 4, Prompt 4.0, contract-first. Документ описывает решения,
> но не реализует таблицы, services, routes, checkers, jobs или LLM. Источником
> фактов служат production-код и Alembic head `20260808_02`; прежние документы
> используются только после сверки. Checking Handoff v1 остаётся неизменным.

## 1. Проверенное исходное состояние и метод аудита

На старте HEAD был `0e232af702e5923ec26d26b69591083f88460bb0`, ровно ожидаемый
handoff, а `git status --short` был пуст. Цепочка миграций заканчивается
`20260808_02_assessment_core_foundation`. `docker compose config` и
`docker compose exec -T backend alembic current` не удалось выполнить: в среде
нет executable `docker`; поэтому фактический revision пользовательской БД не
подтверждён и downgrade не выполнялся. Файл `ai_school_phase4_handoff(1).md` в
`/workspace`, `/tmp` и `/root` не найден.

Сопоставлены README и документы Content Bank/Assessment, application DTO и
валидация, ORM/repositories, presentation schemas/routes, все Alembic revisions,
unit tests методики/normalization/handoff/attempts/assessments и PostgreSQL
integration tests Assessment/Handoff/Phase 3 vertical acceptance. Корневой README
действительно был устаревшим: ошибочно называл папки и Assessment Core
нереализованными.

## 2. Current-state field-by-field gap audit

Общие свойства: методика принадлежит конкретной `task_versions.id`; repository
полностью заменяет агрегат методики только у latest draft. После review/approval
application запрещает изменение, approved version исторически читается по UUID,
а Assessment FK имеет `ON DELETE RESTRICT`. Public Content Bank card API отдаёт
методику и IDs; Checking использует внутренний read port, не публичный HTTP API.

### 2.1 Task/version и skills

| Поле | DB → application DTO/input → API | Валидация, lifecycle, history | Использование / достаточность / изменение |
|---|---|---|---|
| `statement` | `task_versions.statement TEXT`; `VersionContentInput`/`TaskCardVersion`; string в task/card API | draft допускает промежуточную пустоту; review/approval требуют trim-nonblank; immutable после draft; exact version исторична | Prompt context/evidence. Достаточно V1; snapshot в 4.2. |
| `task_type` | PostgreSQL enum; `VersionContentInput.task_type`; API enum string | матрица с answer format проверяется application; version-scoped, immutable после draft | Вторичный routing guard. Достаточно; конфликт → `insufficient_rubric`, 4.3. |
| `answer_format` | enum `single_choice`, `multiple_choice`, `short_text`, `number`, `expression`, `long_text`; DTO/API string | совместимость application; handoff копирует format exact version | Главный routing discriminator. Достаточно для известных форматов; unknown → manual, 4.3. |
| status | `task_versions.status` draft/review/approved/archived; DTO/API | lifecycle draft→review→approved/archive; только draft mutable; approved timestamp сохраняет исторический факт | Intake не revalidate current status; historical submitted use разрешён. Достаточно. |
| `task_version_id` | UUID PK; во всех version DTO/API; FK `assessment_items.task_version_id RESTRICT` | concrete version, не latest; version row не удаляется при historical use | Исторический identity и FK. Достаточно. |
| version skills | `task_skill_links(skill_id, weight, is_primary)`; `SkillLinkInput/DTO`; API array | unique skill/version, weight `(0,1]`, application sum `1.0000`, ровно один primary (также partial unique index); version-scoped | LLM context/findings attribution. Достаточно; snapshot link IDs/weights/names в 4.2. |
| primary skill | тот же link с `is_primary` | application ровно один; DB запрещает >1, но не гарантирует наличие без application | Routing не зависит от skill; finding may reference it. Missing/conflict → methodology invalid, не угадывать. |
| skill weights | `NUMERIC(5,4)` / `Decimal` / decimal representation | сумма application-only; immutable approved | Не являются score weights. Только context; достаточно. |

### 2.2 Expected solution

| Поле | DB / DTO / API | Validation/history | Checking / gap / phase |
|---|---|---|---|
| `solution_text` | `expected_solutions.solution_text TEXT`; Input/DTO string; API string | one per version; required by schema; approval требует expected solution, содержательная полнота ограничена | LLM rubric context, не deterministic truth само по себе. Достаточно структурно; слабое содержание → insufficient, 4.3/4.8. |
| `final_answer` | nullable TEXT; Input/DTO/API nullable string | без type/canonical contract | Подсказка автору/LLM, **не** источник numeric/exact без accepted answer. Gap; typed accepted answers до 4.4/4.5. |
| `solution_steps_json` | JSONB list; `solution_steps: tuple[str]`; API array | сохраняется ordered, validation presentation ограничивает shape | LLM evidence context. Snapshot justified; не исполнять. Достаточно V1. |

### 2.3 Rubric

| Поле | DB / DTO / API | Validation/history | Checking / gap / phase |
|---|---|---|---|
| `rubric.id` | UUID PK; `RubricDTO.id`; API UUID | replacement draft создаёт новую aggregate identity; approved immutable | Stable only for frozen version. Snapshot ID in 4.2. |
| `max_score` | `rubrics.max_score NUMERIC`; derived by repository from item sum; DTO/API Decimal | DB `>=0`; approval rubric required; no equality to assessment points | Authoring scale. Must equal item sum for checking; otherwise insufficient. Scaling to assessment points defined §8. No schema change 4.1. |
| `grading_mode` | enum `points`, `levels`, `binary`; input/DTO/API | enum only | `points` usable by LLM item scoring; `binary` deterministic whole answer; `levels` lacks authored level catalogue → insufficient/manual. 4.3/4.8. |
| `notes` | nullable TEXT everywhere | free text | Supplemental instructions, never new criterion. Snapshot. |
| rubric items | child rows / Input/DTO / ordered API array | ≥1 expected at approval; ordered by `order_index` | Explicit criteria for partial scoring. Required for llm rubric. |
| stable `rubric_item.id` | UUID PK / DTO/API | stable after approval; replacement while draft changes IDs | Required result FK/reference; snapshot. Sufficient. |
| `criterion` | TEXT/string | trim/non-empty application/presentation; no semantic validator | LLM criterion. Empty/malformed → insufficient. |
| `max_points` | NUMERIC/Decimal | DB `>0`; application totals rubric max | Preliminary points, then scale. Sufficient if totals consistent. |
| `required` | BOOLEAN/bool | non-null | Required miss is finding/review signal, not automatic zero unless criterion explicitly says so; 4.8. |
| `common_failure` | nullable TEXT | free text | Context/finding candidate, not proof. Sufficient. |
| `order_index` | INTEGER | unique per rubric, `>=0`; repository order | Deterministic serialization. Sufficient. |

Known gap: `rubric.max_score` need not equal frozen `assessment_item.points`.
V1 deliberately scales a valid rubric result; it never changes frozen points.
Approval requires expected solution and rubric, but does **not** require accepted
answers, a choice catalogue, safely parseable numeric methodology, or sufficient
semantic criteria. Therefore “approved” never means “automatically checkable”.

### 2.4 Accepted answers

| Поле | DB / DTO / API | Validation/history | Checking / gap / phase |
|---|---|---|---|
| `accepted_answer.id` | UUID PK / DTO/API | created/replaced only in draft; stable approved | Alternative identity/evidence. Sufficient identity. |
| `answer_value` | TEXT / string / string | non-empty constraints are weak; untyped | Checker needs typed interpretation. Add explicit `value_kind` plus typed canonical JSON (or strictly defined typed columns) in a **methodology migration before 4.4**; 4.2 snapshot supports schema version. Until then only narrowly parse by answer_format, failures manual/insufficient. |
| `tolerance` | nullable unconstrained-scale NUMERIC / Decimal | nonnegative only | Cannot distinguish absolute/relative. Add nullable `absolute_tolerance NUMERIC` and `relative_tolerance NUMERIC`, each ≥0, mutually explicit; legacy `tolerance` maps to absolute only by declared v1 legacy rule. Migration before 4.5. |
| `unit` | nullable TEXT | free text | Unsafe alias/conversion. Add canonical unit code + authored alias catalogue later Phase 4; V1 only exact canonical unit equality if catalogue exists, otherwise manual. |
| `normalization_rule` | nullable TEXT | free text | Never executable. Replace with allowlisted rule code/version in methodology evolution; unknown/non-null free text → manual/insufficient. |

### 2.5 Typical errors

| Поле | DB / DTO / API | Validation/history | Checking / gap / phase |
|---|---|---|---|
| `typical_error.id` | global UUID PK / DTO/API | error row keyed globally by skill+code; link is version-scoped | Finding reference only when evidence supports it. Snapshot relevant fields. |
| `skill_id` | FK skills RESTRICT / UUID / UUID | must be linked version skill when saving | Finding attribution. Sufficient. |
| `code` | TEXT, unique per skill / string | authored identifier | Stable human-readable finding code; snapshot. |
| `severity` | enum minor/major/critical | enum validation | Suggested finding severity; checker cannot inflate it. |
| `remediation_hint` | nullable TEXT | free text | Future feedback draft only, not Phase 6 remediation. |
| task-error link | `task_error_links(task_version_id, typical_error_id)` unique | version-scoped; cascade with version | Establishes allowlist. Sufficient. |
| `detection_hint` | nullable TEXT on link / DTO/API | free text | LLM/deterministic clue, never proof or executable expression. Sufficient with evidence. |

### 2.6 Assessment/student boundary

| Field | Actual representation | Contract conclusion |
|---|---|---|
| `assessment_item.id` | UUID PK, handoff UUID | result identity; sufficient. |
| frozen points | `assessment_items.points NUMERIC(8,2)`, `(0,999999.99]`; handoff `Decimal` serialized exactly two decimals | authoritative result `max_score`; never recompute assessment. |
| `task_version_id` | FK exact version, RESTRICT; handoff | historical methodology key. |
| `answer_format` | historical version read into handoff | routing assertion; snapshot also records methodology value and rejects mismatch. |
| raw answer | `student_answers.raw_answer JSONB`; handoff JSON/null | evidence/provider context. Potentially sensitive; no ordinary event logging. |
| stored normalized | JSONB produced once at save; handoff object/null | canonical comparison source; Checking never normalizes again. |
| `submitted_at` | submission timestamp, required iff submitted; handoff UTC | intake readiness and fingerprint. |
| unanswered | absent answer row becomes raw/normalized `null/null`, item retained | route to deterministic incorrect/unanswered result without provider; never drop. |

## 3. Bounded context and boundaries

Checking owns run lifecycle, materialized immutable input snapshot, deterministic
routing, checker executions, preliminary versioned results/findings/confidence,
provider execution metadata, append-only events and selection of results needing
human review. It does **not** own attempt/submission lifecycle or normalization,
Assessment composition/points, Content Bank authoring, final teacher result or
override, analytics aggregates, IAM, or remediation.

* **Content Bank:** read exact version methodology through a read port. Archive
  affects new use only. Checking never mutates/“repairs” methodology.
* **Assessment Core / Student Submission:** supplies submitted Handoff v1 and
  frozen points/answers. Checking stores submission UUID only; no participant,
  student, group, actor or assignment identity. It cannot reopen/resubmit.
* **Teacher Review (Phase 5):** consumes immutable preliminary results and may
  create a separate final decision/override referring to result ID; never updates it.
* **LLM provider:** replaceable infrastructure adapter, untrusted output. Domain
  has no SDK types and deterministic paths do not call it.
* **Analytics (Phase 6):** consumes events/final review decisions later; does not
  turn check tables into mutable aggregates.

## 4. Canonical intake, snapshot and fingerprint

### 4.1 Decision

V1 materializes one immutable methodology-and-answer snapshot per run. Mere
references are insufficient because Content Bank draft replacement can change IDs
before approval, typical errors are shared rows, future corrections/migrations can
change semantics, and replay must not silently mix external state. Exact UUID FKs
remain provenance references, while snapshot is execution truth.

Intake accepts only a `CheckingHandoff(version=1)` whose submission is still
`submitted` when locked/read. It iterates `(position, assessment_item_id)` and for
each exact `task_version_id` obtains: statement, task/answer formats, expected
solution, accepted answers, rubric/items, linked typical errors/detection hints,
skills/primary/weights. It combines these with frozen points, raw and already
normalized answers. It neither extends Handoff v1 nor renormalizes. All items,
including unanswered, are snapshotted. PII fields are absent by allowlist.

Canonical JSON uses UTF-8, NFC only for authored metadata at authoring boundaries
(not rewriting stored answers), sorted object keys, compact separators, arrays in
semantic order (`position,id`; rubric `order_index,id`; other sets by UUID), lowercase
canonical UUID, UTC `Z` timestamps, booleans/null JSON-native and Decimal as plain
base-10 strings without exponent (`0` canonical; no insignificant trailing zero
except frozen points retain two-decimal contractual string). Schema version is
included. Hash is lowercase SHA-256 hex over canonical UTF-8 bytes:
`input_fingerprint = sha256(canonical_snapshot_bytes)`.

The snapshot contains Handoff v1 payload, methodology fields/IDs, methodology
snapshot schema version, routing-contract version and source revision markers; it
excludes student/participant/group/actor identity. Archive/assignment close/new
version cannot change it. A rerun always creates a new run but may reuse identical
snapshot bytes only after re-reading and verifying the same hash; mutable data is
never invisibly substituted.

## 5. Deterministic routing contract

Router input is only the snapshot. Precedence: unanswered fast path (checker selected
by normal rule but no provider) → validate known format/type and methodology → most
specific deterministic checker → `llm_rubric` only for explicit open rubric →
`manual_required`. Conflicting/unsafe authored data yields `insufficient_rubric`
when authoring is missing/malformed, versus `manual_required` when valid content is
outside safe V1 capability. Unknown future format is manual, never guessed.

| Checker | Formats/types and minimum methodology | Selection / insufficient / manual | Unanswered, partial, forbidden heuristics |
|---|---|---|---|
| `exact` | `short_text`; ≥1 unambiguous accepted text with allowlisted exact policy | choose only explicit exact policy; missing accepted answers = insufficient; semantic/fuzzy need = manual | unanswered incorrect score 0; no partial; no casefold/trim beyond stored normalized contract, fuzzy, regex or final_answer substitution. |
| `numeric` | `number`; ≥1 Decimal accepted value and explicit tolerance semantics | safe parse + supported no-unit/exact-unit policy; malformed/missing = insufficient; conversion/unknown rules = manual | unanswered incorrect 0; alternatives allowed; no partial; no float or expression coercion. |
| `multiple_choice` | `single_choice` or `multiple_choice`, authored option catalogue and accepted option-ID sets | single choice is a mode of this checker, not a new type; missing catalogue/unknown accepted IDs = insufficient | unanswered incorrect 0; partial only authored multiple-choice policy; no label/text inference. |
| `structured_expression` | `expression`, explicit canonicalizer ID/version and canonical alternatives | canonical exact only; missing contract = insufficient; algebraic equivalence needed = manual | unanswered incorrect 0; no partial in V1; no eval/CAS claims/string heuristics. |
| `llm_rubric` | `long_text` or explicitly rubric-graded `short_text`; valid points rubric and expected solution | long text routes here only with sufficient explicit rubric; missing/contradictory rubric = insufficient; provider unavailable/unsafe = manual | unanswered deterministic 0 without call; partial per items; no invented criteria or “LLM because deterministic absent”. |
| `manual_required` | any known/unknown format not safely provable | valid methodology but unsupported units/equivalence/levels/conflicts requiring judgment; router records reason | unanswered normally handled as incorrect before fallback; no score for manual status; no hidden heuristic. |

`short_text` exact wins only with explicit exact accepted answers; otherwise an
adequate rubric may select LLM, else insufficient/manual. `expression` canonical
match is allowed only under an authored canonicalizer contract; otherwise manual.
`long_text` never exact. Format/type matrix mismatch and incompatible checker hints
are methodology conflict → `insufficient_rubric` and human review.

## 6. Exact and choice contracts

Exact compares only stored `normalized_answer.text` to typed accepted canonical text.
Policy V1 is byte equality of valid JSON Unicode strings after the existing save-time
NFC/line-ending normalization and trim for short text: case-sensitive, internal
whitespace significant. The checker performs no additional Unicode, case or
whitespace normalization. Number/expression shapes are excluded. Multiple distinct
accepted strings are OR alternatives; duplicate canonical alternatives are rejected
as ambiguous methodology. Correct gives all frozen points, incorrect 0.

Choice requires a new version-scoped authored catalogue before reliable checking:
`choice_options(id UUID, task_version_id FK, option_key stable text, label/content,
order_index)` plus a version-scoped scoring policy and accepted sets referencing
option UUID/key, with unique `(version, option_key/order)`. This methodology migration
must precede 4.4 (but not necessarily 4.1 Checking-owned tables). Until it exists,
choice routes `insufficient_rubric`; current arbitrary strings such as `"B"` are not
proof of authored membership.

Single choice normalized source is one canonical option ID; multiple choice is a
sorted set. Order never affects equality; duplicates are invalid at student save and
methodology authoring; unknown IDs make result `unclear`+review (catalogue mismatch),
not incorrect. Accepted alternatives are OR sets. Default scoring is all-or-nothing.
Partial multiple-choice scoring exists only with an authored, versioned per-option
policy: sum positive selected correct weights minus explicit distractor penalties,
clamped `[0,max]`; otherwise no partial. Extra options fail an all-or-nothing set.
Evidence stores matched/missing/extra option IDs, not labels or expected solution;
student feedback must not disclose unselected correct options.

## 7. Numeric and structured-expression contracts

Numeric uses Python/PostgreSQL-equivalent arbitrary precision `Decimal`, never binary
float. Student source is `normalized_answer.decimal`, whose grammar/canonicalization
is the existing Phase 3 normalizer; accepted source is the new typed decimal field
(or strictly parsed legacy `answer_value` during transition). Non-finite/malformed
historical normalized values are `unclear`+review; malformed accepted methodology is
`insufficient_rubric`.

For alternative expected `e`, actual `a`, match iff
`abs(a-e) <= abs_tol + rel_tol * abs(e)`; boundary is inclusive. Missing tolerance
means zero. Near zero is safe because relative component becomes zero and only
absolute tolerance applies. Both tolerances must be nonnegative finite Decimals and
are snapshotted. Alternatives independently carry tolerance/unit; any match succeeds.
Evidence includes canonical actual, accepted-answer ID, delta and threshold (decimal
strings), not other accepted values. Score is frozen max or zero; no numeric partial.

V1 units are either both absent, or identical canonical unit codes from an authored
allowlist. Aliases require explicit versioned alias mapping to canonical code. Unit
conversion is not implemented; dimension/conversion or free-text unit/rule routes
manual. `normalization_rule` is data, never code: no eval, regex/expression execution
or dynamic import. Minimal methodology evolution: typed accepted kind/value,
`absolute_tolerance`, `relative_tolerance`, canonical `unit_code`, allowlisted
`normalization_policy_code/version`; catalogue/alias tables where needed.

Structured expression V1 compares stored expression only after an explicit,
versioned, allowlisted canonicalizer that is syntax-only and resource-bounded. It
never uses `eval`, executes user/model code, or claims algebraic equivalence. The port
`ExpressionEquivalenceAdapter.check(canonical_actual, canonical_expected, policy)`
permits a future sandboxed CAS, but V1 adapter supports identity only. If equality
cannot be proved, status is `manual_required`, not `incorrect`. Malformed student
historical form is `unclear`; malformed authoring is insufficient.

## 8. Result schema and score scale

### 8.1 Structured result v1

Every persisted result validates this conceptual JSON schema. UUIDs are canonical;
all Decimal values serialize as non-exponent plain strings with at most two fractional
digits after scoring. Confidence is an application-derived decimal string in
`[0,1]` with four fractional digits, never a raw model self-rating.

```text
schema_version="1.0"; check_result_id UUID; check_run_id UUID;
assessment_item_id UUID; task_version_id UUID;
checker_type enum; checker_version nonblank <=64;
result_status = correct|partially_correct|incorrect|unclear|insufficient_rubric|manual_required;
score_suggested Decimal|null; max_score Decimal(8,2);
confidence Decimal(5,4); summary string <=2000;
student_feedback_draft string|null <=4000; teacher_summary string|null <=4000;
needs_human_review bool; needs_human_review_reason string|null <=1000;
model_limitations string[] (each <=500, max 20); created_at UTC.
rubric_items[]: {rubric_item_id UUID, status met|partial|not_met|unclear,
 points_suggested Decimal|null, max_points Decimal, evidence[], confidence,
 limitations[]};
findings[]: {type enum, typical_error_id UUID|null, typical_error_code string|null,
 rubric_item_id UUID|null, skill_id UUID|null, evidence[], severity
 minor|major|critical, confidence}.
```

Evidence is an array (max 20) of typed objects `{kind, source, start?, end?, quote?,
value?}`; offsets address Unicode code points in the snapshotted student answer.
Student quote is allowed only when necessary, max 500 chars each/2000 total, treated
as sensitive, and never copied to ordinary events/logs. No HTML is trusted. Nullable
IDs must belong to snapshot allowlists. Finding type is versioned allowlist
(`typical_error`, `rubric_miss`, `answer_mismatch`, `format_problem`, `limitation`).

Consistency: correct=`max`; incorrect=`0`; partial strictly `(0,max)`; unclear may
carry a bounded provisional score only for valid completed rubric evidence but always
review; insufficient/manual have `score=null`. All LLM results require human review.
Reasons must be present iff review is true. Clamp is forbidden as silent repair:
out-of-range provider/checker result fails validation. Round only once, after scaling,
ROUND_HALF_UP to 0.01. Schema uses additive minor versions and breaking major versions;
rows retain schema/checker version and are never rewritten.

### 8.2 Scale decision

Frozen `assessment_item.points` is the sole `result.max_score`. For a rubric to be
valid, `rubric.max_score == sum(rubric_item.max_points) > 0`; mismatch is
`insufficient_rubric`, no score. Equality with assessment points is **not required**.
A valid rubric score `r` scales once as
`score_suggested = round_half_up(r / rubric.max_score * assessment_item.points, 0.01)`.
This preserves assessment composition. Deterministic boolean checkers use exactly
`0` or frozen max; authored choice partial uses the same normalized scale. LLM may
award only rubric-item points and application recomputes total/scale. Intermediate
Decimal precision is at least 28 significant digits; no intermediate rounding.

## 9. Logical PostgreSQL persistence model (design only)

All IDs UUID PK, timestamps `timestamptz` from DB clock, Decimals fixed NUMERIC,
FK updates RESTRICT. No student PII. JSONB is limited to immutable canonical snapshots,
validated structured documents, provider/settings payloads whose schema is versioned.

| Table | Key columns / constraints / indexes | Mutability and delete |
|---|---|---|
| `check_runs` | `id`; `submission_id UUID`; `handoff_version SMALLINT=1`; `input_snapshot JSONB`; `input_fingerprint CHAR(64)` hex; `snapshot_schema_version`; `routing_version`; `checker_set_version`; status enum; `attempt_no`, `retry_count >=0`, `requested_at`, `started_at`, `finished_at`, `heartbeat_at`, failure code/detail; `supersedes_run_id nullable`; UNIQUE `(submission_id, input_fingerprint, checker_set_version, attempt_no)`; partial UNIQUE submission where status pending/running; indexes status/requested, submission/created | Snapshot/fingerprint/versions immutable; status, heartbeat, retry/failure CAS mutable. Submission logical reference (no FK across ownership unless same DB contract chooses RESTRICT). Never delete; future retention tombstones sensitive blobs without falsifying event metadata only by policy. |
| `check_results` | `id`; `check_run_id FK RESTRICT`; assessment item/task version IDs; `checker_type/version`, `schema_version`; status; score/max/confidence; bounded summaries/reasons/limitations; `validated_result JSONB`; created_at; UNIQUE `(run,item)`; checks statuses/ranges/score consistency; indexes item/created, task_version, review | Entire row immutable after insert. No teacher fields. |
| `check_findings` | `id`; `check_result_id FK RESTRICT`; type, nullable source IDs/codes, severity, confidence, `evidence JSONB`; created_at; checks allowed types/ranges; indexes result, typical_error, skill | Immutable. IDs are historical references plus snapshot values; no cascade deletion. |
| `checker_events` | monotonic UUID/id; `run_id FK RESTRICT`, optional result/item; event type, from/to status, reason code, safe `details JSONB`, occurred_at; indexes run/time, type/time | Append-only immutable audit trail; details forbid raw answer/provider output. Status transitions recorded atomically. |
| `prompt_versions` | `id`; stable name, semantic version, template hash, output schema version, template encrypted/restricted text, created_at, retired_at; UNIQUE name/version/hash | Immutable content; retirement mutable. No student content. |
| `model_runs` | `id`; run/item; prompt_version FK RESTRICT; provider/model IDs; settings snapshot JSONB; request fingerprint; provider request ID; status/error taxonomy; attempt_no; timeout/latency; raw output restricted blob/text; validated output JSONB; validation errors JSONB; usage counts; timestamps; UNIQUE `(check_run_id,assessment_item_id,attempt_no)`; indexes status, provider request ID | One immutable attempt record finalized by CAS; raw/validated never ordinary logs. Reattempt inserts row. |
| `cost_events` | `id`; model_run FK RESTRICT; provider currency CHAR(3), input/output/cached tokens nonnegative, amount `NUMERIC(18,8)>=0`, pricing version/source, occurred_at; UNIQUE `(model_run_id,pricing_version)` | Append-only accounting estimate, no PII. |

Routing decision (chosen checker/reason/candidates) belongs in immutable per-item
snapshot/result plus a `routing_decided` event; checker/schema versions on result.
Low-confidence reasons live in result validation JSON and bounded columns. Raw provider
output is access-controlled and retained by configurable policy, not exposed via
Phase 4 API. A result is selected as current by query: latest terminal run under an
explicit requested checker-set version, ordered created/id—not a mutable `current`
flag. Phase 5 may FK its decision to result with RESTRICT but adds no table now.

## 10. Run/item lifecycle, idempotency, concurrency and retry

Run states: `pending → running`; running → `completed`,
`completed_with_review_required`, `failed_retryable`, or `failed_terminal`;
`failed_retryable → pending` while budget remains, else terminal. Completed/review/
failed_terminal are terminal. Recovery may transition stale running to
failed_retryable via CAS and event. There is no silent transition or reopening.

Item execution states are append-event plus result: pending→routed→executing→result;
executing may retry provider without a result. One item terminal failure produces a
manual/unclear review result where possible; other items continue. Run completes only
when every snapshot item has one immutable result, otherwise terminal infrastructure
failure. Zero-item submitted snapshots are invalid intake and terminal methodology/
contract failure (current published assessments should make this impossible).
Unanswered items complete deterministically without provider.

Partial provider failure retries only that model execution; exhausted failure becomes
manual review, allowing `completed_with_review_required`. Invalid JSON is retryable
once; timeout/network/429/5xx retryable; auth, unsupported model, schema invariant or
methodology failure non-retryable. Default provider timeout 30s; total 3 attempts
(initial +2) with full-jitter exponential delays capped at 30s. Worker owns retry;
provider adapter only classifies errors.

Multiple historical runs per submission are allowed. Exactly one active
(pending/running) run per submission is enforced by partial unique index and
transaction/advisory lock on submission UUID. Dedup scope is
`submission_id + input_fingerprint + checker_set_version + explicit request key`;
a replay of the same request returns the existing run, while an explicit rerun
creates incremented `attempt_no` and `supersedes_run_id`. New checker/prompt/model
version always creates a run (and version is in checker set/fingerprint metadata).

Workers execute at least once. Claim uses `SELECT ... FOR UPDATE SKIP LOCKED`, CAS
status/version and heartbeat; optional transaction advisory lock prevents concurrent
same-run work. DB insertion uniqueness gives exactly-once **persistence**, never
exactly-once provider calls. Provider response is first inserted/finalized as a
`model_runs` attempt in one transaction; result/event commit is idempotently resumed
after crash. Crash after external response but before DB commit may repeat the call;
request fingerprint/provider idempotency key is sent if supported, without claiming
guarantee. Stale heartbeat after 2× timeout+backoff is recoverable.

Cancelled worker behaves like crash; lease expiry recovers it. A terminal DB/schema
failure ends the run. Repeated valid methodology failure is not retried. Prompt/model
change never mutates past rows.

## 11. Provider port and LLM rubric contract

Application port, with no SDK types:

```text
LLMProvider.evaluate(ProviderRequest) -> ProviderResponse
ProviderRequest: provider_id, model_id, timeout_ms, settings_snapshot,
 prompt_version_id/hash, output_schema_version, JSON-schema constrained messages,
 request_fingerprint.
ProviderResponse: provider_request_id, raw_output, parsed_candidate,
 usage(input/output/cached tokens), latency_ms, finish_reason, provider metadata.
```

Settings are allowlisted (temperature, seed where supported, max output tokens) and
snapshotted. Structured JSON Schema output is mandatory. Error taxonomy:
`timeout`, `rate_limited`, `transport`, `provider_5xx`, `authentication`,
`invalid_request`, `content_blocked`, `invalid_json`, `schema_invalid`,
`semantic_invalid`, `unknown`; retry classification follows §10. Cost is computed
from versioned pricing metadata, not trusted provider prose.

Minimal LLM prompt contains only statement, raw/stored answer text needed, expected
solution/steps, explicit rubric/items, typed accepted alternatives, linked typical
errors and only necessary related skill names/IDs. It excludes name, student ID,
participant, group/class, actor, unrelated answers/tasks, communication history and
all unused DB fields.

Task/answer/methodology are delimited **untrusted data**, not instructions. System
prompt says never follow embedded instructions, execute code/tools, invent criteria,
change max points/final grade, assert errors without evidence, emit IDs outside input,
or treat missing data as incorrect. No model-generated command/code is executed.

Provider JSON undergoes: strict schema validation (additional properties rejected),
then ID allowlist, Decimal/range, rubric-total, evidence-offset, status/score and text
length validation. Unknown rubric/typical-error/skill ID makes attempt
`semantic_invalid`; retry once, then manual review—never silently delete. Insufficient
rubric is decided before provider. Malformed/exhausted response never falls back to a
different deterministic verdict; only a deterministic checker already provable may
run independently, otherwise manual. Output remains preliminary and review-required.

## 12. Privacy and security boundary

**Checking intake allowlist:** submission ID/time/handoff version; assessment item ID,
position/frozen points; task version ID, statement, task/answer format; expected
solution; accepted answers; rubric/items; linked typical errors; skills/weights;
raw/stored normalized answer; technical schema/version/hash timestamps. **Provider
allowlist** is the smaller prompt list in §11 plus opaque run/item correlation token
that is not a database/student ID.

Forbidden copying: student/participant/assignment/group IDs and names, display name,
external ref, actor/teacher IDs, contact data and unrelated content. Raw answer may
itself contain PII or injection: store only in restricted snapshot/result evidence,
redact from ordinary logs/events/errors/traces, and never place in cost telemetry.
Raw provider output has a separate least-privilege access boundary and configurable
retention; the retention duration is an operational Phase 7 decision, not a compliance
claim. Sensitive blob deletion, if later required, leaves a hash/tombstone and event.

Evidence/summary are plain text/typed JSON, escaped by future UI, never rendered as
trusted HTML, interpolated SQL, shell or template code. Parameterized SQL is required.
Prompt output cannot invoke tools. Phase 4 provides minimization and safe interfaces,
not production compliance: IAM, authorization, secrets, retention hardening and SLO
belong to Phase 7.

## 13. Immutability, history and confidence gate

Input snapshots, results, findings, model attempts, cost events and checker events are
immutable. Only run status/lease/retry fields and prompt retirement change, with CAS
and events. Correction is a new run/result; “latest” is a version-aware query.
Archiving task/version, closing assignment or authoring a new task version does not
alter a run. New checker/prompt/model/threshold creates new versioned execution.
Phase 5 override references, never overwrites, preliminary result.

Deterministic confidence is application-derived: `1.0000` only when input shape,
methodology and proof all validate; unsupported/ambiguous cases do not fabricate a
low numeric confidence—they become manual/insufficient. Numeric proximity inside a
valid inclusive boundary does not lower confidence. LLM confidence is a calibrated
quality-harness metric keyed by provider/model/prompt/schema/threshold version, not
model self-report. Start from calibrated band, then application penalties for
ambiguous evidence, criterion disagreement, truncation, borderline rubric allocation
and validator repair (repair is normally forbidden). Persist each penalty/reason.

Hard review: every LLM result in Phase 4; manual/insufficient/unclear; required rubric
item unclear; conflicting methodology; unknown IDs; provider retry/failure; score
near configured decision boundary; any non-1 deterministic confidence. Threshold
policy has semantic version in run. `manual_required` means safe checker capability
absent; `insufficient_rubric` means authored input inadequate; provider uncertainty is
`unclear`, never relabelled insufficient. Phase 4 LLM output is preliminary even at
high confidence.

## 14. Golden dataset and quality harness policy

Cases are synthetic, no real PII, stored as versioned JSON/YAML fixtures:
`case_id`, dataset/schema version, provenance/license, handoff+methodology snapshot,
expected routing/reason, expected status, exact/interval score, required and forbidden
finding predicates, confidence/review expectations, provider fixture identity. Raw
student-like text must be fabricated or explicitly licensed/de-identified.

Corpus categories: deterministic exact; numeric exact/absolute/relative/inclusive
boundaries/zero/alternatives/malformed; single/multiple choice/order/duplicates/extra/
unknown/no catalogue; expression canonical and manual equivalence; open rubric partial;
malformed provider JSON/invalid IDs/totals; insufficient rubric; low confidence;
archive/history; unanswered; retry/idempotency/crash replay.

Any checker/normalizer/prompt/model/JSON schema/confidence-threshold change requires a
version bump where semantics change, full deterministic regression, routing diff,
score/finding diff review and PostgreSQL idempotency/concurrency tests. Normalizer
changes never rewrite historical answers and require old-version fixtures. Prompt or
model promotion additionally requires a representative real-provider evaluation;
mock-provider PASS proves integration determinism only, never real-provider quality.

## 15. Phase boundaries

* **Phase 4:** preliminary checking persistence, intake snapshots, routing/checkers,
  provider port, confidence/findings/observability and golden harness.
* **Phase 5:** review queue UI, teacher accept/correct/reject, final status/score,
  override reason, bulk confirmation and teacher audit. No such tables/API in 4.x.
* **Phase 6:** aggregates, analytics and remediation/recommendations.
* **Phase 7:** IAM/production authorization, operational security, retention/compliance
  hardening, monitoring/SLO.

## 16. Implementation sequence after 4.0

A small **Content Bank methodology foundation must precede the checker that needs it**.
It need not block generic Checking DB Foundation 4.1 or intake 4.2: add a narrowly
scoped “4.1M” after 4.1/before 4.4 for typed accepted values/tolerances and authored
choice catalogue; do not mix Checking-owned tables into it.

| Prompt | Prerequisites and exact scope | Forbidden scope | Acceptance proof / decisions implemented |
|---|---|---|---|
| 4.1 Checking DB Foundation | This contract; migrations/models for runs/results/findings/events/prompt/model/cost, constraints/indexes only | intake, routes, workers/checkers/provider, teacher tables | upgrade/downgrade/check + PostgreSQL constraint/FK/immutability tests; D-01/02/03/09/14. |
| 4.1M Methodology minimum | Content Bank contract amendment; typed values, abs/rel tolerance, unit/policy codes, choice catalogue/scoring policy | checker logic, changing approved history silently | migration round trip, authoring/API validation and historical fixtures; D-05/06. |
| 4.2 Intake | 4.1; read ports, submitted guard, canonical snapshot/hash, ordered unanswered/history/privacy | Handoff/normalizer/lifecycle changes | unit golden serialization + PostgreSQL archive/close/concurrency test; D-01/02. |
| 4.3 Routing & Checker Protocol | 4.2; enums/ports/reason matrix/manual/insufficient | actual checker algorithms, LLM heuristics | exhaustive table-driven routing/unknown/conflict tests; D-04. |
| 4.4 Exact & Choice | 4.1M+4.3; strict exact, catalogue choice/set/scoring | fuzzy match, option-label inference | golden case matrix and no-solution-leak assertions; D-06/07. |
| 4.5 Numeric | 4.1M+4.3; Decimal parse, abs/rel inclusive formula, alternatives/evidence | float, conversion/free rule execution | boundary/property tests including zero/malformed; D-05. |
| 4.6 Expression & Manual Fallback | 4.3; identity canonical adapter and safe manual distinction | eval, home-grown algebra/CAS | malicious inputs and equivalent-but-unproved manual cases; D-08. |
| 4.7 Provider Boundary | 4.1+4.3; DTO port, adapter contract, attempts/retry/timeout/metadata validation | real grading policy, provider SDK in domain, teacher UI | fake adapter contract + retry/crash/idempotent persistence tests; D-10/11. |
| 4.8 LLM Rubric Checker | 4.7; allowlisted prompt, schema/semantic validation, rubric scoring | final grade, invented IDs/criteria, unrelated PII | adversarial injection/invalid ID/score fixtures and gated real-provider evaluation separately; D-11/12. |
| 4.9 Findings, Confidence & Observability | prior checkers; typed findings, calibrated gate reasons, safe events/cost metrics | analytics aggregates, production SLO, remediation | reason/status invariants, redaction and threshold-version regression; D-12/13. |
| 4.10 Golden Dataset & Vertical Acceptance | all above; corpus policy instantiated, PostgreSQL vertical/retry/history tests | claiming product quality from mocks, Phase 5 UI | full golden report, real-provider-labelled report when configured, migration/static/privacy gates; all decisions. |

## 17. Decision log

| ID | Question | Chosen decision | Rejected alternatives | Rationale / consequences | Prompt |
|---|---|---|---|---|---|
| D-01 | Snapshot or references? | Immutable materialized per-run snapshot plus provenance UUIDs | references only; extend Handoff | repeatability across mutable shared data; storage/privacy burden | 4.1/4.2 |
| D-02 | Rerun semantics? | New immutable run; exact request replay returns same, explicit/version change creates successor | overwrite; silently reuse latest data | audit/history | 4.1/4.2 |
| D-03 | Active uniqueness? | One pending/running per submission, partial unique + lock/CAS | unrestricted active runs | prevents duplicate workers while preserving history | 4.1 |
| D-04 | Router? | deterministic methodology-only precedence; single choice is multiple-choice mode | LLM heuristic; separate type | auditable stable checker enum | 4.3 |
| D-05 | Numeric tolerance/units? | Decimal; inclusive abs+rel formula; no conversions V1 | float; ambiguous tolerance; free unit conversion | needs 4.1M typed evolution; unsafe cases manual | 4.1M/4.5 |
| D-06 | Choice catalogue? | required version-scoped canonical option IDs | infer labels/current strings | current schema is blocker; safe fallback insufficient | 4.1M/4.4 |
| D-07 | Exact matching? | stored normalized text byte equality, explicit policy, case/space sensitive | fuzzy/casefold/hidden trim | predictable and no false proof | 4.4 |
| D-08 | Expressions? | explicit syntax canonical identity only; future CAS adapter; otherwise manual | eval/home algebra/claim equivalence | safety and correct epistemic status | 4.6 |
| D-09 | Result/version/history? | schema major/minor + checker version, immutable rows | mutable JSON/current flag | reproducibility and Phase 5 separation | 4.1 |
| D-10 | Provider retries? | 30s, initial+2 jittered retries; at-least-once call/exactly-once DB row | endless retry/exactly-once claim | bounded cost and honest guarantees | 4.7 |
| D-11 | Raw output retention? | restricted model_runs blob, configurable retention, never logs | discard immediately; ordinary logging | debugging/audit balanced with privacy; Phase 7 policy | 4.7/Phase 7 |
| D-12 | Confidence? | calibrated/versioned application gate with stored reasons; all LLM review | raw model number/high-confidence final | explainable preliminary result | 4.8/4.9 |
| D-13 | Score scaling? | validate rubric sum; scale Decimal once to frozen assessment points | require equal scales; alter points; unvalidated clamp | preserves published assessment | 4.8 |
| D-14 | Immutable history? | correction/new version always new run/result; Phase 5 FK only | update result/override in Phase 4 | complete audit and bounded ownership | 4.1/Phase 5 |

## 18. Explicit gaps and non-goals conclusion

`answer_value` is untyped; tolerance lacks absolute/relative distinction; unit and
normalization rule are free text; authored choice catalogue is absent; rubric and
assessment scales may differ; approval does not require accepted answers; and approved
methodology may still be insufficient for automation. These gaps are exposed as
`insufficient_rubric`/`manual_required`, never hidden behind LLM fallback.

This Prompt 4.0 changes documentation only. It creates no migration, ORM model,
repository/service/route, checker, provider, job, teacher override/review, analytics,
IAM, frontend behavior or dependency.
