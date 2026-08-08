# Проверка trusted pilot frontend справочника тегов

Маршрут `/content-bank/admin/tags` — trusted pilot: production authentication/RBAC отсутствует. Страница не принимает browser-role и постоянно показывает предупреждение. Backend production-код и applied revision `20260808_01` не изменяются.

## Windows PowerShell

```powershell
docker compose build
docker compose up -d postgres
$env:DATABASE_URL = '<TEST_POSTGRES_URL_FROM_YOUR_ENVIRONMENT>'
Set-Location backend
alembic upgrade head
alembic heads
pytest -q
Set-Location ../frontend
npm ci
npm test -- --run
npm run build
npm run dev -- --host 0.0.0.0
```

Используйте отдельный PostgreSQL test URL из локального secret/config, не угаданные credentials. Не выполняйте downgrade рабочей БД, `docker compose down -v` или удаление volumes. Безопасная остановка: `Ctrl+C`, затем `docker compose stop`.

## Ручной checklist

1. Открыть `http://localhost:5173/content-bank/admin/tags`, reload, Back/Forward, active «Теги»; «Импорт» должен быть один.
2. Проверить loading/error/retry, empty/filtered empty, search Enter/trim/reset, category/status/subject-compatible filters, URL и pagination.
3. Создать global/subject tags; проверить validation, fuzzy similarity confirmation, exact duplicate и double submit.
4. Изменить name/category/scope. Проверить PATCH и `expected_updated_at`; в двух вкладках получить CAS без silent overwrite.
5. Проверить usage active/deprecated: historical, distinct task, latest и status counts, включая нули.
6. Деактивировать без замены и с совместимой active replacement; проверить self/deprecated/incompatible exclusion, cycle/scope errors, сохранение старых associations и отсутствие delete.
7. Проверить свежие browser/backend logs: без traceback, SQL и raw JSON.
8. Проверить 320, 375, 520 px и desktop: page без horizontal scroll, actions, длинные имена, scroll dialog.
9. Клавиатурой проверить filters, initial focus, Tab trap, Escape, busy close protection, focus return и visible focus.

Не отмечайте PostgreSQL или ручную проверку пройденной без запуска. Editor/task cards, compact tag filter, proposals, roles/auth, category management, reactivation и physical delete остаются вне scope.
