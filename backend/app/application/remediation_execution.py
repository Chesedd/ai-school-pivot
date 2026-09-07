"""Student execution of assigned remediation plans, without Checking handoff."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.remediation import RemediationError
from app.application.answer_validation import normalize_answer
from app.infrastructure.assessment_models import StudentAnswer, StudentSubmission
from app.infrastructure.models import ChoiceOption, TaskVersion
from app.infrastructure.remediation_models import RemediationPlan, RemediationPlanItem
from app.application.checking_intake import CheckingIntakeRequest, CheckingIntakeService
from app.application.checking_routing import ROUTING_CONTRACT_VERSION
from app.infrastructure.checking_intake_repository import SQLAlchemyCheckingIntakeUnitOfWorkFactory


class RemediationExecutionService:
    """Own the one-attempt remediation execution transaction boundary."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]):
        self.factory = factory

    async def _plan(self, session, plan_id: UUID, student_id: UUID, *, lock: bool = False):
        query = select(RemediationPlan).where(
            RemediationPlan.id == plan_id,
            RemediationPlan.student_id == student_id,
            RemediationPlan.status.in_(("assigned", "cancelled")),
        )
        if lock:
            query = query.with_for_update()
        plan = await session.scalar(query)
        if plan is None:
            raise RemediationError("remediation_not_found", 404)
        return plan

    @staticmethod
    def _mutable(plan, now) -> None:
        if plan.status == "cancelled":
            raise RemediationError("remediation_cancelled", 409)
        if plan.status != "assigned":
            raise RemediationError("remediation_not_assigned", 409)
        if plan.due_at is not None and now > plan.due_at:
            raise RemediationError("remediation_expired", 409)

    async def _submission(self, session, plan_id: UUID, *, lock: bool = False):
        query = select(StudentSubmission).where(
            StudentSubmission.remediation_plan_id == plan_id,
            StudentSubmission.attempt_no == 1,
        )
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def _item(self, session, plan_id: UUID, item_id: UUID):
        row = (await session.execute(
            select(RemediationPlanItem, TaskVersion)
            .join(TaskVersion, TaskVersion.id == RemediationPlanItem.task_version_id)
            .where(RemediationPlanItem.id == item_id,
                   RemediationPlanItem.remediation_plan_id == plan_id)
        )).one_or_none()
        if row is None:
            raise RemediationError("remediation_item_not_found", 404)
        return row

    async def _materialize(self, session, plan, submission=None):
        if submission is None:
            submission = await self._submission(session, plan.id)
        rows = (await session.execute(
            select(RemediationPlanItem, TaskVersion)
            .join(TaskVersion, TaskVersion.id == RemediationPlanItem.task_version_id)
            .where(RemediationPlanItem.remediation_plan_id == plan.id)
            .order_by(RemediationPlanItem.position, RemediationPlanItem.id)
        )).all()
        answers = {}
        if submission is not None:
            answers = {a.remediation_plan_item_id: a for a in await session.scalars(
                select(StudentAnswer).where(StudentAnswer.submission_id == submission.id))}
        options_by_version = {}
        version_ids = [version.id for _, version in rows]
        if version_ids:
            options = await session.scalars(select(ChoiceOption).where(
                ChoiceOption.task_version_id.in_(version_ids)).order_by(
                    ChoiceOption.task_version_id, ChoiceOption.order_index, ChoiceOption.id))
            for option in options:
                options_by_version.setdefault(option.task_version_id, []).append(
                    {"option_id": option.option_key, "content": option.content})
        if submission is not None and submission.status == "submitted":
            execution_status = "submitted"
        elif plan.status == "cancelled":
            execution_status = "cancelled"
        else:
            now = await session.scalar(select(func.clock_timestamp()))
            if plan.due_at is not None and now > plan.due_at:
                execution_status = "expired"
            else:
                execution_status = "in_progress" if submission is not None else "not_started"
        return {
            "remediation_id": plan.id, "title": plan.title, "instructions": plan.instructions,
            "plan_status": plan.status, "due_at": plan.due_at, "execution_status": execution_status,
            "submission_id": submission.id if submission else None,
            "submission_status": submission.status if submission else None,
            "attempt_no": submission.attempt_no if submission else None,
            "items": [{
                "remediation_plan_item_id": item.id, "position": item.position,
                "task_version_id": version.id, "title": version.title,
                "statement": version.statement, "task_type": version.task_type,
                "answer_format": version.answer_format, "difficulty": version.difficulty,
                "choice_options": options_by_version.get(version.id, []),
                "current_raw_answer": answers[item.id].raw_answer if item.id in answers else None,
            } for item, version in rows],
        }

    async def get_execution(self, plan_id: UUID, student_id: UUID):
        async with self.factory() as session:
            plan = await self._plan(session, plan_id, student_id)
            return await self._materialize(session, plan)

    async def start(self, plan_id: UUID, student_id: UUID):
        async with self.factory() as session, session.begin():
            plan = await self._plan(session, plan_id, student_id, lock=True)
            now = await session.scalar(select(func.clock_timestamp()))
            self._mutable(plan, now)
            submission = await self._submission(session, plan.id, lock=True)
            if submission is not None:
                if submission.status == "submitted":
                    raise RemediationError("remediation_attempts_exhausted", 409)
                return await self._materialize(session, plan, submission), 200
            expected = await session.scalar(select(func.count()).select_from(RemediationPlanItem).where(
                RemediationPlanItem.remediation_plan_id == plan.id))
            available = await session.scalar(select(func.count()).select_from(RemediationPlanItem).join(
                TaskVersion, TaskVersion.id == RemediationPlanItem.task_version_id).where(
                    RemediationPlanItem.remediation_plan_id == plan.id))
            if expected == 0 or available != expected:
                raise RemediationError("remediation_execution_unavailable", 409)
            submission = StudentSubmission(assignment_participant_id=None,
                remediation_plan_id=plan.id, attempt_no=1, status="draft", started_at=now)
            session.add(submission)
            await session.flush()
            return await self._materialize(session, plan, submission), 201

    async def save_answer(self, plan_id, item_id, student_id, raw):
        if raw is None:
            await self.delete_answer(plan_id, item_id, student_id)
            return None, 204
        async with self.factory() as session, session.begin():
            plan = await self._plan(session, plan_id, student_id, lock=True)
            now = await session.scalar(select(func.clock_timestamp()))
            self._mutable(plan, now)
            submission = await self._submission(session, plan.id, lock=True)
            if submission is None:
                raise RemediationError("remediation_submission_not_found", 404)
            if submission.status != "draft":
                raise RemediationError("remediation_submission_immutable", 409)
            item, version = await self._item(session, plan.id, item_id)
            normalized = normalize_answer(version.answer_format, raw)
            answer = await session.scalar(select(StudentAnswer).where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswer.remediation_plan_item_id == item.id).with_for_update())
            created = answer is None
            if created:
                answer = StudentAnswer(submission_id=submission.id, assessment_item_id=None,
                    remediation_plan_item_id=item.id, raw_answer=raw,
                    normalized_answer=normalized, created_at=now, updated_at=now)
                session.add(answer)
            else:
                answer.raw_answer = raw
                answer.normalized_answer = normalized
                answer.updated_at = now
            await session.flush()
            return {"remediation_plan_item_id": item.id, "raw_answer": answer.raw_answer,
                "normalized_answer": answer.normalized_answer,
                "created_at": answer.created_at, "updated_at": answer.updated_at}, 201 if created else 200

    async def delete_answer(self, plan_id, item_id, student_id):
        async with self.factory() as session, session.begin():
            plan = await self._plan(session, plan_id, student_id, lock=True)
            now = await session.scalar(select(func.clock_timestamp()))
            self._mutable(plan, now)
            submission = await self._submission(session, plan.id, lock=True)
            if submission is None:
                raise RemediationError("remediation_submission_not_found", 404)
            if submission.status != "draft":
                raise RemediationError("remediation_submission_immutable", 409)
            await self._item(session, plan.id, item_id)
            await session.execute(delete(StudentAnswer).where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswer.remediation_plan_item_id == item_id))

    async def submit(self, plan_id, student_id):
        async with self.factory() as session, session.begin():
            plan = await self._plan(session, plan_id, student_id, lock=True)
            now = await session.scalar(select(func.clock_timestamp()))
            self._mutable(plan, now)
            submission = await self._submission(session, plan.id, lock=True)
            if submission is None:
                raise RemediationError("remediation_submission_not_found", 404)
            if submission.status == "submitted":
                result=await self._materialize(session, plan, submission)
                submission_id=submission.id
            else:
                # Assessment permits partial submissions; only persisted item ownership is checked.
                answer_targets = list(await session.scalars(select(
                    StudentAnswer.remediation_plan_item_id).where(
                        StudentAnswer.submission_id == submission.id).with_for_update()))
                valid_targets = set(await session.scalars(select(RemediationPlanItem.id).where(
                    RemediationPlanItem.remediation_plan_id == plan.id)))
                if any(target not in valid_targets for target in answer_targets):
                    raise RemediationError("remediation_item_not_found", 404)
                submission.status = "submitted"
                submission.submitted_at = now
                await session.flush()
                result=await self._materialize(session, plan, submission)
                submission_id=submission.id
        # Match the existing post-submit boundary: submission is durable before
        # Checking intake. A bounded intake failure is surfaced without rolling it back.
        intake=CheckingIntakeService(SQLAlchemyCheckingIntakeUnitOfWorkFactory(self.factory))
        await intake.create(CheckingIntakeRequest(submission_id,
            f"remediation:{plan_id}:submission:{submission_id}:initial",
            ROUTING_CONTRACT_VERSION,"checking_checker_set_v1","checking_confidence_v1",
            "checking_prompt_model_policy_v1"))
        return result
