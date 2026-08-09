# Assessment → Checking Handoff v1

## Назначение и граница

`version = 1` описывает **внутренний**, read-only снимок отправленной попытки для будущего Checking Engine. Публичного HTTP endpoint нет. Phase 3 не определяет correctness, сравнение accepted answers, tolerance, score, verdict, confidence, AI-вызовы или teacher review.

Assessment владеет submission snapshot. Будущий Checking Engine по точному историческому `task_version_id` отдельно запросит checker-specific authored context у Content Bank; `expected_solution`, `accepted_answers`, rubric, hints, errors и skills в handoff не копируются.

## Схема

Top level:

- `submission_id: UUID` — техническая связь с отправкой;
- `submitted_at: RFC 3339 UTC timestamp`;
- `items: array<item>`.

Item:

- `assessment_item_id: UUID`;
- `task_version_id: UUID`;
- `position: integer`;
- `points: fixed decimal string` — frozen максимальный вес item, **не** начисленные баллы;
- `answer_format: string` — из этой точной исторической task version;
- `raw_answer: JSON | null`;
- `normalized_answer: JSON object | null`.

Items материализуются в порядке `(position ASC, assessment_item_id ASC)`. Включаются все items назначенного variant. Пара `null/null` означает отсутствие `StudentAnswer` row (unanswered), а не сохранённый JSON-null. Поэтому submission без ответов всё равно содержит items.

Answered values читаются непосредственно из сохранённых `StudentAnswer.raw_answer` и `StudentAnswer.normalized_answer`. Handoff не вызывает normalizer повторно: новая версия normalizer не меняет исторический snapshot. Raw сохраняет parsed JSON клиента, включая Unicode, whitespace и порядок массива; normalized имеет консервативную форму, созданную при save.

Историческое чтение использует exact `assessment_item.task_version_id`, без latest/approved revalidation. Последующее архивирование task/version не нарушает handoff. Материализованный immutable DTO не содержит ORM entities.

## Privacy

Намеренно исключены `student_id`, `participant_id`, имена, `external_ref`, group/class, actor/teacher IDs, email, phone и иные PII. Также отсутствуют correctness, score, awarded points, percentage, verdict и confidence.

## Пример

```json
{"submission_id":"10000000-0000-4000-8000-000000000001","submitted_at":"2026-08-09T10:00:00Z","items":[{"assessment_item_id":"20000000-0000-4000-8000-000000000001","task_version_id":"30000000-0000-4000-8000-000000000001","position":1,"points":"1.00","answer_format":"single_choice","raw_answer":"B","normalized_answer":{"option_id":"B"}}]}
```

Контролируемый synthetic dataset находится в `backend/tests/fixtures/checking_handoff_v1.json`. Его `case_id` служат стабильной идентификацией normalization/handoff примеров; это не correctness-labelled golden set.
