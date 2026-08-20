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

### 5.1 Phase 4.3 application contract

The public pure entry point is `route_snapshot(snapshot) -> tuple[RoutingDecision,
...]`. It preserves the canonical item order, reads no repository/current version,
does not normalize an answer, execute a checker, calculate a score, or mutate its
input. Routing execution and persistence (`routing_decided` included) are explicitly
deferred to 4.4+. The supported versions are `checking_input_v1`, Handoff `1`, and
`checking_routing_contract_v1`. A well-shaped future version produces a manual
decision; a malformed envelope raises a typed, privacy-safe routing input error.

`RoutingDecision` is immutable and contains only `assessment_item_id`,
`task_version_id`, the supported routing-contract version, effective `checker_type`,
`candidate_checker_type`, `disposition`, primary `reason_code`, `unanswered`,
`execution_required`, and an ordered tuple of bounded diagnostics. It never contains
answers, statement, expected solution, rubric content, student/participant identity,
or other PII. `ready` uses the natural checker and requires later execution;
`unanswered` keeps that checker but requires no execution; `insufficient_rubric`
uses effective `manual_required` while retaining the natural candidate; and
`manual_required` denotes valid authored intent beyond the V1 safety boundary.

Natural mapping is `short_text` → `exact`, `number` → `numeric`, both choice formats
→ `multiple_choice`, `expression` → `structured_expression`, and `long_text` →
`llm_rubric`. Unanswered means **only** both frozen raw and normalized answers are
null. A format/type conflict remains insufficient even when unanswered. Exact typed
methodology takes precedence over a simultaneously valid rubric; short text falls
back to LLM only with both a valid points rubric and expected solution. Long text is
never exact. Unknown semantic formats and policies are never inferred.

The stable V1 primary reason allowlist is:

| Class | Reason codes |
|---|---|
| ready/unanswered | `routed_exact`, `routed_numeric`, `routed_choice`, `routed_expression_identity`, `routed_open_rubric`, `unanswered` |
| malformed/incompatible methodology | `malformed_snapshot`, `malformed_item`, `answer_format_mismatch`, `incompatible_task_answer_format`, `legacy_untyped_answer`, `missing_typed_accepted_answer`, `incompatible_accepted_answer_kind`, `missing_canonical_value`, `duplicate_canonical_alternative`, `invalid_numeric_tolerance`, `missing_choice_options`, `duplicate_choice_option`, `unknown_choice_option`, `invalid_single_choice_cardinality`, `missing_choice_scoring_policy`, `invalid_weighted_policy`, `missing_expression_identity_contract`, `missing_expected_solution`, `missing_or_empty_rubric`, `rubric_max_items_mismatch`, `contradictory_methodology` |
| valid but outside V1 | `unknown_answer_format`, `unsupported_contract_version`, `unsupported_normalization_policy`, `unsupported_unit`, `semantic_text_judgment_required`, `expression_equivalence_required`, `unsupported_grading_mode`, `outside_v1_capability` |

Every code is a trim-nonblank string of at most 64 characters and carries no authored
or student content. Primary reason selection and diagnostic ordering are deterministic.
The transport-neutral async `Checker` protocol has stable `checker_type` and
`checker_version`, accepts an immutable snapshot-item/decision request, and returns a
typed result draft with `correct`, `partially_correct`, `incorrect`, `unclear`,
`insufficient_rubric`, or `manual_required`. Phase 4.4 adds the application-only
`exact_v1` and `choice_v1` implementations and structured result schema `1.0`.
The application draft intentionally includes `unclear`; the current PostgreSQL enum
does not accept it. Persistence compatibility remains deferred to the later phase
that persists outcomes, so Phase 4.4 is not persisted end-to-end checking.

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

Phase 4.4 executes these rules only from the immutable item and its Phase 4.3
`RoutingDecision`. Exact uses Python string equality and performs no runtime trim,
case folding, NFC, newline conversion, whitespace collapsing, fuzzy comparison, or
other normalization. All-or-nothing choice compares sets against OR alternatives.
For a mismatch, technical evidence references the alternative with the smallest
symmetric difference, breaking ties by canonical accepted-answer UUID.

Version-1 per-option multiple-choice scoring first gives an exact accepted-set match
the frozen maximum. Otherwise it computes `fraction = sum(selected rule weights)`,
then `bounded_fraction = min(1, max(0, fraction))`, and finally applies
`ROUND_HALF_UP(bounded_fraction * frozen points, 0.01)` exactly once, with no
intermediate rounding. The rounded score determines correct, incorrect, or partial.
An unknown snapshotted option UUID produces `unclear` with review, never an ordinary
incorrect verdict. Unanswered routing produces incorrect, zero, confidence `1.0000`
without invoking any checker or provider. Insufficient/manual routing similarly
builds a review result without checker execution.

Deterministic evidence contains only bounded routing codes, canonical technical
UUIDs, counts, policy version/mode, and plain Decimal strings. It excludes raw and
normalized answer text, accepted text, statements, solutions, option labels/content,
and student, participant, assignment, group, or class identity. Student feedback
does not reveal accepted values or missing correct option IDs. Phase 4.4 performs no
result persistence, event transition, provider call, worker orchestration, numeric,
expression, or LLM execution; ready routes for those future checkers fail with a
typed privacy-safe unsupported-execution error.

## 7. Numeric and structured-expression contracts

Phase 4.5 implements an application-only `NumericChecker` (`numeric_v1`) over the
immutable snapshot and Phase 4.3 decision. It reads the student value exclusively
from the already persisted `normalized_answer.decimal` and authored values exclusively
from `accepted_answers[].canonical_decimal`; it never rereads a repository or reruns
normalization. The exact formula is `delta = abs(a-e)`,
`threshold = absolute_tolerance + relative_tolerance * abs(e)`, with a match when
`delta <= threshold` (inclusive). A null tolerance is exactly zero. Each alternative
has its own tolerances, and all arithmetic uses a per-comparison local Decimal context
sized from its operands, without float conversion, intermediate rounding, silent
clamping, or reliance on the default precision of 28.

Any matching alternative produces the frozen maximum; numeric checking has no partial
score. Multiple matches select minimum delta then canonical accepted-answer UUID.
Mismatch evidence selects minimum positive `delta-threshold`, then delta, then UUID,
so accepted-answer order cannot affect canonical serialization. Malformed historical
student values produce `unclear` and review; malformed numeric methodology produces
`insufficient_rubric` and review. Units remain `manual_required`; a defensively forced
ready unit-bearing request fails closed. Free-form `normalization_rule` remains inert.

Numeric evidence is limited to `actual_decimal`, `alternatives_checked`, the compared
technical accepted-answer UUID, matched UUID for a match, `delta`, `threshold`, and
the selected alternative's absolute and relative tolerances. Decimal evidence is
plain, canonical, arbitrary-precision text. It excludes accepted decimal values, raw
answers, statements, solutions, other alternatives, labels, legacy fields, and user,
class, assignment, or group identity. Feedback never reveals the expected value or
tolerance boundary. Phase 4.5 does not persist results in PostgreSQL; the persistence
compatibility gap for `unclear` remains deferred. Expression and LLM execution, units,
Teacher Review, and orchestration remain for later phases.

Numeric uses Python/PostgreSQL-equivalent arbitrary precision `Decimal`, never binary
float. Student source is `normalized_answer.decimal`, whose grammar/canonicalization
is the existing Phase 3 normalizer; accepted source is the typed decimal field only.
Legacy `answer_value` is not correctness truth. Non-finite/malformed
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
| `check_runs` | `id`; `submission_id UUID NOT NULL` FK `student_submissions.id` `ON DELETE RESTRICT ON UPDATE RESTRICT`; `request_key VARCHAR(128) NOT NULL`; `request_hash CHAR(64) NOT NULL` with CHECK `request_hash ~ '^[0-9a-f]{64}$'`; `handoff_version SMALLINT=1`; `input_snapshot JSONB`; `input_fingerprint CHAR(64)` hex; `snapshot_schema_version`; `routing_version`; `checker_set_version`; threshold/prompt/model policy versions; status enum; `attempt_no`, `retry_count >=0`, `requested_at`, `started_at`, `finished_at`, `heartbeat_at`, failure code/detail; `supersedes_run_id nullable`; UNIQUE `(submission_id, request_key)`; partial UNIQUE submission where status pending/running; composite indexes `(submission_id, requested_at DESC, id DESC)` for history and `(submission_id, status)` for active lookup (the request-key UNIQUE index serves idempotency lookup); index status/requested | Snapshot/fingerprint/request identity/versions immutable; status, heartbeat, retry/failure CAS mutable. Assessment and Checking share one PostgreSQL schema: the FK proves source existence and prevents deletion required by history without transferring lifecycle ownership or copying PII. Intake still validates `submitted` status in application code; FK is not readiness validation. Never delete; future retention tombstones sensitive blobs without falsifying event metadata only by policy. |
| `check_results` | `id`; `check_run_id UUID NOT NULL` FK `check_runs.id` `ON DELETE RESTRICT ON UPDATE RESTRICT`; `assessment_item_id UUID NOT NULL` FK `assessment_items.id` `ON DELETE RESTRICT ON UPDATE RESTRICT`; `task_version_id UUID NOT NULL` FK `task_versions.id` `ON DELETE RESTRICT ON UPDATE RESTRICT`; `checker_type/version`, `schema_version`; status; score/max/confidence; bounded summaries/reasons/limitations; `validated_result JSONB`; created_at; UNIQUE `(check_run_id, assessment_item_id)`; checks statuses/ranges/score consistency; indexes item/created, task_version, review | Entire row immutable after insert. Application validator requires both provenance IDs to equal the corresponding item in immutable run snapshot and forbids an item absent from it. Snapshot remains execution truth; FKs provide provenance integrity and never authorize re-reading mutable/latest methodology. No teacher fields. |
| `check_findings` | `id`; `check_result_id UUID NOT NULL` FK `check_results.id` `ON DELETE RESTRICT ON UPDATE RESTRICT`; type; nullable `rubric_item_id`, `typical_error_id`, `skill_id` provenance UUIDs **without FK**; snapshot code/title/criterion fields in bounded structured finding data; severity, confidence, `evidence JSONB`; created_at; checks allowed types/ranges; indexes result and provenance UUIDs | Immutable and self-contained. Application validator requires every nullable provenance UUID to belong to the result/run snapshot allowlist. Content Bank evolution cannot break finding history; no cascade deletion. |
| `checker_events` | monotonic UUID/id; `run_id FK RESTRICT`, optional result/item; event type, from/to status, reason code, safe `details JSONB`, occurred_at; indexes run/time, type/time | Append-only immutable audit trail; details forbid raw answer/provider output. Status transitions recorded atomically. |
| `prompt_versions` | `id`; stable name, semantic version, template hash, output schema version, template encrypted/restricted text, created_at, retired_at; UNIQUE name/version/hash | Immutable content; retirement mutable. No student content. |
| `model_runs` | `id`; `check_run_id UUID NOT NULL` FK `check_runs.id` RESTRICT; `assessment_item_id UUID NOT NULL` FK `assessment_items.id` RESTRICT; `prompt_version_id UUID NOT NULL` FK `prompt_versions.id` RESTRICT; nullable `check_result_id UUID` FK `check_results.id` RESTRICT (all FK use `ON DELETE RESTRICT ON UPDATE RESTRICT`); provider/model IDs; settings snapshot JSONB; request fingerprint; provider request ID; status/error taxonomy; attempt_no; timeout/latency; raw output restricted blob/text; validated output JSONB; validation errors JSONB; usage counts; timestamps; UNIQUE `(check_run_id,assessment_item_id,attempt_no)`; indexes status, provider request ID | One immutable attempt record finalized by CAS; raw/validated never ordinary logs. A failed/invalid provider attempt exists without a result, so `check_result_id` is nullable; a successful later result may be linked without erasing the attempt. Reattempt inserts row. |
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
transaction/advisory lock on submission UUID. V1 idempotency identity is exactly
`(submission_id, request_key)`, where the client/orchestrator supplies a 1..128 byte
request key scoped to that submission. `request_hash` is lowercase SHA-256 hex over
the canonical run request containing `submission_id`, `input_fingerprint`,
`checker_set_version`, routing version, threshold-policy version, requested
prompt/model policy when selected by the request, and explicit rerun intent/attempt
semantics. Canonicalization follows §4.

The same `(submission_id, request_key)` and same hash returns the existing run;
the same key with a different hash returns an idempotency conflict. An explicit
rerun must use a new request key and creates the next historical `attempt_no` with
`supersedes_run_id`. A new checker, prompt, model, routing or threshold version does
not bypass reuse of an old key: it changes the hash and therefore conflicts until a
new key is supplied. `attempt_no` is only the monotonically assigned historical run
ordinal for a submission, not an idempotency key. Concurrent claims rely on UNIQUE
`(submission_id, request_key)` plus transactional insert/conflict handling; the
active-run constraint is independently enforced. The request key provides no
exactly-once external provider-call guarantee.

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
| 4.1 Checking DB Foundation | This contract; migrations/models for runs/results/findings/events/prompt/model/cost, exact request-key/hash and provenance FKs, constraints/indexes only | intake, routes, workers/checkers/provider, teacher tables; re-deciding request identity/FK policy | upgrade/downgrade/check plus PostgreSQL tests: same key+hash replays one run; same key+different hash conflicts; concurrent same key creates one row; missing submission FK and deletion of source submission are rejected; invalid assessment item/task version FKs and duplicate run/item result are rejected; application validator rejects finding provenance outside snapshot; failed model attempt persists with null result; immutability tests; D-01/02/03/09/14/15/16. |
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
| D-02 | Rerun semantics? | New immutable run requires a new request key; exact same-key/hash replay returns existing run; version/policy change with reused key conflicts rather than silently creating | overwrite; silently reuse latest data; version bypass of idempotency | audit/history and unambiguous caller intent | 4.1/4.2 |
| D-03 | Active uniqueness? | One pending/running per submission, partial unique + lock/CAS | unrestricted active runs | prevents duplicate workers while preserving history | 4.1 |
| D-04 | Router? | deterministic methodology-only precedence; single choice is multiple-choice mode | LLM heuristic; separate type | auditable stable checker enum | 4.3 |
| D-05 | Numeric tolerance/units? | Decimal; inclusive abs+rel formula; no conversions V1 | float; ambiguous tolerance; free unit conversion | needs 4.1M typed evolution; unsafe cases manual | 4.1M/4.5 |
| D-06 | Choice catalogue? | required version-scoped canonical option IDs | infer labels/current strings | current schema is blocker; safe fallback insufficient | 4.1M/4.4 |
| D-07 | Exact matching? | stored normalized text byte equality, explicit policy, case/space sensitive | fuzzy/casefold/hidden trim | predictable and no false proof | 4.4 |
| D-08 | Expressions? | explicit syntax canonical identity only; future CAS adapter; otherwise manual | eval/home algebra/claim equivalence | safety and correct epistemic status | 4.6 |
| D-09 | Result/version/history? | schema major/minor + checker version, immutable rows; RESTRICT provenance FKs plus snapshot-consistency validation | mutable JSON/current flag; latest methodology lookup | reproducibility, provenance integrity and Phase 5 separation | 4.1 |
| D-10 | Provider retries? | 30s, initial+2 jittered retries; at-least-once call/exactly-once DB row | endless retry/exactly-once claim | bounded cost and honest guarantees | 4.7 |
| D-11 | Raw output retention? | restricted model_runs blob, configurable retention, never logs | discard immediately; ordinary logging | debugging/audit balanced with privacy; Phase 7 policy | 4.7/Phase 7 |
| D-12 | Confidence? | calibrated/versioned application gate with stored reasons; all LLM review | raw model number/high-confidence final | explainable preliminary result | 4.8/4.9 |
| D-13 | Score scaling? | validate rubric sum; scale Decimal once to frozen assessment points | require equal scales; alter points; unvalidated clamp | preserves published assessment | 4.8 |
| D-14 | Immutable history? | correction/new version always new run/result; Phase 5 FK only | update result/override in Phase 4 | complete audit and bounded ownership | 4.1/Phase 5 |
| D-15 | Run idempotency identity? | UNIQUE `(submission_id, request_key)`; canonical request SHA-256 hash; same hash replay, different hash conflict; rerun needs new key; `attempt_no` is history only | fuzzy compound dedup; version bypass; attempt number as key | exact concurrency-safe replay semantics without claiming exactly-once provider calls | 4.1/4.2 |
| D-16 | DB and finding provenance? | submission/result/model core IDs use explicit RESTRICT FKs; result IDs must match snapshot; finding Content Bank IDs are nullable no-FK snapshot provenance validated by application | optional submission FK; latest methodology reads; cascading/content-row FKs for findings | source integrity plus immutable, self-contained execution history across Content Bank evolution | 4.1/4.2 |

## 18. Explicit gaps and non-goals conclusion

`answer_value` is untyped; tolerance lacks absolute/relative distinction; unit and
normalization rule are free text; authored choice catalogue is absent; rubric and
assessment scales may differ; approval does not require accepted answers; and approved
methodology may still be insufficient for automation. These gaps are exposed as
`insufficient_rubric`/`manual_required`, never hidden behind LLM fallback.

This Prompt 4.0 changes documentation only. It creates no migration, ORM model,
repository/service/route, checker, provider, job, teacher override/review, analytics,
IAM, frontend behavior or dependency.

## 13. Typed Methodology Foundation (Phase 4.1M)

Content Bank now authors checking truth explicitly and per task version. Legacy
`answer_value`, `tolerance`, `unit`, and `normalization_rule` remain unchanged and
non-executable; existing rows are `legacy_untyped` and are never promoted by the
migration. Typed alternatives use `text`, `decimal`, `expression`, or `choice_set`
with allowlisted version-1 policies (`exact_text_v1`, `decimal_v1`, and
`expression_identity_v1`). Decimal values and absolute/relative tolerances are
arbitrary-precision finite values; numeric comparison is specified as
`abs(actual - expected) <= abs_tol + rel_tol * abs(expected)`. The default
absolute and relative tolerance authored for a decimal is zero. Canonical decimal
API output is a plain base-10 string; negative zero is stored as zero.

Choice option IDs are canonical checking identities. Stable `option_key` exists
only as an authoring reference. Accepted sets are relational, non-empty,
version-safe through composite foreign keys, and use OR semantics between accepted
answer rows. `single_choice` requires one member and `multiple_choice` at least one.
The versioned scoring policy is either `all_or_nothing` or explicit `per_option`;
its weights are authored Decimal values. No checker, inference from labels/keys,
unit conversion, free-text normalization, evaluation, or CAS is introduced here.
A canonical `unit_code` only records authored identity and can still require manual
checking until input-unit support exists.

## Phase 4.6 — structured expression identity boundary

Phase 4.6 adds the application-layer `structured_expression` checker version
`expression_identity_v1`. It consumes the frozen Phase 3 normalized mapping
`{"expression": <string>}` exactly as stored. Phase 3 NFC, newline conversion, and
outer trimming are the only preprocessing: the checker does not trim, rewrite,
tokenize, parse, simplify, evaluate, case-fold, or otherwise canonicalize text.
Python string equality is the sole V1 correctness proof. One or more typed
expression alternatives are OR alternatives; a proven match receives the frozen
maximum score, and canonical accepted-answer UUID ordering makes defensive match
selection deterministic.

The transport-neutral `ExpressionEquivalenceAdapter` has only two bounded proof
outcomes: `proven_equivalent` and `unproven`. The production adapter is pure,
identity-only, supports only policy `expression_identity_v1` version `1`, and has
no database, provider, process, network, CAS, or LLM dependency. It cannot report
non-equivalence. Consequently every non-identical expression is
`manual_required`, never `incorrect` or `partially_correct`: automatic identity
comparison could not establish equivalence and a human must review it. A malformed
historical normalized value is `unclear`; malformed authored expression
methodology is `insufficient_rubric`. Unsupported or future policies remain at the
existing routing fallback and never invoke the checker. Unanswered expressions
retain their natural checker identity but bypass checker execution.

Expression strings must be non-empty, at most 60,000 Python characters, valid
UTF-8/JSON data, and fit the existing 65,536-byte compact JSON boundary before an
adapter is invoked. Accepted IDs and canonical strings must be unique and accepted
IDs canonical UUIDs. Legacy `answer_value` and free-form `normalization_rule` are
inert. Evidence is immutable and limited to policy code/version, checker/adapter
version, alternatives checked, bounded proof status, and—only for a proven
identity—the matched accepted-answer UUID. It excludes student and accepted
expressions, other alternatives, statements, solutions, rubric/option content,
student/participant/assignment/class identities, and arbitrary exceptions.
Feedback never reveals an accepted expression.

This phase adds no persistence, schema/migration, orchestration, provider or LLM
execution. A future sandboxed CAS adapter is deferred and is not represented as
implemented. Phase 4.7's provider boundary is also deferred, as are result
persistence/orchestration and resolution of the existing `unclear` compatibility
gap. Phase 4.6 produces only the existing schema `1.0` application result draft;
it does not perform official or final grading.

## Phase 4.7 — versioned LLM provider boundary

Phase 4.7 introduces an application-owned, transport-neutral provider port. Frozen
request/response DTOs contain only allowlisted technical provider data; no SDK type,
Checking run/item identifier, submission identity, actor identity, snapshot, or arbitrary
provider metadata crosses the port. The sole prompt in this phase is the synthetic
provider-contract probe. Its exact UTF-8 template bytes are SHA-256 hashed and its stable
name, semantic version, template hash, and output-schema version form immutable prompt
identity. Retired prompts cannot start new executions but remain valid history for replay.

Settings are limited to a finite plain Decimal-string temperature, bounded integer seed,
and positive bounded output-token limit. Canonical JSON rejects floats, coercion, nested
mutable settings, unknown keys, and unsupported objects. The lowercase SHA-256 request
fingerprint covers provider/model IDs, 30,000 ms timeout, detached settings, all prompt and
strict-schema identity, and exact ordered system/user message text. Attempt and database
IDs are excluded. Its deterministic `sha256:<fingerprint>` token is advisory provider
correlation/idempotency, not an exactly-once claim.

Responses must be exactly one JSON object parsed by ordinary `json.loads`: fences, prose
extraction, repair, coercion, partial parsing, additional fields, and wrong primitive types
are rejected. A supplied parsed candidate must be canonically identical. Malformed JSON
is `invalid_json`; strict Pydantic schema failure is `schema_invalid`; Phase 4.8 alone may
define `semantic_invalid`. The persisted validated envelope contains schema version,
validated candidate, and bounded finish metadata.

Each attempt has an application-enforced 30-second timeout. Timeout, transport,
rate-limit, and provider-5xx failures permit three total attempts; invalid JSON permits
two; authentication, invalid request, content block, schema/semantic invalidity, and
unknown errors permit one. Full-jitter exponential backoff is capped at 30 seconds and
clock, jitter, and sleep are injectable. A short transaction registers/loads the prompt
and claims a `running` attempt, then commits before the external call. A second short
transaction CAS-finalizes it and atomically appends optional cost. Retries claim the next
contiguous row. Provider calls are at least once while attempt rows are persisted exactly
once: a crash before finalization can repeat an external call. A running row is reported
for worker recovery rather than duplicated; terminal success or exhaustion replays
without a call; a changed fingerprint conflicts. Leases, stale-attempt recovery, queues,
workers, and polling remain deferred orchestration work.

Raw output is sensitive and is stored only in `model_runs.raw_output`; it is excluded from
events, cost telemetry, exceptions, logs, traces, and HTTP APIs. Retention duration is a
configurable Phase 7 policy and is intentionally not invented here. Usage is validated
locally. Versioned local pricing uses Decimal token rates and rounds only the final amount
to `NUMERIC(18,8)` with `ROUND_HALF_UP`; provider monetary prose is ignored. Phase 4.9 and provider SDK integration also remain deferred.

### Phase 4.7 corrective persistence acceptance

The persistence-only `ProviderExecutionKey` carries the CheckRun and assessment-item UUIDs
into the attempt store; it is intentionally absent from `ProviderRequest` and can never
reach `LLMProvider.evaluate`. `SQLAlchemyProviderAttemptStore` uses an
`async_sessionmaker` to open and close a fresh transaction for `replay_or_claim`, returns
an explicit `claimed`, `running_existing`, or `terminal_existing` disposition, and closes
that transaction before the service invokes the provider. Finalization uses a second fresh
transaction for the status CAS and optional cost event. UUID attempt identity is never
encoded with control-flow string prefixes.

All schema, parsed-candidate, validated-envelope, attempt, and outcome JSON is recursively
detached and frozen; persistence explicitly thaws it to fresh plain JSONB-compatible
objects. Canonicalization continues to reject floats and unsupported values without
rewriting strings. The application measures each provider call with its injected monotonic
clock, rejects non-finite, Boolean, or backward readings, converts elapsed time to
nonnegative integer milliseconds, and overrides adapter-reported latency before
persistence. These corrections do not introduce rubric prompting, semantic grading,
results, findings, workers, routes, or any Phase 4.8 behavior.


## Phase 4.8 LLM rubric application checker

`llm_rubric_v1` runs only for a READY `llm_rubric` decision and calls the Phase 4.7 boundary through `ProviderExecutionService`. Its fixed `llm_rubric_output_v1` contract forbids extra properties, numeric score values, model confidence, totals, outcomes, review decisions, and grades. The canonical user message contains only the statement, frozen normalized answer text, expected solution, typed accepted alternatives, authored rubric, linked typical errors and skills, and interpretation versions. Persistence/run/item/task/person/assignment identities are excluded. All message content is explicitly untrusted JSON data.

Application validation requires every authored rubric item once and in order; checks canonical Decimal allocations, status consistency, provenance allowlists, and bounded answer-slice evidence; and sorts findings by technical identity. Typical-error and skill provenance is re-derived from the frozen snapshot. Invalid output or exhausted provider failure produces a safe `unclear` draft, while a running attempt produces a typed nonterminal signal.

The application scales exactly once: `ROUND_HALF_UP(rubric_score / rubric.max_score * assessment_item.points, 0.01)`. It uses Decimal with no intermediate rounding. An unclear item suppresses the score. Confidence comes only from an immutable versioned application policy, and every evaluated LLM draft requires review with `llm_human_review_required`; it is never a final grade. Phase 4.9 remains deferred.

## Граница с AI Content Authoring Phase 4A

Checking Engine потребляет точную immutable methodology исторической
`approved` Content Bank task version. Он никогда не создаёт задания, не
додумывает отсутствующие expected answers или rubric, не исправляет неполную
methodology при проверке ответа ученика и не изменяет Content Bank.

[AI Content Authoring v1](ai-content-authoring-v1-contract.md) — отдельный,
принадлежащий Content Bank трек Phase 4A. Authoring sessions, previews и
provider attempts authoring не используют и не перегружают Checking-owned
check runs, checker results, findings, events или provider attempts. Между
Content Bank authoring и Checking application services нет семантической
зависимости.

Это дополнение не меняет реализованные поведенческие контракты Phases 4.0–4.8.
Phase 4.9 и Phase 4.10 остаются Checking-owned. Общая Phase 4 закрывается лишь
после двух независимых gates: Checking Engine Phase 4.10 acceptance и AI
Content Authoring Phase 4A.6 acceptance; ни один gate не заменяет другой.

## Phase 4.9 — Checking findings, confidence gate, and observability (implemented)

Phase 4.9 converts checker drafts into immutable preliminary results. Finding types and severities are application-owned: rubric misses use the frozen required flag, typical errors use the exact authored severity and linked skill, and general mismatch/format/limitation findings use fixed V1 severities. Rubric, typical-error, and skill UUIDs must be members of the exact item provenance allowlists. A generic wrong answer never creates a typical-error finding, and provider messages are excluded from technical evidence.

The confidence gate is an injected immutable Decimal policy whose semantic version must equal the run's frozen threshold-policy version. It persists the application base, effective confidence, ordered reasons and penalties, total penalty, review decision, and bounded review reason. Deterministic proofs and unanswered results have confidence `1.0000`; manual-required, insufficient-rubric, and `unclear` results have `0.0000`. Missing rubric evidence, model limitations, and borderline partial scores are each penalized at most once, with one half-up four-place quantization and an explicit zero-floor reason. Every LLM result requires review; the gate can strengthen but cannot weaken the checker decision. This is not real-provider calibration; calibration remains Phase 4.10 work.

Batch finalization locks the run, validates one draft for every frozen item in snapshot order, prepares the entire batch before inserts, records one result event per item, associates same-run/same-item terminal LLM attempts once, and completes the run atomically. Exact completed-run replay returns existing observability; changed or concurrent losing replays conflict and never reopen a completed run. The model-attempt trigger permits only the terminal `NULL` to exact-result association and continues to reject deletion, reassignment, clearing, terminal rewrites, and request/output/usage/cost changes.

Safe result events contain only checker type, status, bounded reason, canonical confidence, review Boolean, and finding count. Per-run observability (`checking_observability_v1`) contains run/status/policy identity, item/result/review/finding counts, deterministic counts by status/checker/reason, model-attempt status/retry/latency/token totals, and Decimal cost totals grouped by currency/pricing version/source. It excludes snapshots, answers, rubric or solution prose, finding content, evidence, summaries, feedback, raw output, provider request IDs, and person/course/assignment identities. It is technical visibility, not a public API or analytics product.

Migration `20260819_01` resolves the database/application gap by adding `unclear` to `checking_result_status`, aligning its score constraint, and adding reason and versioned confidence metadata. Upgrade backfills legacy rows from each owning run. Downgrade replaces the enum safely and explicitly refuses, without rewriting or deleting history, if `unclear` rows exist. Phase 4.10 golden-dataset quality and calibration remain pending; this phase adds no final grades, Teacher Review, frontend/API, worker, remediation, provider SDK, or Phase 4A.1 behavior.

### Phase 4.9 corrective migration `20260820_01`

Revision `20260820_01` restores `trg_model_runs_guard` after the published Phase 4.9 revision recreated `checking_guard_model_run()` without recreating its table trigger. The corrective head makes the existing one-time terminal result association rule effective again and rejects model-attempt deletion, result unlinking, and result reassignment. Its downgrade removes only the restored trigger; it does not rewrite model-attempt history or alter the guard function.
