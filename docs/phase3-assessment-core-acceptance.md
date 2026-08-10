# Phase 3 Assessment Core — финальная техническая приёмка

## Scope и DoD

Phase 3 покрывает teacher CRUD/composition, атомарную первую publication/assignment,
student start/resume/save/submit/history и внутренний Checking Handoff v1. Учитель может
создать два и более вариантов из конкретных approved task versions, выбрать активную
pilot-группу, опубликовать, после reload обнаружить и необратимо закрыть assignment.
Ученик видит назначение, получает фиксированный детерминированный вариант, сохраняет
raw answers, отправляет и повторно открывает submitted attempt только для чтения.
Checking Engine, score, verdict, review, jobs, analytics и repeated assignment mutation
не входят в scope.

## Routes

Teacher read: `GET /class-groups`, `GET /assessments/{id}/assignments`, существующие
`GET /assignments/{id}`. Mutations: assessment CRUD/composition,
`POST /assessments/{id}/publish-and-assign`, `POST /assignments/{id}/close`.
Student: assignment list/detail, idempotent START, attempt GET, answer PUT/DELETE и
idempotent SUBMIT. Все пути имеют prefix `/api/assessment-core`.

## Инварианты и vertical scenario

Publication блокирует composition и сохраняет concrete task-version references;
participant snapshot не меняется с membership группы. Variant вычисляется из SHA-256
assignment UUID bytes + student UUID bytes и фиксируется. START/SUBMIT используют
idempotency key и транзакционные locks; deadline сравнивается с DB clock. Raw JSON
сохраняется буквально, normalized snapshot создаётся при save и handoff читает именно
его без renormalization. Submitted read/handoff продолжают работать после archive task/version
и после close; новые mutations блокируются.

Реальный PostgreSQL scenario находится в
`tests/integration/test_phase3_vertical_acceptance.py::test_phase3_teacher_student_historical_handoff_vertical`.
Он через production HTTP создаёт assessment и ровно два variants, добавляет concrete approved
number versions, публикует на snapshot из двух студентов и повторно обнаруживает assignment.
Test независимо (без production helper) вычисляет SHA-256 variant, доказывает freeze,
START/SUBMIT replay, raw save + отдельный GET reopen (`" 001,2300e2 "` →
`{"decimal":"123"}`), submitted history, stored Checking Handoff, production archive,
historical read после archive/close, `assignment_closed` для нового START и exactly-once audits
без raw answer в audit details. Focused ownership/sorting/privacy proof submitted history находится
в `tests/integration/test_student_assessment_api.py`.

## Privacy и identity

Group catalogue не раскрывает PII/participant identities; assignment list не содержит
answers; student history ограничена server-side текущим pilot participant; handoff не
содержит student identity. Полноценный IAM — **EXPLICIT MVP OUT-OF-SCOPE**; server actor
и pilot student context остаются временной границей identity.

## Checking Handoff

Handoff v1 технически готов как internal read model stored submitted snapshot. Checking
Engine намеренно не начат.

## Ограничения продукта и 3.CR

* Authored choice option catalogue отсутствует в execution item: **PRODUCT LIMITATION**,
  не blocker технической data integrity, но blocker polished real-child UX.
* Picker ищет current tasks с `status=approved`; older approved version может быть скрыта,
  если latest version draft/review: **PRODUCT/CONTENT-BANK UX FOLLOW-UP**.
* Полный IAM: **EXPLICIT MVP OUT-OF-SCOPE**.
* Реальный корпус 50–100 педагогических items не завершён: **3.CR / PRODUCT ACCEPTANCE DEFERRED**.

Следовательно, `PHASE3_TECHNICAL_ACCEPTANCE` может быть `PASS` только после полного local
PostgreSQL/migration/frontend gate. `PRODUCT_ACCEPTANCE = DEFERRED_UNTIL_CORPUS` независимо
от результата технического gate. Этот документ не фиксирует вымышленные пользовательские PASS.

## Exact acceptance commands

Backend (с `TEST_DATABASE_URL` на disposable БД с suffix `_test`):

```bash
alembic upgrade head
alembic downgrade base
alembic upgrade head
alembic check
python -m compileall -q app tests
pytest -q tests/integration/test_phase3_vertical_acceptance.py
pytest -q tests/integration/test_assessment_api.py tests/integration/test_student_assessment_api.py
pytest -q
```

Frontend:

```bash
npm test -- --run
npm run build
```

Container build, если Docker доступен: `docker compose build backend frontend`.
Manual live smoke не считается выполненным автоматическими тестами.

До выполнения disposable PostgreSQL migration/test gate статус остаётся
`PHASE3_TECHNICAL_ACCEPTANCE = PENDING_LOCAL_GATE`; наличие и collection acceptance test само
по себе не является behavioral PASS. `PRODUCT_ACCEPTANCE = DEFERRED_UNTIL_CORPUS`.

Local compose credentials берутся из repository `.env.example`:
`POSTGRES_USER=content_bank`, `POSTGRES_PASSWORD=change-me-for-local-development`, основной DB
`content_bank`, disposable DB `content_bank_test`. Внутрисетевой test URL:
`postgresql+asyncpg://content_bank:change-me-for-local-development@postgres:5432/content_bank_test`.
