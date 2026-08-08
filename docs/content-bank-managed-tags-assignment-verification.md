# Проверка назначения тегов версиям

Фаза использует существующий Alembic head `20260808_01`: применённая migration не
изменялась. Backend добавляет atomic full-set `PUT`, optional `tag_ids` при создании,
copy-on-version, version audit, DTO и AND-фильтры. Import и frontend tags не входят.

## Контракт API

`PUT /api/content-bank/task-versions/{version_id}/tags` принимает
`{"tag_ids":["uuid"],"expected_updated_at":"RFC3339"}` и возвращает task/version
identity, новый `updated_at` и канонически упорядоченный `tags`. No-op сохраняет
`updated_at`. Lock order: version row, tag rows по UUID, associations, audit.

## Безопасная PowerShell-проверка

```powershell
$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format yyyyMMddHHmmss
$testDb = "ai_school_assignment_$($stamp)_test"
try {
  docker compose config | Out-File "compose-effective-$stamp.txt"
  $devUrl = docker compose exec -T backend python -c "from app.config import get_settings; print(get_settings().database_url)"
  docker compose exec -T postgres pg_dump --format=custom --file="/tmp/dev-$stamp.dump" $devUrl
  docker compose cp "postgres:/tmp/dev-$stamp.dump" ".\dev-$stamp.dump"
  docker compose build backend
  docker compose exec -T backend alembic upgrade head # forward-only; должен подтвердить 20260808_01

  $adminUrl = Read-Host 'Actual admin PostgreSQL URL from Compose'
  docker compose exec -T postgres psql $adminUrl -v ON_ERROR_STOP=1 -c "CREATE DATABASE $testDb"
  $testUrl = Read-Host "DATABASE_URL for $testDb (must end in _test)"
  if ($testUrl -notmatch '_test($|\?)') { throw 'Disposable database URL must end in _test' }
  docker compose run --rm -e DATABASE_URL=$testUrl backend alembic upgrade head
  docker compose run --rm -e DATABASE_URL=$testUrl -e TEST_DATABASE_URL=$testUrl backend pytest -q

  docker compose run --rm -d --name "assignment-api-$stamp" -p 18080:8000 -e DATABASE_URL=$testUrl backend
  # Получите реальные catalog UUID через /catalog/*; создайте global/scoped tags через admin API.
  # Затем проверьте create task с tag_ids, PUT полного набора и повторный PUT со stale timestamp.
  # Переведите draft в review: PUT обязан дать task_version_not_editable; создайте новую версию
  # после approve и проверьте copy. Поиск: ?tag_id=A&tag_id=B, включая folder contents.
  Invoke-RestMethod 'http://localhost:18080/api/content-bank/tasks?tag_id=A&tag_id=B'
  Invoke-RestMethod 'http://localhost:18080/api/content-bank/folders/FOLDER/contents?tag_id=A&tag_id=B'
  Invoke-RestMethod 'http://localhost:18080/api/content-bank/tasks/TASK/audit'
  Invoke-RestMethod 'http://localhost:18080/api/content-bank/admin/tags/TAG/usage'
  # Для CAS concurrency отправьте два PUT с одним expected_updated_at: ожидаются 200 и 409.

  Push-Location frontend
  npm test -- --run
  npm run build
  Pop-Location
} finally {
  docker rm -f "assignment-api-$stamp" 2>$null
  if ($adminUrl -and $testDb -match '_test$') {
    docker compose exec -T postgres psql $adminUrl -c "DROP DATABASE IF EXISTS $testDb WITH (FORCE)"
  }
  docker compose stop
}
```

Не выполняйте downgrade dev-БД, `docker compose down -v`, удаление volumes или
массовое удаление данных. Значения `A`, `B`, `TASK`, `TAG`, `FOLDER` — UUID созданных
smoke fixtures, а не guessed credentials.
