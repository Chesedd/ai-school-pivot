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

Checking Engine уже не является целиком не реализованным: Phases 4.0–4.9
включают контракты, persistence foundation, immutable intake,
детерминированные routing/checkers, provider execution и LLM rubric checker.
Phase 4.9 findings/confidence принята. Техническая приёмка Phase 4.10 включает
детерминированный/application gate и отдельный реальный PostgreSQL gate; она не
заявляется до успешного PostgreSQL прогона. Real-provider quality evaluation
остаётся отдельным явно конфигурируемым gate, а fake provider её не заменяет.

AI-генерация заданий пользователям пока недоступна. Она запланирована как
отдельный, принадлежащий Content Bank трек Phase 4A. Текущая Phase 4A.0
фиксирует только [контракт и roadmap AI Content Authoring v1](docs/ai-content-authoring-v1-contract.md);
реализация API, persistence, UI и provider workflow отложена до Phases
4A.1–4A.6. Аналитика, OCR и полноценные IAM/авторизация также не реализованы.
Demo/dev seed не является реальным методически принятым корпусом 50–100
заданий.

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
- [AI Content Authoring v1 — контракт Phase 4A](docs/ai-content-authoring-v1-contract.md) —
  документационный контракт Phase 4A.0; генерация заданий ещё не реализована.

Полные PowerShell-сценарии и процедуры приёмки намеренно не дублируются здесь:
используйте пользовательский гайд и актуальный acceptance-документ.

### Checking Phase 4.10 acceptance

Phase 4.9 is accepted at `cd0dbbd`. Phase 4.10's executable 60-case application
gate is implemented; PostgreSQL technical acceptance remains pending until the
real local continuation gate passes without failures or skips. Both gates are documented in
[the acceptance specification](docs/checking-engine-phase410-acceptance.md).
Real-provider quality evaluation remains separately pending and explicitly
configured; fake-provider success is not quality acceptance.
No final grades, Teacher Review, or Phase 4A authoring behavior is included.
