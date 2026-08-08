# Managed tags CSV/XLSX import — verification

## Реализованный контракт

Шаблоны добавляют последнюю optional-колонку `tags`; значения — canonical names через `;`, максимум восемь. Старые файлы без колонки остаются допустимыми. Browser parser сохраняет введённые имена и отправляет список строк; authoritative NFKC/casefold/`ё→е` resolution выполняет backend одним batch query.

Preview возвращает raw names, canonical resolved tag DTO и независимые row issues (`unknown_import_tag`, `deprecated_import_tag`, `tag_subject_mismatch`, `tag_limit_exceeded`, `duplicate_import_tag`, `tag_name_invalid`). Token JSON хранит IDs, canonical order и fingerprints. Commit блокирует referenced IDs в сортированном порядке; fingerprint conflict возвращает `409 tag_catalog_changed` до создания заданий. Task, v1, skills, root placement, tag links, task/tag audit и token consumption входят в одну транзакцию.

## Windows PowerShell verification

```powershell
$ErrorActionPreference = 'Stop'
if (!$env:TEST_DATABASE_URL) { throw "Set TEST_DATABASE_URL for a unique disposable _test database" }
if (!$env:PGUSER) { throw "Set PGUSER from the local environment" }
docker compose build backend frontend
docker compose up -d postgres
try {
  docker compose run --rm backend alembic upgrade head
  $testDb = ([uri]$env:TEST_DATABASE_URL).Segments[-1]
  docker compose exec -T postgres createdb -U $env:PGUSER $testDb
  try {
    docker compose run --rm -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic upgrade head
    docker compose run --rm -e DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q
    docker compose run --rm frontend npm test -- --run
    docker compose run --rm frontend npm run build

    Invoke-WebRequest http://localhost:8000/content-bank-tasks-template.csv -OutFile template.csv
    @'
subject_code,grade_number,topic_code,subtopic_code,title,statement,task_type,answer_format,difficulty,source,primary_skill_code,primary_skill_weight,additional_skills,tags
math,7,topic,,Tagged,Question,problem,number,25,,skill,1.0000,,ОГЭ; С параметром
'@ | Set-Content -Encoding utf8 valid-tags.csv
    # Повторить preview для файла без tags, valid, unknown, deprecated и subject mismatch.
    # Для XLSX заполнить те же значения в formula-free Tasks sheet скачанного шаблона.
    # Сохранить token, затем rename/deprecate referenced tag и проверить 409 tag_catalog_changed.
    # Проверить отсутствие tasks/audit после 409, folder_id IS NULL и tag_added_to_version после success.
  } finally {
    docker compose exec -T postgres dropdb -U $env:PGUSER --if-exists $testDb
  }
} finally {
  docker compose stop backend frontend
}
```

Credentials не задаются документом: `TEST_DATABASE_URL` и `PGUSER` обязательны из локального безопасного окружения; dev database не downgrade-ится, volumes и dev data не удаляются. Для rename/deprecate concurrency сценария сначала создайте отдельный disposable tag в `_test` БД.

## Handoff

Вне этой фазы остаются admin UI, task editor picker, tags на обычных cards/lists, compact frontend filter, proposals и authentication/RBAC.
