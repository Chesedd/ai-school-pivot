# Проверка backend управляемых тегов

Фаза добавляет каталог и trusted pilot admin API без production authentication/RBAC.
Downgrade намеренно уничтожает definitions, associations и tag audit и разрешён только
на одноразовой БД, имя которой заканчивается `_test`.

## PowerShell (безопасный сценарий)

```powershell
$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format yyyyMMddHHmmss
$testDb = "ai_school_tags_$($stamp)_test"
try {
  # Получить фактический URL/credentials из Compose, ничего не угадывая.
  docker compose config | Out-File "compose-effective-$stamp.txt"
  docker compose exec -T backend python -c "from app.config import get_settings; print(get_settings().database_url)"
  # Backup dev до forward-only upgrade (подставить фактический URL из команды выше).
  $devUrl = Read-Host 'Actual development DATABASE_URL'
  docker compose exec -T postgres pg_dump --format=custom --file="/tmp/dev-$stamp.dump" $devUrl
  docker compose cp "postgres:/tmp/dev-$stamp.dump" ".\dev-$stamp.dump"
  docker compose build backend
  docker compose exec -T backend alembic upgrade head # только forward на dev

  $adminUrl = Read-Host 'Actual admin PostgreSQL URL from Compose'
  docker compose exec -T postgres psql $adminUrl -v ON_ERROR_STOP=1 -c "CREATE DATABASE $testDb"
  $testUrl = Read-Host "DATABASE_URL for $testDb (must end in _test)"
  if ($testUrl -notmatch '_test($|\?)') { throw 'Disposable database URL must end in _test' }
  docker compose run --rm -e DATABASE_URL=$testUrl backend alembic upgrade head
  docker compose run --rm -e DATABASE_URL=$testUrl backend alembic current
  docker compose run --rm -e DATABASE_URL=$testUrl backend alembic downgrade 20260806_01
  docker compose run --rm -e DATABASE_URL=$testUrl backend alembic upgrade head
  docker compose run --rm -e DATABASE_URL=$testUrl -e TEST_DATABASE_URL=$testUrl backend pytest -q
  docker compose exec -T postgres psql $testUrl -c "\d+ tag_categories" -c "\d+ tags" -c "\d+ task_version_tags" -c "\d+ tag_audit_log" -c "TABLE tag_categories"

  # Запустить API именно на disposable DB, затем smoke/create/detail/deprecate/usage.
  docker compose run --rm -d --name tags-api-$stamp -p 18080:8000 -e DATABASE_URL=$testUrl backend
  $categories = Invoke-RestMethod http://localhost:18080/api/content-bank/tag-categories
  $body = @{category_code='exam';subject_id=$null;name="Smoke-$stamp"}|ConvertTo-Json
  $tag = Invoke-RestMethod -Method Post -ContentType application/json -Body $body http://localhost:18080/api/content-bank/admin/tags
  Invoke-RestMethod "http://localhost:18080/api/content-bank/tags/$($tag.id)"
  Invoke-RestMethod "http://localhost:18080/api/content-bank/tags/similar?name=Smoke-$stamp"
  Invoke-RestMethod "http://localhost:18080/api/content-bank/admin/tags/$($tag.id)/usage"
  docker compose exec -T postgres psql $testUrl -c "SELECT action,before_snapshot,after_snapshot FROM tag_audit_log ORDER BY occurred_at"

  # Duplicate race: отправить два одинаковых POST параллельно; ожидаются 201 и 409.
  1..2 | ForEach-Object { Start-Job { param($n) Invoke-WebRequest -SkipHttpErrorCheck -Method Post -ContentType application/json -Body (@{category_code='exam';name=$n}|ConvertTo-Json) http://localhost:18080/api/content-bank/admin/tags } -ArgumentList "Race-$stamp" } | Wait-Job | Receive-Job
  Push-Location frontend; npm test -- --run; npm run build; Pop-Location
} finally {
  docker rm -f "tags-api-$stamp" 2>$null
  if ($adminUrl -and $testDb -match '_test$') { docker compose exec -T postgres psql $adminUrl -c "DROP DATABASE IF EXISTS $testDb WITH (FORCE)" }
  docker compose stop
}
```

Нельзя применять `downgrade` к dev-БД, удалять volumes или массово очищать dev-данные.
