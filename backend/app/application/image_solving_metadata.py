"""Post-solve Content Bank classification, isolated from extraction and solving."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from decimal import Decimal
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from app.application.content_bank import TASK_TYPES
from app.application.image_solving import ImageSolvingError
from app.application.image_solving_contracts import Confidence, ImageSolvingSession, ImageSolvingStatus

ANSWER_FORMATS = frozenset({"single_choice", "multiple_choice", "short_text", "number", "expression", "long_text"})
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
    grade: GradeSelectionV1
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


class MetadataRecommendationProvider(Protocol):
    async def recommend(self, session: ImageSolvingSession, catalog: MetadataCatalogSnapshotV1) -> ImageTaskMetadataRecommendationV1: ...


class MetadataRecommendationRepository(Protocol):
    async def get_recommendation(self, session_id: UUID): ...
    async def save_recommendation(self, session_id: UUID, value: ImageTaskMetadataRecommendationV1,
                                  catalog_fingerprint: str, provider: object): ...


def validate_recommendation(value: ImageTaskMetadataRecommendationV1,
                            catalog: MetadataCatalogSnapshotV1) -> ImageTaskMetadataRecommendationV1:
    """Fail closed on invented IDs, invalid hierarchy, tags, and near duplicates."""
    groups={"subject":catalog.subjects,"grade":catalog.grades,"topic":catalog.topics,
            "subtopic":catalog.subtopics,"skill":catalog.skills,"folder":catalog.folders}
    def existing(selection, group):
        if selection is None or selection.kind != "existing": return None
        row=next((x for x in groups[group] if x.id==selection.id),None)
        if row is None: raise ValueError(f"unknown_{group}_id")
        return row
    subject=existing(value.subject,"subject"); grade=existing(value.grade,"grade")
    topic=existing(value.topic,"topic"); subtopic=existing(value.subtopic,"subtopic")
    if topic and subject and topic.subject_id != subject.id: raise ValueError("topic_subject_mismatch")
    if topic and grade and topic.grade_id != grade.id: raise ValueError("topic_grade_mismatch")
    if subtopic and topic and subtopic.topic_id != topic.id: raise ValueError("subtopic_topic_mismatch")
    for skill in value.skills:
        row=existing(skill,"skill")
        if row and subtopic and row.subtopic_id != subtopic.id: raise ValueError("skill_subtopic_mismatch")
        if row and not subtopic and topic and row.topic_id not in (None,topic.id): raise ValueError("skill_topic_mismatch")
    existing(value.folder,"folder")
    categories=set(catalog.tag_categories); active={x.id:x for x in catalog.tags}
    def normalized(name:str)->str:
        return re.sub(r"[^\w]+"," ",unicodedata.normalize("NFKC",name).casefold().replace("ё","е")).strip()
    # Existing-first protection is deterministic. Provider-proposed tag spelling
    # variants are converted to the real active tag rather than shown as "new".
    collapsed=[]
    for tag in value.tags:
        if tag.kind=="new":
            candidate=max(catalog.tags,key=lambda x:SequenceMatcher(None,normalized(tag.name),normalized(x.name)).ratio(),default=None)
            if candidate and SequenceMatcher(None,normalized(tag.name),normalized(candidate.name)).ratio()>=.82:
                if subject and candidate.subject_id not in (None,subject.id): raise ValueError("tag_subject_mismatch")
                collapsed.append(ExistingTagRecommendationV1(kind="existing",id=candidate.id,
                    confidence=tag.confidence,reason=f"Найден близкий существующий тег: {candidate.name}."))
                continue
        collapsed.append(tag)
    # Preserve objects while removing duplicates introduced by collapsing.
    unique=[];seen=set()
    for tag in collapsed:
        key=(tag.kind,getattr(tag,"id",None),getattr(tag,"name",None))
        if key not in seen:seen.add(key);unique.append(tag)
    value=value.model_copy(update={"tags":tuple(unique)})
    for tag in value.tags:
        if tag.kind=="existing":
            row=active.get(tag.id)
            if row is None: raise ValueError("unknown_or_inactive_tag_id")
            if subject and row.subject_id not in (None,subject.id): raise ValueError("tag_subject_mismatch")
        else:
            if tag.category_code not in categories: raise ValueError("unknown_tag_category")
            if subject and tag.subject_scope not in (None,subject.id): raise ValueError("tag_subject_mismatch")
    # Do not allow an obvious taxonomy duplicate to escape as a NEW proposal.
    for selection,rows in ((value.subject,catalog.subjects),(value.topic,catalog.topics),
                           (value.subtopic,catalog.subtopics),*[(x,catalog.skills) for x in value.skills]):
        if selection is not None and selection.kind=="new" and any(
                normalized(selection.proposed_name)==normalized(row.name) for row in rows):
            raise ValueError("new_catalog_duplicate")
    return value


class MetadataRecommendationService:
    def __init__(self, sessions, repository: MetadataRecommendationRepository, catalog_loader, provider):
        self.sessions,self.repository,self.catalog_loader,self.provider=sessions,repository,catalog_loader,provider

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
        result=validate_recommendation(await self.provider.recommend(session,catalog),catalog)
        return await self.repository.save_recommendation(session_id,result,catalog.fingerprint,self.provider)
