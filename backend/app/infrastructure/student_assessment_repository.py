"""Transactional SQLAlchemy implementation of the student assessment lifecycle."""
from __future__ import annotations

from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.assessments import AssessmentError
from app.application.checking_handoff import CheckingHandoff, CheckingHandoffItem, CheckingHandoffNotReady
from app.application.student_assessments import command_hash, normalize_answer, select_deterministic_variant
from app.infrastructure.assessment_models import (Assignment, AssignmentParticipant, Assessment,
    AssessmentAuditLog, AssessmentIdempotencyKey, AssessmentItem, AssessmentVariant,
    StudentAnswer, StudentSubmission)
from app.infrastructure.assessment_repository import SQLAlchemyContentBankReadPort


class StudentAssessmentService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]): self.factory = factory

    @staticmethod
    def _audit(session, aggregate_type, aggregate_id, event, student_id, details):
        session.add(AssessmentAuditLog(aggregate_type=aggregate_type, aggregate_id=aggregate_id,
            event_type=event, actor_type="student", actor_id=student_id, details=details))

    @staticmethod
    def _window(assignment, now):
        if assignment.status != "open": raise AssessmentError("assignment_closed", "Назначение закрыто.")
        if now < assignment.start_at: raise AssessmentError("assignment_not_started", "Окно выполнения ещё не открыто.")
        if now >= assignment.due_at: raise AssessmentError("assignment_deadline_passed", "Срок выполнения истёк.")

    async def _own_participant(self, session, assignment_id, student_id, lock=False):
        query = select(AssignmentParticipant).where(AssignmentParticipant.assignment_id == assignment_id,
            AssignmentParticipant.student_id == student_id)
        if lock: query = query.with_for_update()
        row = await session.scalar(query)
        if row is None: raise AssessmentError("assignment_not_found", "Назначение не найдено.", 404)
        return row

    async def _locked_start_context(self, session, assignment_id, student_id):
        """Acquire the command's Assignment -> Participant lock order."""
        assignment = await session.scalar(
            select(Assignment).where(Assignment.id == assignment_id).with_for_update())
        if assignment is None:
            raise AssessmentError("assignment_not_found", "Назначение не найдено.", 404)
        participant = await session.scalar(select(AssignmentParticipant).where(
            AssignmentParticipant.assignment_id == assignment_id,
            AssignmentParticipant.student_id == student_id).with_for_update())
        if participant is None:
            raise AssessmentError("assignment_not_found", "Назначение не найдено.", 404)
        return assignment, participant

    async def list_assignments(self, student_id, offset, limit):
        async with self.factory() as s:
            base=(select(AssignmentParticipant,Assignment,Assessment)
                .join(Assignment,Assignment.id==AssignmentParticipant.assignment_id)
                .join(Assessment,Assessment.id==Assignment.assessment_id)
                .where(AssignmentParticipant.student_id==student_id))
            total=await s.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows=(await s.execute(base.order_by(Assignment.created_at.desc(),Assignment.id).offset(offset).limit(limit))).all()
            items=[]
            for p,a,x in rows:
                count=await s.scalar(select(func.count()).select_from(StudentSubmission).where(StudentSubmission.assignment_participant_id==p.id)) or 0
                items.append(self._assignment_summary(p,a,x,count))
            return {"items":items,"total":total,"offset":offset,"limit":limit}

    @staticmethod
    def _assignment_summary(p,a,x,count):
        return {"assignment_id":a.id,"assessment_id":a.assessment_id,"title":x.title,"status":a.status,
            "start_at":a.start_at,"due_at":a.due_at,"max_attempts":a.max_attempts,
            "assigned_variant_id":p.assigned_variant_id,"attempt_count":count}

    async def assignment_detail(self, assignment_id, student_id):
        async with self.factory() as s:
            p=await self._own_participant(s,assignment_id,student_id)
            a=await s.get(Assignment,assignment_id); x=await s.get(Assessment,a.assessment_id)
            submissions=(await s.scalars(select(StudentSubmission).where(StudentSubmission.assignment_participant_id==p.id))).all()
            result=self._assignment_summary(p,a,x,len(submissions)); result.update(description=x.description,participant_id=p.id,
                current_draft_attempt_id=next((v.id for v in submissions if v.status=="draft"),None),
                submitted_attempt_count=sum(v.status=="submitted" for v in submissions))
            return result

    async def _materialize(self,s,submission,resumed=False):
        p=await s.get(AssignmentParticipant,submission.assignment_participant_id)
        item_rows=(await s.scalars(select(AssessmentItem).where(AssessmentItem.variant_id==p.assigned_variant_id)
            .order_by(AssessmentItem.position,AssessmentItem.id))).all()
        historical=SQLAlchemyContentBankReadPort(s)
        answers=(await s.scalars(select(StudentAnswer).where(StudentAnswer.submission_id==submission.id)
            .order_by(StudentAnswer.assessment_item_id))).all()
        return {"id":submission.id,"attempt_no":submission.attempt_no,"status":submission.status,
            "assigned_variant_id":p.assigned_variant_id,"resumed":resumed,"started_at":submission.started_at,
            "submitted_at":submission.submitted_at,
            "answers":[{"item_id":v.assessment_item_id,"raw_answer":v.raw_answer,"normalized_answer":v.normalized_answer,
                "created_at":v.created_at,"updated_at":v.updated_at} for v in answers],
            "items":[{"id":i.id,"task_version_id":i.task_version_id,"position":i.position,"points":i.points,
                "title":v.title,"statement":v.statement,"task_type":v.task_type,"answer_format":v.answer_format}
                for i in item_rows for v in [await historical.get_historical_version(i.task_version_id)]]}

    async def start(self, assignment_id, student_id, key):
        request_hash=command_hash("start",assignment_id=assignment_id)
        async with self.factory() as s, s.begin():
            assignment,p=await self._locked_start_context(s,assignment_id,student_id)
            prior=await s.scalar(select(AssessmentIdempotencyKey).where(AssessmentIdempotencyKey.assignment_participant_id==p.id,
                AssessmentIdempotencyKey.key==key))
            if prior:
                if prior.operation!="start" or prior.request_hash!=request_hash: raise AssessmentError("idempotency_conflict","Ключ уже использован другой командой.")
                submission=await s.get(StudentSubmission,prior.submission_id)
                return await self._materialize(s,submission,prior.http_status==200),prior.http_status
            now=await s.scalar(select(func.clock_timestamp())); self._window(assignment,now)
            if p.assigned_variant_id is None:
                variants=(await s.scalars(select(AssessmentVariant).where(AssessmentVariant.assessment_id==assignment.assessment_id))).all()
                chosen=select_deterministic_variant(assignment.id,p.student_id,variants)
                p.assigned_variant_id=chosen.id; p.variant_assigned_at=now
                self._audit(s,"assignment",assignment.id,"variant_assigned",student_id,
                    {"participant_id":str(p.id),"student_id":str(student_id),"variant_id":str(chosen.id)})
            draft=await s.scalar(select(StudentSubmission).where(StudentSubmission.assignment_participant_id==p.id,
                StudentSubmission.status=="draft"))
            resumed=draft is not None
            if draft is None:
                attempt_no=(await s.scalar(select(func.coalesce(func.max(StudentSubmission.attempt_no),0)).where(
                    StudentSubmission.assignment_participant_id==p.id)))+1
                if attempt_no>assignment.max_attempts: raise AssessmentError("attempt_limit_reached","Лимит попыток исчерпан.")
                draft=StudentSubmission(assignment_participant_id=p.id,attempt_no=attempt_no,status="draft",started_at=now)
                s.add(draft); await s.flush()
                self._audit(s,"submission",draft.id,"submission_started",student_id,{"assignment_id":str(assignment.id),
                    "participant_id":str(p.id),"attempt_no":attempt_no,"variant_id":str(p.assigned_variant_id)})
            status=200 if resumed else 201
            s.add(AssessmentIdempotencyKey(assignment_participant_id=p.id,key=key,operation="start",
                request_hash=request_hash,submission_id=draft.id,http_status=status))
            await s.flush()
            return await self._materialize(s,draft,resumed),status

    async def get_attempt(self,submission_id,student_id):
        async with self.factory() as s:
            sub=await self._own_submission(s,submission_id,student_id)
            return await self._materialize(s,sub)

    async def _own_submission(self,s,submission_id,student_id):
        row=await s.scalar(select(StudentSubmission).join(AssignmentParticipant,
            AssignmentParticipant.id==StudentSubmission.assignment_participant_id).where(StudentSubmission.id==submission_id,
            AssignmentParticipant.student_id==student_id))
        if row is None: raise AssessmentError("submission_not_found","Попытка не найдена.",404)
        return row

    async def _locked_attempt_context(self,s,submission_id,student_id):
        identifiers=(await s.execute(select(Assignment.id,AssignmentParticipant.id)
            .join(AssignmentParticipant,AssignmentParticipant.assignment_id==Assignment.id)
            .join(StudentSubmission,StudentSubmission.assignment_participant_id==AssignmentParticipant.id)
            .where(StudentSubmission.id==submission_id,
                AssignmentParticipant.student_id==student_id))).one_or_none()
        if identifiers is None:
            raise AssessmentError("submission_not_found","Попытка не найдена.",404)
        assignment_id,participant_id=identifiers
        a=await s.scalar(select(Assignment).where(Assignment.id==assignment_id).with_for_update())
        p=await s.scalar(select(AssignmentParticipant).where(
            AssignmentParticipant.id==participant_id,
            AssignmentParticipant.student_id==student_id).with_for_update())
        sub=await s.scalar(select(StudentSubmission).where(
            StudentSubmission.id==submission_id,
            StudentSubmission.assignment_participant_id==participant_id).with_for_update())
        if p is None or sub is None:
            raise AssessmentError("submission_not_found","Попытка не найдена.",404)
        return a,p,sub

    async def _item(self,s,p,item_id):
        item=await s.scalar(select(AssessmentItem).where(AssessmentItem.id==item_id,
            AssessmentItem.variant_id==p.assigned_variant_id))
        if item is None: raise AssessmentError("item_not_found","Задание не найдено.",404)
        version=await SQLAlchemyContentBankReadPort(s).get_historical_version(item.task_version_id)
        if version is None: raise AssessmentError("item_not_found","Задание не найдено.",404)
        return item,version

    async def save_answer(self,submission_id,item_id,student_id,raw,expected):
        if raw is None: await self.delete_answer(submission_id,item_id,student_id); return None,204
        async with self.factory() as s, s.begin():
            a,p,sub=await self._locked_attempt_context(s,submission_id,student_id)
            if sub.status!="draft": raise AssessmentError("submission_already_submitted","Попытка уже отправлена.")
            now=await s.scalar(select(func.clock_timestamp())); self._window(a,now)
            item,version=await self._item(s,p,item_id)
            normalized=normalize_answer(version.answer_format,raw)
            answer=await s.scalar(select(StudentAnswer).where(StudentAnswer.submission_id==sub.id,
                StudentAnswer.assessment_item_id==item.id).with_for_update())
            created=answer is None
            if (created and expected is not None) or (not created and expected!=answer.updated_at):
                raise AssessmentError("concurrent_conflict","Ответ был изменён параллельно.")
            if created:
                answer=StudentAnswer(submission_id=sub.id,assessment_item_id=item.id,raw_answer=raw,normalized_answer=normalized,
                    created_at=now,updated_at=now); s.add(answer)
            else: answer.raw_answer=raw; answer.normalized_answer=normalized; answer.updated_at=now
            self._audit(s,"submission",sub.id,"answer_saved",student_id,{"item_id":str(item.id),"operation":"create" if created else "update"})
            await s.flush()
            result={"item_id":item.id,"raw_answer":answer.raw_answer,"normalized_answer":answer.normalized_answer,
                "created_at":answer.created_at,"updated_at":answer.updated_at}
            return result,201 if created else 200

    async def delete_answer(self,submission_id,item_id,student_id):
        async with self.factory() as s, s.begin():
            a,p,sub=await self._locked_attempt_context(s,submission_id,student_id)
            if sub.status!="draft": raise AssessmentError("submission_already_submitted","Попытка уже отправлена.")
            now=await s.scalar(select(func.clock_timestamp())); self._window(a,now); await self._item(s,p,item_id)
            answer=await s.scalar(select(StudentAnswer).where(StudentAnswer.submission_id==sub.id,
                StudentAnswer.assessment_item_id==item_id).with_for_update())
            if answer:
                await s.delete(answer); self._audit(s,"submission",sub.id,"answer_deleted",student_id,{"item_id":str(item_id)})

    async def submit(self,submission_id,student_id,key):
        request_hash=command_hash("submit",submission_id=submission_id)
        async with self.factory() as s, s.begin():
            a,p,sub=await self._locked_attempt_context(s,submission_id,student_id)
            prior=await s.scalar(select(AssessmentIdempotencyKey).where(AssessmentIdempotencyKey.assignment_participant_id==p.id,
                AssessmentIdempotencyKey.key==key))
            if prior:
                if prior.operation!="submit" or prior.request_hash!=request_hash or prior.submission_id!=sub.id:
                    raise AssessmentError("idempotency_conflict","Ключ уже использован другой командой.")
                return await self._materialize(s,sub),prior.http_status
            answers=(await s.scalars(select(StudentAnswer).where(StudentAnswer.submission_id==sub.id)
                .order_by(StudentAnswer.assessment_item_id).with_for_update())).all()
            if sub.status!="draft": raise AssessmentError("submission_already_submitted","Попытка уже отправлена.")
            now=await s.scalar(select(func.clock_timestamp())); self._window(a,now)
            valid=set(await s.scalars(select(AssessmentItem.id).where(AssessmentItem.variant_id==p.assigned_variant_id)))
            if any(x.assessment_item_id not in valid for x in answers): raise AssessmentError("item_not_found","Задание не найдено.",404)
            sub.status="submitted"; sub.submitted_at=now
            self._audit(s,"submission",sub.id,"submission_submitted",student_id,{"attempt_no":sub.attempt_no,"answer_count":len(answers)})
            s.add(AssessmentIdempotencyKey(assignment_participant_id=p.id,key=key,operation="submit",request_hash=request_hash,
                submission_id=sub.id,http_status=200)); await s.flush()
            return await self._materialize(s,sub),200


class AssessmentCheckingHandoffService:
    """Materialize one coherent submitted snapshot without leaking ORM objects."""
    def __init__(self, factory: async_sessionmaker[AsyncSession]): self.factory = factory

    async def get(self, submission_id: UUID) -> CheckingHandoff:
        async with self.factory() as session, session.begin():
            submission = await session.get(StudentSubmission, submission_id)
            if submission is None:
                raise AssessmentError("submission_not_found", "Попытка не найдена.", 404)
            if submission.status != "submitted" or submission.submitted_at is None:
                raise CheckingHandoffNotReady("submission is not submitted")
            participant = await session.get(AssignmentParticipant, submission.assignment_participant_id)
            rows = (await session.execute(select(AssessmentItem, StudentAnswer)
                .outerjoin(StudentAnswer, (StudentAnswer.submission_id == submission.id) &
                           (StudentAnswer.assessment_item_id == AssessmentItem.id))
                .where(AssessmentItem.variant_id == participant.assigned_variant_id)
                .order_by(AssessmentItem.position, AssessmentItem.id))).all()
            version_ids = tuple(item.task_version_id for item, _ in rows)
            versions = await SQLAlchemyContentBankReadPort(session).get_historical_versions(version_ids)
            if len(versions) != len(set(version_ids)):
                raise AssessmentError("item_not_found", "Историческая версия задания не найдена.", 404)
            items = tuple(CheckingHandoffItem(item.id, item.task_version_id, item.position,
                item.points, versions[item.task_version_id].answer_format,
                answer.raw_answer if answer else None, answer.normalized_answer if answer else None)
                for item, answer in rows)
            return CheckingHandoff(submission.id, submission.submitted_at, items)
