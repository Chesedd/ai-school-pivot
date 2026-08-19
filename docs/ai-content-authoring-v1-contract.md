# AI Content Authoring v1 — контракт Phase 4A

## 1. Статус и назначение

**Phase 4A.0 — только документация и архитектура.** AI-генерация заданий ещё
недоступна пользователям. Этот PR не реализует authoring API, persistence,
схему/миграции, UI, provider workflow или вызовы provider; реализация
запланирована в Phases 4A.1–4A.6. Phase 4A входит в общую программу Phase 4, но
принадлежит Content Bank, а не Checking Engine.

Цель v1 — по ограниченному brief и выбранному catalog context получить от LLM
задание вместе с предложенной solution/methodology, независимо проверить
решение, преобразовать результат в проверяемый draft template, дать человеку
его отредактировать и лишь по явному подтверждению создать обычное задание
Content Bank.

Термин **database template** означает принадлежащий приложению, валидированный
Content Bank write DTO/aggregate. Это никогда не SQL от модели, не ORM objects
от модели и не прямой доступ модели к базе данных.

## 2. Обязательный пользовательский flow

1. Аутентифицированный author создаёт authoring session.
2. Author задаёт ограниченный brief и выбирает существующий Content Bank
   classification/catalog context.
3. Приложение замораживает request и catalog allowlist.
4. Generator выдаёт строго структурированный task draft: задание и предложенные
   solution/methodology.
5. Отдельный solver/verifier независимо решает замороженный `statement` с
   релевантными author constraints, не получая expected answer generator.
6. Application code строго парсит и семантически валидирует оба результата.
7. Приложение строит versioned preview; Content Bank task ещё не создаётся.
8. Человек просматривает preview и может его редактировать.
9. Каждое редактирование создаёт новую preview revision и повторяет validation.
10. Перед confirmation выполняется существующий duplicate detection.
11. Все blockers устраняются; warnings явно подтверждаются для точных preview
    revision и fingerprint.
12. Явный human confirmation атомарно создаёт обычные Content Bank `task` и
    `task version` № 1 со статусом `draft`.
13. Затем draft проходит отдельный обязательный lifecycle
    `draft → review → approved`.
14. Ни generation, ни confirmation никогда автоматически не отправляют задание
    на review и не approve его.

## 3. Владение и зависимости

Content Bank владеет authoring sessions, generation/solver attempts, previews и
preview revisions, validation findings, duplicate warnings, human confirmation
и преобразованием в обычный Content Bank task aggregate.

Checking Engine владеет immutable submitted snapshots, routing/checker
execution, preliminary checking results, checking findings/confidence/
observability и потреблением точной approved historical task methodology.
Checking не генерирует задания, не выдумывает отсутствующие expected answers
или rubrics, не изменяет Content Bank, не ремонтирует неполную methodology при
проверке student answer и не хранит authoring sessions в Checking runs.

AI Content Authoring не использует и не перегружает Checking-owned tables:
check runs, checker results, findings, events и provider attempts, чей lifecycle
принадлежит Checking. Низкоуровневые provider-neutral primitives допустимо
переиспользовать лишь после превращения их ownership в действительно generic;
семантическая зависимость от Checking application services запрещена. Будущее
выделение shared provider execution ledger требует отдельной migration и
contract. Таким образом, authoring не использует Checking-owned persistence.

## 4. `AuthoringRequestV1`

Версионированный logical DTO содержит:

- `schema_version`;
- author-supplied `task_goal`/`brief`;
- выбранные из существующих catalogs `subject`, `grade`, `topic` и optional
  `subtopic` references;
- `task_type` (`test`, `calculation`, `problem`, `open_question`, `essay`);
- `answer_format` (`single_choice`, `multiple_choice`, `short_text`, `number`,
  `expression`, `long_text`);
- целочисленный `difficulty` в текущем диапазоне Content Bank 1–100;
- выбранные/allowlisted `skills`;
- optional pedagogical constraints, source/reference text и language;
- явную one-task-per-session семантику v1;
- принадлежащую приложению `policy_version`.

Catalog identities разрешает приложение. Provider может только echo reference
из точного frozen allowlist и не может придумывать persistent UUID. Actor,
timestamps, statuses, task IDs и task-version IDs никогда не контролируются
provider. User/source text — untrusted input и не может переопределять system
instructions.

Приложение задаёт явные максимумы строк, source text и массивов в
`policy_version`; пустые, превышающие лимит и неподдерживаемые значения
отклоняются typed application errors (`invalid_request`, `value_too_large`,
`unsupported_value`, `catalog_reference_not_allowed`). Оно не выполняет
скрытые trim, rewrite, translation, normalization или repair семантически
значимого author text. Bulk generation исключена из v1 и возможна только в
будущем versioned contract.

## 5. `GeneratedTaskDraftV1`

Структура использует существующие Content Bank shapes, а не параллельную
methodology model:

- title, statement, supplied source attribution, task type, answer format и
  difficulty;
- classification references, primary и secondary skill references с ровно
  одним primary skill;
- `expected_solution`: `solution_text`, `final_answer`, ordered
  `solution_steps`;
- typed `accepted_answers`, совместимые с answer format;
- для choice — `choice_options` (`option_key`, content, order) и versioned
  `choice_scoring_policy` (`all_or_nothing` или `per_option`);
- `rubric` с grading mode, notes и ordered `rubric_items` (`criterion`, positive
  Decimal `max_points`, required, common failure);
- `typical_errors`, связанные только с allowlisted skills, и ordered `hints`;
- generation limitations и отдельные application-owned validation findings.

Provider output не содержит authoritative database UUID, task/task-version или
actor IDs, timestamps, lifecycle status, approval/audit data, SQL, ORM model
names либо persistence commands. Для внутренних cross-references применяются
ограниченные document-local keys: они уникальны внутри draft, проверяются
приложением и только в atomic confirmation transaction отображаются в новые
application-generated UUID. Неизвестная catalog reference — blocker.
Persistent UUID, ordering metadata, computed totals, audit и lifecycle fields
принадлежат приложению.

## 6. Generator и independent solver

Используются две отдельно версионируемые prompt roles:

- **generator** создаёт structured task, proposed solution и methodology;
- **solver/verifier** независимо решает frozen statement и релевантные author
  constraints, не видя proposed expected answer, accepted answers, rubric
  totals или solution steps generator.

Application сравнивает typed results. Расхождение даёт ограниченный finding
`solution_mismatch` и никогда молча не примиряется выбором ответа одного
provider. Отдельно классифицируются structural invalidity, semantic
incompatibility, generator/solver disagreement, quality warning, provider
failure и duplicate warning.

Каждый provider attempt фиксирует immutable prompt specification, semantic
version, model configuration, retry policy, canonical request fingerprint,
bounded failure code и token/cost accounting. Provider не может обращаться к
БД напрямую, вызывать tools, browse внешние ресурсы (пока будущий contract не
разрешит конкретный source workflow), следовать инструкциям в brief/source/
generated content, создавать или approve Content Bank entities, раскрывать
secrets, environment values либо unrelated repository data.

## 7. Strict parsing и semantic validation

Structured output строго versioned: unknown fields, implicit coercion, NaN,
infinity и binary floats для scored values запрещены. Decimal передаётся
canonical plain string без exponent. Строки и массивы ограничены; ordering
canonical; повтор local keys запрещён; unsupported answer formats отклоняются;
provider output всегда untrusted.

Semantic validation как минимум проверяет:

- иерархию subject/grade/topic/subtopic и membership всех references в frozen
  allowlist;
- skill compatibility, веса и ровно один primary skill;
- совместимость task type/answer format и invariant difficulty 1–100;
- наличие expected solution и совместимость accepted answers;
- требования exact/text, numeric, choice и expression methodology;
- identity choice options и consistency scoring policy;
- ordering rubric items, positive Decimal maxima и точное равенство rubric
  total вычисленной сумме;
- linkage typical errors, ordering hints и generator/solver agreement;
- отсутствие provider-owned metadata, preview size limits и duplicate detection
  перед confirmation.

Stable validation result содержит version, preview revision и fingerprint,
`confirmable`, ordered typed issues, issue path, bounded issue code, severity,
safe user message и признак warning acknowledgement requirement. Ровно два
класса enforcement:

- `blocker` — confirmation запрещён;
- `warning` — требуется явное acknowledgement точного preview fingerprint.

Invalid output никогда автоматически не repair. User edit создаёт новую
revision и аннулирует прежние warning acknowledgements и duplicate results.

## 8. Privacy и prompt-injection boundary

Provider-visible input ограничен выбранным curriculum/catalog context, brief,
намеренно переданным source/reference content, pedagogical constraints, frozen
allowlisted catalog/skill references и versioned output/policy contracts.
Исключены students, submissions, assessment participants, groups/classes,
checking runs/results/findings, unrelated actors, credentials, environment
values, internal database metadata и unrelated tasks. Последние могут появиться
только как bounded duplicate candidates по будущему отдельному contract.

Author content, source, statements, examples и generated text — untrusted data.
System contract запрещает выполнять вложенные инструкции, использовать tools,
менять schemas, придумывать identifiers, обходить policies и выполнять
persistence. Logs/errors не содержат full prompts, raw provider prose, secrets
или unbounded source content.

Хранятся validated structured preview, canonical fingerprints, bounded failure
metadata, token/cost accounting и response hash. Не требуется долговременное
хранение unrestricted raw provider responses.

## 9. Логические lifecycles

### 9.1 Authoring session

Состояния: `draft`, `generating`, `ready`, `confirmed`, `rejected`, `expired`.
Допустимы переходы `draft → generating`; `generating → ready` после полностью
валидированного preview либо `generating → draft` после bounded failure;
`ready → generating` для retry; `ready → confirmed`; `draft|ready → rejected`;
`draft|ready → expired`. `confirmed`, `rejected`, `expired` terminal.

Session имеет immutable author/creation metadata. Confirmed/rejected/expired
session нельзя regenerate или edit; expiration не удаляет уже confirmed task.

### 9.2 Provider attempt

Состояния: `pending`, `running`, `succeeded`, `invalid_output`,
`failed_retryable`, `failed_terminal`. Переходы: `pending → running`, затем
`running → succeeded|invalid_output|failed_retryable|failed_terminal`.
Последние четыре состояния terminal для конкретного attempt; retry после
`failed_retryable` создаёт новый `pending` attempt и сохраняет историю. Каждое
исполнение generator или solver имеет уникальный attempt. Failed attempts не
создают Content Bank task.

Подтверждается только latest fully validated preview revision. Confirmation
использует optimistic concurrency/CAS по expected revision и fingerprint,
идемпотентно и при конкуренции создаёт не более одного task; replay возвращает
ту же confirmed task identity. Изменившиеся request/preview конфликтуют со
старым idempotency key. Создание task и перевод session в `confirmed` — одна
transaction без orphan records. Точная physical schema отложена до Phase 4A.1,
но эти logical invariants обязательны.

## 10. Планируемая API boundary

Следующие transport-neutral commands и вероятное REST mapping **не реализованы
в Phase 4A.0**: create session, start/retry generation, read session/attempt
status, read latest preview, replace preview с expected revision, validate
preview, reject session и confirm preview.

Планируемое resource family в существующем namespace:

- `POST /api/content-bank/authoring-sessions`;
- `GET /api/content-bank/authoring-sessions/{session_id}`;
- `POST /api/content-bank/authoring-sessions/{session_id}/generate`;
- `POST /api/content-bank/authoring-sessions/{session_id}/retry`;
- `PUT /api/content-bank/authoring-sessions/{session_id}/preview`;
- `POST /api/content-bank/authoring-sessions/{session_id}/reject`;
- `POST /api/content-bank/authoring-sessions/{session_id}/confirm`.

Generation должна поддерживать asynchronous execution/polling; Phase 4A.0 не
выбирает worker/queue. Presentation schemas и HTTP status/error mapping будут
финализированы implementing subphase, не ослабляя ownership, concurrency,
privacy и lifecycle guarantees этого контракта.

## 11. Human review и confirmation

Review preview показывает statement/classification, solution/methodology,
generator/solver agreement, все blockers/warnings, duplicate candidates,
provider/model/prompt versions в technical metadata, estimated/recorded cost и
точную preview revision. Человек может менять preview content, но не
server-owned identities, timestamps, status или audit fields.

Human confirmation требует отсутствия blockers, acknowledgement актуальных
warnings и свежего duplicate result для текущего fingerprint. Оно вызывает
существующие Content Bank application boundaries, создаёт обычные entities и
нормальную Content Bank version 1 в `draft`, а provenance записывает без
назначения provider actor. Confirmation никогда не submit for review и не
approve автоматически.

Авторитетными остаются существующие команды: submit review, return to draft,
approve, create later version, archive. Authoring confirmation не является
methodological approval; цикл `draft → review → approved` остаётся отдельной
обязательной human boundary.

## 12. Non-goals

Отложены bulk generation, autonomous approval/publication, замена teacher
review, student-specific generation, adaptation из student PII/submissions,
OCR, web research/retrieval, copyrighted-source ingestion policy, image/audio
generation, автоматическое создание curriculum catalogs, direct SQL
generation, Phase 4.9 findings/confidence implementation, Phase 4.10 Checking
vertical acceptance и Teacher Review из Phase 5.

## 13. Roadmap Phase 4A

### Phase 4A.0 — contract and roadmap

Только документация: boundaries, schemas, lifecycle, validation, privacy и
acceptance plan.

### Phase 4A.1 — provider boundary and durable authoring attempts

Authoring-owned ports/DTO, prompt registry/specification, request
fingerprinting, retry/concurrency/cost и attempt persistence; Content Bank task
ещё не создаётся.

### Phase 4A.2 — task generation and independent solving

Strict generator output, independent solver prompt, typed comparison и bounded
provider failures; confirmation ещё нет.

### Phase 4A.3 — methodology template construction

Typed accepted answers, choice options/scoring, expected solution, rubric,
typical errors, hints и skill mapping через frozen allowlists.

### Phase 4A.4 — semantic validation and preview persistence/API

Session/preview revisions, blockers/warnings, editing/revalidation, async
status/polling и duplicate-result invalidation; final task до confirmation не
создаётся.

### Phase 4A.5 — human review frontend

Generation request form, status/retry, structured preview, editable
task/methodology, findings/duplicate warnings, explicit warning
acknowledgement и confirmation.

### Phase 4A.6 — idempotent Content Bank commit and vertical acceptance

Atomic creation task + draft version + methodology, exact replay/concurrency,
audit provenance, PostgreSQL/frontend acceptance, prompt-injection/privacy
evaluation и versioned real-provider quality corpus.

Общая Phase 4 закрывается только после двух независимых gates:

1. Checking Engine Phase 4.10 acceptance.
2. AI Content Authoring Phase 4A.6 acceptance.

Ни один gate не заменяет другой.

## 14. Будущая acceptance strategy

Категории приёмки:

- strict schema/semantic unit tests;
- fake-provider generator/solver pipeline tests;
- prompt-injection/privacy tests;
- rejection malformed/extra/coerced provider output;
- Decimal и ordering determinism;
- preview revision/warning acknowledgement tests;
- idempotency/concurrency PostgreSQL tests и exactly-once task creation;
- rollback/no-orphan tests и duplicate-result invalidation;
- regression существующего Content Bank lifecycle и полный backend/frontend
  regression;
- Alembic parity;
- real-provider labelled quality evaluation.

Real-provider gate отчитывается отдельно от fake-provider tests. Он доказывает,
что каждый evaluated request завершается либо validated human-reviewable
preview, либо bounded safe failure, и никогда unsafe automatic database commit.
Human evaluators оценивают task correctness, solvability, solution correctness,
methodology completeness, rubric consistency, grade/difficulty
appropriateness, отсутствие solution leakage в statement и качество
duplicate/near-duplicate detection.

Versioned numeric quality thresholds и exact golden corpus должны быть
заморожены до объявления Phase 4A.6 complete. Phase 4A.0 не заявляет и не
фабрикует успешный provider-quality result.
