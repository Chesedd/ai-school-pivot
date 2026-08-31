"""Deterministic resolution of extraction metadata against Content Bank."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from app.application.content_bank import ANSWER_FORMATS, TASK_TYPES
from app.application.image_solving import ImageSolvingError
from app.application.image_solving_contracts import Confidence, ImageSolvingSession, ImageSolvingStatus

TAG_LIMIT = 8


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ExistingCatalogSelectionV1(_Strict):
    kind: Literal["existing"]
    id: UUID
    confidence: Confidence
    reason: StrictStr = Field(min_length=1, max_length=500)


class NewCatalogSelectionV1(_Strict):
    kind: Literal["new"]
    proposed_name: StrictStr = Field(min_length=1, max_length=200)
    parent_id: UUID | None = None
    confidence: Confidence
    reason: StrictStr = Field(min_length=1, max_length=500)


CatalogSelectionV1 = Annotated[
    ExistingCatalogSelectionV1 | NewCatalogSelectionV1, Field(discriminator="kind")]


class GradeSelectionV1(_Strict):
    """Grades deliberately have no `new` variant."""
    kind: Literal["existing"]
    id: UUID
    confidence: Confidence
    reason: StrictStr = Field(min_length=1, max_length=500)
    requires_confirmation: bool = False
    alternatives: tuple[ExistingCatalogSelectionV1, ...] = Field(default=(), max_length=3)


class EnumRecommendationV1(_Strict):
    value: StrictStr
    confidence: Confidence
    reason: StrictStr = Field(min_length=1, max_length=500)


class DifficultyRecommendationV1(_Strict):
    value: StrictInt = Field(ge=1, le=100)
    confidence: Confidence
    reason: StrictStr = Field(min_length=1, max_length=500)


class ExistingTagRecommendationV1(_Strict):
    kind: Literal["existing"]
    id: UUID
    confidence: Confidence
    reason: StrictStr = Field(min_length=1, max_length=500)


class NewTagRecommendationV1(_Strict):
    kind: Literal["new"]
    name: StrictStr = Field(min_length=1, max_length=80)
    category_code: StrictStr = Field(min_length=1, max_length=64)
    subject_scope: UUID | None = None
    confidence: Confidence
    reason: StrictStr = Field(min_length=1, max_length=500)


TagRecommendationV1 = Annotated[
    ExistingTagRecommendationV1 | NewTagRecommendationV1, Field(discriminator="kind")]


class ImageTaskMetadataRecommendationV1(_Strict):
    title_suggestion: StrictStr = Field(min_length=1, max_length=500)
    task_type: EnumRecommendationV1
    answer_format: EnumRecommendationV1
    difficulty: DifficultyRecommendationV1
    subject: CatalogSelectionV1
    grade: GradeSelectionV1 | NewCatalogSelectionV1
    topic: CatalogSelectionV1
    subtopic: CatalogSelectionV1 | None = None
    skills: tuple[CatalogSelectionV1, ...] = Field(min_length=1, max_length=5)
    tags: tuple[TagRecommendationV1, ...] = Field(default=(), max_length=TAG_LIMIT)
    folder: ExistingCatalogSelectionV1 | None = None

    @model_validator(mode="after")
    def enums_and_duplicates(self):
        if self.task_type.value not in TASK_TYPES:
            raise ValueError("unknown_task_type")
        if self.answer_format.value not in ANSWER_FORMATS:
            raise ValueError("unknown_answer_format")
        ids = [x.id for x in self.tags if x.kind == "existing"]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_tags")
        return self


class CatalogItemV1(_Strict):
    id: UUID
    name: StrictStr
    parent_id: UUID | None = None
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None
    grade_number: StrictInt | None = None


class TagCandidateV1(_Strict):
    id: UUID
    name: StrictStr
    category_code: StrictStr
    subject_id: UUID | None = None


class MetadataCatalogSnapshotV1(_Strict):
    subjects: tuple[CatalogItemV1, ...]
    grades: tuple[CatalogItemV1, ...]
    topics: tuple[CatalogItemV1, ...]
    subtopics: tuple[CatalogItemV1, ...]
    skills: tuple[CatalogItemV1, ...]
    folders: tuple[CatalogItemV1, ...] = ()
    tag_categories: tuple[StrictStr, ...]
    tags: tuple[TagCandidateV1, ...]

    @property
    def fingerprint(self) -> str:
        raw=json.dumps(self.model_dump(mode="json"),ensure_ascii=False,sort_keys=True,separators=(",",":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class MetadataRecommendationRepository(Protocol):
    async def get_recommendation(self, session_id: UUID): ...
    async def save_recommendation(self, session_id: UUID, value: ImageTaskMetadataRecommendationV1,
                                  catalog_fingerprint: str): ...


def _normalized(name: str) -> str:
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", name)
        .casefold().replace("ё", "е")).strip()


def _unique_match(name, rows):
    matches = [row for row in rows if _normalized(row.name) == _normalized(name)]
    return matches[0] if len(matches) == 1 else None


def resolve_metadata(session: ImageSolvingSession,
                     catalog: MetadataCatalogSnapshotV1) -> ImageTaskMetadataRecommendationV1:
    """Resolve only unique canonical textual matches inside the chosen hierarchy."""
    semantic = session.extraction_checkpoint.metadata
    one, none = Decimal("1"), Decimal("0")
    def selection(name, row, parent_id=None):
        if row is not None:
            return ExistingCatalogSelectionV1(kind="existing", id=row.id, confidence=one,
                reason="Точное совпадение с текущим каталогом.")
        return NewCatalogSelectionV1(kind="new", proposed_name=name, parent_id=parent_id,
            confidence=none, reason="Безопасное совпадение в текущем каталоге не найдено.")

    subject = _unique_match(semantic.subject, catalog.subjects)
    grades = [row for row in catalog.grades if row.grade_number == semantic.grade]
    grade = grades[0] if len(grades) == 1 else None
    topics = [row for row in catalog.topics if subject and grade and
        row.subject_id == subject.id and row.grade_id == grade.id]
    topic = _unique_match(semantic.topic, topics)
    subs = [row for row in catalog.subtopics if topic and row.topic_id == topic.id]
    subtopic = _unique_match(semantic.subtopic, subs) if semantic.subtopic else None
    skill_rows = [row for row in catalog.skills if
        (subtopic and row.subtopic_id == subtopic.id) or
        (not semantic.subtopic and topic and row.topic_id == topic.id)]
    skills = tuple(selection(name, _unique_match(name, skill_rows),
        subtopic.id if subtopic else None) for name in semantic.skills)
    tags = []
    for name in semantic.tags:
        compatible = [row for row in catalog.tags if subject and row.subject_id in (None, subject.id)]
        row = _unique_match(name, compatible)
        tags.append(ExistingTagRecommendationV1(kind="existing", id=row.id, confidence=one,
            reason="Точное совпадение с активным совместимым тегом.") if row else
            NewTagRecommendationV1(kind="new", name=name, category_code="unresolved",
                subject_scope=subject.id if subject else None, confidence=none,
                reason="Безопасное совместимое совпадение с активным тегом не найдено."))
    grade_selection = (GradeSelectionV1(kind="existing", id=grade.id, confidence=one,
        reason="Класс точно совпал по номеру.") if grade else
        NewCatalogSelectionV1(kind="new", proposed_name=str(semantic.grade), confidence=none,
            reason="Класс с таким номером однозначно не найден."))
    return ImageTaskMetadataRecommendationV1(title_suggestion=semantic.title,
        task_type=EnumRecommendationV1(value=semantic.task_type, confidence=one,
            reason="Предложено при анализе изображения."),
        answer_format=EnumRecommendationV1(value=semantic.answer_format, confidence=one,
            reason="Предложено при анализе изображения."),
        difficulty=DifficultyRecommendationV1(value=semantic.difficulty, confidence=one,
            reason="Предложено при анализе изображения."),
        subject=selection(semantic.subject, subject), grade=grade_selection,
        topic=selection(semantic.topic, topic, subject.id if subject else None),
        subtopic=(selection(semantic.subtopic, subtopic, topic.id if topic else None)
            if semantic.subtopic else None), skills=skills, tags=tuple(tags), folder=None)


class MetadataRecommendationService:
    def __init__(self, sessions, repository: MetadataRecommendationRepository, catalog_loader):
        self.sessions,self.repository,self.catalog_loader=sessions,repository,catalog_loader

    async def get(self, session_id: UUID, owner_id: UUID):
        session=await self.sessions.get_state(session_id=session_id,owner_id=owner_id)
        if session.lifecycle_status is not ImageSolvingStatus.VALIDATED or not session.validation_checkpoint:
            raise ImageSolvingError("recommendation_session_incomplete")
        return await self.repository.get_recommendation(session_id)

    async def generate(self, session_id: UUID, owner_id: UUID):
        session=await self.sessions.get_state(session_id=session_id,owner_id=owner_id)
        if session.lifecycle_status is not ImageSolvingStatus.VALIDATED or not session.validation_checkpoint:
            raise ImageSolvingError("recommendation_session_incomplete")
        cached=await self.repository.get_recommendation(session_id)
        if cached is not None: return cached
        catalog=await self.catalog_loader.load()
        result=resolve_metadata(session,catalog)
        return await self.repository.save_recommendation(session_id,result,catalog.fingerprint)
