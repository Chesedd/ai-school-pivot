# Проверка backend иерархии Content Bank

Backend/schema реализованы revision `20260806_01` поверх `20260730_01`. Доступны folder tree, mutations, task location и folder-scoped task search API. Frontend пока остаётся плоским: дерева, breadcrumb UI и drag-and-drop нет. Импорт по-прежнему создаёт задания в корне; recursive delete отсутствует.

## Локальная проверка

Используйте только одноразовую PostgreSQL БД с именем, заканчивающимся `_test`, для downgrade:

```powershell
$ErrorActionPreference = 'Stop'
git status --short --branch
docker compose build
docker compose up -d
# Dev DB: только forward upgrade
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current
docker compose exec backend alembic check
# Schema
$psql = 'psql -U postgres -d ai_school'
docker compose exec db sh -lc "$psql -c '\\d+ task_folders'"
docker compose exec db sh -lc "$psql -c '\\d+ tasks'"
docker compose exec db sh -lc "$psql -c '\\d+ folder_audit_log'"
docker compose exec db sh -lc "$psql -c \"SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid=enumtypid WHERE typname='audit_action' ORDER BY enumsortorder\""
docker compose exec db sh -lc "$psql -c 'SELECT count(*) FROM tasks WHERE folder_id IS NULL'"

# Disposable migration cycle
$testDb = "folders_$([guid]::NewGuid().ToString('N'))_test"
try {
  docker compose exec db createdb -U postgres $testDb
  $env:TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@db:5432/$testDb"
  docker compose exec -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic upgrade 20260730_01
  # Insert a legacy task through the normal API/fixture here, then migrate.
  docker compose exec -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic upgrade head
  docker compose exec db psql -U postgres -d $testDb -c 'SELECT count(*) FILTER (WHERE folder_id IS NULL) AS root_tasks FROM tasks'
  docker compose exec -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic downgrade 20260730_01
  docker compose exec -e DATABASE_URL=$env:TEST_DATABASE_URL backend alembic upgrade head
  docker compose exec -e DATABASE_URL=$env:TEST_DATABASE_URL -e TEST_DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q
} finally {
  docker compose exec db dropdb -U postgres --if-exists $testDb
  docker compose stop
}

npm --prefix frontend test
npm --prefix frontend run build
```

Никогда не выполняйте downgrade dev-БД и не используйте `docker compose down -v`.
