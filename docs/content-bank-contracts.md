# Content Bank MVP — контракты фазы 2.2

> Статус: проектный контракт v0.1. Этот документ фиксирует границы и
> интерфейсы до создания схемы БД, HTTP-обработчиков и клиентских компонентов.

## 1. Назначение и границы модуля

Content Bank хранит и предоставляет учебные задания и их методическое
содержание: предметную классификацию, навыки, версии условия, эталоны,
рубрики, допустимые ответы, типичные ошибки и подсказки. Он отвечает за
жизненный цикл авторской версии задания, а также за импорт и поиск возможных
дубликатов.

В этап 2 входят этот каталог, справочники, версионирование, методические
блоки, статусы, аудит, импорт и дедупликация. Не входят Assessment, отправка
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
| `difficulty_level` | `basic`, `standard`, `advanced` | `task_versions.difficulty`; NOT NULL, без default |

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
| Создать новую версию | `task_id`, `source_version_no` | новая draft с следующим номером | задача не архивна, нет другой draft/review | `conflict`, `not_found` |
| Отправить на review | task/version | версия review | draft проходит мягкую проверку | `invalid_status_transition`, `validation_error` |
| Вернуть в draft | task/version, reason | draft-версия | статус review | `invalid_status_transition` |
| Утвердить | task/version | approved-версия | status review, строгая проверка | `approval_requirements_not_met`, `invalid_status_transition` |
| Архивировать | `task_id`, reason | архивная карточка | task существует | `not_found`, `conflict` |
| Методические блоки | task/version, блок или набор блоков | сохранённые version-scoped блоки | только draft | `validation_error`, `conflict` |
| Справочники | тип справочника, optional filters | subjects/grades/topics/subtopics/skills | — | `validation_error` |
| Import preview | format, rows/file reference | строки, ошибки, warnings, import_token | валидный формат | `import_validation_error` |
| Import commit | `import_token`, выбранные строки | summary созданных задач | preview действителен и не устарел | `import_validation_error`, `conflict` |
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
    def preview_import(self, command: ImportPreviewCommand, actor: ActorContext) -> ImportPreviewDTO: ...
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
    def save_import_preview(self, preview: ImportPreviewRecord) -> ImportPreviewDTO: ...
    def get_import_preview_for_update(self, token: UUID) -> ImportPreviewRecord | None: ...
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
| POST `/tasks/{task_id}/versions` | `{ "source_version_no": 1 }` | 201 TaskVersion | 404, 409 |
| POST `/tasks/{task_id}/versions/{version_no}/submit-review` | `{}` | 200 TaskVersion | 404, 409, 422 |
| POST `/tasks/{task_id}/versions/{version_no}/return-to-draft` | `{ "reason": "..." }` | 200 TaskVersion | 404, 409 |
| POST `/tasks/{task_id}/versions/{version_no}/approve` | `{}` | 200 TaskVersion | 404, 409, 422 |
| POST `/tasks/{task_id}/archive` | optional `{ "reason": "..." }` | 200 TaskCard | 404, 409 |
| GET `/catalog/{catalog_name}` | `catalog_name`: subjects, grades, topics, subtopics, skills; optional parent filters | 200 CatalogDTO | 404, 422 |
| POST `/imports/preview` | `{ "format": "json", "rows": [...] }` | 200 ImportPreview | 422 |
| POST `/imports/commit` | `{ "import_token": "uuid", "row_numbers": [1] }` | 201 ImportResult | 409, 422 |
| POST `/duplicates/check` | `{ "statement": "...", "subject_id": "uuid", "grade_id": "uuid", "topic_id": "uuid", "exclude_task_id": null }` | 200 DuplicateCandidates | 422 |

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
  "version": {
    "title": "Линейное уравнение с дробями",
    "statement": "Решите уравнение (x - 1) / 2 = 3.",
    "task_type": "calculation",
    "answer_format": "number",
    "difficulty": "basic",
    "source": null
  },
  "skill_links": [
    { "skill_id": "a0dda428-f222-4b1a-9a7e-17e324947943", "weight": 0.7000, "is_primary": true },
    { "skill_id": "2aa91564-7081-4a4c-8094-a95b6e09ec31", "weight": 0.3000, "is_primary": false }
  ]
}
```

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
    "difficulty": "basic",
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

Возможный дубль не является фатальной ошибкой: успешные create/preview могут
вернуть `warnings: [{"code":"duplicate_warning","message":"...",
"candidate_task_ids":["uuid"]}]`. При commit клиент подтверждает решение
выбором строк; только фактический конфликт состояния возвращает 409.

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
| Импорт | rows/file input, ImportPreview, catalog options | preview, исправить вход, выбрать строки, commit, просмотреть warnings | idle, previewing, validation errors by row, committing, completed, error | POST `/imports/preview`, POST `/imports/commit`, catalogs |

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
* **2.8–2.11** — поиск, аудит, импорт и дедупликация.

Этот порядок — план последующих фаз, а не разрешение реализовывать их в фазе
2.1.
