"""Read-only remediation execution/result projections."""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import func, select

from app.application.classroom_results import score_totals
from app.application.remediation import RemediationError
from app.infrastructure.assessment_models import Student, StudentAnswer, StudentSubmission
from app.infrastructure.checking_models import CheckFinding, CheckResult, CheckRun
from app.infrastructure.classroom_models import ClassGroupTeacher
from app.infrastructure.models import ChoiceOption, TaskVersion
from app.infrastructure.remediation_models import RemediationPlan, RemediationPlanItem


def execution_state(plan, submission, run, now):
    if submission is None:
        if plan.status == "cancelled": return "cancelled"
        if plan.due_at is not None and now > plan.due_at: return "expired"
        return "not_started"
    if submission.status == "draft":
        if plan.status == "cancelled": return "cancelled"
        if plan.due_at is not None and now > plan.due_at: return "expired"
        return "in_progress"
    if run is None: return "submitted"
    if run.status in {"pending", "running"}: return "checking"
    if run.status == "completed": return "checked"
    if run.status == "completed_with_review_required": return "review_required"
    if run.status in {"failed_retryable", "failed_terminal"}: return "check_failed"
    return "submitted"


class RemediationResultsService:
    """Build persisted projections only; this service has no mutation/provider API."""
    def __init__(self, session): self.s = session

    async def _plan(self, plan_id, *, student_id=None, actor_id=None, unrestricted=False):
        q = select(RemediationPlan).where(RemediationPlan.id == plan_id)
        if student_id is not None:
            q = q.where(RemediationPlan.student_id == student_id,
                        RemediationPlan.status.in_(("assigned", "cancelled")))
        plan = await self.s.scalar(q)
        if plan is None: raise RemediationError("remediation_not_found", 404)
        if actor_id is not None and not unrestricted:
            member = await self.s.scalar(select(ClassGroupTeacher).where(
                ClassGroupTeacher.class_group_id == plan.class_group_id,
                ClassGroupTeacher.teacher_user_id == actor_id))
            if plan.owner_user_id != actor_id or member is None:
                raise RemediationError("remediation_not_found", 404)
        return plan

    async def _projection(self, plan, *, student_safe):
        student = await self.s.get(Student, plan.student_id)
        submission = await self.s.scalar(select(StudentSubmission).where(
            StudentSubmission.remediation_plan_id == plan.id,
            StudentSubmission.attempt_no == 1))
        run = None
        if submission:
            run = await self.s.scalar(select(CheckRun).where(
                CheckRun.submission_id == submission.id).order_by(
                    CheckRun.attempt_no.desc(), CheckRun.id.desc()).limit(1))
        # Results are deliberately selected only for the latest successful/review run.
        results = list((await self.s.scalars(select(CheckResult).where(
            CheckResult.check_run_id == run.id))).all()) if run and run.status in {
                "completed", "completed_with_review_required"} else []
        result_by_item = {r.remediation_plan_item_id: r for r in results}
        findings = list((await self.s.scalars(select(CheckFinding).where(
            CheckFinding.check_result_id.in_([r.id for r in results])))).all()) if results else []
        finding_by_result = defaultdict(list)
        for finding in findings: finding_by_result[finding.check_result_id].append(finding)
        rows = (await self.s.execute(select(RemediationPlanItem, TaskVersion)
            .join(TaskVersion, TaskVersion.id == RemediationPlanItem.task_version_id)
            .where(RemediationPlanItem.remediation_plan_id == plan.id)
            .order_by(RemediationPlanItem.position, RemediationPlanItem.id))).all()
        answers = {}
        if submission:
            answers = {a.remediation_plan_item_id: a for a in await self.s.scalars(
                select(StudentAnswer).where(StudentAnswer.submission_id == submission.id))}
        options_by_version = defaultdict(list)
        if rows:
            for option in await self.s.scalars(select(ChoiceOption).where(
                ChoiceOption.task_version_id.in_([v.id for _, v in rows])).order_by(
                    ChoiceOption.task_version_id, ChoiceOption.order_index, ChoiceOption.id)):
                options_by_version[option.task_version_id].append({"option_id": option.option_key, "content": option.content})
        items = []
        for item, version in rows:
            result = result_by_item.get(item.id)
            base = {"remediation_plan_item_id": item.id, "position": item.position,
                    "task_version_id": version.id, "task_title": version.title,
                    "task_statement": version.statement,
                    "student_raw_answer": answers[item.id].raw_answer if item.id in answers else None}
            if student_safe:
                base.update({"task_type": version.task_type, "answer_format": version.answer_format,
                    "difficulty": version.difficulty, "choice_options": options_by_version[version.id],
                    "result_status": result.result_status if result else None,
                    "score_suggested": result.score_suggested if result else None,
                    "max_score": result.max_score if result else None,
                    "student_feedback": ("Требуется проверка учителя." if result and result.needs_human_review
                        else result.student_feedback_draft if result else None)})
            else:
                cr = None
                if result:
                    cr = {"check_result_id": result.id, "result_status": result.result_status,
                        "checker_type": result.checker_type, "score_suggested": result.score_suggested,
                        "max_score": result.max_score, "confidence": result.confidence,
                        "summary": result.summary, "teacher_summary": result.teacher_summary,
                        "needs_human_review": result.needs_human_review,
                        "review_reason": result.review_reason, "model_limitations": result.model_limitations}
                base.update({"check_result": cr, "findings": [{"finding_id": f.id,
                    "finding_type": f.finding_type, "rubric_item_id": f.rubric_item_id,
                    "typical_error_id": f.typical_error_id, "skill_id": f.skill_id,
                    "snapshot_code": f.snapshot_code, "snapshot_title": f.snapshot_title,
                    "snapshot_criterion": f.snapshot_criterion, "severity": f.severity,
                    "confidence": f.confidence} for f in finding_by_result[result.id]] if result else []})
            items.append(base)
        now = await self.s.scalar(select(func.clock_timestamp()))
        score, maximum, percent = score_totals(results)
        common = {"remediation_id": plan.id, "plan_status": plan.status,
            "execution_status": execution_state(plan, submission, run, now),
            "submission_id": submission.id if submission else None,
            "submission_status": submission.status if submission else None,
            "started_at": submission.started_at if submission else None,
            "submitted_at": submission.submitted_at if submission else None,
            "check_run_id": run.id if run else None, "check_run_status": run.status if run else None,
            "items": items}
        if student_safe:
            common.update({"title": plan.title, "instructions": plan.instructions, "due_at": plan.due_at,
                           "attempt_no": submission.attempt_no if submission else None})
        else:
            common.update({"student_id": plan.student_id,
                "student_display_name": student.display_name if student else "",
                "suggested_score_total": score, "max_score_total": maximum,
                "suggested_percent": percent,
                "review_required_count": sum(1 for r in results if r.needs_human_review),
                "failure_code": run.failure_code if run else None})
        return common

    async def teacher_result(self, plan_id, actor_id, unrestricted=False):
        return await self._projection(await self._plan(plan_id, actor_id=actor_id,
            unrestricted=unrestricted), student_safe=False)

    async def student_execution(self, plan_id, student_id):
        return await self._projection(await self._plan(plan_id, student_id=student_id), student_safe=True)
