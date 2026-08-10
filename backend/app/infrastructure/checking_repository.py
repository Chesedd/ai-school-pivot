"""SQLAlchemy persistence adapter for atomic Checking operations; never commits."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.application.checking import (ActiveRunConflict, ConcurrentConflict, CreateRunCommand,
    IdempotencyConflict, InvalidPersistenceCommand, SourceSubmissionNotFound, safe_event_details,
    validate_finding, validate_result, validate_transition)
from app.infrastructure.assessment_models import StudentSubmission
from app.infrastructure.checking_models import CheckFinding, CheckResult, CheckRun, CheckerEvent, ModelRun, PromptVersion


class CheckingRepository:
    def __init__(self, session): self.session = session

    async def create_run(self, command: CreateRunCommand) -> CheckRun:
        command.validate()
        # The source row lock serializes attempt allocation and different-key creation.
        source = await self.session.scalar(select(StudentSubmission).where(StudentSubmission.id == command.submission_id).with_for_update())
        if source is None: raise SourceSubmissionNotFound(str(command.submission_id))
        prior = await self.session.scalar(select(CheckRun).where(CheckRun.submission_id == command.submission_id, CheckRun.request_key == command.request_key))
        if prior is not None:
            if prior.request_hash != command.request_hash: raise IdempotencyConflict(command.request_key)
            return prior
        active = await self.session.scalar(select(CheckRun.id).where(CheckRun.submission_id == command.submission_id, CheckRun.status.in_(("pending", "running"))))
        if active is not None: raise ActiveRunConflict(str(active))
        attempt = (await self.session.scalar(select(func.coalesce(func.max(CheckRun.attempt_no), 0)).where(CheckRun.submission_id == command.submission_id))) + 1
        row = CheckRun(**command.__dict__, attempt_no=attempt)
        try:
            async with self.session.begin_nested(): self.session.add(row); await self.session.flush()
        except IntegrityError as exc: raise ConcurrentConflict("run creation race") from exc
        self.session.add(CheckerEvent(check_run_id=row.id, event_type="run_created", details={"attempt_no": attempt}))
        await self.session.flush(); return row

    async def transition_run(self, run_id: UUID, expected_version: int, target: str, *, failure_code: str | None = None, failure_detail: str | None = None, details=None) -> CheckRun:
        row = await self.session.get(CheckRun, run_id)
        if row is None: raise InvalidPersistenceCommand("run not found")
        validate_transition(row.status, target); safe = safe_event_details(details or {})
        now = await self.session.scalar(select(func.clock_timestamp())); values = {"status": target, "row_version": expected_version + 1}
        if target == "running": values.update(started_at=now, heartbeat_at=now)
        if row.status == "failed_retryable" and target == "pending":
            values.update(started_at=None, finished_at=None, heartbeat_at=None,
                          failure_code=None, failure_detail=None, retry_count=row.retry_count + 1)
        if target in {"completed", "completed_with_review_required", "failed_retryable", "failed_terminal"}: values.update(finished_at=now, failure_code=failure_code, failure_detail=failure_detail)
        if row.status == "pending" and target == "failed_terminal": values["started_at"] = now
        changed = await self.session.scalar(update(CheckRun).where(CheckRun.id == run_id, CheckRun.row_version == expected_version).values(**values).returning(CheckRun.id))
        if changed is None: raise ConcurrentConflict("stale run version")
        self.session.add(CheckerEvent(check_run_id=run_id, event_type="run_transition", from_status=row.status, to_status=target, reason_code=failure_code, details=safe)); await self.session.flush()
        return await self.session.get(CheckRun, run_id, populate_existing=True)

    async def record_result(self, result: CheckResult, findings: tuple[CheckFinding, ...]) -> None:
        """Validate and append one complete result aggregate in the caller transaction."""
        run = await self.session.get(CheckRun, result.check_run_id)
        if run is None: raise InvalidPersistenceCommand("run not found")
        validate_result(run.input_snapshot, result.assessment_item_id, result.task_version_id,
                        result.max_score, result.result_status, result.score_suggested)
        self.session.add(result); await self.session.flush()
        for finding in findings:
            validate_finding(run.input_snapshot, result.assessment_item_id, finding.rubric_item_id,
                             finding.typical_error_id, finding.skill_id, finding.evidence)
            finding.check_result_id = result.id
        self.session.add_all(findings)
        self.session.add(CheckerEvent(check_run_id=run.id, check_result_id=result.id,
            assessment_item_id=result.assessment_item_id, event_type="result_recorded",
            details={"checker_type": result.checker_type, "result_status": result.result_status}))
        await self.session.flush()

    async def finalize_model_run(self, model_run_id: UUID, status: str, values: dict) -> None:
        if status not in {"succeeded", "failed", "invalid"}: raise InvalidPersistenceCommand("invalid terminal model status")
        changed = await self.session.scalar(update(ModelRun).where(ModelRun.id == model_run_id, ModelRun.status == "running").values(status=status, finished_at=func.clock_timestamp(), **values).returning(ModelRun.id))
        if changed is None: raise ConcurrentConflict("model attempt already terminal")

    async def retire_prompt(self, prompt_id: UUID, created_at: datetime) -> None:
        changed = await self.session.scalar(update(PromptVersion).where(PromptVersion.id == prompt_id, PromptVersion.created_at == created_at, PromptVersion.retired_at.is_(None)).values(retired_at=func.clock_timestamp()).returning(PromptVersion.id))
        if changed is None: raise ConcurrentConflict("prompt already retired or stale")
