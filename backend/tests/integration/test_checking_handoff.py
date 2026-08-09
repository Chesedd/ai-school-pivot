"""Real PostgreSQL projection tests for internal Checking Handoff v1."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.application.checking_handoff import CheckingHandoffNotReady
from app.infrastructure.student_assessment_repository import AssessmentCheckingHandoffService
from tests.integration.test_student_assessment_api import client, database, scenario, start

pytestmark = pytest.mark.asyncio


async def test_submitted_all_formats_unanswered_archive_and_stored_snapshot(client, database, monkeypatch):
    engine, factory = database
    ids = await scenario(database, formats=("short_text", "short_text"))
    attempt = (await start(client, ids)).json()
    async with factory() as session:
        participant = await session.get(__import__("app.infrastructure.assessment_models", fromlist=["AssignmentParticipant"]).AssignmentParticipant, ids["participant"])
        variant_id = participant.assigned_variant_id
        existing = (await session.execute(text("SELECT ai.id, tv.id AS version_id, t.id AS task_id FROM assessment_items ai JOIN task_versions tv ON tv.id=ai.task_version_id JOIN tasks t ON t.id=tv.task_id WHERE ai.variant_id=:v"), {"v": variant_id})).mappings().one()
    formats = ["single_choice", "multiple_choice", "number", "expression", "long_text"]
    added = []
    async with engine.begin() as conn:
        metadata = (await conn.execute(text("SELECT subject_id,grade_id,topic_id,created_by FROM tasks WHERE id=:id"), {"id": existing["task_id"]})).mappings().one()
        for position, fmt in enumerate(formats, 2):
            task_id, version_id, item_id = uuid4(), uuid4(), uuid4()
            await conn.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject_id,:grade_id,:topic_id,:created_by)"), {**metadata, "task": task_id})
            await conn.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'Synthetic','problem',CAST(:fmt AS answer_format),50,'approved',:created_by)"), {**metadata, "version": version_id, "task": task_id, "fmt": fmt})
            await conn.execute(text("INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,:position,:points)"), {"item": item_id, "variant": variant_id, "version": version_id, "position": position, "points": f"{position}.25"})
            added.append((item_id, version_id, task_id, fmt))
    inputs = {"single_choice": "B", "multiple_choice": ["b", "a"], "number": "001,2300",
              "expression": "  x−1\r\n+ X  ", "long_text": " A\rB "}
    for item_id, _, _, fmt in added:
        response = await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item_id}", json={"raw_answer": inputs[fmt], "expected_updated_at": None})
        assert response.status_code == 201
    submitted = await client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit", json={}, headers={"Idempotency-Key": "submit-handoff"})
    assert submitted.status_code == 200
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:id OR id = ANY(:ids)"), {"id": existing["task_id"], "ids": [row[2] for row in added]})
    import app.application.student_assessments as normalization
    monkeypatch.setattr(normalization, "normalize_answer", lambda *_: (_ for _ in ()).throw(AssertionError("must not normalize")))
    handoff = await AssessmentCheckingHandoffService(factory).get(UUID(attempt["id"]))
    assert len(handoff.items) == 6
    assert [(x.position, x.assessment_item_id) for x in handoff.items] == sorted((x.position, x.assessment_item_id) for x in handoff.items)
    assert handoff.items[0].raw_answer is None and handoff.items[0].normalized_answer is None
    by_format = {item.answer_format: item for item in handoff.items}
    assert by_format["multiple_choice"].raw_answer == ["b", "a"]
    assert by_format["multiple_choice"].normalized_answer == {"option_ids": ["a", "b"]}
    assert by_format["number"].normalized_answer == {"decimal": "1.23"}
    assert by_format["expression"].normalized_answer == {"expression": "x−1\n+ X"}
    assert by_format["long_text"].normalized_answer == {"text": " A\nB "}
    assert by_format["single_choice"].points == __import__("decimal").Decimal("2.25")
    assert {x.task_version_id for x in handoff.items} == {existing["version_id"], *(row[1] for row in added)}
    projected = handoff.as_dict()
    serialized = str(projected)
    for forbidden in ("student_id", "participant_id", "display_name", "score", "verdict", "correctness", "confidence"):
        assert forbidden not in serialized


async def test_draft_not_ready_and_zero_answer_submission(client, database):
    _, factory = database; ids = await scenario(database)
    attempt = (await start(client, ids)).json(); service = AssessmentCheckingHandoffService(factory)
    with pytest.raises(CheckingHandoffNotReady):
        await service.get(UUID(attempt["id"]))
    assert (await client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit", json={}, headers={"Idempotency-Key": "zero"})).status_code == 200
    handoff = await service.get(UUID(attempt["id"]))
    assert len(handoff.items) == 1 and handoff.items[0].raw_answer is None and handoff.items[0].normalized_answer is None

@pytest.mark.parametrize(("fmt", "raw", "normalized"), [
    ("short_text", "  A\r\nB\rC  ", {"text": "A\nB\nC"}),
    ("expression", "  x\r\n+ X\r  ", {"expression": "x\n+ X"}),
])
async def test_put_preserves_raw_and_normalizes_text_newlines(client, database, fmt, raw, normalized):
    ids = await scenario(database, formats=(fmt, fmt))
    attempt = (await start(client, ids)).json()
    item_id = ids["item_0"] if attempt["assigned_variant_id"] == str(ids["variant_a"]) else ids["item_1"]
    response = await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item_id}",
                                json={"raw_answer": raw, "expected_updated_at": None})
    assert response.status_code == 201
    assert response.json()["raw_answer"] == raw
    assert response.json()["normalized_answer"] == normalized
