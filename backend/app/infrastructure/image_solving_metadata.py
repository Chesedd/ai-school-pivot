"""Current-catalog loader and AIPRIME Anthropic metadata transport."""
import json
from time import monotonic
from sqlalchemy import select
from pydantic import ValidationError

from app.application.authoring import FailureCode, ProviderFailure, Usage
from app.application.image_solving_metadata import (CatalogItemV1, ImageTaskMetadataRecommendationV1,
    MetadataCatalogSnapshotV1, TagCandidateV1)
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Tag, TagCategory, TaskFolder, Topic
from app.infrastructure.extraction_providers import ExtractionTelemetry, _tool_input, _normalize_tool_strings
from app.infrastructure.authoring_providers import _failure

METADATA_SYSTEM="""Вы классифицируете уже существующее решённое задание для Банка контента.
Не создавайте другое задание и не меняйте математическую истину. Используйте русские названия и причины.
Всегда предпочитайте подходящие существующие значения. existing.id можно брать ТОЛЬКО из переданных кандидатов.
Не придумывайте UUID. Новое значение предлагайте только если семантически подходящего существующего нет.
Класс всегда выбирайте из существующих классов; при сомнении укажите до трёх альтернатив и requires_confirmation.
Выберите минимальный достаточный набор навыков и обычно 2–5 полезных тегов."""

class SqlAlchemyMetadataCatalogLoader:
    def __init__(self,db):self.db=db
    async def load(self):
        subjects=(await self.db.scalars(select(Subject).order_by(Subject.name))).all()
        grades=(await self.db.scalars(select(Grade).order_by(Grade.number))).all()
        topics=(await self.db.scalars(select(Topic).order_by(Topic.name))).all()
        subs=(await self.db.scalars(select(Subtopic).order_by(Subtopic.name))).all()
        skills=(await self.db.scalars(select(Skill).order_by(Skill.name))).all()
        folders=(await self.db.scalars(select(TaskFolder).order_by(TaskFolder.name))).all()
        categories=(await self.db.scalars(select(TagCategory.code).order_by(TagCategory.sort_order))).all()
        tags=(await self.db.scalars(select(Tag).where(Tag.status=="active").order_by(Tag.normalized_name))).all()
        topic_by={x.id:x for x in topics}; sub_by={x.id:x for x in subs}
        return MetadataCatalogSnapshotV1(
            subjects=tuple(CatalogItemV1(id=x.id,name=x.name) for x in subjects),
            grades=tuple(CatalogItemV1(id=x.id,name=x.name) for x in grades),
            topics=tuple(CatalogItemV1(id=x.id,name=x.name,subject_id=x.subject_id,grade_id=x.grade_id) for x in topics),
            subtopics=tuple(CatalogItemV1(id=x.id,name=x.name,topic_id=x.topic_id,
                subject_id=topic_by[x.topic_id].subject_id,grade_id=topic_by[x.topic_id].grade_id) for x in subs),
            skills=tuple(CatalogItemV1(id=x.id,name=x.name,subtopic_id=x.subtopic_id,
                topic_id=sub_by[x.subtopic_id].topic_id) for x in skills),
            folders=tuple(CatalogItemV1(id=x.id,name=x.name,subject_id=x.subject_id,parent_id=x.parent_id) for x in folders),
            tag_categories=tuple(categories),tags=tuple(TagCandidateV1(id=x.id,name=x.name,
                category_code=x.category_code,subject_id=x.subject_id) for x in tags))

class AnthropicMetadataRecommendationProvider:
    provider_id="anthropic"
    def __init__(self,client,route):self.client,self.route=client,route;self.last_telemetry=None
    async def recommend(self,session,catalog):
        started=monotonic()
        supplied={"task":{"statement":session.extraction_checkpoint.structured_statement,
            "extracted_text":session.extraction_checkpoint.extracted_text,
            "solution":session.solver_checkpoint.reasoning_summary,"final_answer":session.solver_checkpoint.final_answer},
            "allowed_task_types":["test","calculation","problem","open_question","essay"],
            "allowed_answer_formats":["single_choice","multiple_choice","short_text","number","expression","long_text"],
            "catalog":catalog.model_dump(mode="json")}
        try:
            response=await self.client.messages.create(model=self.route.model_id,system=METADATA_SYSTEM,max_tokens=4096,
                messages=[{"role":"user","content":json.dumps(supplied,ensure_ascii=False)}],
                tools=[{"name":"record_metadata_recommendations","description":"Записать рекомендации метаданных, не создавая записи БД.",
                    "input_schema":ImageTaskMetadataRecommendationV1.model_json_schema()}],
                tool_choice={"type":"tool","name":"record_metadata_recommendations"})
            payload=_tool_input(response,"record_metadata_recommendations")
            payload=_normalize_tool_strings(payload,("title_suggestion",))
            result=ImageTaskMetadataRecommendationV1.model_validate_json(json.dumps(payload,ensure_ascii=False))
            usage=Usage(response.usage.input_tokens,response.usage.output_tokens,
                getattr(response.usage,"cache_read_input_tokens",0) or 0,getattr(response.usage,"cache_creation_input_tokens",0) or 0)
            self.last_telemetry=ExtractionTelemetry(response.id,usage,None,max(0,int((monotonic()-started)*1000)))
            return result
        except ProviderFailure:raise
        except (ValidationError,AttributeError,ValueError,TypeError):raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        except Exception as exc:raise _failure(exc) from None
