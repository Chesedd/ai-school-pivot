"""Human-reviewed Image Solving result -> ordinary Content Bank draft."""
from decimal import Decimal
import logging
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.application.content_bank import (AcceptedAnswerInput, ActorContext,
    CreateTaskCommand, CreateTaskOperation, ExpectedSolutionInput,
    SaveMethodologyCommand, SkillLinkInput, VersionContentInput)
from app.application.image_solving_api import ImageSolvingApiError
from app.application.image_solving_contracts import (ExtractionResultV1,
    SolverResultV1, ValidationResultV1)
from app.infrastructure.image_solving_models import (ImageSolvingCheckpointRow,
    ImageSolvingMetadataRecommendationRow, ImageSolvingSessionRow)
from app.infrastructure.image_solving_repository import deserialize_json_contract
from app.infrastructure.models import AuditLog, TaskVersion
from app.infrastructure.models import (CurriculumCatalogAlias, Skill, Subject, Subtopic, Topic,
    normalize_catalog_name)
from app.infrastructure.repository import SQLAlchemyContentBankRepository
from app.presentation.image_solving_schemas import (PromoteImageSolvingRequest,
    PromoteImageSolvingResponse)


logger = logging.getLogger(__name__)


def validate_persisted_checkpoints(checkpoints):
    """Validate JSONB values and their provenance exactly as the aggregate does."""
    restored = {}
    for name, contract in (("extraction", ExtractionResultV1),
            ("solver", SolverResultV1), ("validation", ValidationResultV1)):
        value = deserialize_json_contract(checkpoints[name].payload, contract)
        if value.fingerprint != checkpoints[name].fingerprint:
            raise ValueError("checkpoint_fingerprint_mismatch")
        restored[name] = value
    return restored


class PromoteImageSolvingService:
    def __init__(self, db):
        self.db = db
        self.content = SQLAlchemyContentBankRepository(db)

    async def promote(self, session_id: UUID, actor_id: UUID,
                      reviewed: PromoteImageSolvingRequest) -> PromoteImageSolvingResponse:
        session = await self.db.scalar(select(ImageSolvingSessionRow).where(
            ImageSolvingSessionRow.id == session_id,
            ImageSolvingSessionRow.owner_id == actor_id).with_for_update())
        if session is None:
            self._reject("image_solving_session_not_found", 404)
        existing = await self._existing(session_id)
        if existing:
            await self.db.commit()
            return existing
        if not reviewed.review_confirmed:
            self._reject("image_solving_review_confirmation_required")
        if session.status != "validated":
            self._reject("image_solving_source_incomplete")
        rows = (await self.db.scalars(select(ImageSolvingCheckpointRow).where(
            ImageSolvingCheckpointRow.session_id == session_id))).all()
        checkpoints = {row.stage: row for row in rows}
        if set(checkpoints) != {"extraction", "solver", "validation"}:
            self._reject("image_solving_source_incomplete")
        try:
            validation = validate_persisted_checkpoints(checkpoints)["validation"]
        except Exception as exc:
            self._log_failure(session_id, "checkpoint_validation", exc)
            self._reject("image_solving_source_incomplete")
        if reviewed.answer_format != "long_text" and not reviewed.final_answer:
            self._reject("image_solving_final_answer_required", 422)

        weights = self._weights(len(reviewed.skill_ids))
        links = tuple(SkillLinkInput(skill_id, weights[index], index == 0)
                      for index, skill_id in enumerate(reviewed.skill_ids))
        command = CreateTaskCommand(reviewed.subject_id, reviewed.grade_id,
            reviewed.topic_id, reviewed.subtopic_id,
            VersionContentInput(reviewed.title, reviewed.statement,
                reviewed.task_type, reviewed.answer_format, reviewed.difficulty,
                "image_solving", links),tag_ids=reviewed.tag_ids)
        stage = "task_create"
        try:
            created = await CreateTaskOperation(self.content).create(command,
                ActorContext(actor_id))
            stage = "methodology_save"
            answer = reviewed.final_answer or "Ответ оценивается экспертом"
            await self.content.replace_methodology(SaveMethodologyCommand(
                created.initial_version.id,
                ExpectedSolutionInput(reviewed.solution, reviewed.final_answer, ()),
                None, (AcceptedAnswerInput(answer, None, None, None),), (), (), (), None))
        except ImageSolvingApiError:
            raise
        except Exception as exc:
            self._log_failure(session_id, stage, exc)
            self._reject("image_solving_review_invalid", 422)
        details = {"image_solving_session_id": str(session_id),
            "input_artifact_id": str(session.input_artifact_id),
            "validation_status": validation.validation_status,
            "validation_findings": list(validation.findings),
            "human_review_confirmed": True}
        details["catalog_alias_conflicts"] = await self._learn_aliases(
            session_id, reviewed, checkpoints["extraction"].payload, actor_id)
        if reviewed.review_note:
            details["review_note"] = reviewed.review_note
        try:
            await self.db.execute(update(AuditLog).where(AuditLog.task_id == created.id,
                AuditLog.action == "task_created").values(details=details))
        except Exception as exc:
            self._log_failure(session_id, "audit_update", exc)
            self._reject("image_solving_review_invalid", 422)
        try:
            await self.db.commit()
        except Exception as exc:
            self._log_failure(session_id, "commit", exc)
            self._reject("image_solving_review_invalid", 422)
        return PromoteImageSolvingResponse(session_id=session_id, task_id=created.id,
            task_version_id=created.initial_version.id, status="draft",
            already_existing=False)

    async def _learn_aliases(self, session_id, reviewed, extraction_payload, actor_id):
        """Persist only final candidate-derived confirmations in the promotion transaction."""
        recommendation=await self.db.scalar(select(ImageSolvingMetadataRecommendationRow).where(
            ImageSolvingMetadataRecommendationRow.session_id==session_id))
        payload=recommendation.payload if recommendation else {}
        offered=set()
        for kind in ("subject","topic","subtopic"):
            value=payload.get(kind) or {}
            for candidate in value.get("candidates",[]): offered.add((kind,value.get("proposed_name"),candidate.get("id")))
        for value in payload.get("skills",[]):
            for candidate in value.get("candidates",[]): offered.add(("skill",value.get("proposed_name"),candidate.get("id")))
        metadata=extraction_payload.get("metadata") or {}
        chosen={"subject":reviewed.subject_id,"topic":reviewed.topic_id,
            "subtopic":reviewed.subtopic_id,"skill":set(reviewed.skill_ids)}
        expected={"subject":{metadata.get("subject")},"topic":{metadata.get("topic")},
            "subtopic":{metadata.get("subtopic")},"skill":set(metadata.get("skills") or ())}
        models={"subject":Subject,"topic":Topic,"subtopic":Subtopic,"skill":Skill}
        target_columns={"subject":"subject_target_id","topic":"topic_target_id",
            "subtopic":"subtopic_target_id","skill":"skill_target_id"}
        conflicts=[]
        for item in reviewed.alias_confirmations:
            if (item.kind,item.recognized_label,str(item.target_id)) not in offered: continue
            if item.recognized_label not in expected[item.kind]: continue
            if (item.kind=="skill" and item.target_id not in chosen[item.kind]) or (item.kind!="skill" and item.target_id!=chosen[item.kind]): continue
            target=await self.db.get(models[item.kind],item.target_id)
            if target is None or target.status not in ("active","provisional"): continue
            valid=(item.kind=="subject" or
                item.kind=="topic" and target.subject_id==reviewed.subject_id and target.grade_id==reviewed.grade_id or
                item.kind=="subtopic" and target.topic_id==reviewed.topic_id or
                item.kind=="skill" and target.subtopic_id==reviewed.subtopic_id)
            if not valid: continue
            normalized=normalize_catalog_name(item.recognized_label)
            values={"kind":item.kind,"alias_name":item.recognized_label,
                "normalized_alias":normalized,target_columns[item.kind]:item.target_id,"created_by":actor_id}
            if item.kind=="topic": values.update(subject_id=reviewed.subject_id,grade_id=reviewed.grade_id)
            elif item.kind=="subtopic": values["topic_id"]=reviewed.topic_id
            elif item.kind=="skill": values["subtopic_id"]=reviewed.subtopic_id
            elements={"subject":["normalized_alias"],"topic":["normalized_alias","subject_id","grade_id"],
                "subtopic":["normalized_alias","topic_id"],"skill":["normalized_alias","subtopic_id"]}[item.kind]
            result=await self.db.execute(insert(CurriculumCatalogAlias).values(**values)
                .on_conflict_do_nothing(index_elements=elements,
                    index_where=CurriculumCatalogAlias.kind==item.kind).returning(CurriculumCatalogAlias.id))
            if result.scalar_one_or_none() is None:
                conditions=[CurriculumCatalogAlias.kind==item.kind,
                    CurriculumCatalogAlias.normalized_alias==normalized]
                for field in ("subject_id","grade_id","topic_id","subtopic_id"):
                    if field in values: conditions.append(getattr(CurriculumCatalogAlias,field)==values[field])
                existing=await self.db.scalar(select(CurriculumCatalogAlias).where(*conditions))
                if existing and getattr(existing,target_columns[item.kind]) != item.target_id:
                    conflicts.append({"kind":item.kind,"normalized_alias":normalized})
        return conflicts

    @staticmethod
    def _weights(count: int) -> tuple[Decimal, ...]:
        base = (Decimal("1.0000") / count).quantize(Decimal("0.0001"))
        values = [base] * count
        values[-1] = Decimal("1.0000") - sum(values[:-1], Decimal(0))
        return tuple(values)

    async def _existing(self, session_id: UUID):
        audit = await self.db.scalar(select(AuditLog).where(
            AuditLog.action == "task_created",
            AuditLog.details["image_solving_session_id"].astext == str(session_id)))
        if audit is None:
            return None
        version = await self.db.get(TaskVersion, audit.task_version_id)
        return PromoteImageSolvingResponse(session_id=session_id,
            task_id=audit.task_id, task_version_id=version.id, status="draft",
            already_existing=True)

    @staticmethod
    def _reject(code: str, status: int = 409):
        raise ImageSolvingApiError(code, status)

    @staticmethod
    def _log_failure(session_id: UUID, stage: str, exc: Exception) -> None:
        code = getattr(exc, "code", None)
        constraint = getattr(getattr(exc, "orig", None), "diag", None)
        constraint_name = getattr(constraint, "constraint_name", None)
        logger.error(
            "image solving promotion failed session_id=%s "
            "operation=image_solving_promotion stage=%s exception=%s "
            "application_code=%s constraint=%s",
            session_id, stage, type(exc).__name__, code, constraint_name)
