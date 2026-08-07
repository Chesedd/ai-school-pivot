# Финальная UI-проверка банка заданий

## Реализованное поведение

Внутри subject root и папки поиск виден постоянно, а фильтры добавляются по одному через клавиатурно доступную кнопку «Добавить фильтр». Список закрывается по Escape с возвратом фокуса; уже активные пункты из него исключаются. Доступны все применимые фильтры текущего `TaskListQuery`: класс, тема, подтема, навык, тип задания, статус и числовая сложность `1–100`. `answer_format` не входит в `TaskListQuery`, поэтому не показывается. Subject определяется URL и внутри предмета не дублируется. Тема зависит от предмета и класса, подтема — от темы, навык — от темы/подтемы; смена родителя очищает дочерние значения.

Состояние задания сериализуется в query string с именами API (`grade_id`, `topic_id`, `subtopic_id`, `skill_id`, `task_type`, `status`, `difficulty_min`, `difficulty_max`, `q`, `offset`, `limit`, `sort_by`, `sort_order`). Пустые и неизвестные enum-значения не отправляются. Изменения создают browser-history entries, поэтому reload и Back/Forward восстанавливают состояние. Без явной сортировки backend выбирает `created_at desc`, а при непустом `q` — `relevance desc`. Фильтры затрагивают только direct tasks: папки и breadcrumb сохраняются.

На главной оставлена единственная стабильная точка импорта — ссылка **«Импорт»** в глобальной навигации. Удалены дубли из старой панели `TaskList` и его empty state; глобальная навигация доступна при пустом и непустом банке. Прямой `/content-bank/import` и root-only workflow не изменены.

Название задания теперь является настоящей ссылкой на `/content-bank/tasks/{task_id}` в FolderBrowser и табличном TaskList. Правая ссылка «Открыть» удалена; пустое название получает ссылку-fallback «Задание без названия». Действие перемещения остаётся справа.

## Windows PowerShell: чистый handoff без удаления dev-БД

Из корня репозитория:

```powershell
# 1. Пересобрать оба приложения.
docker compose build backend frontend

# 2. Пересоздать контейнеры, сохранив volume БД.
docker compose up -d --force-recreate postgres backend frontend

# 3. Только forward-only upgrade.
docker compose run --rm backend alembic upgrade head

# 4. Создать уникальную test-БД; логин и пароль берутся из вашей конфигурации, а не угадываются.
$TestDb = "ai_school_test_$([DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss'))"
$DbUser = Read-Host "POSTGRES_USER из локального .env"
docker compose exec -T postgres createdb -U $DbUser $TestDb
$DbPassword = Read-Host "POSTGRES_PASSWORD из локального .env"
$TestDatabaseUrl = "postgresql+asyncpg://${DbUser}:$([uri]::EscapeDataString($DbPassword))@postgres:5432/${TestDb}"

# 5. Backend suite без integration skips (проверьте итог: 0 skipped).
docker compose run --rm -e TEST_DATABASE_URL=$TestDatabaseUrl backend pytest -q -rs

# 6–7. Полный frontend suite и production build.
docker compose run --rm frontend npm test -- --run
docker compose run --rm frontend npm run build

# 8. Запустить UI.
docker compose up -d backend frontend
Start-Process "http://localhost:5173/content-bank"

# 9. Свежие logs (Ctrl+C прекращает просмотр, не контейнеры).
docker compose logs --since 10m -f backend frontend

# 10. Безопасная остановка без удаления containers/volumes.
docker compose stop
```

После тестов уникальную test-БД можно удалить точечно только при отсутствии подключений: `docker compose exec -T postgres dropdb -U $DbUser $TestDb`. Не использовать downgrade dev-БД, `docker compose down -v` или удаление volumes.

## Ручной UI-checklist (не считается выполненным автоматически)

1. Открыть subject root.
2. Убедиться, что большой панели всех фильтров нет.
3. Добавить каждый filter по одному.
4. Удалить отдельный filter.
5. Нажать «Сбросить все».
6. Проверить зависимые каталоги класса, темы, подтемы и навыка.
7. Проверить сложность `1`, `100`, `20–60` и ошибку `80–20`.
8. Совместить поиск и несколько фильтров.
9. Проверить pagination и сохранение параметров.
10. Перезагрузить страницу.
11. Проверить Back/Forward.
12. Перейти во вложенную папку с активными фильтрами.
13. При нулевом результате убедиться, что папки остаются.
14. На главной проверить ровно одну ссылку импорта в навигации.
15. Открыть import page и проверить preview/commit.
16. Открыть карточку по ссылке в title.
17. Проверить fallback для nullable/пустого title.
18. Пройти controls клавиатурой и проверить focus после add/remove.
19. Проверить ширины 320, 375 и 520 px.
20. Проверить browser console и Network: query names, direct endpoint, отсутствие stale rendering.

## Известные ограничения

Импорт по-прежнему root-only. Нет drag-and-drop папок, recursive delete и импорта folder path. PostgreSQL integration tests требуют явно заданный `TEST_DATABASE_URL`; ручной browser checklist и визуальная проверка не объявляются пройденными этим документом.
