# Content Bank MVP — финальная техническая приёмка (фаза 2.14)

## Паспорт приёмки

- **Дата:** 2026-07-29 (UTC).
- **Проверяемый code baseline:** `41f86eb1868f6d55e954f12d513b85ee0f6887aa`;
  документационные corrective commits могут находиться поверх него.
- **Content Bank technical MVP:** `READY_FOR_LOCAL_ACCEPTANCE`.
- **Content corpus:** `DEFERRED_UNTIL_AGENT`.
- **Окончательная пользовательская приёмка:** не выполнена. Статус выше означает
  готовность к воспроизводимой локальной проверке, а не подтверждение пользователем.

Отсутствие корпуса из 50–100 реальных заданий не является техническим дефектом
Content Bank. Это независимая задача content readiness.

## Область MVP и завершённые фазы

MVP включает PostgreSQL/Alembic-модель каталогов, заданий и версий; создание,
список, поиск и карточку; методическую структуру; status lifecycle; audit trail;
CSV/XLSX import preview/commit; эвристическую дедупликацию; React UI и
регрессионные backend/frontend-тесты.

Зафиксирован следующий результат фаз 2.0–2.12:

| Фаза | Результат |
|---|---|
| 2.0 | Технический FastAPI/React/PostgreSQL skeleton и Compose |
| 2.1 | Контракты и границы Content Bank |
| 2.2 | Каталоги, task/version schema и Alembic foundation |
| 2.3 | Атомарное создание задания и skill links |
| 2.4 | Список, фильтры, сортировка и pagination |
| 2.5 | Карточка задания и версии |
| 2.6 | Методическая структура и редактор |
| 2.7 | Status lifecycle, approval validation и versioning |
| 2.8 | PostgreSQL FTS, ranking и UI search |
| 2.9 | Атомарный audit trail и история в UI |
| 2.10 | CSV/XLSX preview/commit import protocol и UI wizard |
| 2.11 | `pg_trgm`-дедупликация и warning-only workflow |
| 2.12 | Финальная UI/regression hardening для import и duplicates |

Фаза 2.14 не меняет функциональность, контракты, схему или алгоритмы.

## Acceptance matrix

`PASS` означает, что проверка выполнена в текущем окружении. `PENDING_LOCAL`
означает, что evidence существует, но проверку необходимо повторить локально с
Docker/браузером. `DEFERRED` — явно вынесенная задача. `FAIL` означал бы
воспроизводимый production defect; таких дефектов в этой приёмке не найдено.

| Требование | Automated evidence | Manual evidence | Статус | Test file / команда |
|---|---|---|---|---|
| Технический запуск: Compose, PostgreSQL health, backend health, frontend route | Healthchecks описаны в Compose; Docker CLI отсутствует в acceptance environment | Открыть `/health` и `/content-bank` после healthy Compose | PENDING_LOCAL | `docker compose up -d --build`; `docker compose ps`; шаги 1–2 checklist |
| Database: один head; clean/repeat upgrade; downgrade base; отсутствие application tables; повторный upgrade/current/check; `pg_trgm`, GIN и именованные constraints/indexes; dev data/volumes сохранены | Migration и integration tests присутствуют; Docker/PostgreSQL lifecycle здесь не запускался | Выполнить одноразовый `_test` lifecycle ниже; dev DB не downgrade, `down -v` не выполнять | PENDING_LOCAL | `backend/alembic/versions`; полный PowerShell-набор |
| Каталоги: subjects, grades, topics, subtopics, skills; code/number; согласованная иерархия | Backend unit suite: 92/92; frontend catalog/import normalization входит в 109/109 | Проверить каскадные selectors при создании и импорте | PASS | `pytest -ra -q -W error tests/unit`; `frontend/src/importCatalog.test.ts` |
| Создание: UI/API, atomic task/v1/skill links, ровно один primary skill, warnings, `task_created` | Backend unit suite и frontend duplicate-create regressions прошли | Создать 2 UI + 1 API задания; проверить primary skill и audit | PASS | `backend/tests/unit/test_create_task.py`; `frontend/src/DuplicateCreate.test.tsx` |
| Список: pagination, sorting, фильтры, archived semantics, без размножения строк skill links | Backend list tests и frontend search/list tests прошли | Применить каждый фильтр, sort, pagination и archived view | PASS | `backend/tests/unit/test_list_tasks.py`; `frontend/src/TaskSearch.test.tsx` |
| Поиск: PostgreSQL FTS, `q`, ranking, stale response, empty query, search + filters | Unit/UI search regressions прошли; реальный PostgreSQL query plan локально не исполнялся | Проверить relevance и комбинированный запрос на Compose | PASS | `backend/tests/unit/test_list_tasks.py`; `frontend/src/TaskSearch.test.tsx` |
| Карточка: latest/approved versions, version list, skills, methodology, audit | Backend card/audit и UI history tests прошли | Сверить карточку до/после approve/new version | PASS | `backend/tests/unit/test_get_task_card.py`; `frontend/src/AuditHistory.test.tsx` |
| Методика: solution, rubric/items, accepted answers, errors, hints, draft save, read-only review/approved/archive | Backend methodology и 10 UI editor tests прошли | Заполнить все вкладки, сохранить draft, проверить read-only | PASS | `backend/tests/unit/test_save_methodology.py`; `frontend/src/MethodologyEditor.test.tsx` |
| Lifecycle: draft→review; return with reason; approve; soft/strict validation; approved immutable; new version; archive | Backend status and frontend action tests прошли | Выполнить полный цикл checklist 7–12 | PASS | `backend/tests/unit/test_status_cycle.py`; `frontend/src/StatusActions.test.tsx` |
| Audit: create/methodology/review/return/approve/new version/archive, filtering/pagination | Backend audit suite и 9 UI history tests прошли | Сверить порядок, metadata, action filter и pages | PASS | `backend/tests/unit/test_audit.py`; `frontend/src/AuditHistory.test.tsx` |
| Import: CSV/XLSX, preview, row errors/warnings, token lifecycle, selected commit/recommit, expired/not-found, formulas limitation, templates | Backend import suite и frontend parser/API/wizard tests прошли | Импортировать ≥2 CSV и ≥1 XLSX строки; invalid/warning/recommit cases | PASS | `backend/tests/unit/test_imports.py`; `frontend/src/ImportPage.test.tsx`; `frontend/src/importParser.test.ts` |
| Dedup: endpoint, exact/similarity thresholds, primary skill/final answer, archived/history exclusion, create/import/intra-file warnings, warning-only continue | Backend duplicate suite и create/import UI regressions прошли; extension SQL требует local Docker | Создать две похожие задачи и продолжить после warning | PASS | `backend/tests/unit/test_duplicates.py`; `frontend/src/DuplicateCreate.test.tsx`; `frontend/src/ImportPage.test.tsx` |
| Frontend: direct routes, shell/nav, desktop/mobile, keyboard/focus, states, no React/act/key warnings or unhandled rejections | 14 files, 109 passed, 0 failed, 0 skipped/pending; verbose run без указанных warnings; build main chunk 283.30 kB (87.37 kB gzip) | Viewports 1440/1024/768/360; keyboard/focus; browser console | PASS | `npm run test:run -- --reporter=verbose`; `npm run build`; `frontend/src/AppShell.test.tsx` |
| Временный acceptance dataset (2 UI, 1 API, 2 CSV, 1 XLSX, approved, archived, similar pair) | Не является seed и намеренно не создавался без локального Compose | Создать по checklist и удалить/оставить только в dev по решению пользователя | PENDING_LOCAL | Ручные шаги 3–17 |
| Корпус 50–100 методически качественных заданий | Не является техническим acceptance criterion | Отдельная content/agent-фаза | DEFERRED | Раздел «Deferred scope» |

## Результаты автоматических проверок

### Backend

- `python -m compileall -q app tests`: **PASS**.
- `pytest -ra -q -W error tests/unit`: **92 passed, 0 failed, 0 skipped,
  0 warnings**.
- Full PostgreSQL pytest, clean migration lifecycle и SQL inspection:
  **PENDING_LOCAL**, поскольку в acceptance environment отсутствует команда
  `docker`. Это ограничение среды, не product failure.
- Seed на test DB не запускался. Dev DB и Docker volumes не очищались.

### Frontend

- `npm ci`: **PASS** (168 packages installed); npm сообщил только environment
  warning об устаревающем `http-proxy` config.
- verbose/default/JSON Vitest: **109 passed, 0 failed, 0 skipped/pending**, 14
  test files. В выводе нет React, `act`, duplicate-key warnings или unhandled
  promise rejections.
- `npm run build`: **PASS**; main chunk 283.30 kB / 87.37 kB gzip. Резкого роста
  относительно текущего lockfile/build не выявлено; это зафиксированный baseline.
- `npm ls exceljs --all`: **PASS**, дерево пусто — ExcelJS отсутствует.
- `npm audit --omit=dev --audit-level=high`: **PENDING_LOCAL**: registry audit
  endpoint вернул HTTP 403, поэтому отсутствие high/critical уязвимостей в этой
  среде не заявляется. В локальном handoff exit 0 означает PASS, HTTP 403
  оставляет приёмку в PENDING_LOCAL, а найденная high/critical vulnerability
  означает FAIL. Только явно распознанный `HTTP 403`/`403 Forbidden` позволяет
  продолжить остальные проверки; иная ошибка audit завершает сценарий с FAIL.
  В любом случае внешний `finally` пытается безопасно удалить только созданную
  `_test` БД.

## Ручной local checklist

Для acceptance достаточно временного минимального набора; не добавлять его в
production seed и не генерировать 50–100 искусственных заданий.

| # | Действие | Ожидаемый результат | Отметка |
|---:|---|---|---|
| 1 | Запустить Compose | Все три service запущены; PostgreSQL/backend healthy | ☐ |
| 2 | Применить migration head и dev seed | Один Alembic head; seed идемпотентен; каталоги доступны | ☐ |
| 3 | Создать первое задание через UI | Созданы task, version 1, skills; один primary; audit `task_created` | ☐ |
| 4 | Создать похожее через «Создать всё равно» | Показан warning/candidate; явное продолжение создаёт второе задание | ☐ |
| 5 | Проверить список, поиск, sort, pages и все фильтры | Результаты согласованы; search + filters работают; строки не дублируются | ☐ |
| 6 | Открыть карточку и методические вкладки | Видны latest/approved/version list, skills и все методические блоки | ☐ |
| 7 | Выполнить draft → review | Soft warnings видны; валидная версия переходит в review; audit есть | ☐ |
| 8 | Вернуть review → draft с reason | Причина обязательна и видна в audit; версия снова редактируема | ☐ |
| 9 | Повторить review → approved | Strict validation соблюдена; approved_version указывает на версию | ☐ |
| 10 | Попробовать изменить approved | UI read-only; API не изменяет approved version | ☐ |
| 11 | Создать новую версию из approved | Создан следующий version number в draft; approved не изменён | ☐ |
| 12 | Архивировать отдельную карточку | Task archived, отсутствует в default list и доступен с archived filter | ☐ |
| 13 | Проверить audit history | Все семь типов событий, фильтр и pagination корректны | ☐ |
| 14 | CSV preview/commit (≥2 валидных строк) | Preview ничего не создаёт; выбранные строки создаются один раз | ☐ |
| 15 | XLSX preview/commit (≥1 валидной строки) | Template читается; выбранная строка импортируется | ☐ |
| 16 | Import warning и invalid row | Warning selectable; invalid disabled/422; preview и selection сохраняются | ☐ |
| 17 | Duplicate warning, включая intra-file | Candidate/source row показаны; warning-only continuation доступно | ☐ |
| 18 | Desktop 1440 и 1024 px | Нет горизонтальной поломки shell/forms/card; table wrapper работает | ☐ |
| 19 | Tablet 768 px | Навигация, формы, tabs/dialogs доступны без перекрытий | ☐ |
| 20 | Mobile 360 px | Основные действия, import и card usable; контент не обрезан | ☐ |
| 21 | Пройти keyboard/focus flow | Skip link, tab order, tabs, dialogs и возврат focus предсказуемы | ☐ |
| 22 | Проверить backend logs и browser console | Нет 5xx, JS errors, React/act/key warnings, unhandled rejections | ☐ |

Дополнительно создать минимум одно задание прямым API. После шагов должны
существовать ≥1 approved, ≥1 archived и ≥2 похожих задания.

## Known issues / ограничения

- Авторизация отсутствует; используется configured dev actor.
- Роли и permissions отсутствуют.
- Реальный корпус 50–100 заданий не сформирован.
- Агент генерации/подготовки контента отсутствует.
- XLSX formula cells не гарантированно обнаруживаются клиентским parser.
- Дедупликация эвристическая и warning-only, а не запрет создания.
- Пороги дедупликации потребуют настройки на реальном корпусе.
- Production deployment, monitoring и backups не входят в этот этап.
- Assessment, submissions и checking engine отсутствуют.
- `npm audit` зависит от доступности registry audit endpoint; текущий endpoint
  ответил 403.

## Deferred scope

**`DEFERRED_UNTIL_AGENT`:**

- создание 50–100 методически качественных заданий;
- массовое заполнение эталонов и критериев;
- настройка порогов дедупликации на реальном корпусе;
- content quality review;
- расширение базы до пилотного объёма.

Данные можно импортировать уже сейчас; переносится их содержательная подготовка,
а не техническая возможность импорта.

## Критерии перехода к Assessment Core

Для старта этапа 3 достаточно стабильных `task_id`/`task_version_id`, approved
task versions, неизменяемости approved version, поиска/выбора заданий,
создания новой версии, audit trail и нескольких временных approved tasks для
разработки. Assessment обязан ссылаться на конкретный `task_version_id`, а не
на изменяемое «последнее» состояние task. Реализация Assessment в фазу 2.14 не
входит.

## Rollback / recovery notes

- Перед локальной проверкой сохранить `git status --short` и не использовать
  `docker compose down -v`: dev volume должен сохраниться.
- Никогда не выполнять `alembic downgrade base` с dev `DATABASE_URL`.
- Полный downgrade допускается только для одноразовой БД, имя которой
  оканчивается на `_test`; скрипт ниже проверяет suffix до destructive command.
- После downgrade application tables и application indexes должны отсутствовать,
  но Alembic может сохранить служебную таблицу `alembic_version`, а `pg_trgm`
  намеренно остаётся как shared database capability.
- При неуспешной migration остановить проверку, сохранить логи и удалить только
  одноразовую test DB. Восстановление dev данных — из отдельного backup; эта
  acceptance-процедура dev DB не очищает.
- При найденном production bug не рефакторить: записать exact scenario,
  expected/actual и слой, выставить `FAIL`/`BLOCKED` и остановить acceptance.

## Полный безопасный PowerShell-набор локальной проверки

Команды рассчитаны на PowerShell 5.1, запускаются из корня репозитория и не
печатают connection strings. Test DB не seed-ится. `finally` удаляет только
одноразовую `_test` БД; dev DB и volume сохраняются. Backend и frontend
ожидаются ограниченно: каждый URL должен вернуть HTTP 200 не позднее 60 секунд.

```powershell
$ErrorActionPreference = "Stop"
$CodeAcceptanceBaseline = "41f86eb1868f6d55e954f12d513b85ee0f6887aa"
$TestDatabaseCreated = $false
$AuditStatus = "NOT_RUN"

function Assert-NativeSuccess {
  param([string]$Message)

  if ($LASTEXITCODE -ne 0) {
    throw "$Message. Exit code: $LASTEXITCODE"
  }
}

function Wait-Http200 {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$ServiceName,
    [int]$TimeoutSeconds = 60,
    [int]$RetryDelaySeconds = 2
  )

  $Timer = [System.Diagnostics.Stopwatch]::StartNew()
  $LastFailure = "no response"
  while ($Timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    $RemainingSeconds = [int][Math]::Ceiling($TimeoutSeconds - $Timer.Elapsed.TotalSeconds)
    if ($RemainingSeconds -le 0) { break }
    $RequestTimeoutSeconds = [Math]::Max(1, [Math]::Min(10, $RemainingSeconds))
    try {
      $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $RequestTimeoutSeconds
      if ($Response.StatusCode -eq 200) {
        Write-Host "$ServiceName is ready: HTTP 200"
        return
      }
      $LastFailure = "HTTP $($Response.StatusCode)"
    } catch {
      $LastFailure = $_.Exception.Message
    }
    $RemainingSeconds = [int][Math]::Floor($TimeoutSeconds - $Timer.Elapsed.TotalSeconds)
    if ($RemainingSeconds -gt 0) {
      Start-Sleep -Seconds ([Math]::Min($RetryDelaySeconds, $RemainingSeconds))
    }
  }

  throw "$ServiceName did not return HTTP 200 within $TimeoutSeconds seconds. Last failure: $LastFailure"
}

git status --short
git log -1 --oneline
git merge-base --is-ancestor $CodeAcceptanceBaseline HEAD
if ($LASTEXITCODE -ne 0) {
  throw "Documented code baseline is not an ancestor of HEAD"
}

docker compose --progress=plain build
Assert-NativeSuccess "Docker Compose build failed"
docker compose up -d postgres backend frontend
Assert-NativeSuccess "Docker Compose startup failed"
docker compose ps
Assert-NativeSuccess "Unable to inspect Docker Compose services"

$PgUserOutput = docker compose exec -T postgres printenv POSTGRES_USER
Assert-NativeSuccess "Unable to read PostgreSQL user"
$PgUser = ($PgUserOutput | Out-String).Trim()
$DevDatabaseOutput = docker compose exec -T postgres printenv POSTGRES_DB
Assert-NativeSuccess "Unable to read development database name"
$DevDatabase = ($DevDatabaseOutput | Out-String).Trim()
$DevDatabaseUrlOutput = docker compose exec -T backend printenv DATABASE_URL
Assert-NativeSuccess "Unable to read backend database configuration"
$DevDatabaseUrl = ($DevDatabaseUrlOutput | Out-String).Trim()
$ActorIdOutput = docker compose exec -T backend printenv CONTENT_BANK_DEV_ACTOR_ID
Assert-NativeSuccess "Unable to read Content Bank dev actor"
$ActorId = ($ActorIdOutput | Out-String).Trim()
$TestDatabase = "content_bank_acceptance_$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))_test"
if (-not $TestDatabase.EndsWith("_test")) { throw "Unsafe test database name" }
if ($TestDatabase -eq $DevDatabase) { throw "Test database must differ from development database" }
$TestDatabaseUrl = $DevDatabaseUrl -replace '/[^/?]+(\?.*)?$', "/$TestDatabase`$1"

try {
  # Dev: only forward migration and idempotent dev seed. Never downgrade dev.
  docker compose exec -T backend alembic upgrade head
  Assert-NativeSuccess "Development database upgrade failed"
  docker compose exec -T backend python -m app.db.seed
  Assert-NativeSuccess "Development seed failed"
  docker compose exec -T backend python -m app.db.seed
  Assert-NativeSuccess "Repeated development seed failed"
  Wait-Http200 -Url "http://localhost:8000/health" -ServiceName "Backend"
  Wait-Http200 -Url "http://localhost:5173/content-bank" -ServiceName "Frontend"

  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PgUser -d postgres `
    -c "CREATE DATABASE $TestDatabase;"
  Assert-NativeSuccess "Unable to create disposable test database"
  $TestDatabaseCreated = $true

  # Clean migration, idempotence and exactly one head.
  $HeadsOutput = docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic heads
  Assert-NativeSuccess "Unable to inspect Alembic heads"
  $Heads = $HeadsOutput | Out-String
  if (($Heads | Select-String '\(head\)').Count -ne 1) { throw "Expected one Alembic head" }
  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic upgrade head
  Assert-NativeSuccess "Clean Alembic upgrade failed"
  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic upgrade head
  Assert-NativeSuccess "Repeated Alembic upgrade failed"
  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic current
  Assert-NativeSuccess "Alembic current failed"
  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic check
  Assert-NativeSuccess "Alembic check failed"

  # Full lifecycle is ONLY on the disposable _test database.
  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic downgrade base
  Assert-NativeSuccess "Alembic downgrade base failed"
  $ApplicationTableCountOutput = docker compose exec -T postgres psql -At -v ON_ERROR_STOP=1 `
    -U $PgUser -d $TestDatabase `
    -c "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version';"
  Assert-NativeSuccess "Unable to inspect tables after downgrade"
  $ApplicationTableCount = ($ApplicationTableCountOutput | Out-String).Trim()
  if ($ApplicationTableCount -ne "0") { throw "Application tables remain after downgrade base" }
  $ApplicationIndexCountOutput = docker compose exec -T postgres psql -At -v ON_ERROR_STOP=1 `
    -U $PgUser -d $TestDatabase `
    -c "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename <> 'alembic_version';"
  Assert-NativeSuccess "Unable to inspect indexes after downgrade"
  $ApplicationIndexCount = ($ApplicationIndexCountOutput | Out-String).Trim()
  if ($ApplicationIndexCount -ne "0") { throw "Application indexes remain after downgrade base" }
  $TrgmAfterDownOutput = docker compose exec -T postgres psql -At -v ON_ERROR_STOP=1 `
    -U $PgUser -d $TestDatabase -c "SELECT count(*) FROM pg_extension WHERE extname='pg_trgm';"
  Assert-NativeSuccess "Unable to inspect pg_trgm after downgrade"
  $TrgmAfterDown = ($TrgmAfterDownOutput | Out-String).Trim()
  if ($TrgmAfterDown -ne "1") { throw "pg_trgm should intentionally remain installed" }

  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic upgrade head
  Assert-NativeSuccess "Alembic recovery upgrade failed"
  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic current
  Assert-NativeSuccess "Alembic current after recovery failed"
  docker compose exec -T -e "DATABASE_URL=$TestDatabaseUrl" backend alembic check
  Assert-NativeSuccess "Alembic check after recovery failed"
  $RestoredTableCountOutput = docker compose exec -T postgres psql -At -v ON_ERROR_STOP=1 `
    -U $PgUser -d $TestDatabase `
    -c "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version';"
  Assert-NativeSuccess "Unable to verify restored schema"
  $RestoredTableCount = ($RestoredTableCountOutput | Out-String).Trim()
  if ([int]$RestoredTableCount -le 0) { throw "Recovery upgrade did not restore application tables" }
  $RestoredTrgmIndexCountOutput = docker compose exec -T postgres psql -At -v ON_ERROR_STOP=1 `
    -U $PgUser -d $TestDatabase `
    -c "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname='ix_task_versions_statement_trgm_gin' AND indexdef LIKE '%USING gin%gin_trgm_ops%';"
  Assert-NativeSuccess "Unable to verify restored trigram GIN index"
  $RestoredTrgmIndexCount = ($RestoredTrgmIndexCountOutput | Out-String).Trim()
  if ($RestoredTrgmIndexCount -ne "1") { throw "Recovery upgrade did not restore trigram GIN index" }

  docker compose exec -T backend python -m compileall -q app tests
  Assert-NativeSuccess "Backend compileall failed"
  docker compose exec -T `
    -e "DATABASE_URL=$TestDatabaseUrl" `
    -e "TEST_DATABASE_URL=$TestDatabaseUrl" `
    -e "CONTENT_BANK_DEV_ACTOR_ID=$ActorId" `
    backend pytest -ra -q -W error
  Assert-NativeSuccess "Backend test suite failed"

  # Extension, trigram GIN and every explicitly named model constraint/index.
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PgUser -d $TestDatabase `
    -c "SELECT extname FROM pg_extension WHERE extname='pg_trgm';"
  Assert-NativeSuccess "pg_trgm verification failed"
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PgUser -d $TestDatabase `
    -c "SELECT indexname,indexdef FROM pg_indexes WHERE indexname='ix_task_versions_statement_trgm_gin' AND indexdef LIKE '%USING gin%gin_trgm_ops%';"
  Assert-NativeSuccess "Trigram GIN index verification failed"
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PgUser -d $TestDatabase `
    -c "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace AND conname NOT LIKE '%_pkey' ORDER BY conname;"
  Assert-NativeSuccess "Constraint inspection failed"
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PgUser -d $TestDatabase `
    -c "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname;"
  Assert-NativeSuccess "Index inspection failed"

  Push-Location frontend
  try {
    npm ci
    Assert-NativeSuccess "Frontend dependency installation failed"
    npm run test:run -- --reporter=verbose
    Assert-NativeSuccess "Verbose frontend test run failed"
    npm run test:run
    Assert-NativeSuccess "Frontend test run failed"
    $VitestJson = Join-Path ([System.IO.Path]::GetTempPath()) "content-bank-vitest.json"
    Remove-Item -Force -ErrorAction SilentlyContinue $VitestJson
    try {
      npm run test:run -- --reporter=json --outputFile=$VitestJson
      Assert-NativeSuccess "JSON frontend test run failed"
      $Report = Get-Content -Raw $VitestJson | ConvertFrom-Json
      $Assertions = @($Report.testResults | ForEach-Object { $_.assertionResults })
      $Failed = @($Assertions | Where-Object { $_.status -eq 'failed' }).Count
      $Pending = @($Assertions | Where-Object { $_.status -in @('pending','skipped','todo') }).Count
      if ($Assertions.Count -lt 109 -or $Failed -ne 0 -or $Pending -ne 0) {
        throw "Unexpected frontend test totals: total=$($Assertions.Count), failed=$Failed, pending=$Pending"
      }
    } finally {
      Remove-Item -Force -ErrorAction SilentlyContinue $VitestJson
    }
    npm run build
    Assert-NativeSuccess "Frontend production build failed"

    $AuditOutput = npm audit --omit=dev --audit-level=high 2>&1
    $AuditExitCode = $LASTEXITCODE
    $AuditText = $AuditOutput | Out-String
    $AuditOutput | ForEach-Object { Write-Host $_ }
    $AuditIsExplicit403 = $AuditText -match '(?i)(\b403\s+Forbidden\b|\bHTTP(?:/\d(?:\.\d)?)?\s+403\b)'
    $AuditHasHighOrCritical = $AuditText -match '(?i)\b(high|critical)\s+severity\s+vulnerabilit(?:y|ies)\b'

    if ($AuditExitCode -eq 0) {
      $AuditStatus = "PASS"
    } elseif ($AuditHasHighOrCritical) {
      $AuditStatus = "FAIL"
      throw "npm audit found high/critical vulnerabilities"
    } elseif ($AuditIsExplicit403) {
      $AuditStatus = "PENDING_LOCAL"
      Write-Warning "npm registry audit endpoint returned explicit HTTP 403; audit must be repeated with registry access"
    } else {
      $AuditStatus = "FAIL"
      throw "npm audit failed with an unrecognized error. Exit code: $AuditExitCode"
    }

    $ExcelMetadata = Select-String `
      -Path package.json, package-lock.json `
      -Pattern '"exceljs"' `
      -SimpleMatch
    if ($ExcelMetadata) {
      $ExcelMetadata
      throw "ExcelJS is present in package metadata"
    }

    $ExcelLsOutput = npm ls exceljs --all 2>&1
    $ExcelLsExitCode = $LASTEXITCODE
    $ExcelLsText = $ExcelLsOutput | Out-String
    Write-Host $ExcelLsText
    if ($ExcelLsExitCode -notin @(0, 1)) {
      throw "Unable to inspect ExcelJS dependency tree"
    }
    if ($ExcelLsText -notmatch '\(empty\)') {
      throw "ExcelJS unexpectedly exists in dependency tree"
    }
  } finally {
    Pop-Location
  }

  git diff --check
  Assert-NativeSuccess "Git diff check failed"
  $GitState = git status --porcelain
  Assert-NativeSuccess "Unable to inspect Git working tree"
  if ($GitState) {
    $GitState
    throw "Working tree is not clean"
  }
  git log -1 --oneline

  Write-Host "Final npm audit status: $AuditStatus"
  if ($AuditStatus -eq "PENDING_LOCAL") {
    Write-Warning "All other technical checks passed, but final READY cannot be declared until npm audit is repeated with an accessible registry"
  } elseif ($AuditStatus -ne "PASS") {
    throw "Final npm audit status is not PASS: $AuditStatus"
  } else {
    Write-Host "All automated local acceptance checks passed, including npm audit"
  }
} finally {
  if ($TestDatabaseCreated -and
      $TestDatabase.EndsWith("_test") -and
      $TestDatabase -ne $DevDatabase) {
    docker compose exec -T postgres pg_isready -U $PgUser -d postgres *> $null
    $PostgreSqlAvailable = ($LASTEXITCODE -eq 0)
    if ($PostgreSqlAvailable) {
      docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PgUser -d postgres `
        -c "DROP DATABASE IF EXISTS $TestDatabase WITH (FORCE);"
      Assert-NativeSuccess "Unable to drop disposable test database"
      $TestDatabaseCreated = $false
    } else {
      Write-Warning "PostgreSQL is unavailable; disposable test database could not be removed"
    }
  }
  # Deliberately no `docker compose down -v`.
}
```

После автоматического набора выполнить 22 ручных шага выше и зафиксировать
отметки. Только после этого пользователь может подтвердить окончательную
приёмку. До подтверждения честный итог: **`READY_FOR_LOCAL_ACCEPTANCE`**.


## Acceptance: difficulty 1–100

- [ ] Создание принимает 1 и 100, отклоняет 0, 101, дробь, boolean, строку и отсутствие поля стандартным validation envelope.
- [ ] Список и карточка возвращают число; новая версия сохраняет его; сортировка 9 перед 100 по возрастанию.
- [ ] `difficulty_min`/`difficulty_max` фильтруют диапазон и отклоняют min > max.
- [ ] CSV/XLSX preview и commit принимают 1, 25, 50, 75, 100 и отклоняют прежние enum-значения.
- [ ] Свежая БД, upgrade существующей БД, downgrade и повторный upgrade проходят.

## Будущая приёмка иерархии папок (ещё не реализовано)

Этот раздел не входит в приёмку уже реализованного Stage 2. Полный выбранный
контракт и матрица unit/integration/frontend/manual проверок находятся в
[контракте иерархии папок](content-bank-folder-hierarchy-contract.md#10-acceptance-criteria-будущей-реализации).
После backend- и frontend-фаз необходимо отдельно подтвердить: Subject как
виртуальный root; создание, rename, move и удаление только пустых folders;
глубину 8 и запрет уровня 9; sibling name uniqueness без учёта регистра;
same-subject placement одного task; direct contents и recursive subtree search;
совместимость с `difficulty_min`/`difficulty_max`; root-only import; прямые URL,
breadcrumb и четыре empty states; task/folder audit и конкурентные конфликты.

## Post-MVP roadmap note — 2026-08-19

Историческая отметка `Content corpus: DEFERRED_UNTIL_AGENT` и исходный baseline
этого acceptance passport остаются точными для момента приёмки. Отсутствовавшая
работа по generation/content readiness теперь спланирована в принадлежащем
Content Bank треке [AI Content Authoring Phase 4A](ai-content-authoring-v1-contract.md).

Phase 4A.0 добавляет только документацию. Этот note не заявляет наличие corpus,
generator, API, persistence, UI либо успешно пройденного real-provider quality
gate и не меняет ретроактивно техническую MVP-приёмку Content Bank. Будущая
реализация и отдельная приёмка предусмотрены Phases 4A.1–4A.6.
