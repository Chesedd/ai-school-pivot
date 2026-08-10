# Assessment Core — implementation contract Phase 3.0

> **Статус:** проектный контракт v1, 2026-08-08. Это единственный источник
> доменной семантики для следующих подфаз Assessment Core. Таблицы, модели и
> endpoints ниже **ещё не реализованы**. Alembic head на момент проектирования —
> `20260808_01`.

## 1. Назначение и границы bounded context

Assessment Core создаёт работы и варианты, публикует их назначением группе,
фиксирует участников и вариант ученика, управляет попытками, draft-ответами и
submit. Он является source of truth для `assessment*`, pilot-групп/учеников,
назначений, попыток, ответов и собственного audit.

Content Bank остаётся source of truth для карточки и содержания задания,
`answer_format` и lifecycle `task_version`. Assessment не копирует это как
изменяемый content: `assessment_items.task_version_id` — историческая ссылка на
конкретную версию. Для нового item и в момент publication требуются одновременно
неархивная task и `task_version.status = approved`. После publication архивирование
не инвалидирует чтение, start, submit или будущую проверку.

Checking Engine позже читает frozen item/version и submitted answer, но не меняет
их; correctness, score, teacher review и analytics здесь отсутствуют. Pilot
`class_groups`/`students` — не School Core и не IAM. Authentication/RBAC здесь не
симулируются. Ни одно правило не вызывает AI provider; модуль работает без него.

## 2. Термины

| Термин | Точное значение |
| --- | --- |
| assessment (работа) | Авторский aggregate metadata и одного или нескольких вариантов; до publication редактируем. |
| assessment variant | Именованный упорядоченный состав items одной работы. |
| assessment item | Позиция варианта с FK на конкретную `task_version` и положительными points. |
| published/frozen composition | Assessment в `published`; metadata, variants, item FK/order/points неизменяемы. |
| assignment (назначение) | Окно прохождения опубликованной работы одной pilot-группой с лимитом attempts. |
| assignment participant | Снимок конкретного student в момент publication и место хранения навсегда закреплённого variant. |
| class group | Минимальная pilot-группа; не роль и не security principal. |
| student | Минимальная pilot-запись ученика и будущая ссылка на identity. |
| attempt / student submission | Нумерованное прохождение одним participant; `draft` до submit, `submitted` после. |
| student answer | Единственный текущий ответ на item внутри submission. |
| draft attempt | Изменяемая незавершённая submission. |
| submitted attempt | Неизменяемый снимок ответов с `submitted_at`. |
| assigned variant | `assignment_participants.assigned_variant_id`, выбранный атомарно при первом успешном start и используемый во всех attempts. |
| raw answer | Точный JSON-ввод клиента, сохранённый без семантического преобразования. |
| normalized answer | Производное консервативное JSON-представление для Checking Engine; не verdict. |

## 3. Минимальная ERD / data contract

Общие conventions: PostgreSQL UUID PK/FK, `gen_random_uuid()` server default;
`TIMESTAMPTZ` и `clock_timestamp()`; API UUID — canonical string, время — RFC 3339
UTC с `Z`. Все FK `ON UPDATE RESTRICT`. Неуказанные delete rules — `RESTRICT`:
исторические business rows физически не удаляются.

### 3.1 Таблицы

| Таблица | Колонки и constraints | Индексы / delete semantics |
| --- | --- | --- |
| `class_groups` | `id UUID PK`; `name VARCHAR(120) NOT NULL CHECK(trim length 1..120)`; `external_ref VARCHAR(120) NULL`; `created_at TIMESTAMPTZ NOT NULL server`; `created_by UUID NOT NULL`; `archived_at TIMESTAMPTZ NULL`; UNIQUE partial `external_ref WHERE external_ref IS NOT NULL` | `ix_class_groups_active(archived_at,id)`; referenced assignments `RESTRICT`. |
| `students` | `id UUID PK`; `class_group_id UUID NOT NULL FK`; `display_name VARCHAR(120) NOT NULL CHECK(trim length 1..120)`; `external_ref VARCHAR(120) NULL`; `created_at` server; `archived_at NULL` | UNIQUE `(class_group_id,external_ref)` partial non-null; index `(class_group_id,archived_at,id)`; group delete `RESTRICT`. No email/phone/birthdate. |
| `assessments` | `id UUID PK`; `title VARCHAR(200) NOT NULL CHECK(trim length 1..200)`; `description TEXT NULL CHECK(length<=4000)`; `status assessment_status NOT NULL DEFAULT draft`; `created_by UUID NOT NULL`; `created_at`, `updated_at` server; `published_at NULL`; `published_by UUID NULL`; CHECK published fields both null or both non-null; CHECK status/published consistency | index `(status,created_at DESC,id)`; no physical delete after references. `updated_at` changes on draft mutation. |
| `assessment_variants` | `id UUID PK`; `assessment_id UUID NOT NULL FK`; `name VARCHAR(80) NOT NULL CHECK(trim length 1..80)`; `position SMALLINT NOT NULL CHECK(position>0)`; `created_at` server | UNIQUE `(assessment_id,position)` and `(assessment_id,name)`; index redundant only by PK/uniques; delete `CASCADE` is allowed only while parent draft, enforced application/lock. |
| `assessment_items` | `id UUID PK`; `variant_id UUID NOT NULL FK`; `task_version_id UUID NOT NULL FK task_versions`; `position INTEGER NOT NULL CHECK(position>0)`; `points NUMERIC(8,2) NOT NULL CHECK(points>0 AND points<=999999.99)`; `created_at` server | UNIQUE `(variant_id,position)`; UNIQUE `(variant_id,task_version_id)` (one version once per variant); index `(task_version_id)`; variant delete `CASCADE` draft-only, task version delete `RESTRICT`. |
| `assignments` | `id UUID PK`; `assessment_id UUID NOT NULL FK`; `class_group_id UUID NOT NULL FK`; `status assignment_status NOT NULL DEFAULT open`; `start_at TIMESTAMPTZ NOT NULL`; `due_at TIMESTAMPTZ NOT NULL`; `max_attempts SMALLINT NOT NULL DEFAULT 1`; `created_at` server; `created_by UUID NOT NULL`; `closed_at NULL`; `closed_by NULL`; CHECK `start_at < due_at`; CHECK `1<=max_attempts<=100`; CHECK close fields consistent with status | **Нет UNIQUE по `assessment_id`**: assessment исторически имеет 0..N assignments. Indexes `(assessment_id)`, `(class_group_id,status,start_at,due_at)` и `(status,due_at)`; FK `RESTRICT`. |
| `assignment_participants` | `id UUID PK`; `assignment_id UUID NOT NULL FK`; `student_id UUID NOT NULL FK`; `assigned_variant_id UUID NULL FK`; `created_at` server; `variant_assigned_at NULL`; CHECK variant fields both null or both non-null | UNIQUE `(assignment_id,student_id)`; indexes `(student_id,assignment_id)`, `(assigned_variant_id)`; all deletes `RESTRICT`. Application validates variant belongs to assignment assessment. |
| `student_submissions` | `id UUID PK`; `assignment_participant_id UUID NOT NULL FK`; `attempt_no SMALLINT NOT NULL CHECK(attempt_no>0)`; `status submission_status NOT NULL DEFAULT draft`; `started_at TIMESTAMPTZ NOT NULL server`; `submitted_at NULL`; CHECK state/timestamp consistency | UNIQUE `(assignment_participant_id,attempt_no)`; UNIQUE partial `(assignment_participant_id) WHERE status='draft'`; indexes `(assignment_participant_id,status)`, `(status,started_at)`; delete `RESTRICT`. |
| `student_answers` | `id UUID PK`; `submission_id UUID NOT NULL FK`; `assessment_item_id UUID NOT NULL FK`; `raw_answer JSONB NOT NULL`; `normalized_answer JSONB NOT NULL`; `created_at`, `updated_at` server | UNIQUE `(submission_id,assessment_item_id)`; index `(assessment_item_id)`; delete `RESTRICT`. Application validates item belongs to assigned variant. |
| `assessment_idempotency_keys` | `id UUID PK`; `assignment_participant_id UUID NOT NULL FK`; `key VARCHAR(128) NOT NULL`; `operation VARCHAR(16) NOT NULL CHECK IN ('start','submit')`; `request_hash CHAR(64) NOT NULL`; `submission_id UUID NOT NULL FK`; `http_status SMALLINT NOT NULL CHECK IN (200,201)`; `created_at`, `completed_at` server NOT NULL | UNIQUE `(assignment_participant_id,key)`; index `(submission_id)`; all deletes `RESTRICT`; committed rows immutable and permanent. |

`assessment_status = draft,published`; `assignment_status = open,closed`;
`submission_status = draft,submitted`. Никаких speculative statuses. Total points —
вычисляемая сумма, не колонка. Idempotency result восстанавливается из submission;
keys не очищаются. Для submit пустой body также хешируется канонически.

### 3.2 Audit data contract

Отдельная `assessment_audit_log`: `id UUID PK server`, `aggregate_type
VARCHAR(32) NOT NULL` (`assessment|assignment|submission`), `aggregate_id UUID
NOT NULL` (без polymorphic FK), `event_type VARCHAR(64) NOT NULL`, `actor_type
VARCHAR(16) NOT NULL` (`teacher|student|system`), `actor_id UUID NOT NULL`,
`occurred_at TIMESTAMPTZ NOT NULL server`, `details JSONB NOT NULL DEFAULT '{}'`;
CHECK enum-like values/known event values, index `(aggregate_type,aggregate_id,
occurred_at DESC,id DESC)`. Это отдельный append-only log: существующий
`audit_log` семантически task-scoped. Update/delete audit запрещены application DB
role. В details только IDs, attempt number, old/new status, window/limit и changed
field names; никаких display names, raw/normalized answers или task statement.

## 4. Статусы и state machines

### Assessment

`draft -> published`, обратно нельзя. Draft: metadata/variants/items/points/order
mutable. Published: всё перечисленное immutable. Publication означает успешную
атомарную проверку, freeze, создание assignment и participant snapshot; один без
другого в pilot невозможен.

### Assignment

`open -> closed`, обратно нельзя. `open` означает administratively open, но start
и submit дополнительно требуют временного окна. Teacher может close в любое время;
после close запрещены start/save/submit. Read и submitted data сохраняются. Close
не меняет drafts и не создаёт submit. Lifecycle каждого assignment независим:
закрытие одного assignment не меняет assessment или другие assignments.

### Submission

`draft -> submitted`, обратно нельзя. В draft разрешён upsert answers. Поля
identity, participant, attempt_no, started_at и assigned variant никогда не
меняются. После submit immutable вся submission и answers, включая оба answer
representations; reopen отсутствует.

## 5. Draft assessment и variants

Create создаёт пустой draft. Metadata PATCH меняет только явно переданные title /
description. Варианты создаются с очередной position; вариант/assessment можно
физически удалить только пока draft и без assignment. Items add/remove/reorder и
points change только под lock assessment. Reorder принимает полный permutation
item IDs одного variant; позиции перенумеровываются 1..N атомарно (временные
позиции либо deferrable constraint — implementation detail).

Publication readiness: title непустой; >=1 variant; каждый variant имеет >=1 item;
position sequences contiguous; points >0; item versions уникальны внутри variant;
все referenced tasks сейчас не archived и versions сейчас `approved`; total каждого
variant >0. Равные totals и равное число items между variants **не требуются**:
это педагогическая политика вне MVP. Пустой variant допустим в draft, не при publish.
Архивированная после add версия остаётся видна в draft, но blocks publication;
teacher удаляет/заменяет item явной draft-командой. Никакого auto-upgrade на latest.

## 6. Publication и assignment

Phase 3 сохраняет одну application-команду `publish-and-assign` для **первого**
назначения draft assessment: атомарность исключает publication без первого
assignment. Это удобство use case, а не ограничение data model: published assessment
может исторически иметь дополнительные assignments с собственными group, participant
snapshot, окном, `max_attempts` и lifecycle. Phase 3 не обязана предоставлять
отдельный endpoint повторного назначения. Request содержит
`class_group_id,start_at,due_at,max_attempts`; assessment ID в path. Транзакция:
lock assessment; если published — конфликт (endpoint предназначен только для draft;
exact command replay не поддерживается: endpoint без idempotency key); lock/validate active group;
validate readiness и Content Bank rows под shared/row locks; snapshot всех
неархивных students этой group (>=1); set published fields; create assignment and
participants; append `assessment_published`, `assignment_created`; commit.
Concurrent publisher loser получает `assessment_immutable`, без partial state.

Publication freeze относится только к composition assessment. Будущий второй
assignment использует тот же frozen composition; его lifecycle независим, а его
закрытие assessment не меняет. Множественность assignments не меняет историческую
ссылку каждого item на конкретную `task_version`.

`start_at` inclusive: операция разрешена при актуальном database wall-clock `now >=
start_at`. `due_at` exclusive: разрешена только при `now < due_at`. Требуется
`start_at < due_at`; прошлый start разрешён, но due должен быть в будущем на момент
publication. Database clock — источник времени. Для START/SUBMIT сервер сначала
получает необходимые row locks, затем непосредственно перед mutation читает
актуальное database wall-clock time (`clock_timestamp()` в PostgreSQL или
эквивалент), а не transaction-start timestamp. Начатый до due запрос, получивший
lock при `now >= due_at`, завершается deadline error.
В API принимается только offset-aware RFC 3339; ответ канонизируется UTC `Z`.

Participant snapshot не меняется при последующих переводах/архивации students.
Новые students группы не добавляются. Close — отдельная команда, audit-атомарная.

## 7. Variant assignment

При первом успешном START participant row блокируется. Variant выбирается
детерминированно: варианты сортируются по `(position,id)`, индекс равен первым
8 байтам `SHA-256(assignment_id.bytes || student_id.bytes)` как unsigned integer
modulo variant count. Алгоритм и byte order (UUID network order, big-endian integer)
фиксированы и тестируются. Результат и `variant_assigned_at` записываются до
submission в той же транзакции. Один variant естественно всегда индекс 0.

Первый concurrent start сериализуется row lock/unique constraints; subsequent
attempts используют stored variant, не пересчитывают его. Teacher не может менять
assignment. Archive Content Bank и изменение student/group ничего не меняют.

## 8. Attempt lifecycle

START — explicit создание/resume:

1. Проверить participant/student pilot identity, assignment `open`, `start_at <=
   now < due_at`.
2. Exact replay key вернуть исходную submission. Если есть draft, новый START с
   другим key возвращает её (`200`): это **логическая resume**, не replay, но её
   собственный key/result также записываются для последующих exact replay;
   response отмечает `resumed:true`.
3. Если draft нет и submitted count < max_attempts, создать attempt_no = max+1.
   Таким образом attempt 2+ создаётся только новым START после submit предыдущей.
4. Если лимит исчерпан — 409. До start, at/after due, closed — запрет.

Первая новая submission — `201`; resume/replay — `200`. GET не создаёт state.
Save answer требует draft/open/window и upsert одного item; reopen draft означает
GET/START resume, не transition. Submit атомарно переводит draft в submitted.
После due нельзя save также (чтобы deadline нельзя обходить); submitted всегда read.

## 9. Idempotency contract

Header `Idempotency-Key` обязателен для START и SUBMIT: 1..128 printable ASCII
символов `[A-Za-z0-9._:-]`, leading/trailing whitespace запрещён. Область
уникальности — `(assignment_participant_id, key)` сразу для обеих операций;
`operation` входит в hash, поэтому START-key нельзя переиспользовать для SUBMIT.
START/submit result ссылается на соответствующую submission. Ключи сохраняются
столько же, сколько historical submission (без TTL).

Request hash = lowercase hex SHA-256 канонического command: UTF-8 RFC 8785-style
JSON с operation, path identifiers и body (sorted keys, no insignificant space;
UUID lowercase, times UTC). Header не входит. START body `{}`; SUBMIT body `{}` —
ответы уже зафиксированы rows, а transaction lock определяет snapshot.

Exact replay того же operation/scope/hash возвращает byte-equivalent semantic
resource/status outcome (`201` для originally-created START, `200` submit; новые
`request_id`/headers допустимы). Concurrent identical replay ждёт owner row,
затем replay. Один key с иным hash или operation в том же participant scope —
`409 idempotency_conflict`. Таблица `assessment_idempotency_keys` из §3.1 хранит
key, operation, hash, result submission
и исходный HTTP status. Row создаётся/claim в business transaction; до commit он
может быть незавершённым только как незаметная другим транзакциям in-flight запись;
rollback удаляет claim. Exact concurrent
request blocks on unique/row then observes result. No orphan “processing”.

Сохраняются только успешные результаты. Domain/validation error откатывает claim;
retry с тем же key заново проверяет актуальное состояние и может получить иной
ошибочный или успешный результат. Поэтому долговременный replay гарантирован для
committed success, а не для ошибок.

Логически повторный SUBMIT с другим key после submitted — `409
submission_already_submitted`, не success. START с новым key после submit создаёт
next attempt либо limit error. Idempotency не обходит time/status checks для новой
операции; exact completed replay возвращается даже после due/close.

## 10. Student answers

Один row на `(submission,item)`. PUT заменяет обе representations атомарно;
отсутствие row означает unanswered. `raw_answer` — JSON value после JSON parsing,
без trim/coercion/reordering; строка сохраняет Unicode/code points и whitespace,
arrays сохраняют порядок/duplicates. JSON object допустим только если format
контракт его допускает; MVP formats используют string либо arrays of string IDs.
`normalized_answer` вычисляется сервером из raw и answer_format при каждом draft
save. Клиент его не присылает. Empty string — сохранённый ответ, `null` body value
означает explicit unanswered и удаляет row (204), а не JSON null answer.

PUT использует optimistic precondition `expected_updated_at` (null только create).
Два stale save: первый выигрывает, второй `409 concurrent_conflict`; blind
last-write-wins запрещён. Submit locks submission then answers, проверяет, что item
принадлежит assigned variant, и freezes rows. Не требуется отвечать на все items.

## 11. Normalization contract

Общее: Unicode сохраняется в raw; normalized strings применяют Unicode NFC,
перевод CRLF/CR в LF. Никакого correctness. Формат определяется immutable
`task_version.answer_format`.

| Format | Raw / normalized |
| --- | --- |
| `single_choice` | raw string option ID; normalized `{"option_id": raw}`; ID trim недопустим: whitespace => 422. |
| `multiple_choice` | raw array уникальных string option IDs; normalized `{"option_ids":[...]}` sorted Unicode code-point order; duplicates/blank => 422. Raw order сохраняется. |
| `short_text` | raw string; NFC, trim outer whitespace, internal whitespace unchanged: `{"text":"..."}`. Case preserved. |
| `number` | raw string; outer trim, accept optional sign, digits, one `.` or `,`, optional exponent; comma canonicalized `.`, parse arbitrary-precision Decimal, reject NaN/Infinity/group separators; normalized decimal plain canonical string without insignificant leading/trailing zeros (`-0` -> `0`). Unit is not parsed: any letters => 422. |
| `expression` | raw string; NFC + outer trim only, internal characters/spacing/case preserved in `{"expression":"..."}`. No algebra, Unicode symbol substitution or evaluation. |
| `long_text` | raw string; NFC + newline normalization; outer whitespace and case preserved in `{"text":"..."}` to avoid changing authored prose. |

Empty normalized text/expression is valid draft input. Server size limits: raw JSON
serialized UTF-8 <=64 KiB; text <=60,000 Unicode code points; choice count <=100,
each ID 1..200. Units for numeric tasks belong to future checking rules/Content Bank
accepted answer, not student number representation.

## 12. API contract

Namespace: `/api/assessment-core`. Pagination `offset=0,limit=20` (1..100).
Teacher actor is server-side `ActorContext`; no client `actor_id/created_by/role`.
Pilot student routes receive server-resolved `PilotStudentContext`; until auth it is
configured trusted dev identity, never a body/query/header role selector.

Поиск, folders, tags и фильтрация заданий остаются в существующем Content Bank
read API. Teacher UI получает там конкретный `task_version_id` и передаёт его в
Assessment add-item mutation. Assessment не проксирует и не дублирует Content Bank
HTTP query semantics; application всё равно самостоятельно валидирует version через
`ContentBankReadPort` при add-item и publication.

### 12.1 Route catalogue

| Method path | Success | Purpose |
| --- | --- | --- |
| GET `/assessments?status=&offset=&limit=` | 200 page | list teacher assessments |
| POST `/assessments` | 201 | create draft |
| GET `/assessments/{id}` | 200 | full composition |
| PATCH `/assessments/{id}` | 200 | draft metadata |
| POST `/assessments/{id}/variants` | 201 | create variant |
| DELETE `/assessments/{id}/variants/{variant_id}` | 204 | delete draft variant |
| POST `/assessments/{id}/variants/{variant_id}/items` | 201 | add approved version |
| DELETE `/assessments/{id}/variants/{variant_id}/items/{item_id}` | 204 | remove item |
| PUT `/assessments/{id}/variants/{variant_id}/item-order` | 200 | full reorder |
| PATCH `/assessments/{id}/variants/{variant_id}/items/{item_id}` | 200 | change points |
| POST `/assessments/{id}/publish-and-assign` | 201 | atomic publish/snapshot |
| GET `/assignments/{id}` | 200 | assignment/participants summary |
| POST `/assignments/{id}/close` | 200 | irreversible close |
| GET `/student/assignments?offset=&limit=` | 200 page | current pilot student's assignments |
| GET `/student/assignments/{id}` | 200 | frozen work/window/status |
| POST `/student/assignments/{id}/attempts/start` | 201/200 | start/resume, key required |
| GET `/student/attempts/{id}` | 200 | own draft/submitted attempt |
| PUT `/student/attempts/{id}/answers/{item_id}` | 200/201 | save draft answer |
| DELETE `/student/attempts/{id}/answers/{item_id}` | 204 | mark unanswered |
| POST `/student/attempts/{id}/submit` | 200 | submit, key required |

### 12.2 Mutating request/response examples and preconditions

Common DTO IDs abbreviated below. Responses include server timestamps.

* Create: `POST /assessments` body `{"title":"Алгебра 1","description":null}`
  -> 201 `{"id":"…","status":"draft","title":"Алгебра 1","description":null,
  "variants":[],"created_at":"…Z","updated_at":"…Z"}`. 422 validation.
* Metadata PATCH body `{"title":"Алгебра","expected_updated_at":"…Z"}` ->
  200 Assessment. Requires draft/current timestamp; 409 immutable/concurrent.
* Create variant body `{"name":"A"}` -> 201 `{id,name,position:1,items:[],
  total_points:"0.00"}`. Duplicate name 409. Delete ->204; draft only.
* Add item body `{"task_version_id":"…","points":"2.50"}` -> 201
  `{id,task_version_id,position:1,points:"2.50"}`. Content version must currently
  be approved/nonarchived; 409 otherwise. Delete ->204.
* Reorder body `{"item_ids":["i2","i1"],"expected_updated_at":"…Z"}` ->
  200 variant. Exact complete permutation, draft/current; 422 malformed, 409 stale.
* Points PATCH body `{"points":"3.00","expected_updated_at":"…Z"}` -> 200 item.
* Publish body `{"class_group_id":"…","start_at":"2026-09-01T09:00:00Z",
  "due_at":"2026-09-01T10:00:00Z","max_attempts":1}` -> 201
  `{"assessment":{…"status":"published"},"assignment":{"id":"…",
  "status":"open","participant_count":24,…}}`; readiness 422, invalid content
  409, concurrent/immutable 409.
* Close body `{}` -> 200 assignment with `status:"closed"`; exact repeat is
  `invalid_status_transition` 409.
* START body `{}`, required header -> 201 new or 200
  `{"id":"…","attempt_no":1,"status":"draft","assigned_variant_id":"…",
  "resumed":false,"answers":[]}`. Errors from window/limit/idempotency.
* Save body `{"raw_answer":" 42 ","expected_updated_at":null}` -> 201
  `{"id":"…","raw_answer":" 42 ","normalized_answer":{"decimal":"42"},
  "updated_at":"…Z"}`; update uses prior timestamp and returns 200. 422 format,
  409 stale/non-draft/window.
* Submit body `{}`, required header -> 200 `{"id":"…","status":"submitted",
  "submitted_at":"…Z","attempt_no":1,"answers":[…]}`. No completeness rule.

All GETs return only authorized pilot context's records. Assignment teacher response
does not expose answer content. `Location` accompanies 201. Decimal points serialized
as fixed decimal strings.

## 13. Error envelope

Always reuse:

```json
{"error":{"code":"assessment_immutable","message":"Опубликованную работу нельзя изменить.","details":[],"request_id":"uuid"}}
```

`details` is JSON array of `{field,code,message}` for validation and `[]` otherwise;
do not introduce object-shaped variant. Minimal mapping:

| HTTP | Codes |
| --- | --- |
| 400 | `invalid_request` only for syntactically valid but non-canonical idempotency header/time representation; JSON/schema errors normally 422. |
| 404 | `assessment_not_found`, `assignment_not_found`, `submission_not_found`, `participant_not_found`, `item_not_found` (also used to avoid cross-student disclosure). |
| 409 | `assessment_immutable`, `invalid_task_version`, `invalid_status_transition`, `assignment_not_started`, `assignment_deadline_passed`, `assignment_closed`, `attempt_limit_reached`, `submission_already_submitted`, `idempotency_conflict`, `concurrent_conflict`. |
| 422 | `validation_error`, `publication_requirements_not_met`, `answer_format_invalid`. |
| 410 | Не используется: archived historical resources remain readable. |
| 500 | `internal_error`; no internal details/stack. |

Не плодить generic `conflict`. Exact due boundary gives
`assignment_deadline_passed`; before start gives `assignment_not_started`.

## 14. Transaction boundaries

| Command | One transaction / locking / final guard |
| --- | --- |
| create assessment | insert + audit; UUID/validation constraints; rollback both |
| metadata/composition | lock assessment; validate draft + optimistic updated_at; mutate + audit; uniques/checks final |
| publication | locks assessment, variants/items, referenced task/version and group students consistently; validate, freeze, assignment/snapshot + two audits; uniques final |
| first/next start | claim idempotency; lock assignment+participant; после locks прочитать актуальный DB wall-clock и проверить window/limit; set variant, compute max attempt, insert submission + audit/result; partial rows rollback |
| save answer | lock submission; check window; conditional insert/update by expected timestamp; normalize + audit metadata only; unique answer final |
| submit | claim key; lock assignment, submission then answers; после locks прочитать актуальный DB wall-clock и проверить time/state; status/timestamp + audit + idempotency result; all rollback together |
| close | lock assignment; transition/timestamps + audit atomic |

Global lock order: assessment -> assignment -> participant -> submission -> answers;
Content Bank rows are locked after assessment composition and before group snapshot.
Repository never commits; UoW owns commit/rollback as current architecture does.

## 15. Concurrency scenarios

| Race | Outcome / winner / mechanism / loser |
| --- | --- |
| two publish | first lock creates the first assignment and commits; second sees published, 409 `assessment_immutable`; assessment row lock serializes commands; no partial. |
| publish vs reorder/mutation | assessment row lock orders them. Mutation first may publish revised snapshot; publish first makes mutation 409 immutable. |
| two first-start | participant lock; first assigns/creates #1; same-key replay returns it; different-key second resumes same draft 200. Unique draft/attempt protects. |
| two START same key | idempotency unique claim/lock; one executes, other exact replay; incompatible hash 409. |
| two START different keys | participant lock; one creates, other resumes draft; оба key сохраняют собственный result/status, submission одна. |
| two save same answer | expected timestamp CAS/answer unique; first commits, stale loser 409 `concurrent_conflict`. |
| submit vs save | submission lock serializes. Save first is included then submit; submit first makes save 409 already submitted. |
| two submit same key | one transition; other exact stored replay 200. |
| two submit different keys | one wins; loser 409 `submission_already_submitted`; no second event. |
| start/submit at due | После acquisition всех нужных row locks читается актуальный DB wall-clock: `< due` разрешает mutation, `>= due` даёт `assignment_deadline_passed`. Запрос, начавшийся до due, но дождавшийся conflicting lock только после due, проигрывает; partial state нет. |
| archive during add/publication | Content Bank version/task row locks serialize. Archive first blocks add/publish with invalid version; publish/add first completes, later archive allowed and historical FK remains. Draft add does not guarantee later publication. |

## 16. Audit

Events: `assessment_created`, `assessment_metadata_updated`, `variant_created`,
`variant_deleted`, `item_added`, `item_removed`, `items_reordered`, `item_points_changed`,
`assessment_published`, `assignment_created`, `assignment_closed`,
`variant_assigned`, `submission_started`, `answer_saved`, `answer_deleted`,
`submission_submitted`. Actor teacher comes from server ActorContext; student from
PilotStudentContext; system only for explicit future server jobs (none required now).
Business state and events commit atomically. Answer audit says only submission/item
IDs and operation, never value.

## 17. Identity и privacy boundary

Teacher/dev actor is existing configured server-side UUID; it proves no real
authorization. PilotStudentContext is separately server-resolved `{student_id}`;
the temporary deployment may configure one dev student, but clients cannot select
student/role in payload/query. Student domain record (`display_name`, optional
school-local opaque `external_ref`, group) is not authentication. Every student
operation checks participant ownership server-side.

PII minimization: no email, phone, legal name, birth date, parent data. UI display
name may be pseudonym. Future Checking Engine receives submission ID, item/version
ID, answer format, raw + normalized answer; it does not need display_name,
external_ref, class name or actor IDs.

## 18. Historical safety

`assessment_item -> task_version.id` is RESTRICT and never “latest”. New Content
Bank versions have no effect. Publication freezes item/order/points. Later task or
version archive changes eligibility only for new composition/publication, never
historical access/execution. Participant stores assigned variant once. Submitted
raw and normalized answers cannot change. Thus future replay receives identical
version ID, item scoring weight, raw input and normalization snapshot; newer
normalizer versions must not rewrite stored normalized values.

## 19. Application / repository boundaries

Новый contour, не расширение `ContentBankService`; routes only map transport.

```python
class AssessmentCommandService(Protocol):
    async def create(command, actor: ActorContext): ...
    async def mutate_composition(command, actor: ActorContext): ...
    async def publish_and_assign(command, actor: ActorContext): ...
    async def close_assignment(command, actor: ActorContext): ...

class StudentAttemptService(Protocol):
    async def start(command, student: PilotStudentContext, key: IdempotencyKey): ...
    async def save_answer(command, student: PilotStudentContext): ...
    async def submit(command, student: PilotStudentContext, key: IdempotencyKey): ...

class AssessmentQueryService(Protocol):
    async def list_assessments(query, actor): ...
    async def get_assessment(id, actor): ...
    async def get_assignment(id, context): ...
    async def get_submission(id, student): ...

class AssessmentRepository(Protocol):
    async def lock_assessment(id): ...
    async def lock_assignment(id): ...
    async def lock_participant(assignment_id, student_id): ...
    async def lock_submission(id): ...
    async def mutate_composition(...): ...
    async def create_assignment_snapshot(...): ...
    async def create_submission(...): ...
    async def upsert_answer_cas(...): ...
    async def claim_idempotency(...): ...
    async def append_audit(...): ...

class ContentBankReadPort(Protocol):
    async def get_version_for_assessment(version_id): ...
    async def lock_and_validate_new_usage(version_ids): ...
    async def get_historical_version(version_id): ...

class UnitOfWork(Protocol):
    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc, tb): ...
```

`ContentBankReadPort` — application boundary, не HTTP proxy и не владение search.
Для add-item он возвращает version вместе с owning task и проверяет существование,
принадлежность task, lifecycle/status и eligibility для нового использования. Для
publication он повторяет эти проверки под согласованной блокировкой для всех items.
Historical read получает именно запрошенную version независимо от последующего
archive. Tags, folders, полнотекстовый поиск и Content Bank filters в этот port не
переносятся; UI использует существующий Content Bank read API напрямую.

## 20. Test matrix следующих фаз

| Layer | Required cases |
| --- | --- |
| DB constraints | every FK/RESTRICT, enum/check, variant positions/names/items, participant, attempts, single draft, answers, idempotency uniqueness, timestamp consistency. |
| application unit | state matrices, readiness, totals, deterministic variant golden vectors, max attempts, windows/close, normalization per format, ownership/privacy. |
| repository/integration | locks/order, snapshot, historical archived version reads, atomic audit, rollback fault injection, CAS answer. |
| API | every route/request/status/envelope, server actor, no client role/actor, pagination, decimal/time serialization, cross-student 404. |
| concurrency | all 11 rows in section 15 with real PostgreSQL parallel transactions; SQLite substitutes insufficient. |
| idempotency | exact sequential/concurrent replay, hash/key conflict, different key logical repeats, rollback/retry, persistence after deadline/close. |
| deadline | `start_at-ε`, exact start, `due_at-ε`, exact due, after due; актуальный DB wall-clock после locks; обязательная regression: request starts before due, waits for conflicting row lock, acquires after due and START/SUBMIT fails. |
| history | new/archived Content Bank version, frozen composition/variant, submitted answer immutability and replay inputs. |
| frontend later | teacher draft editor/readiness/errors; assignment window; student resume/save conflict/submit; accessibility; no role selector; deadline rendering. |

## 21. Явные non-goals и принятые assumptions

Не входят: Checking Engine (deterministic/LLM), correctness/scores, teacher review,
analytics, production IAM/RBAC/School Core, journal, notifications, OCR/generation,
chat/adaptive/random AI, proctoring, reopen, exports и frontend. Assumptions:
PostgreSQL remains authoritative DB; Content Bank retains task versions physically;
server uses the DB UTC wall-clock; pilot provisioning of groups/students is
seed/admin-operational work outside this API; Phase 3 создаёт первое assignment
атомарно с publication, но schema поддерживает 0..N assignments на assessment.
Изменение любого из этих решений требует новой версии контракта до
миграции, а не скрытого implementation choice.

## Phase 3.9 hardening amendment

Это минимальное read-only расширение для UX discovery, а не новый lifecycle: публикация
по-прежнему атомарна только через первый `publish-and-assign`, assessment сохраняет 1:N
историю assignments, повторной mutation назначения и reopen нет.

* `GET /api/assessment-core/class-groups?offset=0&limit=20` возвращает страницу только
  активных групп (`id`, `name`, `active_student_count`), упорядоченных по `name ASC, id ASC`;
  identities учеников и `external_ref` не раскрываются.
* `GET /api/assessment-core/assessments/{assessment_id}/assignments?offset=0&limit=20`
  возвращает страницу исторических назначений (`id`, assessment/group IDs, `class_group_name`,
  status, window, max attempts, participant count, created/closed timestamps), по
  `created_at ASC, id ASC`; неизвестная работа даёт `assessment_not_found`.
* `GET /api/assessment-core/student/assignments/{id}` дополнительно возвращает
  `submitted_attempts: [{id, attempt_no, submitted_at}]` только текущего participant,
  только submitted, по `attempt_no ASC`. `submitted_attempt_count` равен длине списка;
  answers и чужие попытки отсутствуют.
