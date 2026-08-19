"""SQLAlchemy persistence adapter for atomic Checking operations; never commits."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.application.checking import (ActiveRunConflict, ConcurrentConflict, CreateRunCommand,
    IdempotencyConflict, InvalidPersistenceCommand, SourceSubmissionNotFound, safe_event_details,
    validate_finding, validate_result, validate_transition)
from app.infrastructure.assessment_models import StudentSubmission
from app.infrastructure.checking_models import CostEvent, CheckFinding, CheckResult, CheckRun, CheckerEvent, ModelRun, PromptVersion
from app.application.checking_provider import Pricing, PromptSpec, ProviderRequest, ProviderResponse, ProviderUsage


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
        from_status = row.status
        validate_transition(from_status, target); safe = safe_event_details(details or {})
        now = await self.session.scalar(select(func.clock_timestamp())); values = {"status": target, "row_version": expected_version + 1}
        if target == "running": values.update(started_at=now, heartbeat_at=now)
        if from_status == "failed_retryable" and target == "pending":
            values.update(started_at=None, finished_at=None, heartbeat_at=None,
                          failure_code=None, failure_detail=None, retry_count=row.retry_count + 1)
        if target in {"completed", "completed_with_review_required", "failed_retryable", "failed_terminal"}: values.update(finished_at=now, failure_code=failure_code, failure_detail=failure_detail)
        if from_status == "pending" and target == "failed_terminal": values["started_at"] = now
        changed = await self.session.scalar(update(CheckRun).where(
            CheckRun.id == run_id, CheckRun.row_version == expected_version,
            CheckRun.status == from_status,
        ).values(**values).returning(CheckRun.id))
        if changed is None: raise ConcurrentConflict("stale run version")
        self.session.add(CheckerEvent(check_run_id=run_id, event_type="run_transition",
            from_status=from_status, to_status=target, reason_code=failure_code, details=safe)); await self.session.flush()
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

    async def register_prompt(self, spec: PromptSpec) -> PromptVersion:
        """Return an identical immutable prompt, or insert it once under concurrency."""
        # The schema's historical identity includes the hash; serialize the narrower
        # name/version policy here so concurrent different content cannot both win.
        await self.session.execute(select(func.pg_advisory_xact_lock(
            func.hashtextextended(spec.stable_name + "\x00" + spec.semantic_version, 0))))
        rows = (await self.session.scalars(select(PromptVersion).where(
            PromptVersion.name == spec.stable_name,
            PromptVersion.semantic_version == spec.semantic_version).with_for_update())).all()
        for row in rows:
            if (row.template_hash, row.template_text, row.output_schema_version) != (
                    spec.template_hash, spec.template_text, spec.output_schema_version):
                raise IdempotencyConflict("prompt name/version already has different content")
            return row
        row = PromptVersion(name=spec.stable_name, semantic_version=spec.semantic_version,
            template_hash=spec.template_hash, output_schema_version=spec.output_schema_version,
            template_text=spec.template_text)
        try:
            async with self.session.begin_nested():
                self.session.add(row); await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(select(PromptVersion).where(
                PromptVersion.name == spec.stable_name, PromptVersion.semantic_version == spec.semantic_version,
                PromptVersion.template_hash == spec.template_hash))
            if existing is None or existing.template_text != spec.template_text or existing.output_schema_version != spec.output_schema_version:
                raise IdempotencyConflict("prompt registration conflict") from None
            return existing
        return row

    async def model_attempts(self, run_id: UUID, item_id: UUID) -> tuple[ModelRun, ...]:
        return tuple((await self.session.scalars(select(ModelRun).where(
            ModelRun.check_run_id == run_id, ModelRun.assessment_item_id == item_id)
            .order_by(ModelRun.attempt_no))).all())

    async def claim_model_attempt(self, run_id: UUID, item_id: UUID, prompt: PromptVersion,
                                  request: ProviderRequest, max_attempts: int = 3) -> ModelRun:
        """Claim one append-only attempt. Caller commits before making the provider call."""
        run = await self.session.scalar(select(CheckRun).where(CheckRun.id == run_id).with_for_update())
        if run is None: raise InvalidPersistenceCommand("run not found")
        matches = [item for item in run.input_snapshot.get("items", ())
                   if item.get("assessment_item_id") == str(item_id)]
        if len(matches) != 1: raise InvalidPersistenceCommand("assessment item is absent or duplicated in snapshot")
        prompt = await self.session.get(PromptVersion, prompt.id)
        if prompt is None: raise InvalidPersistenceCommand("prompt not found")
        attempts = await self.model_attempts(run_id, item_id)
        if any(row.request_fingerprint != request.request_fingerprint for row in attempts):
            raise IdempotencyConflict("request fingerprint conflict")
        if attempts and attempts[-1].status == "running": return attempts[-1]
        if prompt.retired_at is not None: raise InvalidPersistenceCommand("prompt is retired")
        if [row.attempt_no for row in attempts] != list(range(1, len(attempts) + 1)) or len(attempts) >= max_attempts:
            raise InvalidPersistenceCommand("attempt budget exhausted or history is noncontiguous")
        row = ModelRun(check_run_id=run_id, assessment_item_id=item_id, prompt_version_id=prompt.id,
            check_result_id=None, provider_id=request.provider_id, model_id=request.model_id,
            settings_snapshot=dict(request.settings), request_fingerprint=request.request_fingerprint,
            attempt_no=len(attempts) + 1, timeout_ms=request.timeout_ms, status="running")
        try:
            async with self.session.begin_nested(): self.session.add(row); await self.session.flush()
        except IntegrityError as exc: raise ConcurrentConflict("model attempt claim race") from exc
        self.session.add(CheckerEvent(check_run_id=run_id, assessment_item_id=item_id,
            event_type="model_attempt", details={"model_run_id": str(row.id), "attempt_no": row.attempt_no}))
        await self.session.flush(); return row

    async def finalize_provider_attempt(self, model_run_id: UUID, *, status: str,
                                        response: ProviderResponse | None = None,
                                        validated_output: dict | None = None,
                                        error_code: str | None = None,
                                        validation_errors: dict | None = None,
                                        pricing: Pricing | None = None) -> ModelRun:
        """CAS-finalize and append optional locally calculated cost in one transaction."""
        values: dict = {"provider_request_id": response.provider_request_id if response else None,
            "raw_output": response.raw_output if response else None,
            "validated_output": validated_output, "validation_errors": validation_errors,
            "latency_ms": response.latency_ms if response else None, "error_code": error_code,
            "error_detail": None}
        usage = response.usage if response else None
        if usage: values.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                                cached_tokens=usage.cached_tokens)
        await self.finalize_model_run(model_run_id, status, values)
        row = await self.session.get(ModelRun, model_run_id, populate_existing=True)
        if pricing is not None and usage is not None:
            event = CostEvent(model_run_id=model_run_id, currency=pricing.currency,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens, amount=pricing.cost(usage),
                pricing_version=pricing.pricing_version, pricing_source=pricing.pricing_source)
            try:
                async with self.session.begin_nested(): self.session.add(event); await self.session.flush()
            except IntegrityError:
                existing = await self.session.scalar(select(CostEvent).where(
                    CostEvent.model_run_id == model_run_id,
                    CostEvent.pricing_version == pricing.pricing_version))
                if existing is None or existing.amount != pricing.cost(usage):
                    raise IdempotencyConflict("cost event conflict") from None
        return row

    async def retire_prompt(self, prompt_id: UUID, created_at: datetime) -> None:
        changed = await self.session.scalar(update(PromptVersion).where(PromptVersion.id == prompt_id, PromptVersion.created_at == created_at, PromptVersion.retired_at.is_(None)).values(retired_at=func.clock_timestamp()).returning(PromptVersion.id))
        if changed is None: raise ConcurrentConflict("prompt already retired or stale")
