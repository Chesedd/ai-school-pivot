# Content Bank MVP — контракты фаз 2.2–2.11A

> Статус: проектный контракт v0.1. Этот документ фиксирует границы и
> интерфейсы до создания схемы БД, HTTP-обработчиков и клиентских компонентов.

> **Дополнение, только проект:** иерархическое размещение заданий зафиксировано в
> [отдельном folder hierarchy contract](content-bank-folder-hierarchy-contract.md).
> Предмет выбран виртуальным корнем, `TaskFolder` — организационным узлом до
> глубины 8, а nullable `tasks.folder_id` — единственным размещением задания.
> Этот контракт ещё не реализован: текущие API, БД и UI остаются плоскими;
> backend и frontend будут добавлены следующими фазами.

## 1. Назначение и границы модуля

Content Bank хранит и предоставляет учебные задания и их методическое
содержание: предметную классификацию, навыки, версии условия, эталоны,
рубрики, допустимые ответы, типичные ошибки и подсказки. Он отвечает за
жизненный цикл авторской версии задания, а также за поиск возможных
дубликатов.

В этап 2 входят этот каталог, справочники, версионирование, методические
блоки, статусы, аудит и дедупликация. Не входят Assessment, отправка
или хранение ответов учеников, AI-проверка, авторизация и аналитика. Будущая
работа ученика должна ссылаться на конкретную неизменяемую `task_version`, а
не только на `task`.

`tasks` — стабильная карточка и классификация задания. `task_versions` —
изменяемое, нумеруемое содержание. В частности, условие, формат ответа и
методические блоки принадлежат версии. Поля классификации (`subject_id`,
`grade_id`, `topic_id`, `subtopic_id`) принадлежат карточке. Изменение
классификации не создаёт версию содержания в v0.1, но отражается в аудите.

## 2.2 Согласованная минимальная ERD и enum

Все первичные ключи Content Bank и соответствующие внешние ключи имеют PostgreSQL тип `UUID`; UUID создаётся сервером. `created_by` и `approved_by` также UUID, но без FK на пользователя до этапа авторизации.

| Таблица | Поля |
| --- | --- |
| `tasks` | `id`, `subject_id`, `grade_id`, `topic_id`, `subtopic_id NULL`, `created_by`, `created_at`, `archived_at NULL` |
| `task_versions` | `id`, `task_id`, `version_no`, `title NULL`, `statement TEXT NOT NULL`, `task_type NOT NULL`, `answer_format NOT NULL`, `difficulty NOT NULL`, `source TEXT NULL`, `status NOT NULL DEFAULT draft`, `created_by`, `created_at`, `approved_by NULL`, `approved_at NULL` |
| `task_skill_links` | `id`, `task_version_id`, `skill_id`, `weight NUMERIC(5,4) NOT NULL`, `is_primary NOT NULL` |

`title`, `statement`, `task_type`, `answer_format` и `difficulty` принадлежат только версии. `statement` может быть пустой строкой в незавершённом draft, но перед review/approved application-валидатор требует непустой текст после trim.

| PostgreSQL enum | Точные значения | Колонка |
| --- | --- | --- |
| `task_version_status` | `draft`, `review`, `approved`, `archived` | `task_versions.status`; NOT NULL, server default `draft` |
| `task_type` | `test`, `calculation`, `problem`, `open_question`, `essay` | `task_versions.task_type`; NOT NULL, без default |
| `answer_format` | `single_choice`, `multiple_choice`, `short_text`, `number`, `expression`, `long_text` | `task_versions.answer_format`; NOT NULL, без default |
| числовая сложность | `SMALLINT`, 1–100 | `task_versions.difficulty`; NOT NULL, constraint `ck_task_versions_difficulty_range` |

Матрица `task_type`/`answer_format` проверяется application-слоем, без сложного DB CHECK: `test` — `single_choice`, `multiple_choice`; `calculation` — `short_text`, `number`, `expression`; `problem` — `number`, `expression`, `long_text`; `open_question` — `short_text`, `long_text`; `essay` — `long_text`.

`weight` лежит в `(0, 1]`; application-инвариант требует сумму весов навыков версии ровно `1.0000` и ровно один primary skill. `approved_at` и `approved_by` либо оба NULL, либо оба не NULL; зависимость этих полей от status DB не проверяет.

## 2. Термины и семантика версий

| Термин | Точное значение |
| --- | --- |
| **task** | Стабильная карточка с UUID, классификацией, архивным признаком и метаданными. |
| **task version** | Неизменяемый после утверждения снимок содержания одной карточки; имеет `version_no` и `status`. |
| **latest_version** | Версия с наибольшим `version_no`; она может быть `draft`, `review`, `approved` или `archived`. |
| **approved_version** | Последняя исторически утверждённая версия: версия с наибольшим `version_no`, для которой задан `approved_at`. После архивирования она остаётся этим указателем, хотя её `status` может стать `archived`. Значение `null`, если утверждений не было. |
| **draft version** | Единственная редактируемая версия со статусом `draft`. |
| **primary skill** | Единственная связь `task_skill_links` версии с `is_primary: true`; это основной проверяемый навык. |
| **архивное задание** | Карточка с `archived_at != null`. Она не показывается в обычном списке и не доступна для нового учебного использования. |

Термин `current_version` не используется: он неоднозначен. У задания может
одновременно быть `approved_version` №1 и `latest_version` №2 в статусе
`draft`. В таком случае №1 остаётся доступной как утверждённая историческая
версия и не редактируется, а изменения вносятся исключительно в №2.

## 3. Основные операции Content Bank

`ActorContext` передаётся отдельно во все команды и содержит как минимум
`actor_id` и `actor_type`; клиент не передаёт audit-поля.

| Операция | Входные данные | Результат | Предусловия | Доменные ошибки |
| --- | --- | --- | --- | --- |
| Создать задание | классификация, `version`, `skill_links` | task и draft v1 | справочники существуют, один primary skill | `validation_error`, `conflict` |
| Изменить draft | `task_id`, `version_no`, изменяемые поля версии | обновлённая версия | версия — draft, задача не архивна | `not_found`, `conflict` |
| Список заданий | filters, sort, offset, limit | страница кратких карточек | корректные параметры | `validation_error` |
| Полная карточка | `task_id` | task, выбранные версии и методика | task существует | `not_found` |
| Отправить на review | task/version | версия review | draft проходит мягкую проверку | `invalid_status_transition`, `validation_error` |
| Вернуть в draft | task/version, reason | draft-версия | статус review | `invalid_status_transition` |
| Утвердить | task/version | approved-версия | status review, строгая проверка | `approval_requirements_not_met`, `invalid_status_transition` |
| Архивировать | `task_id`, reason | архивная карточка | task существует | `not_found`, `conflict` |
| Методические блоки | task/version, блок или набор блоков | сохранённые version-scoped блоки | только draft | `validation_error`, `conflict` |
| Справочники | тип справочника, optional filters | subjects/grades/topics/subtopics/skills | — | `validation_error` |
| Найти дубликаты | текст условия, классификация, optional task_id | candidates с причиной и score | непустое условие | `validation_error` |

Методические операции покрывают эталон (`expected_solution`), рубрику с
`rubric_items`, `accepted_answers`, `typical_errors` и `hints`; сохранение
одного блока может заменять соответствующий упорядоченный набор целиком.

## 4. Service-интерфейсы

Интерфейсы application-слоя оперируют командами, запросами и DTO, но не
FastAPI, SQLAlchemy или PostgreSQL. Команды содержат только пользовательские
данные; `ActorContext` — отдельный параметр.

```python
class ContentBankService(Protocol):
    def create_task(self, command: CreateTaskCommand, actor: ActorContext) -> TaskCardDTO: ...
    def update_draft_version(self, command: UpdateDraftVersionCommand, actor: ActorContext) -> TaskVersionDTO: ...
    def create_new_version(self, command: CreateNewVersionCommand, actor: ActorContext) -> TaskVersionDTO: ...
    def submit_for_review(self, command: VersionCommand, actor: ActorContext) -> TaskVersionDTO: ...
    def return_to_draft(self, command: ReturnToDraftCommand, actor: ActorContext) -> TaskVersionDTO: ...
    def approve_version(self, command: VersionCommand, actor: ActorContext) -> TaskVersionDTO: ...
    def archive_task(self, command: ArchiveTaskCommand, actor: ActorContext) -> TaskCardDTO: ...
    def save_methodology(self, command: SaveMethodologyCommand, actor: ActorContext) -> MethodologyDTO: ...
    def commit_import(self, command: ImportCommitCommand, actor: ActorContext) -> ImportResultDTO: ...

class ContentBankQueryService(Protocol):
    def list_tasks(self, query: ListTasksQuery) -> TaskPageDTO: ...
    def get_task_card(self, task_id: UUID) -> TaskCardDTO: ...
    def get_catalog(self, query: CatalogQuery) -> CatalogDTO: ...
    def find_duplicate_candidates(self, query: DuplicateQuery) -> DuplicateCandidatesDTO: ...
```

`CreateTaskCommand` включает классификацию, `VersionContentInput` и
`SkillLinkInput[]`; `SaveMethodologyCommand` содержит `task_id`, `version_no`
и нужные блоки. `created_by`, `approved_by`, `created_at`, `approved_at` не
являются полями этих входов и назначаются сервисом из `ActorContext` и часов.

## 5. Repository-интерфейсы

Persistence-порты возвращают DTO/record-типы, не ORM-объекты. Публичные
репозитории не имеют `commit()`/`rollback()`; application service открывает
transaction boundary и вызывает их в одной транзакции.

```python
class ContentBankRepository(Protocol):
    def create_task_with_initial_version(self, draft: InitialTaskDraft, actor: ActorContext) -> TaskCardDTO: ...
    def get_task_card(self, task_id: UUID) -> TaskCardDTO | None: ...
    def list_tasks(self, query: ListTasksQuery) -> TaskPageDTO: ...
    def get_version_for_update(self, task_id: UUID, version_no: int) -> TaskVersionDTO | None: ...
    def lock_task_for_new_version(self, task_id: UUID) -> LockedTaskDTO | None: ...
    def save_draft_version(self, draft: DraftVersionRecord) -> TaskVersionDTO: ...
    def add_version_from_snapshot(self, version: NewVersionRecord) -> TaskVersionDTO: ...
    def save_methodology(self, methodology: MethodologyRecord) -> MethodologyDTO: ...
    def archive_task(self, task_id: UUID, archived_at: datetime, actor_id: UUID, reason: str | None) -> TaskCardDTO: ...
    def append_audit(self, event: AuditEventRecord) -> None: ...
```

`create_task_with_initial_version` атомарно создаёт `tasks`, v1 `task_versions`
и `task_skill_links`. `lock_task_for_new_version` либо эквивалентный механизм
сериализует вычисление следующего `version_no` и проверку единственной
незавершённой версии. Методические блоки сохраняются агрегатом версии, а не
репозиторием на каждую таблицу. Конкретные таблицы, locking и import storage
на этой фазе намеренно не выбираются.

## 6. API-контракты

Все маршруты начинаются с `/api/content-bank`. Идентификаторы — UUID в
канонической строке; даты — ISO 8601 с `Z` (UTC). v0.1 использует
offset-пагинацию: `offset` (>=0, default 0), `limit` (1..100, default 20),
поскольку она проста для первого списка. Nullable означает JSON `null`;
необязательное поле запроса можно опустить, а `null` допустим только там, где
это явно сказано. Необязательная `subtopic_id` может быть `null`; пустые
строки не заменяют `null`.

| Метод и путь | Назначение / request | Успех | Ошибки |
| --- | --- | --- | --- |
| POST `/tasks` | Создать: JSON из раздела 7 | 201 TaskCard | 400, 409, 422 |
| GET `/tasks` | `subject_id`, `grade_id`, `topic_id`, `skill_id`, `status`, `archived` (default false), `q`, offset/limit, `sort` | 200 TaskPage | 422 |
| GET `/tasks/{task_id}` | Полная карточка | 200 TaskCard | 404 |
| PATCH `/tasks/{task_id}/versions/{version_no}` | Partial draft content и/или skills | 200 TaskVersion | 404, 409, 422 |
| POST `/tasks/{task_id}/versions/{version_no}/submit-review` | `{}` | 200 TaskVersion | 404, 409, 422 |
| POST `/tasks/{task_id}/versions/{version_no}/return-to-draft` | `{ "reason": "..." }` | 200 TaskVersion | 404, 409 |
| POST `/tasks/{task_id}/versions/{version_no}/approve` | `{}` | 200 TaskVersion | 404, 409, 422 |
| POST `/tasks/{task_id}/archive` | optional `{ "reason": "..." }` | 200 TaskCard | 404, 409 |
| GET `/catalog/{catalog_name}` | `catalog_name`: subjects, grades, topics, subtopics, skills; optional parent filters | 200 CatalogDTO | 404, 422 |
| POST `/task-versions/check-duplicates` | `{ "statement": "...", "primary_skill_id": "uuid", "final_answer": null, "exclude_task_id": null, "limit": 5 }` | 200 DuplicateCandidates | 422 |

## 2.11A Базовая дедупликация (warning-only)

`POST /api/content-bank/task-versions/check-duplicates` принимает обязательные
непустые после trim `statement` и UUID `primary_skill_id`, nullable
`final_answer`, nullable `exclude_task_id`, а также `limit` (default 5, 1..20).
Ответ: `{ "has_likely_duplicates": true, "items": [...] }`; item содержит
`task_id`, `task_version_id`, `version_no`, nullable `title`, `status`, полное
`statement`, округлённый до четырёх знаков `statement_similarity`, флаги
`same_primary_skill`, `same_final_answer` и `reasons`.

Допустимые причины строго ограничены `exact_statement`,
`high_statement_similarity`, `same_primary_skill`, `same_final_answer`.
Текст trim-ится, Unicode-case-нормализуется и схлопывает whitespace. PostgreSQL
`pg_trgm similarity()` рассчитывает сходство условия. Final answer сравнивается
точно после trim/casefold/схлопывания whitespace и только когда оба ответа
непустые. Candidate scan использует GIN-совместимый оператор `%` с минимальным
порогом 0.55. Вероятный дубликат: точное условие; similarity >= 0.85;
similarity >= 0.70 плюс тот же primary skill; либо similarity >= 0.65 плюс тот
же final answer. Один короткий одинаковый ответ без похожего условия не
достаточен. Порядок: exact, similarity DESC, same skill, same answer, task UUID;
`limit` применяется после policy-фильтрации.

SQL-область состоит только из неархивных `tasks`, их версии с максимальным
`version_no` и статусом `draft`, `review` или `approved`; archived-карточки,
исторические версии и `exclude_task_id` исключаются. Primary skill присоединён
с условием `is_primary`, поэтому дополнительные skill links не размножают item.

Проверка никогда не блокирует создание, смену статуса или утверждение.
Создание выполняет проверку в той же UoW до insert, всё равно атомарно создаёт
task и ровно один `task_created`, а `TaskResponse` аддитивно возвращает
`duplicate_warnings` (по умолчанию `[]`) из тех же candidate items. В этой фазе отсутствуют LLM, embeddings, vector DB и внешние сервисы.

## 2.9A Audit Log

`audit_log` — append-only журнал бизнес-мутаций. Его схема: `id UUID PRIMARY
KEY DEFAULT gen_random_uuid()`, `task_id UUID NOT NULL` с `FK tasks.id ON
DELETE RESTRICT`, nullable `task_version_id UUID` с `FK task_versions.id ON
DELETE RESTRICT`, nullable `version_no INTEGER CHECK (version_no > 0)`,
`action audit_action NOT NULL`, `actor_id UUID NOT NULL`, nullable `reason
TEXT`, `details JSONB NOT NULL DEFAULT '{}'`, `occurred_at TIMESTAMPTZ NOT NULL
DEFAULT CURRENT_TIMESTAMP`. Индексы ограничены ближайшими запросами:
`(task_id, occurred_at)`, `(task_id, action, occurred_at)` и
`(task_version_id)`.

Закрытый PostgreSQL enum `audit_action` содержит ровно `task_created`,
`methodology_updated`, `submitted_for_review`, `returned_to_draft`,
`version_approved`, `version_created`, `task_archived`. Создание task/v1 пишет
только `task_created`. `actor_id` всегда берётся из server-side
`CONTENT_BANK_DEV_ACTOR_ID`; в API нет поля или команды, позволяющих клиенту
создать событие либо подменить actor. FK на users отсутствует до появления
authentication.

`reason` используется для `returned_to_draft` и опционально для
`task_archived`. `details` хранит только компактные метаданные: переходы
статуса, `source_version_no` либо количества rubric items, accepted answers,
hints и typical-error links. Условие, решение, ответы, текст рубрики,
подсказки, payload целиком, секреты и URL БД в журнал не попадают.

Application/repository API журнала предоставляет только append и read, без
update/delete. `AuditWriter` использует repository того же Unit of Work и
выполняет insert после успешной бизнес-мутации, но до единственного commit.
Он не создаёт session и не делает commit. Ошибка insert не подавляется и
откатывает всю транзакцию. Validation/not-found/conflict, rollback, read
операции и повторное идемпотентное архивирование событий не создают.

`GET /api/content-bank/tasks/{task_id}/audit` принимает `offset` (>= 0,
default 0), `limit` (1..100, default 50) и optional `action: audit_action`.
Ответ `{ "items": [AuditEvent], "total": 1, "offset": 0, "limit": 50 }`;
AuditEvent содержит `id`, `task_id`, nullable `task_version_id`, nullable
`version_no`, `action`, `actor_id`, nullable `reason`, `details` и UTC
`occurred_at`. События отсортированы newest-first с tie-breaker по `id`;
`total` вычисляется после action-filter и до pagination. Архивная карточка
доступна, неизвестная task даёт стандартный 404, неверные action/pagination —
стандартный validation envelope. Endpoint строго read-only.

`sort` принимает только `created_at`, `updated_at`, `title`, `latest_version_no`
с префиксом `-` для убывания; default `-updated_at`. `status` фильтрует
`latest_version.status`. `q` — простой нечувствительный к регистру поиск по
`title` и `latest_version.statement`; он не является обещанием полнотекстового
поиска. `title` — необязательное, nullable поле карточки; `statement` обязателен
для review и approval, но может быть пустым только в черновике.

## 7. JSON создания задания

Запрос не принимает `id`, `version_no`, `status`, `created_at`, `created_by`,
`approved_at` или `approved_by`. Операция целиком атомарна.

```json
{
  "subject_id": "3d4f0c51-0bb2-4bbb-98e3-b92fb0af6177",
  "grade_id": "2f1a37af-03e2-4f7c-a7b8-fdd4a41a94b8",
  "topic_id": "c5e84748-755e-459d-aec1-e3bfa293f43d",
  "subtopic_id": null,
  "initial_version": {
    "title": "Линейное уравнение с дробями",
    "statement": "Решите уравнение (x - 1) / 2 = 3.",
    "task_type": "calculation",
    "answer_format": "number",
    "difficulty": 25,
    "source": null,
    "skills": [
    { "skill_id": "a0dda428-f222-4b1a-9a7e-17e324947943", "weight": 0.7000, "is_primary": true },
    { "skill_id": "2aa91564-7081-4a4c-8094-a95b6e09ec31", "weight": 0.3000, "is_primary": false }
    ]
  }
}
```

Для вертикального среза 2.3 конкретизирован ответ справочника: каждый
`GET /catalog/{catalog_name}` возвращает `{ "catalog": "subjects", "items":
[...] }`. Элемент всегда содержит `id`, `name` и nullable родительские ключи
`subject_id`, `grade_id`, `topic_id`, `subtopic_id`; frontend фильтрует
зависимые списки по этим ключам.

## 8. JSON полной карточки задания

Методика включена лишь для `latest_version`; краткие метаданные остальных
версий не дублируют её содержание. `approved_version` — ссылка-метаданные,
полный исторический snapshot запрашивается будущим отдельным version-read
контрактом, когда он потребуется Assessment.

```json
{
  "id": "78d46611-94d0-4a4c-b6ef-c3302d0667e4",
  "subject": { "id": "3d4f0c51-0bb2-4bbb-98e3-b92fb0af6177", "name": "Математика" },
  "grade": { "id": "2f1a37af-03e2-4f7c-a7b8-fdd4a41a94b8", "name": "7 класс" },
  "topic": { "id": "c5e84748-755e-459d-aec1-e3bfa293f43d", "name": "Уравнения" },
  "subtopic": null,
  "archived_at": null,
  "latest_version": {
    "id": "16f2cfd4-167c-4f76-a5a0-768f8787d8f5",
    "version_no": 2,
    "status": "draft",
    "title": "Линейное уравнение с дробями",
    "statement": "Решите уравнение (x - 1) / 2 = 3 и запишите ответ.",
    "task_type": "calculation",
    "answer_format": "number",
    "difficulty": 25,
    "source": null,
    "created_at": "2026-07-19T10:15:00Z",
    "created_by": { "id": "4a86fe9d-6c1f-4a68-b81e-f1838e44b01c", "display_name": "Автор" },
    "approved_at": null,
    "approved_by": null
  },
  "approved_version": { "id": "31b319cb-d4fe-44d3-bd27-30ecfc2c8e76", "version_no": 1, "approved_at": "2026-07-18T12:00:00Z" },
  "versions": [
    { "id": "31b319cb-d4fe-44d3-bd27-30ecfc2c8e76", "version_no": 1, "status": "approved", "created_at": "2026-07-18T09:00:00Z", "approved_at": "2026-07-18T12:00:00Z" },
    { "id": "16f2cfd4-167c-4f76-a5a0-768f8787d8f5", "version_no": 2, "status": "draft", "created_at": "2026-07-19T10:15:00Z", "approved_at": null }
  ],
  "skills": [
    { "id": "a0dda428-f222-4b1a-9a7e-17e324947943", "name": "Решение линейных уравнений", "weight": 1.0000, "is_primary": true }
  ],
  "methodology": {
    "expected_solution": { "text": "x - 1 = 6; x = 7.", "max_points": 2 },
    "rubric": { "title": "Решение уравнения", "max_score": 2, "items": [
      { "order_index": 1, "description": "Корректно умножает обе части на 2", "max_points": 1 },
      { "order_index": 2, "description": "Получает x = 7", "max_points": 1 }
    ] },
    "accepted_answers": [{ "value": "7", "normalization": "trim" }],
    "typical_errors": [{ "id": "f1a41e1b-a797-4dc1-a986-91442e95f70d", "description": "Не умножает правую часть на 2", "linked": true }],
    "hints": [{ "order_index": 1, "text": "Умножьте обе части на 2." }]
  },
  "audit": { "created_at": "2026-07-18T09:00:00Z", "updated_at": "2026-07-19T10:15:00Z", "created_by": { "id": "4a86fe9d-6c1f-4a68-b81e-f1838e44b01c", "display_name": "Автор" }, "last_event_at": "2026-07-19T10:15:00Z" }
}
```

## 9. Ошибки API

Единый фатальный формат:

```json
{
  "error": {
    "code": "approval_requirements_not_met",
    "message": "Версия не готова к утверждению.",
    "details": [
      { "field": "methodology.rubric.items", "code": "min_items", "message": "Нужен хотя бы один критерий." }
    ],
    "request_id": "6c7444b6-6f03-4430-950e-e1e1fdd4b008"
  }
}
```

HTTP-валидация пути, query или JSON возвращает 422 `validation_error` с
`details`. Доменные `not_found` возвращают 404; `conflict`,
`invalid_status_transition` и блокировка конкурирующей операции — 409;
`approval_requirements_not_met` — 422; `import_validation_error` — 422.
Неожиданная ошибка — 500 `internal_error` без внутренних деталей и traceback.
Каждая валидационная проблема всегда имеет `field`, `code`, `message`.

## 10. Статусы и переходы

| Из | В | Команда | Предусловия / кто | Запрещённый результат |
| --- | --- | --- | --- | --- |
| draft | review | submit_for_review | мягкая проверка; actor из server context | 409 `invalid_status_transition` или 422 validation |
| review | draft | return_to_draft | указан reason; actor из server context | 409 `invalid_status_transition` |
| review | approved | approve_version | строгая проверка; actor из server context | 409 или 422 `approval_requirements_not_met` |
| approved | archived | archive_task | архивируется карточка и её исторически утверждённая версия; actor из server context | 409 `invalid_status_transition` |

`review` не редактируется. Его возвращают в `draft` отдельной командой, после
чего редактирование снова разрешено. Создание новой версии от approved создаёт
следующий номер в `draft` и не меняет approved-версию. Архивирование задаёт
`task.archived_at`, исключает карточку из default list и переводит её
approved-версию в `archived`; audit/approved timestamp сохраняются. Новая
версия архивного задания в v0.1 запрещена (`409 conflict`); разархивирование
не входит в контракт.

## 11. Требования для утверждения

Мягкая проверка перед `review`: существуют subject/grade/topic и skills,
ровно один primary skill, `statement` непустой после trim, нет повторяющихся
skills, а числовые поля не отрицательны. Она позволяет ещё не иметь полной
методики.

Строгая проверка перед `approved` дополнительно требует эталонное решение,
рубрику, минимум один `rubric_item`, `rubric.max_score > 0`, каждый
`rubric_item.max_points > 0` и точное равенство суммы `max_points` значению
`rubric.max_score`. Таким образом требуются непустое условие, предмет, класс,
тема и ровно один основной навык. Ошибки возвращаются как
`approval_requirements_not_met` с массивом `details` формата раздела 9; один
элемент на каждое нарушенное правило.

## 12. Frontend-экраны как контракты

| Экран | Данные | Действия | Состояния | API |
| --- | --- | --- | --- | --- |
| Список заданий | TaskPage, filters, catalog options | фильтровать, сортировать, перейти в карточку/создание | loading, empty, error с retry | GET `/tasks`, GET `/catalog/{catalog_name}` |
| Создание/редактирование | catalogs, create payload или TaskCard/latest draft | создать, изменить draft, создать следующую версию, submit/return/approve | loading form, validation errors, saving, conflict, error | POST `/tasks`, PATCH version, POST version/status routes, catalogs |
| Карточка | полный TaskCard, versions, methodology, audit | просмотреть, редактировать draft, статусные команды, архивировать | loading, not found, error, archived read-only | GET `/tasks/{task_id}` и соответствующие commands |

Это поведенческие контракты, а не требования к визуальному дизайну.

## 13. Инварианты

1. `version_no` уникален в пределах одного `task`, начинается с 1 и положителен.
2. Approved-версия не изменяется напрямую; её содержание всегда snapshot.
3. У версии ровно один primary skill; связи навыков не дублируются; `weight` в `(0, 1]`, а их сумма для версии равна `1.0000`.
4. `rubric.max_score` и `rubric_item.max_points` положительны; `order_index`
   однозначен внутри одной рубрики.
5. Task, первая draft-версия и skill links создаются атомарно.
6. Запрещённые статусные переходы возвращают 409 conflict с кодом
   `invalid_status_transition`.
7. `approved_at` и `approved_by` либо оба NULL, либо оба не NULL; клиент не управляет audit-полями, including created/approved actor и time.
8. Архивная карточка не принимает новые версии и не появляется в default list.

## 14. Порядок реализации

* **2.2** — минимальная БД, отражающая `tasks`/`task_versions` и справочники.
* **2.3** — атомарное создание задания.
* **2.4** — список заданий.
* **2.5** — полная карточка.
* **2.6** — методическая структура.
* **2.7** — статусы и переходы.
* **2.8–2.11** — поиск, аудит и дедупликация.

Этот порядок — план последующих фаз, а не разрешение реализовывать их в фазе
2.1.

## 15. Методическая структура (фаза 2.6A)

Методические данные принадлежат конкретной `task_version`. Их изменение не
создаёт новую версию автоматически. В 2.6A PUT разрешён исключительно для
`latest_version` со статусом `draft`; `review`, `approved`, `archived` и не
последняя версия доступны только для чтения. Все пять блоков сохраняются
атомарно с семантикой полной замены. `expected_solution` и `rubric` могут быть
`null`, остальные блоки — пустыми массивами. Это допустимо для draft;
обязательность решения и критериев перед approve относится к строгому
валидатору фазы 2.7.

`grading_mode` v0.1 имеет единственное значение `points`; `severity` имеет
ровно `low`, `medium`, `high`. Сервер вычисляет `rubric.max_score` как сумму
`max_points`, а `rubric_items.order_index` как нулевой индекс элемента в
request.

### PUT `/api/content-bank/task-versions/{task_version_id}/methodology`

Все пять верхнеуровневых полей обязательны. Request:

```json
{
  "expected_solution": {"solution_text":"Полное эталонное решение","final_answer":"3","solution_steps":["Перенести 2 в правую часть","Вычислить значение x"]},
  "rubric": {"grading_mode":"points","notes":null,"items":[
    {"criterion":"Правильно выполнен перенос","max_points":"1.0000","required":true,"common_failure":"Не изменён знак"},
    {"criterion":"Получен правильный ответ","max_points":"1.0000","required":true,"common_failure":null}
  ]},
  "accepted_answers":[{"answer_value":"3","tolerance":null,"unit":null,"normalization_rule":null}],
  "typical_errors":[{"skill_id":"a0dda428-f222-4b1a-9a7e-17e324947943","code":"sign_not_changed","title":"Не изменён знак","description":"При переносе слагаемого ученик не меняет знак","severity":"medium","remediation_hint":"Повторить правило переноса","detection_hint":"В промежуточной строке осталось +2"}],
  "hints":[{"level":1,"hint_text":"Перенесите известное слагаемое вправо"},{"level":2,"hint_text":"При переносе поменяйте знак"}]
}
```

Response `200` возвращает сохранённый aggregate и сгенерированные UUID:

```json
{
  "expected_solution":{"id":"11111111-1111-4111-8111-111111111111","solution_text":"Полное эталонное решение","final_answer":"3","solution_steps":["Перенести 2 в правую часть","Вычислить значение x"]},
  "rubric":{"id":"22222222-2222-4222-8222-222222222222","grading_mode":"points","max_score":"2.0000","notes":null,"items":[
    {"id":"33333333-3333-4333-8333-333333333333","criterion":"Правильно выполнен перенос","max_points":"1.0000","required":true,"common_failure":"Не изменён знак","order_index":0},
    {"id":"44444444-4444-4444-8444-444444444444","criterion":"Получен правильный ответ","max_points":"1.0000","required":true,"common_failure":null,"order_index":1}
  ]},
  "accepted_answers":[{"id":"55555555-5555-4555-8555-555555555555","answer_value":"3","tolerance":null,"unit":null,"normalization_rule":null}],
  "typical_errors":[{"id":"66666666-6666-4666-8666-666666666666","skill_id":"a0dda428-f222-4b1a-9a7e-17e324947943","code":"sign_not_changed","title":"Не изменён знак","description":"При переносе слагаемого ученик не меняет знак","severity":"medium","remediation_hint":"Повторить правило переноса","detection_hint":"В промежуточной строке осталось +2"}],
  "hints":[{"id":"77777777-7777-4777-8777-777777777777","level":1,"hint_text":"Перенесите известное слагаемое вправо"},{"id":"88888888-8888-4888-8888-888888888888","level":2,"hint_text":"При переносе поменяйте знак"}]
}
```

`GET /tasks/{task_id}` помещает эту же структуру в
`latest_version.methodology`; `approved_version` и `versions` остаются
summary. Ошибки используют envelope раздела 9: неизвестная версия — `404
not_found`; не latest/draft — `409 conflict`; несовпадающее глобальное
определение `(skill_id, code)` — `409 typical_error_definition_conflict`;
невалидный UUID, JSON или доменные данные — `422 validation_error`.

## 16. Фаза 2.7A — статусные команды

Структурная проверка перед review блокирует команду (`422 validation_error`) и возвращает все нарушения в `error.details.issues`: непустой `statement`, наличие skill link, ровно один primary skill, отсутствие повторов, числовые веса в `(0, 1]` с суммой `1.0000`, совместимость `task_type`/`answer_format` и согласованность классификации. Методическая неполнота не блокирует review: `missing_expected_solution`, `missing_rubric`, `missing_rubric_items` (и `missing_accepted_answer`, если требование будет введено для формата ответа) возвращаются в `validation.issues`. `valid_for_approval` равен `false`, если есть предупреждения.

Публичные команды (альтернативных путей через `task_version_id` нет):

* `POST /api/content-bank/tasks/{task_id}/versions/{version_no}/submit-review`, request `{}`;
* `POST /api/content-bank/tasks/{task_id}/versions/{version_no}/return-to-draft`, request `{ "reason": "Требуется дополнить критерии оценивания." }`; reason после trim обязателен, максимум 1000 символов. До фазы 2.9 он передаётся application-команде, но постоянно не хранится; хранение начинается вместе с Audit Log;
* `POST /api/content-bank/tasks/{task_id}/versions/{version_no}/approve`, request `{}`;
* `POST /api/content-bank/tasks/{task_id}/archive`, optional request `{ "reason": "..." }` или без body.

Status response (`submit-review`, `return-to-draft`, `approve`) имеет форму:

```json
{
  "task_id": "uuid", "task_version_id": "uuid", "version_no": 1,
  "previous_status": "draft", "status": "review",
  "created_at": "2026-07-27T12:00:00Z", "created_by": "uuid",
  "approved_at": null, "approved_by": null,
  "validation": {"valid_for_approval": false, "issues": [
    {"field": "methodology.expected_solution", "code": "missing_expected_solution", "message": "Добавьте эталонное решение."}
  ]}
}
```

`validation` равно `null`, когда отчёт неприменим; успешный approve возвращает пустой report с `valid_for_approval: true`. Строгая проверка включает структурные и методические требования раздела 11 и при любой ошибке возвращает `422 approval_requirements_not_met` с полным массивом `error.details.issues`. Неверный переход и действие над не-latest версией возвращают `409 invalid_status_transition`; архивная карточка — `409 conflict`; неизвестная пара task/version — `404 not_found`.

Create-version response (`201`, `Location: /api/content-bank/tasks/{task_id}`):

```json
{
  "task_id": "uuid", "task_version_id": "new-uuid", "version_no": 2,
  "status": "draft", "created_at": "2026-07-27T12:00:00Z",
  "created_by": "server-actor-uuid", "approved_at": null, "approved_by": null
}
```

Источник должен принадлежать карточке и быть одновременно latest и текущей approved version. Неизвестный номер — `404 not_found`; существующий, но не latest approved источник — `409 invalid_source_version`. Клонируются version content, skill links, expected solution, rubric/items, accepted answers, task-error links с переиспользованием global typical errors и hints. Все version-owned UUID и server-owned metadata создаются заново.

Archive response:

```json
{"task_id":"uuid", "archived_at":"2026-07-27T12:00:00Z", "latest_status":"archived"}
```

Архивирование идемпотентно, сохраняет первоначальный `archived_at` и approval metadata, переводит незавершённые и текущую approved версии в `archived`. Историческая approved version продолжает определяться по `approved_at`.

## 17. Фаза 2.8 — поиск актуальной версии и `updated_at`

`GET /api/content-bank/tasks` принимает необязательный `q`. Значение обрезается с
обоих краёв; отсутствующая или пустая после обрезки строка отключает поиск. Длина
непустого значения ограничена 200 символами (`422 validation_error`). Поиск всегда
применяется **только к `latest_version`** (версии с максимальным `version_no`): текст
исторической версии сам по себе никогда не возвращает карточку. Он сочетается с
фильтрами subject, grade, topic, subtopic, skill (`EXISTS`), task_type, difficulty,
status и прежней archived-семантикой. Offset/limit не меняются, а `total` считается
после поиска и фильтров, до пагинации.

PostgreSQL хранит generated column `task_versions.search_vector` типа `tsvector`:

```sql
GENERATED ALWAYS AS (
  setweight(to_tsvector('russian'::regconfig, COALESCE(title, '')), 'A') ||
  setweight(to_tsvector('russian'::regconfig, COALESCE(statement, '')), 'B') ||
  setweight(to_tsvector('russian'::regconfig, COALESCE(source, '')), 'C')
) STORED
```

Таким образом, индексируются только title (A), statement (B), source (C). Методика,
классификация, навыки и audit metadata не входят в вектор. Индекс создаётся точным
SQL-эквивалентом `CREATE INDEX ix_task_versions_search_vector_gin ON task_versions
USING gin (search_vector)`. Запрос строится параметризованным
`websearch_to_tsquery('russian', :q)`, совпадение — `search_vector @@ tsquery`, rank —
`ts_rank_cd(search_vector, tsquery)`. Внутренние `tsvector` и relevance в API не
возвращаются.

Допустим `sort_by=relevance`. Без q он даёт 422. При q и отсутствующем `sort_by`
порядок: relevance DESC, `tasks.updated_at` DESC, `task_id` ASC. Явный sort сохраняет
выбранное поле/направление; `created_at`, `updated_at`, `title`, `difficulty`,
`status`, `version_no` продолжают работать, NULLS LAST применяется как прежде, и
каждый порядок завершается стабильным `task_id ASC`. Без q effective default остаётся
`created_at DESC`.

В `tasks` и `task_versions` имеются `updated_at TIMESTAMPTZ NOT NULL DEFAULT
CURRENT_TIMESTAMP`; миграция сначала заполняет существующие значения из
`created_at`. `task_versions.updated_at` меняется при status/methodology/content
changes, а `tasks.updated_at` — при изменениях, влияющих на отображаемую карточку:
latest methodology/status, создании версии и архивировании. Клиент эти поля не
передаёт. List item возвращает `task.updated_at`; карточка — `updated_at` карточки и
`latest_version.updated_at`.

Пример:

```http
GET /api/content-bank/tasks?q=%D0%B4%D0%B2%D0%B8%D0%B6%D0%B5%D0%BD%D0%B8%D0%B5&subject_id=2f8b...&offset=0&limit=20
```

```json
{
  "items": [{
    "task_id": "c91d...", "title": "Равномерное движение",
    "statement": "Найдите скорость...", "status": "draft",
    "created_at": "2026-07-27T12:00:00Z",
    "updated_at": "2026-07-27T12:05:00Z"
  }],
  "total": 1, "offset": 0, "limit": 20
}
```

Карточка дополнительно содержит:

```json
{"updated_at":"2026-07-27T12:05:00Z","latest_version":{"updated_at":"2026-07-27T12:05:00Z"}}
```


## Числовая сложность (revision 20260730_01)

`difficulty` — обязательное строгое целое число 1–100 и свойство версии задания. API списка принимает необязательные `difficulty_min` и `difficulty_max` (1–100); минимум не может превышать максимум. Сортировка `sort_by=difficulty` числовая.
CSV/XLSX сохраняет колонку `difficulty`, но принимает только целые 1–100; пустые, дробные, текстовые и прежние enum-значения отклоняются. Допустимые примеры: 1, 25, 50, 75, 100.
Миграция переводит `basic`→25, `standard`→50, `advanced`→75; downgrade группирует 1–33/34–66/67–100 обратно.

## Post-MVP: AI Content Authoring Phase 4A

Будущий AI-assisted authoring является отдельным треком, принадлежащим Content
Bank; его обязательные границы и roadmap заданы в
[AI Content Authoring v1 contract](ai-content-authoring-v1-contract.md). Phase
4A.0 добавляет только документацию: API, persistence, UI и provider workflow
ещё не реализованы.

После human confirmation будущий authoring flow должен создавать через
обычные application boundaries карточку `task` и `task version` № 1 со
статусом `draft`. Это не меняет исторические контракты Phase 2 и не обходит
существующие инварианты и отдельный цикл `draft → review → approved`. Provider
не является actor, не создаёт и не утверждает Content Bank entities напрямую;
автоматические submit for review и approval запрещены.
