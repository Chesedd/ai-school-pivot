# AI School Pivot

Content Bank — реализованный каталог учебных заданий на FastAPI, SQLAlchemy,
PostgreSQL 17 и React/Vite. Alembic остаётся владельцем схемы БД.

Сейчас доступны справочники, создание и поиск заданий, карточка и версии,
редактирование методики draft-версии, статусный цикл review/approval,
архивирование, audit history, CSV/XLSX preview и commit, а также
warning-only проверка возможных дубликатов.

Также реализованы иерархические папки заданий и управляемые теги. Assessment
Core поддерживает составление и публикацию работ, назначения, фиксированный
вариант, student attempt lifecycle до отправки ответа и внутренний
[Checking Handoff v1](docs/assessment-checking-handoff-v1.md).

Следующий, пока не реализованный этап — Checking Engine (детерминированная и
LLM-проверка). Аналитика, OCR, генерация заданий и полноценные IAM/авторизация
также не реализованы. Demo/dev seed не является реальным методически принятым
корпусом 50–100 заданий.

## Быстрый запуск

Из корня репозитория:

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}

docker compose --progress=plain build
docker compose up -d postgres backend frontend
docker compose exec -T backend alembic upgrade head
docker compose exec -T backend python -m app.db.seed
docker compose ps
```

Интерфейс: <http://localhost:5173/content-bank>. Проверка backend:
<http://localhost:8000/health>. Не используйте `docker compose down -v` для
обычной остановки: эта команда удаляет локальный PostgreSQL volume.

## Документация

- [Пользовательский гайд Content Bank](docs/content-bank-user-guide.md) —
  возможности, ограничения, локальный запуск и безопасная полная проверка.
- [Индекс документации](docs/README.md).
- [Финальная техническая приёмка Content Bank](docs/content-bank-stage2-acceptance.md).

Полные PowerShell-сценарии и процедуры приёмки намеренно не дублируются здесь:
используйте пользовательский гайд и актуальный acceptance-документ.
