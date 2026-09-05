from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.image_solving_contracts import ExtractionResultV1, ImageSolvingSession, ImageSolvingStatus
from app.application.image_solving_metadata import (CatalogAliasV1, CatalogItemV1,
    CachedMetadataRecommendation, ImageTaskMetadataRecommendationV1, MetadataCatalogSnapshotV1,
    TagCandidateV1, resolve_metadata)
from app.infrastructure.image_solving_repository import deserialize_json_contract


def item(name, **values):
    return CatalogItemV1(id=uuid4(), name=name,
        catalog_status=values.pop("catalog_status", "active"), **values)


def resolve(*, duplicate_subject=False, incompatible_tag=False,
            extracted_subject="Математика", include_subject=True, status="active"):
    subject=item("Математика", catalog_status=status); other=item("Физика")
    grade=item("6 класс", grade_number=6, catalog_status=status)
    topic=item("Уравнения", subject_id=subject.id, grade_id=grade.id, catalog_status=status)
    wrong_topic=item("Уравнения", subject_id=other.id, grade_id=grade.id)
    sub=item("Линейные уравнения", topic_id=topic.id, catalog_status=status)
    wrong_sub=item("Линейные уравнения", topic_id=wrong_topic.id)
    skill=item("Решать линейные уравнения", topic_id=topic.id, subtopic_id=sub.id, catalog_status=status)
    wrong_skill=item("Решать линейные уравнения", topic_id=wrong_topic.id, subtopic_id=wrong_sub.id)
    tag=TagCandidateV1(id=uuid4(), name="Алгебра", category_code="method",
        subject_id=other.id if incompatible_tag else subject.id)
    catalog=MetadataCatalogSnapshotV1(subjects=((subject,) if include_subject else ()) +
        ((item(" математика "),) if duplicate_subject else ()),
        grades=(grade,), topics=(topic,wrong_topic), subtopics=(sub,wrong_sub),
        skills=(skill,wrong_skill), tag_categories=("method",), tags=(tag,))
    extraction=ExtractionResultV1(extracted_text="7 · (X - 3) = 21",
        structured_statement="Решить уравнение 7 · (X - 3) = 21.",
        detected_task_type="calculation",detected_answer_format="number",choices=None,
        extraction_confidence=Decimal(".99"),ocr_issues=(),metadata={
            "title":"Решение линейного уравнения","subject":extracted_subject,"grade":6,
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
    assert (result.subject.label, result.grade.label, result.topic.label,
            result.subtopic.label, result.skills[0].label) == (
        "Математика", "6", "Уравнения", "Линейные уравнения",
        "Решать линейные уравнения")


@pytest.mark.parametrize(("grade_number", "topic_name", "subtopic_name", "skill_name"), [
    (5, "Дроби", "Десятичные дроби", "Сравнивать десятичные дроби"),
    (6, "Дроби", "Проценты", "Находить процент от величины"),
])
def test_mathematics_5_6_catalog_metadata_resolves_locally(
        grade_number, topic_name, subtopic_name, skill_name):
    subject = item("Математика")
    grade = item(str(grade_number), grade_number=grade_number)
    topic = item(topic_name, subject_id=subject.id, grade_id=grade.id)
    subtopic = item(subtopic_name, topic_id=topic.id)
    skill = item(skill_name, topic_id=topic.id, subtopic_id=subtopic.id)
    catalog = MetadataCatalogSnapshotV1(
        subjects=(subject,), grades=(grade,), topics=(topic,), subtopics=(subtopic,),
        skills=(skill,), tag_categories=(), tags=())
    extraction = ExtractionResultV1(
        extracted_text="Задание", structured_statement="Выполнить задание.",
        detected_task_type="calculation", detected_answer_format="number", choices=None,
        extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={
            "title": "Задание", "subject": "Математика", "grade": grade_number,
            "topic": topic_name, "subtopic": subtopic_name, "skills": (skill_name,),
            "task_type": "calculation", "answer_format": "number", "difficulty": 2,
            "tags": (),
        })
    now = datetime.now(UTC)
    session = ImageSolvingSession(
        session_id=uuid4(), owner_id=uuid4(), input_artifact_id=uuid4(),
        extraction_checkpoint=extraction, lifecycle_status=ImageSolvingStatus.VALIDATED,
        created_at=now, updated_at=now)

    result = resolve_metadata(session, catalog)

    assert (result.subject.id, result.grade.id, result.topic.id, result.subtopic.id,
            result.skills[0].id) == (
                subject.id, grade.id, topic.id, subtopic.id, skill.id)


@pytest.mark.parametrize(("grade_number", "topic_name", "subtopic_name", "skill_name"), [
    (1, "Орфография и пунктуация", "ЖИ–ШИ", "Правильно писать сочетания жи–ши"),
    (2, "Орфография и пунктуация", "Безударные гласные в корне слова",
     "Подбирать проверочное слово для безударной гласной"),
    (3, "Морфология", "Падеж имён существительных",
     "Определять падеж имени существительного"),
    (4, "Морфология", "Спряжение глагола", "Определять спряжение глагола"),
])
def test_russian_primary_catalog_metadata_resolves_locally(
        grade_number, topic_name, subtopic_name, skill_name):
    subject = item("Русский язык")
    grade = item(str(grade_number), grade_number=grade_number)
    topic = item(topic_name, subject_id=subject.id, grade_id=grade.id)
    subtopic = item(subtopic_name, topic_id=topic.id)
    skill = item(skill_name, topic_id=topic.id, subtopic_id=subtopic.id)
    catalog = MetadataCatalogSnapshotV1(
        subjects=(subject,), grades=(grade,), topics=(topic,), subtopics=(subtopic,),
        skills=(skill,), tag_categories=(), tags=())
    extraction = ExtractionResultV1(
        extracted_text="Задание", structured_statement="Выполнить задание.",
        detected_task_type="open_question", detected_answer_format="short_text", choices=None,
        extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={
            "title": "Задание", "subject": "Русский язык", "grade": grade_number,
            "topic": topic_name, "subtopic": subtopic_name, "skills": (skill_name,),
            "task_type": "open_question", "answer_format": "short_text", "difficulty": 2,
            "tags": (),
        })
    now = datetime.now(UTC)
    session = ImageSolvingSession(
        session_id=uuid4(), owner_id=uuid4(), input_artifact_id=uuid4(),
        extraction_checkpoint=extraction, lifecycle_status=ImageSolvingStatus.VALIDATED,
        created_at=now, updated_at=now)

    result = resolve_metadata(session, catalog)

    assert (result.subject.id, result.grade.id, result.topic.id, result.subtopic.id,
            result.skills[0].id) == (
                subject.id, grade.id, topic.id, subtopic.id, skill.id)


@pytest.mark.parametrize(("grade_number", "topic_name", "subtopic_name", "skill_name"), [
    (5, "Морфология", "Имя существительное", "Определять падеж существительного"),
    (5, "Синтаксис", "Тире между подлежащим и сказуемым",
     "Определять условие постановки тире между подлежащим и сказуемым"),
    (6, "Морфология", "Имя числительное", "Определять разряд числительного по значению"),
    (6, "Словообразование", "Словообразовательный анализ",
     "Выполнять словообразовательный анализ"),
    (7, "Морфология", "Причастие", "Находить причастия в тексте"),
    (7, "Орфография и пунктуация", "Деепричастный оборот",
     "Расставлять знаки при деепричастном обороте"),
    (8, "Синтаксис и пунктуация", "Односоставные предложения",
     "Определять вид односоставного предложения"),
    (9, "Синтаксис и пунктуация", "Сложноподчинённое предложение",
     "Определять придаточную часть СПП"),
    (9, "Синтаксис и пунктуация", "Бессоюзное сложное предложение",
     "Распознавать бессоюзное сложное предложение"),
])
def test_russian_5_9_catalog_metadata_resolves_locally(
        grade_number, topic_name, subtopic_name, skill_name):
    subject = item("Русский язык")
    grade = item(str(grade_number), grade_number=grade_number)
    topic = item(topic_name, subject_id=subject.id, grade_id=grade.id)
    subtopic = item(subtopic_name, topic_id=topic.id)
    skill = item(skill_name, topic_id=topic.id, subtopic_id=subtopic.id)
    catalog = MetadataCatalogSnapshotV1(
        subjects=(subject,), grades=(grade,), topics=(topic,), subtopics=(subtopic,),
        skills=(skill,), tag_categories=(), tags=())
    extraction = ExtractionResultV1(
        extracted_text="Задание", structured_statement="Выполнить задание.",
        detected_task_type="open_question", detected_answer_format="short_text",
        choices=None, extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={
            "title": "Задание", "subject": "Русский язык", "grade": grade_number,
            "topic": topic_name, "subtopic": subtopic_name, "skills": (skill_name,),
            "task_type": "open_question", "answer_format": "short_text",
            "difficulty": 2, "tags": (),
        })
    now = datetime.now(UTC)
    session = ImageSolvingSession(
        session_id=uuid4(), owner_id=uuid4(), input_artifact_id=uuid4(),
        extraction_checkpoint=extraction, lifecycle_status=ImageSolvingStatus.VALIDATED,
        created_at=now, updated_at=now)

    result = resolve_metadata(session, catalog)

    assert (result.subject.id, result.grade.id, result.topic.id, result.subtopic.id,
            result.skills[0].id) == (subject.id, grade.id, topic.id, subtopic.id, skill.id)


@pytest.mark.parametrize(("grade_number", "topic_name", "subtopic_name", "skill_name"), [
    (7, "Функции", "Линейная функция", "Строить график линейной функции"),
    (8, "Уравнения и неравенства", "Квадратные уравнения", "Вычислять дискриминант"),
    (9, "Числовые последовательности и прогрессии", "Арифметическая прогрессия", "Находить n-й член арифметической прогрессии"),
    (9, "Вероятность и статистика", "Испытания Бернулли", "Применять формулу Бернулли"),
    (10, "Уравнения и неравенства", "Тригонометрические уравнения", "Решать простейшие тригонометрические уравнения"),
    (10, "Геометрия", "Прямые и плоскости в пространстве", "Применять признаки взаимного расположения прямых и плоскостей"),
    (11, "Начала математического анализа", "Производная функции", "Находить производную функции"),
    (11, "Вероятность и статистика", "Математическое ожидание", "Находить математическое ожидание по распределению"),
])
def test_mathematics_7_9_catalog_metadata_resolves_locally(
        grade_number, topic_name, subtopic_name, skill_name):
    subject = item("Математика")
    grade = item(str(grade_number), grade_number=grade_number)
    topic = item(topic_name, subject_id=subject.id, grade_id=grade.id)
    subtopic = item(subtopic_name, topic_id=topic.id)
    skills = (() if skill_name is None else
              (item(skill_name, topic_id=topic.id, subtopic_id=subtopic.id),))
    catalog = MetadataCatalogSnapshotV1(
        subjects=(subject,), grades=(grade,), topics=(topic,), subtopics=(subtopic,),
        skills=skills, tag_categories=(), tags=())
    extraction = ExtractionResultV1(
        extracted_text="Задание", structured_statement="Выполнить задание.",
        detected_task_type="calculation", detected_answer_format="number", choices=None,
        extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={
            "title": "Задание", "subject": "Математика", "grade": grade_number,
            "topic": topic_name, "subtopic": subtopic_name,
            "skills": (() if skill_name is None else (skill_name,)),
            "task_type": "calculation", "answer_format": "number", "difficulty": 2,
            "tags": (),
        })
    now = datetime.now(UTC)
    session = ImageSolvingSession(
        session_id=uuid4(), owner_id=uuid4(), input_artifact_id=uuid4(),
        extraction_checkpoint=extraction, lifecycle_status=ImageSolvingStatus.VALIDATED,
        created_at=now, updated_at=now)

    result = resolve_metadata(session, catalog)

    assert (result.subject.id, result.grade.id, result.topic.id, result.subtopic.id) == (
        subject.id, grade.id, topic.id, subtopic.id)
    if skill_name is not None:
        assert result.skills[0].id == skills[0].id


def test_subject_exact_matching_is_normalized_across_capitalization():
    result, expected = resolve(extracted_subject="математика")

    assert result.subject.kind == "existing"
    assert result.subject.id == expected[0].id


def test_provisional_exact_matches_retain_status_through_the_hierarchy():
    result, expected = resolve(extracted_subject="МАТЕМАТИКА", status="provisional")
    subject, grade, topic, subtopic, skill, _ = expected
    assert (result.subject.id, result.grade.id, result.topic.id, result.subtopic.id,
        result.skills[0].id) == (subject.id, grade.id, topic.id, subtopic.id, skill.id)
    assert {result.subject.catalog_status, result.grade.catalog_status,
        result.topic.catalog_status, result.subtopic.catalog_status,
        result.skills[0].catalog_status} == {"provisional"}


def test_subject_absent_from_live_snapshot_does_not_resolve_as_existing():
    # The catalog loader excludes deprecated entities; the resolver must not infer
    # that an absent subject is selectable.
    result, _ = resolve(include_subject=False)

    assert result.subject.kind == "new"
    assert result.subject.proposed_name == "Математика"
    assert result.topic.kind == "new"


def test_ambiguous_subject_leaves_hierarchy_unresolved_without_fake_ids():
    result,_=resolve(duplicate_subject=True)
    assert result.subject.kind=="new" and result.subject.proposed_name=="Математика"
    assert result.topic.kind==result.subtopic.kind==result.skills[0].kind=="new"
    assert (result.topic.proposed_name, result.subtopic.proposed_name,
            result.skills[0].proposed_name) == (
        "Уравнения", "Линейные уравнения", "Решать линейные уравнения")


def test_incompatible_tag_is_an_unresolved_name():
    result,_=resolve(incompatible_tag=True)
    assert result.tags[0].kind=="new" and result.tags[0].name=="Алгебра"


def test_mixed_recommendation_roundtrips_through_persisted_api_shape():
    result,_=resolve(duplicate_subject=True)
    payload=result.model_dump(mode="json")
    restored=deserialize_json_contract(payload, ImageTaskMetadataRecommendationV1)

    assert restored == result
    assert payload["subject"]["proposed_name"] == "Математика"
    assert payload["topic"]["proposed_name"] == "Уравнения"
    assert payload["skills"][0]["proposed_name"] == "Решать линейные уравнения"


def test_old_existing_recommendation_without_catalog_status_still_deserializes():
    result, _ = resolve()
    payload = result.model_dump(mode="json")
    for key in ("subject", "grade", "topic", "subtopic"):
        payload[key].pop("catalog_status", None)
    payload["skills"][0].pop("catalog_status", None)
    restored = deserialize_json_contract(payload, ImageTaskMetadataRecommendationV1)
    assert restored.subject.catalog_status is None


def test_catalog_fingerprint_observes_provisional_creation_and_confirmation():
    active = item("Физика")
    provisional = item("Математика", catalog_status="provisional")
    def snapshot(subjects):
        return MetadataCatalogSnapshotV1(subjects=subjects, grades=(), topics=(), subtopics=(),
            skills=(), tag_categories=(), tags=())
    before = snapshot((active,))
    created = snapshot((active, provisional))
    confirmed = snapshot((active, provisional.model_copy(update={"catalog_status": "active"})))
    assert before.fingerprint != created.fingerprint
    assert created.fingerprint != confirmed.fingerprint


def test_alias_resolution_is_scoped_and_exact_still_wins():
    result, rows = resolve()
    subject, grade, topic, subtopic, skill, _ = rows
    alias_topic = item("Решение линейных уравнений", subject_id=subject.id, grade_id=grade.id)
    aliases=(CatalogAliasV1(kind="topic",normalized_alias="уравнения",target=alias_topic,
        subject_id=subject.id,grade_id=grade.id),)
    # The exact Topic row must win even when the same source wording has an alias.
    catalog = MetadataCatalogSnapshotV1(subjects=(subject,),grades=(grade,),topics=(topic,alias_topic),
        subtopics=(subtopic,),skills=(skill,),tag_categories=(),tags=(),aliases=aliases)
    assert resolve_metadata(_session_for(rows, "Уравнения"), catalog).topic.id == topic.id
    alias_only=catalog.model_copy(update={"topics":(alias_topic,)})
    resolved=resolve_metadata(_session_for(rows, "Уравнения"),alias_only)
    assert resolved.topic.id==alias_topic.id
    assert resolved.topic.label=="Решение линейных уравнений"
    assert resolved.topic.resolution_source=="alias"

    wrong_scope=alias_only.model_copy(update={"aliases":(aliases[0].model_copy(
        update={"grade_id":uuid4()}),)})
    assert resolve_metadata(_session_for(rows,"Уравнения"),wrong_scope).topic.kind=="new"


def _session_for(rows, topic_name):
    subject, grade, _, _, _, _=rows
    extraction=ExtractionResultV1(extracted_text="x",structured_statement="x",
        detected_task_type="calculation",detected_answer_format="number",choices=None,
        extraction_confidence=Decimal(".9"),ocr_issues=(),metadata={"title":"x",
        "subject":subject.name,"grade":grade.grade_number,"topic":topic_name,
        "subtopic":None,"skills":("unknown",),"task_type":"calculation",
        "answer_format":"number","difficulty":2,"tags":()})
    now=datetime.now(UTC)
    return ImageSolvingSession(session_id=uuid4(),owner_id=uuid4(),input_artifact_id=uuid4(),
        extraction_checkpoint=extraction,lifecycle_status=ImageSolvingStatus.VALIDATED,
        created_at=now,updated_at=now)


def test_alias_changes_catalog_fingerprint_deterministically():
    target=item("Решение линейных уравнений")
    base=MetadataCatalogSnapshotV1(subjects=(target,),grades=(),topics=(),subtopics=(),
        skills=(),tag_categories=(),tags=())
    alias=CatalogAliasV1(kind="subject",normalized_alias="линейные уравнения",target=target)
    assert base.fingerprint != base.model_copy(update={"aliases":(alias,)}).fingerprint

from types import SimpleNamespace
from app.application.image_solving_metadata import MetadataRecommendationService, MetadataResolutionError

class _Sessions:
    async def get_state(self, **_):
        return SimpleNamespace(lifecycle_status=ImageSolvingStatus.VALIDATED, validation_checkpoint=object())
class _Repository:
    def __init__(self, *, cached=None, save_error=None):
        self.cached,self.save_error=cached,save_error
        self.saved=[]
    async def get_recommendation(self, _): return self.cached
    async def save_recommendation(self, *args):
        if self.save_error: raise self.save_error
        self.saved.append(args)
        return args[1]
class _Catalog:
    def __init__(self, fingerprint="fingerprint"): self.fingerprint=fingerprint
class _Loader:
    def __init__(self, error=None, catalog=None): self.error,self.catalog=error,catalog or _Catalog()
    async def load(self):
        if self.error: raise self.error
        return self.catalog

@pytest.mark.asyncio
async def test_unchanged_catalog_fingerprint_reuses_cached_recommendation(monkeypatch):
    cached=object(); loader=_Loader()
    repository=_Repository(cached=CachedMetadataRecommendation(cached,"fingerprint"))
    monkeypatch.setattr("app.application.image_solving_metadata.resolve_metadata",
        lambda *_: (_ for _ in ()).throw(AssertionError("resolver must not run")))
    assert await MetadataRecommendationService(_Sessions(),repository,loader).generate(uuid4(),uuid4()) is cached
    assert repository.saved == []

@pytest.mark.asyncio
async def test_changed_catalog_fingerprint_refreshes_and_replaces_cache(monkeypatch):
    stale=object(); refreshed=object()
    repository=_Repository(cached=CachedMetadataRecommendation(stale,"old"))
    monkeypatch.setattr("app.application.image_solving_metadata.resolve_metadata",
        lambda *_: refreshed)
    result=await MetadataRecommendationService(_Sessions(),repository,
        _Loader(catalog=_Catalog("current"))).generate(uuid4(),uuid4())
    assert result is refreshed
    assert repository.saved[0][1:] == (refreshed,"current")


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog_change", ["subject_added", "subject_activated"])
async def test_stale_cache_refresh_resolves_newly_active_subject(monkeypatch, catalog_change):
    stale, _ = resolve(include_subject=False)
    refreshed, expected = resolve(extracted_subject="математика")
    repository = _Repository(cached=CachedMetadataRecommendation(stale, "before"))
    calls = []

    def local_resolver(*_):
        calls.append(catalog_change)
        return refreshed

    monkeypatch.setattr("app.application.image_solving_metadata.resolve_metadata", local_resolver)
    result = await MetadataRecommendationService(
        _Sessions(), repository, _Loader(catalog=_Catalog("after"))).generate(uuid4(), uuid4())

    assert result.subject.kind == "existing"
    assert result.subject.id == expected[0].id
    assert calls == [catalog_change]
    assert repository.saved[0][2] == "after"

@pytest.mark.asyncio
@pytest.mark.parametrize(("failure","stage"),[("catalog","catalog_load"),("resolve","resolve"),("persistence","persistence")])
async def test_local_resolution_failures_are_controlled(monkeypatch,failure,stage):
    loader=_Loader(RuntimeError("catalog")) if failure=="catalog" else _Loader()
    repository=_Repository(save_error=RuntimeError("save")) if failure=="persistence" else _Repository()
    if failure=="resolve": monkeypatch.setattr("app.application.image_solving_metadata.resolve_metadata",lambda *_: (_ for _ in ()).throw(ValueError("resolve")))
    else: monkeypatch.setattr("app.application.image_solving_metadata.resolve_metadata",lambda *_: object())
    with pytest.raises(MetadataResolutionError) as error:
        await MetadataRecommendationService(_Sessions(),repository,loader).generate(uuid4(),uuid4())
    assert error.value.stage==stage

@pytest.mark.parametrize(("grade_number", "topic_name", "subtopic_name", "skill_name"), [
    (10, "Фонетика и орфоэпия", "Акцентологические нормы", "Определять нормативное ударение"),
    (10, "Лексикология и фразеология", "Паронимы", "Выбирать пароним по контексту"),
    (11, "Синтаксис и синтаксические нормы", "Согласование сказуемого с подлежащим", "Выявлять нарушение согласования сказуемого с подлежащим"),
    (11, "Пунктуация", "Сложное предложение с разными видами связи", "Расставлять знаки в сложном предложении с разными видами связи"),
    (11, "Функциональная стилистика и культура речи", "Официально-деловой стиль", "Распознавать официально-деловой стиль"),
])
def test_russian_10_11_catalog_metadata_resolves_locally(grade_number, topic_name, subtopic_name, skill_name):
    subject = item("Русский язык")
    grade = item(str(grade_number), grade_number=grade_number)
    topic = item(topic_name, subject_id=subject.id, grade_id=grade.id)
    subtopic = item(subtopic_name, topic_id=topic.id)
    skill = item(skill_name, topic_id=topic.id, subtopic_id=subtopic.id)
    catalog = MetadataCatalogSnapshotV1(subjects=(subject,), grades=(grade,), topics=(topic,),
        subtopics=(subtopic,), skills=(skill,), tag_categories=(), tags=())
    extraction = ExtractionResultV1(extracted_text="Задание", structured_statement="Выполнить задание.",
        detected_task_type="open_question", detected_answer_format="short_text", choices=None,
        extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={"title": "Задание",
        "subject": "Русский язык", "grade": grade_number, "topic": topic_name,
        "subtopic": subtopic_name, "skills": (skill_name,), "task_type": "open_question",
        "answer_format": "short_text", "difficulty": 2, "tags": ()})
    now = datetime.now(UTC)
    session = ImageSolvingSession(session_id=uuid4(), owner_id=uuid4(), input_artifact_id=uuid4(),
        extraction_checkpoint=extraction, lifecycle_status=ImageSolvingStatus.VALIDATED,
        created_at=now, updated_at=now)
    result = resolve_metadata(session, catalog)
    assert (result.subject.id, result.grade.id, result.topic.id, result.subtopic.id,
            result.skills[0].id) == (subject.id, grade.id, topic.id, subtopic.id, skill.id)
