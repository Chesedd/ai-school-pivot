# Content Bank: контракт управляемых тегов

Статус документа: обязательный технический контракт. Backend-фундамент фазы 1
(schema, catalog и trusted pilot API) реализован revision `20260808_01`; backend
assignment/copy/read/AND-filter фазы 2 реализованы без новой migration. CSV/XLSX tags фазы 3 реализованы без новой migration; trusted pilot admin frontend фазы 4 реализован. Финальный пользовательский frontend реализован: выбор при создании и в latest draft, read-only/card/list отображение, локализация истории и compact AND-фильтр с repeated `tag_id`, URL restoration и Back/Forward.
Базовая revision — `20260806_01`.

## 1. Контекст существующей системы

Контракт встраивается в текущую архитектуру, а не заменяет её:

- `tasks` хранит классификацию и location: `subject_id`, `grade_id`, `topic_id`,
  nullable `subtopic_id`, nullable `folder_id`, автора, timestamps и `archived_at`;
- `task_versions` хранит версионное содержание, числовую `difficulty` 1–100,
  `task_type`, `answer_format`, status, автора и approval metadata; skills связаны с
  версией через `task_skill_links`;
- создание задания атомарно создаёт `tasks`, initial `task_versions` v1 со статусом
  `draft`, skills и событие `task_created`;
- `SaveMethodologyService` блокирует версию (`SELECT FOR UPDATE`) и разрешает менять
  только latest `draft`; workflow `draft → review → approved`, `review → draft` и
  архивирование реализован отдельными командами с row locks и audit;
- `CreateVersionService` принимает только latest approved source, блокирует её,
  вызывает `clone_version` и создаёт следующий `draft`, копируя содержание, skills и
  methodology; это место должно также копировать associations тегов;
- `TaskListQuery` обслуживает общий список и переиспользуется subject/folder contents;
  repository сначала формирует подзапрос latest version каждого task, затем применяет
  полнотекстовый поиск, facets, сортировку, `total`, offset/limit;
- `/subjects/{subject_id}/contents` и `/folders/{folder_id}/contents` возвращают папки
  и direct tasks; их task page имеет тот же контракт списка;
- существующий `audit_log` append-only и пишется `AuditWriter` в бизнес-транзакции;
  справочнику тегов дополнительно нужен отдельный журнал;
- catalog endpoints сейчас имеют форму `/api/content-bank/catalog/{name}`;
- CSV/XLSX разбираются на frontend, затем нормализованный JSON проходит server preview;
  preview token ограничен actor и сроком, блокируется при commit и одноразовый; commit
  повторно проверяет catalogs и атомарно создаёт выбранные задания и audit;
- frontend содержит create form, карточку, history/status actions,
  `MethodologyEditor`, compact list filters, folder browser и единую страницу импорта;
- actor во всех routes сейчас берётся из `Settings.content_bank_dev_actor_id` и
  помещается в `ActorContext`. Модели пользователей, sessions, ownership и ролей нет.

Существующий HTTP error envelope сохраняется:

```json
{
  "error": {
    "code": "machine_readable_code",
    "message": "Сообщение для человека",
    "details": [{"field": "tag_ids", "code": "invalid", "message": "..."}],
    "request_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

## 2. Назначение и словарь

Теги — дополнительные поперечные признаки. Они не заменяют предмет, класс, тему,
подтему, навык, тип задания, формат ответа, сложность или folder location.

Хорошие теги: `ОГЭ`, `ЕГЭ`, `Олимпиадное`, `Межпредметное`, `С параметром`,
`На внимательность`, `Практическая задача`, `Для устной работы`, `Работа в группе`.
Плохие теги: `Математика`, `7 класс`, `Сложное`: это дубликаты соответственно
предмета, класса и структурированной сложности.

Семантический запрет дублирования структурированных полей обеспечивается кураторством
управляемого справочника и admin-процессом. Ненадёжная автоматическая классификация
имени или текста задания для этого не применяется.

### 2.1 Границы первой реализации

В scope входят: фиксированный справочник категорий; управляемые global/subject tags;
admin create/update; lifecycle `active|deprecated`; optional replacement; назначение
до восьми тегов draft; copy-on-new-version; immutable review/approved/archived;
отображение; AND filter; CSV/XLSX preview/commit; exact duplicate protection;
similar-name lookup; version и catalog audit.

Не входят: пользовательские предложения; состояния proposal
`pending|approved|merged|rejected`; уведомления; полноценный RBAC; физическое удаление;
автозамена deprecated associations в старых версиях. Proposal workflow — отдельная
будущая фаза после production identity/RBAC: editor создаёт предложение, admin решает
его судьбу, merge явно указывает canonical tag и пишет отдельный audit. В pilot нет
ни endpoint, ни кнопки предложения.

## 3. Доступ и доверительная граница

### 3.1 Целевая модель

- **Editor** выбирает теги доступной ему draft-версии.
- **Reviewer** видит теги, но не редактирует.
- **Admin** управляет справочником.

### 3.2 Первая pilot-реализация

Backend обеспечивает status restrictions для `draft`, `review`, `approved`,
`archived`. Actor приходит только из существующего pilot/dev механизма
`content_bank_dev_actor_id`; ownership «только свои версии» **не обеспечен** и не
декларируется. Admin endpoints — доверительный pilot interface. До production они
должны быть закрыты настоящей authentication и server-side authorization policy.
Frontend state, client-supplied role и произвольный actor/role header не являются
авторизацией и не должны добавляться.

## 4. Категории

`tag_categories` — read-only catalog:

| `code` | Display name |
| --- | --- |
| `exam` | Экзамен |
| `purpose` | Назначение |
| `methodology` | Методика |
| `task_feature` | Особенность задания |
| `usage_level` | Уровень использования |

Поля: `code varchar(32) PRIMARY KEY`, `display_name varchar(80) NOT NULL`,
`sort_order smallint NOT NULL`; `uq_tag_categories_sort_order`,
`ck_tag_categories_code` (`code ~ '^[a-z][a-z0-9_]*$'`) и
`ck_tag_categories_sort_order_nonnegative` (`sort_order >= 0`). Строки seed-ятся
миграцией в порядке таблицы. Обычный пользователь и pilot admin API их не изменяют.
Таблица, а не free-form string, даёт FK integrity, стабильные машинные коды,
детерминированную сортировку и безопасное переименование display name.

## 5. Модель данных

### 5.1 `tags`

| Column | SQL contract |
| --- | --- |
| `id` | `uuid PRIMARY KEY DEFAULT gen_random_uuid()` |
| `category_code` | `varchar(32) NOT NULL` |
| `subject_id` | `uuid NULL` |
| `name` | `varchar(80) NOT NULL` |
| `normalized_name` | `varchar(80) NOT NULL` |
| `status` | `tag_status NOT NULL DEFAULT 'active'` |
| `replacement_tag_id` | `uuid NULL` |
| `created_at` | `timestamptz NOT NULL DEFAULT clock_timestamp()` |
| `created_by` | `uuid NOT NULL` |
| `updated_at` | `timestamptz NOT NULL DEFAULT clock_timestamp()` |
| `updated_by` | `uuid NOT NULL` |

`tag_status` — PostgreSQL enum `active`, `deprecated`. Точные constraints:

- `pk_tags` на `id`;
- `fk_tags_category_code_tag_categories`:
  `category_code → tag_categories(code) ON UPDATE RESTRICT ON DELETE RESTRICT`;
- `fk_tags_subject_id_subjects`: `subject_id → subjects(id) ON DELETE RESTRICT`;
- `fk_tags_replacement_tag_id_tags`:
  `replacement_tag_id → tags(id) ON DELETE RESTRICT`;
- `uq_tags_normalized_name` на `normalized_name` — имя уникально глобально, независимо
  от subject/category/status; deprecated имя навсегда зарезервировано;
- `ck_tags_name_length`: `char_length(name) BETWEEN 1 AND 80`;
- `ck_tags_normalized_name_length`: `char_length(normalized_name) BETWEEN 1 AND 80`;
- `ck_tags_replacement_not_self`: `replacement_tag_id IS NULL OR replacement_tag_id <> id`;
- `ck_tags_active_without_replacement`:
  `status = 'deprecated' OR replacement_tag_id IS NULL`.

Indexes: `ix_tags_catalog_order(category_code, normalized_name, id)`,
`ix_tags_subject_catalog(subject_id, category_code, normalized_name, id)`,
`ix_tags_status(status)`, `ix_tags_replacement_tag_id(replacement_tag_id)` и GIN
`ix_tags_normalized_name_trgm` с `gin_trgm_ops`.

`subject_id IS NULL` означает global. Иначе тег допустим только для этого subject.
Физического delete endpoint нет. Application transaction валидирует replacement:
не self; target active; target scope совместим; цепочка не образует cycle. Database
FK/CHECK закрывают локальные инварианты, а cycle/scope требуют locked graph validation.
Перед deprecation тега, который служит replacement для других tags, операция
отклоняется: сначала надо изменить входящие ссылки. Replacement compatibility:
global source допускает только global target; subject-specific source допускает
global target либо target того же subject. Так рекомендация всегда применима там,
где применялся source.

### 5.2 `task_version_tags`

| Column | SQL contract |
| --- | --- |
| `task_version_id` | `uuid NOT NULL` |
| `tag_id` | `uuid NOT NULL` |
| `attached_at` | `timestamptz NOT NULL DEFAULT clock_timestamp()` |
| `attached_by` | `uuid NOT NULL` |

Constraints: composite `pk_task_version_tags(task_version_id, tag_id)`;
`fk_task_version_tags_task_version_id_task_versions` to `task_versions(id)
ON DELETE CASCADE`; `fk_task_version_tags_tag_id_tags` to `tags(id) ON DELETE RESTRICT`.
Связь намеренно принадлежит `task_versions`, не `tasks`.

PK покрывает загрузку набора версии и не допускает duplicates. Для reverse lookup и
AND filtering нужен `ix_task_version_tags_tag_version(tag_id, task_version_id)`.
Дополнительный index только по `task_version_id` не нужен: он является leading частью
PK. Лимит восемь — transactional business invariant, не row-local CHECK.

### 5.3 `tag_audit_log`

Append-only catalog audit: `id uuid PK`, `tag_id uuid NOT NULL`,
`action tag_audit_action NOT NULL`, `actor_id uuid NOT NULL`, `occurred_at timestamptz
NOT NULL DEFAULT clock_timestamp()`, `before_snapshot jsonb NULL`, `after_snapshot
jsonb NULL`. Enum: `tag_created`, `tag_renamed`, `tag_scope_changed`,
`tag_deprecated`, `tag_replacement_changed`. FK
`fk_tag_audit_log_tag_id_tags ON DELETE RESTRICT`; CHECK
`ck_tag_audit_log_snapshot_present` требует хотя бы один snapshot. Index
`ix_tag_audit_log_tag_occurred(tag_id, occurred_at DESC, id DESC)`.

Snapshots содержат `id`, `name`, `normalized_name`, `category_code`, `subject_id`,
`status`, `replacement_tag_id`, timestamps/actors и не вычисляются join-ом при чтении.
История остаётся после rename/deprecation. UPDATE/DELETE журнала запрещаются правами
DB application role; application предоставляет только append.

## 6. Единственная нормализация имени

`normalize_tag_name(input)` выполняется строго так:

1. Unicode NFKC;
2. trim начального/конечного Unicode whitespace;
3. collapse каждой непустой последовательности Unicode whitespace в ASCII space;
4. Unicode `casefold`;
5. замена `ё` на `е`.

Display `name` проходит шаги 1–3, сохраняя выбранный admin регистр и `ё`; оно не
заменяется normalized value. После очистки name должен иметь 1–80 Unicode code points,
не содержать Unicode category `Cc`/`Cf` control/format characters и `;`, и содержать
хотя бы одну букву или цифру (не быть только punctuation/separators). Эти правила
проверяются application validation; DB хранит очищенный name, проверяет length и
unique normalized value.

Конфликтуют `ОГЭ`, `огэ`, `  ОГЭ  `; `Для   группы` и `для группы`; любые пары,
различающиеся Unicode whitespace; `Всё` и `все`. Application validation даёт понятный
409 до flush, а `uq_tags_normalized_name` закрывает race.

## 7. Subject compatibility и ordering

- global tag доступен любой версии; scoped tag — только версии task с тем же
  `tasks.subject_id`;
- добавить можно только `active`; ранее назначенный deprecated остаётся видимым в
  исторической версии, но не может быть добавлен вновь;
- смена subject задания (если появится такой command) блокирует task row и все tags
  редактируемой версии и отклоняется при несовместимом scoped tag; прямой обход
  assignment validation невозможен;
- выдача selector: subject-specific текущего subject раньше global, затем
  `tag_categories.sort_order`, `tags.normalized_name`, `tags.id`;
- replacement следует scope rules из §5.1; deprecated source не перепривязывает
  association автоматически.

## 8. Лимит и атомарное назначение

Максимум — **8 тегов на одну task version**. Request с повторяющимся UUID отклоняется
400 `duplicate_tag_assignment`; сервер не молча нормализует неоднозначный ввод.

Выбран отдельный endpoint полного replace: tags имеют самостоятельный компактный UI и
не входят в существующий `SaveMethodologyCommand`; так не расширяется крупный
methodology payload и сохраняется атомарная семантика набора.

Repository в одной транзакции делает `SELECT ... FOR UPDATE` для `task_versions`,
проверяет latest + `draft`, сравнивает `expected_updated_at` с
`task_versions.updated_at`, блокирует выбранные tag rows, валидирует status/scope/count,
вычисляет diff, изменяет associations, обновляет `task_versions.updated_at` через
`clock_timestamp()`, пишет audit diff и commit. Один row lock сериализует два editor;
поэтому concurrent updates не превышают лимит. CAS соответствует имеющейся модели
draft locking, добавляя отсутствующую защиту от lost update. Frontend check — только UX.

## 9. Версионность

Tags принадлежат версии. Их меняют только у latest `draft`; при submit в `review` row
lock сначала сериализуется с tag save, после перехода набор immutable. В `review`,
`approved`, `archived` replace запрещён.

`clone_version` в той же транзакции после блокировки latest approved source копирует
`task_version_tags` в новый draft, сохраняя исходные `tag_id`, но записывая новые
`attached_at` и actor новой версии. Deprecated tags тоже копируются как историческое
содержание, однако новая draft не может сохранить их после удаления из набора и не
может добавить обратно; UI предлагает replacement. Копирование не создаёт
`tag_added_to_version` для каждого тега: `version_created` содержит source version и
snapshot copied tag IDs; дальнейшие изменения дают diff events. Старые версии не
меняются.

Rename справочного tag сразу меняет display name во всех DTO, потому что association
ссылается на canonical row; immutable audit snapshots сохраняют старое имя.
Deprecation не удаляет associations и не переписывает историю.

## 10. API

Все paths имеют prefix `/api/content-bank`. UUID/timestamps — строки RFC 4122/RFC 3339.

### 10.1 DTO

```json
{
  "id": "tag-uuid",
  "category": {"code": "exam", "name": "Экзамен", "sort_order": 10},
  "subject": null,
  "name": "ОГЭ",
  "normalized_name": "огэ",
  "status": "active",
  "replacement": null,
  "created_at": "2026-08-07T12:00:00Z",
  "created_by": "actor-uuid",
  "updated_at": "2026-08-07T12:00:00Z",
  "updated_by": "actor-uuid"
}
```

`subject`, когда задан: `{"id":"...","code":"math","name":"Математика"}`.
`replacement` — компактный `TagRef`:

```json
{"id":"...","name":"ЕГЭ","category_code":"exam","subject_id":null,"status":"active"}
```

`TagRef` в task DTO имеет ту же форму и nullable `replacement` (вложенный compact ref
без дальнейшей рекурсии). `normalized_name` возвращается только admin/catalog detail,
не task items.

### 10.2 Read catalog

- `GET /tag-categories` → `{"items":[{"code":"exam","name":"Экзамен",
  "sort_order":10}]}`; без pagination, пять строк.
- `GET /tags?q=&subject_id=&category_code=&status=active&offset=0&limit=20` →
  `{"items":[Tag],"total":42,"offset":0,"limit":20}`. `limit` 1–100. Если дан
  `subject_id`, включаются scoped-to-subject и global; без него — все scopes.
  Default status `active`; admin явно передаёт `active|deprecated|all`. `q` trim,
  максимум 80, substring/trigram-assisted search. Ordering по §7.
- `GET /tags/{tag_id}` → full `Tag`; deprecated тоже доступен; 404.
- `GET /tags/similar?name=...&exclude_tag_id=...&limit=5` → §15; `limit` 1–20.

`/tags/similar` объявляется раньше динамического `/{tag_id}` route.

### 10.3 Pilot admin

`POST /admin/tags`:

```json
{"category_code":"exam","subject_id":null,"name":"ОГЭ"}
```

Ответ 201 + `Location: /api/content-bank/tags/{id}` + full `Tag`.

`PATCH /admin/tags/{tag_id}` меняет name/category/scope, а у deprecated tag также
replacement (omitted — unchanged):

```json
{
  "name":"ЕГЭ профиль",
  "category_code":"exam",
  "subject_id":"subject-uuid",
  "replacement_tag_id":"replacement-uuid-or-null",
  "expected_updated_at":"2026-08-07T12:00:00Z"
}
```

Scope change проверяет **все** historical associations; изменение запрещено, если
хотя бы одна версия другого subject стала бы несовместима. Response 200 full `Tag`.

`POST /admin/tags/{tag_id}/deprecate`:

```json
{"replacement_tag_id":"uuid-or-null","expected_updated_at":"2026-08-07T12:00:00Z"}
```

Idempotency не скрывает stale write: уже deprecated с совпавшим CAS возвращает 200;
с устаревшим timestamp — 409. `GET /admin/tags/{tag_id}/usage` описан в §16.

### 10.4 Assignment

`PUT /task-versions/{version_id}/tags` полностью заменяет набор:

```json
{
  "tag_ids":["tag-uuid-1","tag-uuid-2"],
  "expected_updated_at":"2026-08-07T12:00:00Z"
}
```

200:

```json
{
  "task_id":"task-uuid",
  "task_version_id":"version-uuid",
  "version_no":2,
  "updated_at":"2026-08-07T12:01:00Z",
  "tags":[{"id":"tag-uuid-1","name":"ОГЭ","category_code":"exam","subject_id":null,"status":"active","replacement":null}]
}
```

Пустой array снимает все tags. Ordering ответа канонический (§7).

### 10.5 Расширение task responses

Поле `tags: TagRef[]` обязательное (пустой array, не null) добавляется:

- в `TaskVersionResponse` create response и initial version;
- в `TaskCardVersionResponse` для `latest_version`;
- в каждый history version DTO, чтобы выбранная версия показывала свой набор;
- в `TaskListItemResponse` и task item внутри subject/folder contents;
- в import preview resolved data и commit item.

Task version/card shape:

```json
{"id":"version-uuid","version_no":2,"status":"draft","updated_at":"...","tags":[TagRef]}
```

List/contents item:

```json
{"task_id":"...","task_version_id":"...","version_no":2,"title":"...","status":"draft","tags":[TagRef]}
```

Create response сохраняет существующие task/initial-version поля и добавляет:

```json
{"id":"task-uuid","initial_version":{"id":"version-uuid","version_no":1,"status":"draft","tags":[TagRef]}}
```

Import commit item:

```json
{"row_number":2,"task_id":"...","task_version_id":"...","version_no":1,"tags":[TagRef]}
```

## 11. Ошибки

| Status | Code | Условие |
| --- | --- | --- |
| 404 | `tag_not_found` | tag UUID отсутствует |
| 422 | `tag_name_invalid` | normalization/length/character rule |
| 409 | `tag_name_conflict` | normalized name занят, включая race/deprecated |
| 404 | `tag_category_not_found` | category code отсутствует |
| 422 | `tag_subject_mismatch` | scope не совпадает с task/replacement |
| 409 | `tag_deprecated` | попытка нового assignment inactive tag |
| 422 | `tag_limit_exceeded` | более восьми |
| 422 | `tag_replacement_invalid` | self/inactive/incoming-link/scope rule |
| 409 | `tag_replacement_cycle` | replacement создаёт cycle |
| 409 | `tag_concurrent_modification` | stale `expected_updated_at` |
| 409 | `task_version_not_editable` | не latest draft или task archived |
| 400 | `duplicate_tag_assignment` | UUID повторён в request |
| 422 | `unknown_import_tag` | имя из строки не найдено |
| 422 | `deprecated_import_tag` | найден только deprecated canonical tag |
| 409 | `tag_catalog_changed` | preview snapshot несовместим с commit |

Malformed UUID/query/body остаются существующим 422 `validation_error`. Details всегда
указывают field; import row errors дополнительно содержат `row_number`.

## 12. Task search и contents

К `GET /tasks`, `/subjects/{subject_id}/contents` и
`/folders/{folder_id}/contents` добавляется repeated parameter:
`tag_id=<uuid>&tag_id=<uuid>`. Pydantic dependency преобразует его в
`TaskListQuery.tag_ids: tuple[UUID, ...]`. Повтор одного UUID — 400
`duplicate_tag_assignment`.

Один ID требует tag в отображаемой (latest) версии; N IDs требуют все N в **одной и
той же** отображаемой версии. Associations разных historical versions одного task не
складываются. `total` считается после filter и до pagination; существующая sorting,
включая relevance, применяется после filter. Неизвестный UUID даёт 404
`tag_not_found`. Deprecated tag разрешён как filter для воспроизводимого поиска
истории/latest associations и явно помечается в filter catalog.

SQL strategy: добавить к существующему latest-version subquery correlated `EXISTS`:

```sql
WHERE EXISTS (
  SELECT 1 FROM task_version_tags tvt
  WHERE tvt.task_version_id = latest_version.id
    AND tvt.tag_id = :tag_id_1
)
AND EXISTS (... :tag_id_2)
```

При максимуме 8 и index `(tag_id, task_version_id)` это проще встраивается до count и
pagination, не размножает latest rows и исключает historical false positives.
`GROUP BY/HAVING COUNT(DISTINCT tag_id)=N` также корректен, но усложняет текущие
count/sort projections; поэтому не выбран.

Глобальный поиск `q` остаётся по title/statement/source; tag names не добавляются в
`search_vector`, чтобы rename не требовал перестройки версий. Явный `tag_id` filter
комбинируется с `q` через AND.

## 13. CSV/XLSX import

В конец `Tasks` headers добавляется `tags`. Формат: `ОГЭ; С параметром; Для повторения`.
Внутри canonical name `;` запрещён. CSV quoting остаётся обязанностью CSV parser:
сама cell может содержать semicolon-separated tags независимо от delimiter файла.
XLSX template добавляет обычную string cell/header и instruction, без formulas.

Preview для каждой строки split по `;`, применяет §6, игнорирует пустые fragments как
ошибку `tag_name_invalid`, безопасные normalized duplicates удаляет с warning
`duplicate_import_tag`, находит **только exact normalized** existing tags, требует
active, проверяет subject и максимум 8. Unknown/deprecated/mismatch делает строку
`invalid`; новые tags не создаются.

```json
{
  "row_number":2,
  "status":"invalid",
  "normalized":{"subject_id":"...","title":"..."},
  "resolved_tags":[
    {"input":" ОГЭ","tag_id":"...","name":"ОГЭ","category_code":"exam","subject_id":null}
  ],
  "issues":[
    {"severity":"error","code":"unknown_import_tag","field":"tags","message":"Тег не найден.","value":"Для повторения"}
  ]
}
```

Server preview record сохраняет для каждой resolved позиции `tag_id` и immutable
catalog fingerprint: `updated_at`, status, subject scope, normalized name; общий token
также сохраняет fingerprint набора. Commit использует IDs из preview token, **не ищет
заново display names**. Под row lock проверяет, что tag существует, остаётся active,
его `updated_at`, normalized name и scope совпадают. Rename, deprecation или scope
change после preview дают 409 `tag_catalog_changed`, весь commit rollback и требуют
новый preview. Совместимый unrelated catalog change не блокирует commit.

Tags initial v1 вставляются атомарно с task, version, skills, import token consumption и
`task_created`; version tag audit events создаются по initial empty→resolved diff.
Commit response использует shape §10.5. Ни одна выбранная строка не импортируется при
ошибке любой выбранной строки.

## 14. Audit

### 14.1 Version audit

В существующий `audit_action` добавляются `tag_added_to_version` и
`tag_removed_from_version`. На full replace события строятся по before/after diff в
детерминированном порядке tag UUID. `audit_log.details` содержит immutable snapshot:

```json
{
  "task_id":"...","version_id":"...","tag_id":"...",
  "canonical_name":"ОГЭ","category_code":"exam","subject_id":null,
  "actor_id":"...","occurred_at":"..."
}
```

Top-level `audit_log` уже хранит task/version/actor/time; дублирование в details
намеренно делает snapshot самодостаточным. Actor/time задаёт сервер.

### 14.2 Catalog audit

Отдельный `tag_audit_log` пишет `tag_created`, `tag_renamed`, `tag_scope_changed`,
`tag_deprecated`, `tag_replacement_changed`; при PATCH с несколькими изменениями — по
одному событию каждого типа с полными before/after snapshots. Catalog/version mutation
и audit всегда в одной transaction: rollback отменяет оба; audit без успешной mutation
не возникает.

## 15. Similarity

Exact normalized duplicate блокируется. Similar names не блокируются, не merge-ятся и
не назначаются автоматически: admin получает warning перед create/rename и принимает
явное решение. Существующая PostgreSQL схема уже использует `pg_trgm`, поэтому endpoint
использует `similarity(tags.normalized_name, :normalized)` и trigram index. Threshold
0.30, затем `similarity DESC, normalized_name, id`; exact conflict тоже возвращается
с `exact_match: true`, хотя mutation будет отклонена.

`GET /tags/similar?name=Олимпиада&exclude_tag_id=...&limit=5`:

```json
{
  "normalized_query":"олимпиада",
  "items":[
    {"tag":{"id":"...","name":"Олимпиадное","category_code":"task_feature","subject_id":null,"status":"active"},"similarity":0.71,"exact_match":false}
  ]
}
```

## 16. Admin usage

`GET /admin/tags/{tag_id}/usage` доступен и для deprecated:

```json
{
  "tag_id":"...",
  "historical_version_count":14,
  "distinct_task_count":9,
  "latest_version_count":7,
  "status_counts":{"draft":2,"review":1,"approved":5,"archived":6},
  "latest_status_counts":{"draft":1,"review":0,"approved":4,"archived":2}
}
```

`historical_version_count` считает associations, поэтому несколько versions одного task
считаются несколько раз; `distinct_task_count` — tasks; `latest_version_count` — только
association в max `version_no` каждого task, включая archived. Status counts относятся
к version rows. Запросы опираются на `ix_task_version_tags_tag_version`,
`uq_task_versions_task_version_no` и `ix_task_versions_task_id_status`.

## 17. Будущая Alembic migration

Новая linear revision имеет `down_revision = "20260806_01"`; старые revisions не
редактируются. Порядок upgrade:

1. удостовериться, что `pg_trgm` уже создан существующей историей;
2. создать enums `tag_status`, `tag_audit_action`, расширить `audit_action` новыми
   version events безопасным PostgreSQL способом;
3. создать `tag_categories`, seed пять строк;
4. создать `tags` без self FK, затем self FK и indexes;
5. создать `task_version_tags` и indexes;
6. создать `tag_audit_log` и index;
7. добавить ORM mappings после migration contract implementation.

Backfill отсутствует: все существующие versions получают логически пустой `tags: []`;
никакие guesses по тексту/классификации не выполняются.

Downgrade сначала удаляет audit/association tables, затем tags, categories и новые
enums/values настолько, насколько требует PostgreSQL recreate strategy. Намеренно
теряются все tag definitions, assignments и tag audit. `tasks`, `task_versions`, их
содержание и прежний audit не удаляются. Re-upgrade снова seed-ит только categories;
tags остаются пустыми.

## 18. Concurrency matrix

| Сценарий | Защита | HTTP/code | Rollback и audit |
| --- | --- | --- | --- |
| Два admin rename одного tag | `SELECT FOR UPDATE` + `expected_updated_at`; первый меняет `clock_timestamp()` | проигравший 409 `tag_concurrent_modification` | весь второй tx rollback; только audit победителя |
| Два editor replace одной draft | version row lock + CAS | проигравший 409 `tag_concurrent_modification` | associations/diff audit проигравшего отсутствуют |
| Tag deprecated между form и save | lock version, затем tag rows; active recheck | 409 `tag_deprecated` | весь set неизменен, audit отсутствует |
| Catalog изменён между preview/commit | preview row lock + stored tag fingerprints | 409 `tag_catalog_changed` | ни tasks, ни token consumption, ни audit |
| Version уходит в review одновременно с save | обе команды lock одну version; победитель первый | если submit первый: 409 `task_version_not_editable`; если save первый: submit фиксирует новый set и 200 | проигравшая mutation rollback; audit только committed операций |
| Два create с одинаковым normalized name | application precheck + `uq_tags_normalized_name`; unique violation mapped | один 201, второй 409 `tag_name_conflict` | row и audit проигравшего rollback |

Lock order для устранения deadlock: version/task row → tag rows по UUID → replacement
graph rows по UUID → associations. Admin mutation блокирует primary tag, затем related
tags по UUID. Ни один audit не commit-ится отдельно.

## 19. Frontend contract

### 19.1 Draft editor

Поле «Теги» выполняет debounced search, передаёт current subject, показывает scoped
раньше global и только active. Выбранные значения — removable chips; максимум 8,
duplicate UUID disabled. Save передаёт complete set + latest `updated_at`; 409 вызывает
reload/reconciliation, не overwrite. В non-draft поле read-only. В первой версии нет
proposal action; для отсутствующего значения: «Тег не найден. Обратитесь к
администратору справочника».

### 19.2 Card и history

Card показывает tags latest version; history/selected version — именно её tags.
Deprecated chip визуально и текстом отмечен «Устарел», не только цветом; replacement
показывается рекомендацией «Рекомендуется: …», но association не переписывается.

### 19.3 Compact filter

Необязательный multiselect «Теги» объясняет «Задание должно содержать все выбранные
теги». URL хранит repeat params `tag_id=A&tag_id=B` в стабильном selection order;
reload восстанавливает выбор. Chips входят в общий active-filter summary и общий reset.
Task item показывает tags рядом с title: первые три по canonical ordering и `+N` с
доступным полным списком. Deprecated selected filter остаётся отображаемым.

### 19.4 Admin screen

List поддерживает search, category/status/subject filters, pagination; create/edit;
similar warning без блокировки; usage; deprecate; compatible replacement picker.
Экран постоянно содержит pilot warning: «Интерфейс не защищён production RBAC».
Destructive deprecation требует confirmation с usage counts.

Все controls имеют labels, keyboard navigation, visible focus, live status/error;
chips имеют доступные названия remove buttons, status не кодируется одним цветом.

## 20. Обязательная тестовая матрица

Будущая реализация считается неполной без:

- unit/property tests NFKC, casefold, trim/collapse, Unicode whitespace, `ё/е`, controls,
  punctuation-only, semicolon и 80-character boundary;
- application + PostgreSQL duplicate race и unique-error mapping;
- global/scoped compatibility, subject change, deprecated add rejection/preservation;
- 0/8/9 count, duplicate request, atomic replacement, concurrent max-eight;
- immutable review/approved/archived и latest-only draft;
- clone copy, old-version stability, deprecated copy behavior;
- admin/editor CAS and timestamp refresh;
- replacement self/scope/inactive/incoming references and multi-hop cycle;
- one/many AND search, historical false-positive fixture, q combination, unknown and
  deprecated filters, filtered total, sorting and pagination;
- similarity ordering/threshold/exclusion and no automatic merge;
- exact version/catalog audit snapshots, before/after diff, transaction rollback;
- CSV and XLSX header/template (no formulas), parser split/duplicates/limit;
- preview resolved shape and unknown/deprecated/mismatch invalid rows;
- commit by IDs, catalog change between preview/commit, atomic task/tags/audit/token;
- frontend selector search/order/chips/limit/duplicate/status/CAS error;
- filter URL/reload/reset/AND explanation and card/history/deprecated replacement;
- accessibility keyboard, labels, focus, announcements, non-color status;
- real PostgreSQL integration for locks, FK/check/unique/index query plans and races;
- Alembic upgrade, downgrade, re-upgrade from `20260806_01`, empty existing versions.

## 21. Последовательность реализации и gates

1. **Backend schema, catalog, pilot admin API.** Вход: этот контракт, head
   `20260806_01`, `pg_trgm`, pilot actor. Готово: migration/ORM, seeded categories,
   normalization, CRUD/deprecation/similarity/usage/CAS/audit проходят PostgreSQL tests.
2. **Version assignment, copy, status protection, audit, search.** Вход: phase 1 и
   текущие version locks/latest query. Готово: atomic PUT, max 8, clone, immutable
   statuses, task DTOs, AND filters/total and audit проходят unit/integration races.
3. **CSV/XLSX preview and commit (реализовано).** Вход: phases 1–2 и существующий token workflow.
   Готово: template/parser/preview resolution/fingerprint/atomic commit и catalog-change
   cases покрыты CSV/XLSX/PostgreSQL tests.
4. **Admin frontend.** Вход: stable admin/catalog APIs. Готово: list/forms/similar/
   usage/deprecate/replacement и pilot warning проходят UI/accessibility acceptance.
5. **Draft editor, display, compact task filter.** Вход: assignment and task query DTOs.
   Готово: chips/CAS/read-only/card/history/URL AND filter работают после reload и
   проходят component/accessibility tests.
6. **Full PostgreSQL and UI acceptance.** Вход: phases 1–5. Готово: migration cycle,
   concurrency suite, import E2E, latest-version search, browser workflows и regression
   suite зелёные; production rollout остаётся заблокирован до настоящих auth/RBAC.

## 22. Зафиксированные решения

Первая версия намеренно выбирает global uniqueness имени, strict duplicate request,
separate atomic PUT, correlated `EXISTS`, immutable preview fingerprints, no backfill,
no physical delete и no proposals. Эти решения являются контрактом, а не открытыми
вариантами реализации.
