# Content Bank: контракт иерархии папок заданий

> **Статус на 2026-08-07:** backend и schema реализованы revision `20260806_01`.
> Frontend-дерево ещё не реализовано, поэтому текущий UI остаётся плоским.
> Новые endpoints доступны для локальной и API-проверки; см.
> `content-bank-folder-hierarchy-backend-verification.md`.

## 1. Контекст, термины и неизменяемые границы

Базой остаётся реализованный Content Bank: `Task` — стабильная карточка,
`TaskVersion` — версия содержания, а `Subject` — педагогический справочник.
Числовая `difficulty` остаётся `SMALLINT` 1–100 с
`ck_task_versions_difficulty_range`; список продолжает принимать
`difficulty_min`/`difficulty_max`. Новый контракт этого не меняет.

* **Предмет (`Subject`)** одновременно является виртуальным корнем дерева. Он не
  является папкой и не создаётся, не переименовывается и не удаляется folder API.
  Физическая папка-копия предмета не создаётся.
* **Папка (`TaskFolder`, API-термин `folder`, UI-термин «Папка»)** — только
  организационный узел одного предмета. Она не заменяет `topic`, `subtopic`,
  `skill` или `grade`, не влияет на классификацию и не входит в `task_versions`.
* **Размещение задания** — nullable `Task.folder_id`. `NULL` означает виртуальный
  корень его предмета; иначе задание находится ровно в одной папке того же
  предмета. Размещение не версионируется. Перемещение не меняет `subject_id`, не
  создаёт `TaskVersion` и не затрагивает содержимое версий.

Максимальная глубина равна **8 папкам** от предмета: папка непосредственно под
предметом имеет depth 1. Обычное содержимое уровня включает только его
непосредственные дочерние папки и задания; потомки попадают лишь в явно
рекурсивный поиск.

## 2. Продуктовые правила

1. Каноническое имя получается удалением Unicode whitespace по краям (`strip`),
   обязательно, содержит 1–120 Unicode code points, не равно `.` или `..` и не
   содержит `/` либо `\`. В БД хранится очищенное, но с сохранением регистра имя.
2. Среди детей одного parent нельзя повторить имя без учёта регистра. Для
   корневых папок parent — виртуальный корень одного subject. Одинаковые имена в
   разных parents разрешены. Сравнение MVP — PostgreSQL `lower(name)`, без
   транслитерации и без Unicode normalization/casefold сверх поведения БД.
3. Папку можно создать, переименовать, переместить под папку того же предмета или
   в корень предмета. Нельзя поместить её в себя/потомка, сменить предмет или
   получить глубину поддерева больше 8.
4. Удаляется только полностью пустая папка. Дочерняя папка или любое задание,
   включая архивное, делает её непустой. Рекурсивного удаления в MVP нет.
5. Архивирование задания сохраняет `folder_id`. Импорт CSV/XLSX не создаёт папок,
   не требует колонки пути и создаёт задания с `folder_id = NULL`.
6. Папки выводятся перед заданиями и по умолчанию сортируются по
   `lower(name) ASC, name ASC, id ASC`. Сортировка заданий сохраняет текущий
   контракт, с `task_id` как стабильным последним ключом.
7. Глобальный поиск не зависит от папок. В выбранном предмете доступен поиск по
   всему предмету, а в выбранной папке — явный рекурсивный поиск по поддереву.

## 3. Точная модель данных

### 3.1 Новая таблица `task_folders`

| Колонка | PostgreSQL type / nullability | Значение |
| --- | --- | --- |
| `id` | `UUID NOT NULL DEFAULT gen_random_uuid()` | PK |
| `subject_id` | `UUID NOT NULL` | владеющий предмет |
| `parent_id` | `UUID NULL` | parent; `NULL` — корень предмета |
| `name` | `VARCHAR(120) NOT NULL` | очищенное отображаемое имя |
| `created_by` | `UUID NOT NULL` | actor из `ActorContext`, пока без user FK |
| `updated_by` | `UUID NOT NULL` | actor последней структурной записи |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP` | создание |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP` | версия для concurrency |

Actor/owner-модели нет: папка не персональная, поэтому `owner_id` отсутствует.

**Constraints:**

* `pk_task_folders` primary key (`id`);
* `fk_task_folders_subject_id_subjects`: `subject_id -> subjects.id ON DELETE RESTRICT`;
* `uq_task_folders_id_subject_id`: UNIQUE (`id`, `subject_id`), опора составных FK;
* `fk_task_folders_parent_subject`: (`parent_id`, `subject_id`) ->
  `task_folders(id, subject_id) ON DELETE RESTRICT`, `MATCH SIMPLE`; так parent
  физически не может принадлежать другому предмету;
* `ck_task_folders_parent_not_self`: `parent_id IS NULL OR parent_id <> id`;
* `ck_task_folders_name_valid`: `name = btrim(name) AND char_length(name) BETWEEN
  1 AND 120 AND name NOT IN ('.', '..') AND strpos(name, '/') = 0 AND
  strpos(name, chr(92)) = 0`.

**Индексы (точные имена):**

* unique expression partial `uq_task_folders_root_subject_name_ci` on
  (`subject_id`, `lower(name)`) `WHERE parent_id IS NULL`;
* unique expression partial `uq_task_folders_parent_name_ci` on
  (`parent_id`, `lower(name)`) `WHERE parent_id IS NOT NULL`;
* `ix_task_folders_subject_parent` on (`subject_id`, `parent_id`);
* `ix_task_folders_parent_id` on (`parent_id`);
* `ix_task_folders_subject_name` on (`subject_id`, `lower(name)`, `id`).

### 3.2 Изменение `tasks`

Добавить `folder_id UUID NULL`, FK
`fk_tasks_folder_subject` (`folder_id`, `subject_id`) ->
`task_folders(id, subject_id) ON DELETE RESTRICT`, и индекс
`ix_tasks_subject_folder` (`subject_id`, `folder_id`). Отдельный FK только по
`folder_id` не создаётся. `NULL` — размещение непосредственно в корне предмета.
Составной FK запрещает cross-subject placement и RESTRICT запрещает удаление
папки с любым заданием независимо от `archived_at`.

### 3.3 Что обеспечивает БД, а что application

PostgreSQL обеспечивает типы/nullability, допустимую форму уже переданного
имени, self-parent, same-subject parent/task, sibling name uniqueness и запрет
удаления папки с прямым child/task. Application переводит unique/FK violations в
доменные ошибки.

Application обязан выполнить trim; проверить существование/доступ; обнаружить
любой цикл; вычислить depth новой позиции и высоту всего переносимого поддерева;
гарантировать лимит 8; различить причины RESTRICT; вести audit. Эти свойства
произвольного adjacency tree обычными CHECK/FK не выражаются.

### 3.4 Пошаговая будущая миграция

**Upgrade после head `20260730_01`:** (1) создать `task_folders` и все constraints;
(2) создать три non-unique и два partial unique индекса; (3) добавить nullable
`tasks.folder_id`; (4) создать составной FK и индекс; (5) не выполнять backfill:
все существующие задания автоматически остаются в корне; (6) расширить enum
`audit_action` значениями из §9 отдельными autocommit SQL statements, если этого
требует версия PostgreSQL/Alembic. Не создавать строки-корни и не менять старые
revisions или `task_versions`.

**Downgrade:** сначала явно удалить/отклонить все новые folder audit events,
которые enum старой схемы представить не может; удалить
`fk_tasks_folder_subject`, `ix_tasks_subject_folder`, затем `tasks.folder_id`;
удалить индексы/constraints и `task_folders`; пересоздать `audit_action` без новых
значений через временный enum и cast, сохранив прежние события. Downgrade теряет
только организационную структуру; tasks и versions сохраняются. Перед downgrade
оператор получает явное предупреждение об этой потере.

## 4. Application-инварианты и транзакции

Все structural commands выполняются в одном UoW. Для create/rename/move/delete
folder сервис в начале берёт `pg_advisory_xact_lock` от стабильного 64-bit hash
`subject_id`; это сериализует изменения дерева предмета. Затем строки target,
parent и при move всё поддерево читаются `FOR UPDATE`. Перед записью повторяются
existence, expected timestamp, sibling-name, cycle и depth проверки; commit
происходит после audit. `IntegrityError` всё равно маппится в конкретный 409.

* **Create:** проверить subject; нормализовать имя; lock subject; повторно
  проверить parent и его subject; depth(parent)+1 <= 8; проверить sibling name;
  insert с actor/timestamps.
* **Rename:** lock subject и folder; `expected_updated_at` должен совпасть;
  нормализовать имя; проверить sibling conflict, update `name`, `updated_by/at`.
  Повтор того же имени всё равно обновляет audit только если значение реально
  изменилось; иначе возвращает текущий DTO без события.
* **Move folder:** lock; проверить expected timestamp; target parent существует и
  того же subject; получить descendants recursive CTE; target не folder и не в
  descendants; проверить sibling name; `new_parent_depth + 1 + subtree_height <=
  8`; update parent/timestamp и audit. Проверяется всё поддерево, не только root.
* **Delete:** lock folder и subject, проверить expected timestamp, под тем же lock
  запросами `EXISTS` проверить direct child и task без фильтра archived; DELETE;
  FK является финальной защитой. Уже исчезнувшая папка — `folder_not_found`, не
  успешный idempotent delete.
* **Move task:** одна транзакция, `SELECT task FOR UPDATE`; сравнить
  `expected_folder_id` (CAS); если target не NULL, lock/read folder и сравнить
  subjects; update `tasks.folder_id` и `updated_at`; append task audit; commit.
* **Create task in folder:** существующий create UoW до insert читает target
  folder, проверяет subject и создаёт task, version, skills и `task_created` audit
  атомарно. При `folder_id = NULL` поведение прежнее.

Удалённая/недоступная folder всегда даёт `folder_not_found` (не раскрываем
существование чужого ресурса). Сбой optimistic precondition или serialization —
`folder_concurrent_modification`; клиент перечитывает tree/content и повторяет
осознанно. Deadlock/serialization failure повторяется сервером максимум один раз
для read-safe части; команда целиком не повторяется автоматически после
неизвестного commit outcome.

## 5. DTO, commands, queries и repository ports

Даты — UTC ISO 8601, UUID — строки. Выбран **полный tree endpoint**: ожидаемый
MVP-объём мал, depth ограничен 8, а единый снимок упрощает URL/breadcrumb. Задания
в tree не включаются; contents остаётся пагинированным.

```text
FolderSummaryDTO(id, subject_id, parent_id, name, depth, created_at, updated_at)
FolderTreeNodeDTO(id, subject_id, parent_id, name, depth, children: tuple[FolderTreeNodeDTO])
BreadcrumbDTO(subject: SubjectRootDTO, folders: tuple[FolderSummaryDTO])
TaskLocationDTO(subject_id, folder_id, breadcrumb)
TaskListItemDTO(existing fields..., folder_id: UUID|null, folder_name: str|null)
LevelContentsDTO(subject, folder: FolderSummaryDTO|null, breadcrumb,
                 folders: tuple[FolderSummaryDTO], tasks: TaskListPageDTO,
                 level_task_total: int, subject_task_total: int)
CreateFolderCommand(subject_id, parent_id: UUID|null, name, actor)
RenameFolderCommand(folder_id, name, expected_updated_at, actor)
MoveFolderCommand(folder_id, parent_id: UUID|null, expected_updated_at, actor)
DeleteFolderCommand(folder_id, expected_updated_at, actor)
MoveTaskCommand(task_id, folder_id: UUID|null, expected_folder_id: UUID|null, actor)
FolderTreeQuery(subject_id)
LevelContentsQuery(subject_id, folder_id, current filters, offset, limit, sort)
TaskListQuery(existing fields..., folder_id: UUID|null, folder_scope: direct|subtree|null)
```

`TaskCreateRequest/Command`, `TaskResponse`, `TaskCardResponse` совместимо
расширяются nullable `folder_id`; `TaskListItemResponse` — `folder_id` и
`folder_name`. Отсутствующий `folder_id` в create равнозначен `null`.

Required application services: `GetFolderTreeService`, `GetLevelContentsService`,
`CreateFolderService`, `RenameFolderService`, `MoveFolderService`,
`DeleteFolderService`, `MoveTaskService`; существующие `CreateTaskService` и
`ListTasksService` расширяются.

Required repository methods:

```text
list_subject_roots(); list_folder_tree(subject_id); get_level_contents(query)
get_folder(folder_id); get_folder_for_update(folder_id)
get_folder_subtree_for_update(folder_id); sibling_name_exists(subject_id,parent_id,name,exclude_id)
create_folder(record); rename_folder(...); move_folder(...); delete_empty_folder(...)
lock_task(task_id); set_task_folder(task_id,folder_id,updated_at)
list_tasks(query); create_task_with_initial_version(command,actor); append_audit(event)
```

Repository не commit-ит. Recursive CTE возвращает depth/height и используется
для tree/search/subtree validation. Application владеет UoW и error mapping.

## 6. REST API

Общий prefix — `/api/content-bank`; envelope ошибки остаётся
`{"error":{"code","message","details","request_id"}}`. `details` для новых
ошибок — объект контекста (не массив). Unknown request fields запрещены.

### 6.1 Сводка endpoints

| Method/path | Назначение | Успех | Идемпотентность |
| --- | --- | --- | --- |
| `GET /catalog/subjects` | предметы как виртуальные roots | 200 | да |
| `GET /subjects/{subject_id}/folders/tree` | полное дерево без tasks | 200 | да |
| `GET /subjects/{subject_id}/contents` | прямое содержимое root | 200 | да |
| `GET /folders/{folder_id}/contents` | прямое содержимое folder | 200 | да |
| `POST /subjects/{subject_id}/folders` | создать root/nested folder | 201 | нет |
| `PATCH /folders/{folder_id}` | переименовать | 200 | условно, по timestamp |
| `POST /folders/{folder_id}/move` | переместить folder | 200 | условно, desired state |
| `DELETE /folders/{folder_id}` | удалить пустую | 204 | нет: повтор даёт 404 |
| `PUT /tasks/{task_id}/location` | переместить task/root | 200 | условно, CAS |
| `POST /tasks` | создать task с nullable folder | 201 | нет |
| `GET /tasks` | global/subject/subtree search | 200 | да |

### 6.2 Общие query и responses

Contents принимает существующие `grade_id`, `topic_id`, `subtopic_id`,
`skill_id`, `task_type`, `difficulty_min`, `difficulty_max`, `status`, `q`,
`offset=0`, `limit=20`, `sort_by`, `sort_order`. Folder list не пагинируется;
task pagination/`total` относится только к tasks. Contents всегда direct и не
принимает `folder_scope`.

`GET /tasks` сохраняет прежние параметры и добавляет `folder_id` и
`folder_scope=direct|subtree`. Правила: без `subject_id/folder_id` — global;
`subject_id` без folder — весь subject; `folder_id` требует совпадающий
`subject_id`; `folder_scope` разрешён только с `folder_id`, default `direct`.
Для subtree search клиент передаёт `q`, `subject_id`, `folder_id`,
`folder_scope=subtree`. Global search не передаёт folder-параметры.

Level response:

```json
{"subject":{"id":"...","name":"Математика"},"folder":null,
 "breadcrumb":[],"folders":[{"id":"...","subject_id":"...","parent_id":null,"name":"Алгебра","depth":1,"created_at":"...Z","updated_at":"...Z"}],
 "tasks":{"items":[],"total":0,"offset":0,"limit":20},
 "level_task_total":0,"subject_task_total":12}
```

Tree response: `{"subject":{"id":"...","name":"Математика"},"folders":[
{"id":"...","subject_id":"...","parent_id":null,"name":"Алгебра","depth":1,"children":[]}]}`.
Folder mutation response is `FolderSummaryDTO`; create also returns
`Location: /content-bank/subjects/{subject_id}/folders/{id}`. Task location
response: `{"task_id":"...","subject_id":"...","folder_id":null,
"previous_folder_id":"...","updated_at":"...Z"}`.

### 6.3 Точные requests, примеры и ошибки

* `GET /catalog/subjects`: no query; response — существующий `CatalogResponse`,
  например `{"catalog":"subjects","items":[{"id":"...","name":"Математика"}]}`;
  200; стандартный 500.
* `GET /subjects/{subject_id}/folders/tree`: no query; response Tree выше; 200;
  `subject_not_found` 404.
* `GET /subjects/{subject_id}/contents?...`: query §6.2; Level response; 200;
  `subject_not_found` 404, `validation_error` 422.
* `GET /folders/{folder_id}/contents?...`: query §6.2; тот же response с folder и
  полным breadcrumb; 200; `folder_not_found` 404, `validation_error` 422.
* `POST /subjects/{subject_id}/folders` body
  `{"name":" Алгебра ","parent_id":null}`; response summary с `name:"Алгебра"`;
  201; folder name/parent/subject/depth/name conflict errors (§7).
* `PATCH /folders/{folder_id}` body
  `{"name":"Геометрия","expected_updated_at":"2026-07-30T10:00:00Z"}`;
  summary; 200; not found/name/name conflict/concurrency errors.
* `POST /folders/{folder_id}/move` body
  `{"parent_id":null,"expected_updated_at":"2026-07-30T10:00:00Z"}`;
  summary; 200; not found/mismatch/cycle/depth/name/concurrency errors.
* `DELETE /folders/{folder_id}?expected_updated_at=2026-07-30T10%3A00%3A00Z`:
  empty body; 204 empty response; not found/nonempty/concurrency errors.
* `PUT /tasks/{task_id}/location` body
  `{"folder_id":"...","expected_folder_id":null}` (to root: `folder_id:null`);
  TaskLocation response; 200; task/folder not found, cross-subject placement and
  concurrency errors. Sending current desired location with matching expected
  value returns 200 without duplicate audit.
* `POST /tasks`: existing body gains `"folder_id":"..."` next to `subject_id`;
  response gains same nullable field; 201; existing errors plus folder not found
  and `task_folder_subject_mismatch`. Example fragment:
  `{"subject_id":"...","folder_id":"...","grade_id":"...","topic_id":"...","subtopic_id":null,"initial_version":{"difficulty":50,"...":"unchanged"}}`.
* `GET /tasks?q=квадрат&subject_id=...&folder_id=...&folder_scope=subtree&difficulty_min=25&difficulty_max=75`:
  existing TaskListPage with item `folder_id`/`folder_name`; 200;
  folder/subject mismatch 409 or validation 422. Global example:
  `/tasks?q=квадрат&difficulty_min=1&difficulty_max=100`.

GET/PUT/PATCH/POST-move are safe to retry only according to table semantics;
create POST is not. Validation failures use 422; domain conflicts use 409.

## 7. Machine-readable errors

| Code | HTTP | `details` context | Условие |
| --- | ---: | --- | --- |
| `folder_not_found` | 404 | `folder_id` | отсутствует/удалена/недоступна folder |
| `subject_not_found` | 404 | `subject_id` | нет subject root |
| `folder_name_invalid` | 422 | `field`, `reason`, `min_length`, `max_length` | имя нарушает §2 |
| `folder_name_conflict` | 409 | `subject_id`, `parent_id`, `name`, `conflicting_folder_id` если доступен | case-insensitive sibling duplicate |
| `folder_subject_mismatch` | 409 | `folder_id`, `folder_subject_id`, `subject_id` | parent/query и subject различаются |
| `folder_cycle` | 409 | `folder_id`, `parent_id` | self/descendant target |
| `folder_max_depth_exceeded` | 409 | `folder_id` nullable, `parent_id`, `max_depth:8`, `resulting_depth` | новое дерево глубже 8 |
| `folder_not_empty` | 409 | `folder_id`, `has_child_folders`, `has_tasks` | delete nonempty, archived counted |
| `task_folder_subject_mismatch` | 409 | `task_id` nullable, `task_subject_id`, `folder_id`, `folder_subject_id` | task/create target другого subject |
| `folder_concurrent_modification` | 409 | `resource_type`, `resource_id`, `expected_updated_at` или `expected_folder_id`, `actual_*` | stale CAS/serialization conflict |
| `task_not_found` | 404 | `task_id` | move неизвестного task |

Пример: `{"error":{"code":"folder_cycle","message":"Папку нельзя переместить в её поддерево.","details":{"folder_id":"...","parent_id":"..."},"request_id":"..."}}`.
Envelope совместим по верхнему уровню; реализация должна расширить текущие
exception classes так, чтобы не терять structured details.

## 8. Навигация и frontend URL (будущая фаза)

* `/content-bank` — landing/global search и список subject roots.
* `/content-bank/subjects/{subject_id}` — виртуальный root предмета.
* `/content-bank/subjects/{subject_id}/folders/{folder_id}` — выбранная folder.
* URL — source of truth; query string хранит `q`, filters, sort, offset/limit.
  Refresh/direct link заново загружает subject, tree и contents. Folder должна
  принадлежать subject из URL, иначе ошибка/редирект как ниже.
* Breadcrumb: `Content Bank / Subject / folder ... / selected folder`; каждый
  сегмент — ссылка, вычисленная backend breadcrumb. Выбранный tree node получает
  `aria-current`; Back/Forward восстанавливают URL, selection, filters и page.
* После create перейти в новую folder. После rename остаться на том же URL и
  обновить tree/breadcrumb. После move folder остаться на её URL с новым
  breadcrumb. После delete перейти на бывший parent или subject root.
* После move task удалить item из direct view; если destination — текущий level,
  вставить/перезагрузить его; task card URL `/content-bank/tasks/{task_id}` не
  меняется.
* Удалённая/недоступная folder: показать `folder_not_found`, удалить stale
  selection и предложить ссылку на subject root; автоматически не выбирать
  произвольную соседнюю folder.
* Drag-and-drop контрактом не вводится; конкретный control определит UI-фаза.

Четыре разные empty states: (1) `subject_task_total=0` — в предметной базе вообще
нет tasks; (2) root без folders и direct tasks; (3) selected folder без children
и direct tasks; (4) `tasks.total=0` при активном q/filter — ничего не найдено.

## 9. Поиск, фильтры, сортировка и audit

### 9.1 Search/filter contract

Глобальный `GET /tasks` ищет по всем folders/root. Subject search задаёт только
`subject_id`. Folder subtree search задаёт `subject_id`, `folder_id`,
`folder_scope=subtree`; сама folder включена. Direct contents никогда не включает
потомков. Все текущие filters применяются AND после scope, включая числовые
`difficulty_min`/`difficulty_max` 1–100. Очистка q/filters сохраняет текущий
subject/folder, возвращает direct mode и offset 0; «глобальный поиск» отдельно
сбрасывает location scope.

Folders не смешиваются с task `total` и pagination. Они всегда все, раньше tasks,
с сортировкой §2. Tasks имеют `total` после scope/search/filters и существующую
offset pagination (20 default, 100 max). При q default `relevance DESC`, иначе
сохраняется current default `created_at DESC`; явные `title`, `difficulty`,
`updated_at`, `status`, `version_no` работают как раньше.

### 9.2 Audit contract

Текущий `audit_log` task-centric достаточен для перемещения task: добавить enum
action **`task_folder_moved`**, событие с `task_id`, nullable
`task_version_id/version_no`, actor, `occurred_at` и immutable JSON details:
`{"before":{"folder_id":null,"folder_name":null},"after":{"folder_id":"...","folder_name":"Алгебра"}}`.
Имена сохраняются snapshot-ом, поэтому rename/delete позже не меняет историю.
Перемещение не создаёт content version.

Для folder lifecycle нужен отдельный совместимый **`folder_audit_log`**, потому
что `audit_log.task_id NOT NULL`. Колонки: `id UUID PK`, `folder_id UUID NULL`
**без FK** (история переживает delete), `subject_id UUID NOT NULL` без FK,
`action audit_action NOT NULL`, `actor_id UUID NOT NULL`, `details JSONB NOT NULL
DEFAULT '{}'`, `occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP`;
индекс `ix_folder_audit_folder_occurred_at(folder_id, occurred_at)` и
`ix_folder_audit_subject_occurred_at(subject_id, occurred_at)`. Enum также
получает `folder_created`, `folder_renamed`, `folder_moved`, `folder_deleted`.
Details всегда имеют before/after snapshots с `id`, `subject_id`, `parent_id`,
`name`; create before null, delete after null. Audit append атомарен mutation.

## 10. Acceptance criteria будущей реализации

| # | Сценарий и ожидаемый результат | Уровень |
| ---: | --- | --- |
| 1 | Subjects показаны virtual roots; физических копий нет | integration + frontend test |
| 2 | Root folder создаётся с parent null/depth 1 | unit + integration |
| 3 | Nested folder создаётся в том же subject | unit + integration |
| 4 | Цепочка depth 8 успешно создаётся | integration |
| 5 | Девятый уровень отклонён `folder_max_depth_exceeded` | unit + integration |
| 6 | `Алгебра`/`алгебра` у одного parent конфликтуют | integration |
| 7 | Одинаковое имя в разных branches разрешено | integration |
| 8 | Rename trim-ит имя, обновляет tree/breadcrumb/audit | unit + integration + frontend test |
| 9 | Move обновляет parent и breadcrumb всего subtree | integration + frontend test |
| 10 | Move в себя/descendant отклонён `folder_cycle` | unit + integration |
| 11 | Folder/task нельзя переместить между subjects | integration |
| 12 | Empty folder удаляется, direct URL затем 404 | integration + frontend test |
| 13 | Folder с child или task не удаляется | integration |
| 14 | Create task с folder сохраняет location без новой семантики version | integration |
| 15 | Move task меняет только `tasks.folder_id` | unit + integration |
| 16 | Move task в root устанавливает null | integration |
| 17 | После upgrade все прежние tasks имеют folder null | migration integration |
| 18 | Archived task блокирует delete folder | integration |
| 19 | Global search находит tasks независимо от location | integration + frontend test |
| 20 | Subtree search включает selected folder и descendants, не соседей | unit + integration |
| 21 | Scope совместим с `difficulty_min/max` 1–100 | integration |
| 22 | Direct URL/refresh восстанавливает selection и filters | frontend test + ручная |
| 23 | Breadcrumb корректен после nested move/rename | frontend test |
| 24 | Все четыре empty state различимы | frontend test |
| 25 | Task move audit содержит old/new IDs/names, actor/time | integration |
| 26 | CSV/XLSX import не меняет формат и создаёт root tasks | integration + frontend test |
| 27 | Concurrent rename/move/delete не теряет update и даёт 409 stale client | integration |
| 28 | Folder create/rename/move/delete имеют immutable folder audit | integration |
| 29 | Folders precede tasks; folders name-sort, tasks pagination stable | integration + frontend test |
| 30 | Browser Back/Forward восстанавливает узел и query state | frontend test + ручная |

## 11. Фазирование и риски handoff

Следующая backend-фаза реализует schema/model/repository/application/HTTP и
unit/integration/migration tests строго по §§3–7,9. Затем frontend-фаза реализует
§8 и frontend acceptance. До обеих фаз этот документ нельзя трактовать как
пользовательскую возможность.

Открытые **реализационные риски, не архитектурные альтернативы**: корректный
stable hash advisory lock во всех process; mapping двух partial unique indexes;
recursive CTE height under lock; PostgreSQL enum downgrade; сохранение structured
error details текущим handlers; производительность полного tree при неожиданно
большом числе folders (наблюдать, но MVP endpoint не менять); Unicode `lower`
зависит от locale/collation БД и должно быть одинаково в validation/tests.
