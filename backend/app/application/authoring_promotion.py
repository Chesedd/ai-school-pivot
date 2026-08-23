"""Explicit, human-triggered boundary from authoring to Content Bank drafts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update

from app.application.authoring import AuthoringRequestV1, FrozenCatalogContext
from app.application.authoring_pipeline import PipelineResumeState
from app.application.content_bank import (
    AcceptedAnswerInput, ActorContext, ChoiceOptionInput, ChoiceScoringPolicyInput,
    CreateTaskCommand, CreateTaskOperation, ExpectedSolutionInput, HintInput,
    SaveMethodologyCommand, SkillLinkInput, VersionContentInput,
)
from app.infrastructure.authoring_models import AuthoringSession
from app.infrastructure.models import AuditLog, Grade, Skill, Subject, Subtopic, TaskVersion, Topic
from app.infrastructure.repository import SQLAlchemyContentBankRepository


QUESTIONABLE = frozenset({"answer_mismatch", "manual_review_required"})
ACCEPTABLE = frozenset({"validated", *QUESTIONABLE})


@dataclass(frozen=True)
class AuthoringPromotionResponseV1:
    session_id: UUID
    task_id: UUID
    task_version_id: UUID
    created_at: datetime
    lifecycle_status: str
    already_existing: bool


class PromoteAuthoringArtifactService:
    """Maps a committed artifact in one short DB transaction; it has no provider port."""

    def __init__(self, session):
        self.db = session
        self.content = SQLAlchemyContentBankRepository(session)

    async def accept(self, session_id: UUID, actor_id: UUID, *, acceptance_note: str | None,
                     confirm_questionable: bool) -> AuthoringPromotionResponseV1:
        row = await self.db.scalar(select(AuthoringSession).where(
            AuthoringSession.id == session_id, AuthoringSession.owner_id == actor_id,
        ).with_for_update())
        if row is None:
            from app.application.authoring_api import AuthoringApiError
            raise AuthoringApiError("authoring_session_not_found", 404)

        existing = await self._existing(session_id)
        if existing is not None:
            await self.db.commit()
            return existing

        state = self._artifact(row)
        status = state.validation_result.status
        if row.semantic_status != status:
            self._reject("authoring_artifact_incomplete")
        if status not in ACCEPTABLE or state.solver_result.status != "solvable":
            self._reject("authoring_artifact_not_acceptable")
        if status in QUESTIONABLE and not confirm_questionable:
            self._reject("authoring_acceptance_confirmation_required")

        request = AuthoringRequestV1.model_validate(row.frozen_request)
        try:
            frozen = row.frozen_allowlist
            FrozenCatalogContext(frozen["subject"], frozen["grade"], frozen["topic"],
                frozen.get("subtopic"), tuple(frozen["skills"])).validate_request(request)
        except Exception:
            self._reject("authoring_catalog_context_invalid")
        subject, grade, topic, subtopic, skills = await self._catalog(request)
        weight = Decimal("1") / Decimal(len(skills))
        links = tuple(SkillLinkInput(skill.id, weight, index == 0) for index, skill in enumerate(skills))
        # Keep the exact four-decimal invariant for any non-even division.
        if len(links) > 1:
            rounded = [Decimal("0.0001") * (weight / Decimal("0.0001")).quantize(Decimal("1")) for _ in links]
            rounded[-1] = Decimal("1.0000") - sum(rounded[:-1], Decimal(0))
            links = tuple(SkillLinkInput(link.skill_id, rounded[index], link.is_primary) for index, link in enumerate(links))
        draft = state.generated_draft
        command = CreateTaskCommand(subject.id, grade.id, topic.id, subtopic.id if subtopic else None,
            VersionContentInput(draft.title, draft.statement, draft.task_type, draft.answer_format,
                request.difficulty, None, links))
        created = await CreateTaskOperation(self.content).create(command, ActorContext(actor_id))
        version_id = created.initial_version.id
        option_keys = tuple(draft.expected_answer.split(",")) if draft.choice_options else ()
        answer = AcceptedAnswerInput(draft.expected_answer, None, None, None,
            value_kind="choice_set" if option_keys else "legacy_untyped", option_keys=option_keys)
        methodology = SaveMethodologyCommand(version_id,
            ExpectedSolutionInput(draft.solution, draft.expected_answer, ()), None, (answer,), (),
            tuple(HintInput(index + 1, value) for index, value in enumerate(draft.hints)),
            tuple(ChoiceOptionInput(value.key, value.content, index) for index, value in enumerate(draft.choice_options)),
            ChoiceScoringPolicyInput("all_or_nothing") if draft.choice_options else None)
        await self.content.replace_methodology(methodology)
        details = {"authoring_session_id": str(session_id), "acceptance_policy": status}
        if acceptance_note is not None:
            details["acceptance_note"] = acceptance_note
        await self.db.execute(update(AuditLog).where(AuditLog.task_id == created.id,
            AuditLog.action == "task_created").values(details=details))
        row.status = "confirmed"
        await self.db.commit()
        return AuthoringPromotionResponseV1(session_id, created.id, version_id,
            created.initial_version.created_at, created.initial_version.status, False)

    def _artifact(self, row):
        from app.application.authoring_api import AuthoringApiError
        try:
            state = PipelineResumeState.from_persisted(row.generated_draft, row.generator_attempt_id,
                row.solver_result, row.solver_attempt_id, row.validation_result)
        except Exception:
            raise AuthoringApiError("authoring_artifact_incomplete", 409) from None
        if state.artifact is None:
            raise AuthoringApiError("authoring_artifact_incomplete", 409)
        return state.artifact

    @staticmethod
    def _reject(code):
        from app.application.authoring_api import AuthoringApiError
        raise AuthoringApiError(code, 409)

    async def _existing(self, session_id):
        audit = await self.db.scalar(select(AuditLog).where(AuditLog.action == "task_created",
            AuditLog.details["authoring_session_id"].astext == str(session_id)))
        if audit is None:
            return None
        version = await self.db.get(TaskVersion, audit.task_version_id)
        return AuthoringPromotionResponseV1(session_id, audit.task_id, version.id,
            version.created_at, version.status, True)

    async def _catalog(self, request):
        subject = await self.db.scalar(select(Subject).where(Subject.code == request.subject))
        number = int(request.grade.removeprefix("g"))
        grade = await self.db.scalar(select(Grade).where(Grade.number == number))
        topic = await self.db.scalar(select(Topic).where(Topic.code == request.topic,
            Topic.subject_id == subject.id, Topic.grade_id == grade.id)) if subject and grade else None
        subtopic = await self.db.scalar(select(Subtopic).where(Subtopic.code == request.subtopic,
            Subtopic.topic_id == topic.id)) if request.subtopic and topic else None
        skills = list((await self.db.scalars(select(Skill).where(Skill.code.in_(request.skills),
            Skill.subtopic_id == subtopic.id))).all()) if subtopic else []
        if not subject or not grade or not topic or (request.subtopic and not subtopic) or len(skills) != len(request.skills):
            self._reject("authoring_catalog_context_invalid")
        by_code = {skill.code: skill for skill in skills}
        return subject, grade, topic, subtopic, [by_code[code] for code in request.skills]
