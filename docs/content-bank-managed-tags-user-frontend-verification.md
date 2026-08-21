# Проверка пользовательского frontend управляемых тегов

## Безопасный Windows PowerShell gate

Запускайте из корня репозитория. Сценарий получает реальные Compose-настройки, обновляет dev-БД только вперёд и создаёт отдельную БД с обязательным `_test`. Он не удаляет volumes и пользовательские данные.

```powershell
$ErrorActionPreference = 'Stop'
$headBefore = git rev-parse HEAD
git status --short --branch
docker compose config | Out-File compose-effective.txt
docker compose build
try {
  docker compose up -d postgres backend
  $devUrl = docker compose exec -T backend python -c "from app.config import get_settings; print(get_settings().database_url)"
  $pgUser = docker compose exec -T postgres sh -lc 'printf %s "$POSTGRES_USER"'
  if (!$devUrl -or !$pgUser) { throw 'Compose DATABASE_URL/POSTGRES_USER were not resolved' }
  docker compose exec -T backend alembic upgrade head
  docker compose exec -T backend alembic current
  docker compose exec -T backend alembic heads
  docker compose exec -T backend alembic check

  $stamp = Get-Date -Format yyyyMMddHHmmss
  $testDb = "ai_school_user_tags_${stamp}_test"
  if ($testDb -notmatch '_test$') { throw 'Unsafe test database name' }
  $uri = [Uri]$devUrl.Trim()
  $testUrl = $devUrl.Trim().Substring(0, $devUrl.Trim().LastIndexOf('/') + 1) + $testDb
  docker compose exec -T postgres createdb -U $pgUser $testDb
  try {
    docker compose run --rm -e DATABASE_URL=$testUrl backend alembic upgrade head
    docker compose run --rm -e DATABASE_URL=$testUrl backend alembic current
    docker compose run --rm -e DATABASE_URL=$testUrl backend alembic heads
    docker compose run --rm -e DATABASE_URL=$testUrl backend alembic check
    $backend = docker compose run --rm -e DATABASE_URL=$testUrl -e TEST_DATABASE_URL=$testUrl backend pytest -q
    $backend | Write-Host
    if ($backend -match 'skipped') { throw 'Backend suite contains skipped tests' }
    docker compose run --rm frontend npm test -- --run
    docker compose run --rm frontend npm run build
    docker compose up -d frontend
    Write-Host 'Open http://localhost:5173/content-bank and run the checklist below.'
  } finally {
    docker compose exec -T postgres dropdb -U $pgUser --if-exists --force $testDb
  }
} finally {
  docker compose logs --no-color --since=30m backend frontend
  docker compose stop
  git status --short --branch
  git rev-parse HEAD
}
```

## Ручной checklist

1. Создайте задание с global и совместимым предметным тегом; проверьте chips, максимум 8, удаление и точный результат в карточке.
2. Смените предмет и проверьте уведомление о снятии несовместимых тегов.
3. В latest draft измените набор и сохраните отдельной кнопкой; в двух вкладках проверьте CAS и загрузку актуального snapshot без перезагрузки страницы.
4. Проверьте review/approved/archived: только read-only, deprecated помечен текстом, replacement дан как подсказка.
5. Добавьте compact-фильтр «Теги» в global/subject/folder views; выберите два тега и проверьте AND-результат, repeated `tag_id`, сохранение q/difficulty/sort/location/pagination, reload и Back/Forward.
6. Откройте direct URL с active, deprecated, unknown и duplicate `tag_id`; проверьте labels, дедупликацию и отсутствие raw UUID/error.
7. Проверьте списки и карточку: 0, 1–3 и 4+ тегов, доступное `+N`; убедитесь по Network, что нет запросов тегов на строку.
8. Проверьте клавиатуру, visible focus и ширины 320/375/520 px, затем browser console и backend logs: без raw JSON, stack trace и неожиданных ошибок.
9. Проверьте lifecycle, методику, folders, difficulty 1–100, pagination.

Теги физически не удаляются. В dev создавайте только канонические теги, которые планируется сохранить; mutation smoke безопаснее выполнять на disposable БД с `_test`.
