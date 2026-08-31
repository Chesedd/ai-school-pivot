from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.application.image_solving_contracts import ExtractionResultV1, ImageSolvingSession, ImageSolvingStatus
from app.application.image_solving_metadata import CatalogItemV1, MetadataCatalogSnapshotV1, TagCandidateV1, resolve_metadata


def item(name, **values):
    return CatalogItemV1(id=uuid4(), name=name, **values)


def resolve(*, duplicate_subject=False, incompatible_tag=False):
    subject=item("Математика"); other=item("Физика")
    grade=item("6 класс", grade_number=6)
    topic=item("Уравнения", subject_id=subject.id, grade_id=grade.id)
    wrong_topic=item("Уравнения", subject_id=other.id, grade_id=grade.id)
    sub=item("Линейные уравнения", topic_id=topic.id)
    wrong_sub=item("Линейные уравнения", topic_id=wrong_topic.id)
    skill=item("Решать линейные уравнения", topic_id=topic.id, subtopic_id=sub.id)
    wrong_skill=item("Решать линейные уравнения", topic_id=wrong_topic.id, subtopic_id=wrong_sub.id)
    tag=TagCandidateV1(id=uuid4(), name="Алгебра", category_code="method",
        subject_id=other.id if incompatible_tag else subject.id)
    catalog=MetadataCatalogSnapshotV1(subjects=(subject,) + ((item(" математика "),) if duplicate_subject else ()),
        grades=(grade,), topics=(topic,wrong_topic), subtopics=(sub,wrong_sub),
        skills=(skill,wrong_skill), tag_categories=("method",), tags=(tag,))
    extraction=ExtractionResultV1(extracted_text="7 · (X - 3) = 21",
        structured_statement="Решить уравнение 7 · (X - 3) = 21.",
        detected_task_type="calculation",detected_answer_format="number",choices=None,
        extraction_confidence=Decimal(".99"),ocr_issues=(),metadata={
            "title":"Решение линейного уравнения","subject":"Математика","grade":6,
            "topic":"Уравнения","subtopic":"Линейные уравнения",
            "skills":("Решать линейные уравнения",),"task_type":"calculation",
            "answer_format":"number","difficulty":2,"tags":("Алгебра",)})
    now=datetime.now(UTC)
    session=ImageSolvingSession(session_id=uuid4(),owner_id=uuid4(),input_artifact_id=uuid4(),
        extraction_checkpoint=extraction,lifecycle_status=ImageSolvingStatus.VALIDATED,
        created_at=now,updated_at=now)
    return resolve_metadata(session,catalog), (subject,grade,topic,sub,skill,tag)


def test_exact_resolution_is_scoped_to_selected_hierarchy():
    result, expected=resolve()
    subject,grade,topic,sub,skill,tag=expected
    assert (result.subject.id,result.grade.id,result.topic.id,result.subtopic.id)==(subject.id,grade.id,topic.id,sub.id)
    assert result.skills[0].id==skill.id and result.tags[0].id==tag.id
    assert result.folder is None


def test_ambiguous_subject_leaves_hierarchy_unresolved_without_fake_ids():
    result,_=resolve(duplicate_subject=True)
    assert result.subject.kind=="new" and result.subject.proposed_name=="Математика"
    assert result.topic.kind==result.subtopic.kind==result.skills[0].kind=="new"


def test_incompatible_tag_is_an_unresolved_name():
    result,_=resolve(incompatible_tag=True)
    assert result.tags[0].kind=="new" and result.tags[0].name=="Алгебра"
