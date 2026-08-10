"""PostgreSQL UoW/read adapters for atomic Checking intake; never self-commit."""
from __future__ import annotations

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.application.checking import InvalidPersistenceCommand
from app.application.checking_handoff import CheckingHandoff, CheckingHandoffItem
from app.application.checking_intake import SubmissionNotFound, SubmissionNotSubmitted, InvalidCheckingInput
from app.infrastructure.assessment_models import AssessmentItem, AssignmentParticipant, StudentAnswer, StudentSubmission
from app.infrastructure.checking_models import CheckRun
from app.infrastructure.checking_repository import CheckingRepository
from app.infrastructure.models import (AcceptedAnswer, ChoiceScoringPolicy, ExpectedSolution, Rubric,
    TaskErrorLink, TaskSkillLink, TaskVersion)


class SQLAlchemyCheckingIntakeUnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]): self.factory=factory; self.session=None
    async def __aenter__(self): self.session=self.factory(); return self
    async def __aexit__(self,exc_type,exc,tb):
        if exc_type: await self.session.rollback()
        await self.session.close()
    async def commit(self): await self.session.commit()

    async def load_locked_handoff(self, submission_id: UUID) -> CheckingHandoff:
        sub=await self.session.scalar(select(StudentSubmission).where(StudentSubmission.id==submission_id).with_for_update())
        if sub is None: raise SubmissionNotFound("submission not found")
        if sub.status!="submitted" or sub.submitted_at is None: raise SubmissionNotSubmitted("submission is not submitted")
        participant=await self.session.get(AssignmentParticipant,sub.assignment_participant_id)
        rows=(await self.session.execute(select(AssessmentItem,StudentAnswer).outerjoin(StudentAnswer,
            (StudentAnswer.submission_id==sub.id)&(StudentAnswer.assessment_item_id==AssessmentItem.id))
            .where(AssessmentItem.variant_id==participant.assigned_variant_id)
            .order_by(AssessmentItem.position,AssessmentItem.id))).all()
        version_ids=tuple(i.task_version_id for i,_ in rows)
        versions={x.id:x for x in (await self.session.scalars(select(TaskVersion).where(TaskVersion.id.in_(version_ids)))).all()}
        if len(versions)!=len(set(version_ids)): raise InvalidCheckingInput("historical task version is missing")
        return CheckingHandoff(sub.id,sub.submitted_at,tuple(CheckingHandoffItem(i.id,i.task_version_id,i.position,i.points,
            versions[i.task_version_id].answer_format,a.raw_answer if a else None,a.normalized_answer if a else None) for i,a in rows))

    async def load_methodologies(self, version_ids: tuple[UUID,...]):
        query=select(TaskVersion).where(TaskVersion.id.in_(set(version_ids))).options(
            selectinload(TaskVersion.skill_links).selectinload(TaskSkillLink.skill),
            selectinload(TaskVersion.expected_solution),
            selectinload(TaskVersion.rubric).selectinload(Rubric.items),
            selectinload(TaskVersion.accepted_answers).selectinload(AcceptedAnswer.option_links),
            selectinload(TaskVersion.choice_options),
            selectinload(TaskVersion.choice_scoring_policy).selectinload(ChoiceScoringPolicy.option_rules),
            selectinload(TaskVersion.error_links).selectinload(TaskErrorLink.typical_error))
        rows=(await self.session.scalars(query)).all(); result={}
        for v in rows:
            options={x.id:x for x in v.choice_options}
            solution=v.expected_solution
            rubric=v.rubric
            policy=v.choice_scoring_policy
            result[v.id]={"statement":v.statement,"task_type":v.task_type,"answer_format":v.answer_format,
                "skills":[{"id":x.skill_id,"code":x.skill.code,"name":x.skill.name,"weight":x.weight,"is_primary":x.is_primary} for x in v.skill_links],
                "expected_solution":({"id":solution.id,"solution_text":solution.solution_text,"final_answer":solution.final_answer,
                    "solution_steps":list(solution.solution_steps_json)} if solution else None),
                "accepted_answers":[{"id":x.id,"answer_value":x.answer_value,"tolerance":x.tolerance,"unit":x.unit,
                    "normalization_rule":x.normalization_rule,"value_kind":x.value_kind,"canonical_text":x.canonical_text,
                    "canonical_decimal":x.canonical_decimal,"option_ids":[l.choice_option_id for l in x.option_links],
                    "absolute_tolerance":x.absolute_tolerance,"relative_tolerance":x.relative_tolerance,"unit_code":x.unit_code,
                    "normalization_policy_code":x.normalization_policy_code,"normalization_policy_version":x.normalization_policy_version}
                    for x in v.accepted_answers],
                "choice_options":[{"id":x.id,"option_key":x.option_key,"content":x.content,"order_index":x.order_index} for x in v.choice_options],
                "choice_scoring_policy":({"mode":policy.mode,"policy_version":policy.policy_version,"option_rules":[
                    {"option_id":x.choice_option_id,"option_key":options[x.choice_option_id].option_key,"role":x.role,"weight":x.weight}
                    for x in policy.option_rules]} if policy else None),
                "rubric":({"id":rubric.id,"grading_mode":rubric.grading_mode,"max_score":rubric.max_score,"notes":rubric.notes,
                    "items":[{"id":x.id,"criterion":x.criterion,"max_points":x.max_points,"required":x.required,
                        "common_failure":x.common_failure,"order_index":x.order_index} for x in rubric.items]} if rubric else None),
                "typical_errors":[{"id":x.typical_error.id,"skill_id":x.typical_error.skill_id,"code":x.typical_error.code,
                    "severity":x.typical_error.severity,"remediation_hint":x.typical_error.remediation_hint,"detection_hint":x.detection_hint}
                    for x in v.error_links]}
        return result

    async def validate_supersedes(self,run_id,submission_id):
        run=await self.session.get(CheckRun,run_id)
        if run is None or run.submission_id!=submission_id or run.status in {"pending","running"}:
            raise InvalidCheckingInput("superseded run is not same-submission history")

    async def create_run(self,command): return await CheckingRepository(self.session).create_run(command)


class SQLAlchemyCheckingIntakeUnitOfWorkFactory:
    def __init__(self,factory): self.factory=factory
    def __call__(self): return SQLAlchemyCheckingIntakeUnitOfWork(self.factory)
