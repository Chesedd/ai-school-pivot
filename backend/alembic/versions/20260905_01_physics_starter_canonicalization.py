"""Canonicalize the untouched historical Physics starter Topic.

Revision ID: 20260905_01
Revises: 20260904_01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260905_01"
down_revision = "20260904_01"
branch_labels = None
depends_on = None

SUBJECT_NAME = "Физика"
SUBJECT_NORMALIZED = "физика"
GRADE_NUMBER = 7
OLD_TOPIC_NAME = "Механика"
OLD_TOPIC_NORMALIZED = "механика"
NEW_TOPIC_NAME = "Движение и взаимодействие тел"
NEW_TOPIC_NORMALIZED = "движение и взаимодействие тел"
HISTORICAL_SUBTOPIC = "Равномерное движение"
HISTORICAL_SUBTOPIC_NORMALIZED = "равномерное движение"
HISTORICAL_SKILLS = {
    "Вычислять скорость": "вычислять скорость",
    "Строить график движения": "строить график движения",
}


def _fail(reason: str) -> None:
    raise RuntimeError(
        "Physics historical starter canonicalization refused: "
        f"{reason}. Manual catalog reconciliation is required."
    )


def _untouched(row) -> bool:
    return (
        row.status == "active"
        and row.proposed_by is None
        and row.resolved_by is None
        and row.resolved_at is None
        and row.replacement_id is None
        and row.resolution_reason is None
    )


def upgrade():
    connection = op.get_bind()
    old_topics = connection.execute(sa.text("""
        SELECT t.* FROM topics t
        JOIN subjects s ON s.id = t.subject_id
        JOIN grades g ON g.id = t.grade_id
        WHERE s.name = :subject_name AND s.normalized_name = :subject_normalized
          AND g.number = :grade_number
          AND (t.name = :old_name OR t.normalized_name = :old_normalized)
        FOR UPDATE OF t
    """), {
        "subject_name": SUBJECT_NAME, "subject_normalized": SUBJECT_NORMALIZED,
        "grade_number": GRADE_NUMBER, "old_name": OLD_TOPIC_NAME,
        "old_normalized": OLD_TOPIC_NORMALIZED,
    }).mappings().all()
    if not old_topics:
        return
    if len(old_topics) != 1:
        _fail("the historical Topic identity is ambiguous")
    topic = old_topics[0]

    if (topic.name != OLD_TOPIC_NAME or topic.normalized_name != OLD_TOPIC_NORMALIZED
            or not _untouched(topic)):
        _fail("the historical Topic has operator-owned lifecycle or identity changes")

    target = connection.execute(sa.text("""
        SELECT id FROM topics
        WHERE subject_id = :subject_id AND grade_id = :grade_id
          AND (name = :name OR normalized_name = :normalized)
          AND status IN ('active', 'provisional') AND id <> :old_id
    """), {"subject_id": topic.subject_id, "grade_id": topic.grade_id,
            "name": NEW_TOPIC_NAME, "normalized": NEW_TOPIC_NORMALIZED,
            "old_id": topic.id}).first()
    if target:
        _fail("a different live/provisional canonical target already exists")

    subtopics = connection.execute(sa.text(
        "SELECT * FROM subtopics WHERE topic_id = :topic_id FOR UPDATE"
    ), {"topic_id": topic.id}).mappings().all()
    if len(subtopics) != 1:
        _fail("the historical Topic does not have exactly one untouched Subtopic")
    subtopic = subtopics[0]
    if (subtopic.name != HISTORICAL_SUBTOPIC
            or subtopic.normalized_name != HISTORICAL_SUBTOPIC_NORMALIZED
            or not _untouched(subtopic)):
        _fail("the historical Subtopic has identity or lifecycle changes")

    skills = connection.execute(sa.text(
        "SELECT * FROM skills WHERE subtopic_id = :subtopic_id FOR UPDATE"
    ), {"subtopic_id": subtopic.id}).mappings().all()
    actual_skills = {row.name: row for row in skills}
    if len(skills) != 2 or set(actual_skills) != set(HISTORICAL_SKILLS):
        _fail("the historical Subtopic does not have exactly the two starter Skills")
    for name, normalized in HISTORICAL_SKILLS.items():
        row = actual_skills[name]
        if row.normalized_name != normalized or not _untouched(row):
            _fail(f"historical Skill {name!r} has identity or lifecycle changes")

    unsafe_task = connection.execute(sa.text("""
        SELECT id FROM tasks
        WHERE topic_id = :topic_id
          AND (subtopic_id IS NULL OR subtopic_id <> :subtopic_id)
        LIMIT 1
    """), {"topic_id": topic.id, "subtopic_id": subtopic.id}).first()
    if unsafe_task:
        _fail("a Task has a broad or unexpected historical Topic reference")

    result = connection.execute(sa.text("""
        UPDATE topics
        SET name = :name, normalized_name = :normalized, updated_at = clock_timestamp()
        WHERE id = :id
    """), {"name": NEW_TOPIC_NAME, "normalized": NEW_TOPIC_NORMALIZED,
            "id": topic.id})
    if result.rowcount != 1:
        _fail("the guarded Topic update did not affect exactly one row")


def downgrade():
    raise NotImplementedError(
        "20260905_01 is a forward-only canonical catalog history migration"
    )
