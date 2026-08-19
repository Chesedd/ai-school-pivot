# Документация

- [AI Content Authoring v1 — контракт Phase 4A](ai-content-authoring-v1-contract.md) —
  Phase 4A.0, только документация и архитектура. Принадлежащие Content Bank
  реализационные Phases 4A.1–4A.6 ещё не начаты.
- [Проверка Checking intake Phase 4.2](checking-intake-phase42-verification.md) — детерминированный исторический snapshot, хеши, транзакция и privacy boundary.

- [Проверка Checking DB Foundation](checking-engine-db-foundation-verification.md) —
  схема, PostgreSQL invariants, migration lifecycle и локальный gate Phase 4.1.
- [Checking Engine v1: контракт и gap audit](checking-engine-v1-contract.md) —
  исходные решения Phase 4.0 и последующие контракты. Phases 4.0–4.8 уже
  включают persistence foundation, immutable intake, routing/checkers,
  provider execution и LLM rubric checker; Checking Phases 4.9 и 4.10 остаются
  незавершёнными, поэтому production-wide готовность не заявляется.
- [Финальная приёмка Assessment Core Phase 3](phase3-assessment-core-acceptance.md).

- [Пользовательский гайд Content Bank](content-bank-user-guide.md) —
  возможности интерфейса, ограничения, локальный запуск и безопасная проверка.
- [Контракты Content Bank](content-bank-contracts.md).
- [Assessment → Checking Handoff v1](assessment-checking-handoff-v1.md) — внутренний read model отправленной попытки без correctness и PII.
- [Контракт Assessment Core](assessment-core-contracts.md) — Phase 3.0:
  схема, состояния, API, конкурентность, идемпотентность и исторические гарантии
  до начала production-реализации.
- [Контракт управляемых тегов Content Bank](content-bank-managed-tags-contract.md) —
  схема, API, импорт, поиск, аудит и границы pilot-доступа.
- [Финальная техническая приёмка Content Bank](content-bank-stage2-acceptance.md).
- [Контракт иерархии папок заданий](content-bank-folder-hierarchy-contract.md) —
  исходный contract-first дизайн; backend и frontend папок уже реализованы.

* [Проверка backend иерархии папок](content-bank-folder-hierarchy-backend-verification.md)

- [Проверка пользовательского frontend управляемых тегов](content-bank-managed-tags-user-frontend-verification.md)
