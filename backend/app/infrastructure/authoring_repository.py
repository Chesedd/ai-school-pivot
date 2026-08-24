"""Transactional repositories for Content Bank authoring attempts and reads."""
from dataclasses import dataclass
from uuid import UUID, uuid4
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.authoring import AuthoringConflict, AuthoringRole, ExecutionRequest, FailureCode, FrozenCatalogContext, ProviderResult, RETRYABLE, thaw_json
from app.infrastructure.authoring_models import (AuthoringProviderAttempt, AuthoringReview,
    AuthoringReviewAudit, AuthoringReviewRevision, AuthoringSession)
from app.infrastructure.models import AuditLog


@dataclass(frozen=True)
class AuthoringWorkspaceRecord:
    session: AuthoringSession
    attempts: tuple[AuthoringProviderAttempt, ...]
    review: AuthoringReview | None
    revisions: tuple[AuthoringReviewRevision, ...]
    review_audits: tuple[AuthoringReviewAudit, ...]
    promotion: AuditLog | None


class AuthoringWorkspaceRepository:
    """Fixed-query projection loader: one owned root plus batched child reads.

    The query count is constant regardless of attempt/revision counts. Children are
    loaded in bulk in at most five further queries (the read-model equivalent of
    select-in eager loading), avoiding
    both N+1 access and the cartesian expansion of one large multi-collection join.
    """
    def __init__(self, db: AsyncSession): self.db = db

    async def get(self, session_id: UUID, owner_id: UUID) -> AuthoringWorkspaceRecord | None:
        session = await self.db.scalar(select(AuthoringSession).where(
            AuthoringSession.id == session_id, AuthoringSession.owner_id == owner_id))
        if session is None:
            return None
        attempts = tuple((await self.db.scalars(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id).order_by(
            AuthoringProviderAttempt.created_at, AuthoringProviderAttempt.attempt_number))).all())
        review = await self.db.scalar(select(AuthoringReview).where(
            AuthoringReview.session_id == session_id, AuthoringReview.owner_id == owner_id))
        revisions: tuple[AuthoringReviewRevision, ...] = ()
        audits: tuple[AuthoringReviewAudit, ...] = ()
        if review is not None:
            revisions = tuple((await self.db.scalars(select(AuthoringReviewRevision).where(
                AuthoringReviewRevision.review_id == review.id).order_by(
                AuthoringReviewRevision.revision_number))).all())
            audits = tuple((await self.db.scalars(select(AuthoringReviewAudit).where(
                AuthoringReviewAudit.review_id == review.id).order_by(AuthoringReviewAudit.created_at))).all())
        promotion = await self.db.scalar(select(AuditLog).where(AuditLog.action == "task_created",
            AuditLog.details["authoring_session_id"].astext == str(session_id)))
        return AuthoringWorkspaceRecord(session, attempts, review, revisions, audits, promotion)


class AuthoringRepository:
    def __init__(self, db: AsyncSession): self.db=db

    async def commit(self) -> None:
        """End a short transition transaction before application/provider work."""
        await self.db.commit()

    async def create_session(self, owner_id: UUID, request, catalog: FrozenCatalogContext) -> AuthoringSession:
        catalog.validate_request(request)
        row=AuthoringSession(id=uuid4(),owner_id=owner_id,schema_version=request.schema_version,policy_version=request.policy_version,
            frozen_request=request.model_dump(mode="json"),request_fingerprint=request.fingerprint,frozen_allowlist=catalog.as_json())
        self.db.add(row); await self.db.flush(); return row

    async def create_attempt(self, session_id: UUID, execution: ExecutionRequest) -> tuple[AuthoringProviderAttempt,bool]:
        existing=await self.db.scalar(select(AuthoringProviderAttempt).where(AuthoringProviderAttempt.session_id==session_id,AuthoringProviderAttempt.idempotency_key==execution.idempotency_key))
        if existing:
            if (existing.request_fingerprint != execution.request_fingerprint or existing.provider_id != execution.provider_id or existing.model_id != execution.model_id): raise AuthoringConflict()
            return existing,False
        # Lock aggregate: serializes retry numbering and creation for a session.
        session=await self.db.scalar(select(AuthoringSession).where(AuthoringSession.id==session_id).with_for_update())
        if session is None: raise AuthoringConflict()
        # Legacy generator attempts remain bound to the frozen authoring request.
        # Extractor attempts use the same deployed enum value but are bound to the
        # immutable InputArtifactV1 fingerprint instead.
        if (execution.role is AuthoringRole.GENERATOR
                and execution.prompt.stable_name != "extractor.task"
                and session.request_fingerprint != execution.request_fingerprint): raise AuthoringConflict()
        existing=await self.db.scalar(select(AuthoringProviderAttempt).where(AuthoringProviderAttempt.session_id==session_id,AuthoringProviderAttempt.idempotency_key==execution.idempotency_key))
        if existing:
            if (existing.request_fingerprint != execution.request_fingerprint or existing.provider_id != execution.provider_id or existing.model_id != execution.model_id): raise AuthoringConflict()
            return existing,False
        number=(await self.db.scalar(select(func.coalesce(func.max(AuthoringProviderAttempt.attempt_number),0)).where(AuthoringProviderAttempt.session_id==session_id,AuthoringProviderAttempt.role==execution.role.value)))+1
        prompt={"stable_name":execution.prompt.stable_name,"role":execution.prompt.role.value,"semantic_version":execution.prompt.semantic_version,"template_version":execution.prompt.template_version,"template_hash":execution.prompt.template_hash,"output_schema_version":execution.prompt.output_schema_version,"policy_version":execution.prompt.policy_version}
        row=AuthoringProviderAttempt(id=uuid4(),session_id=session_id,role=execution.role.value,attempt_number=number,idempotency_key=execution.idempotency_key,
            provider_id=execution.provider_id,model_id=execution.model_id,settings_snapshot=thaw_json(execution.settings),prompt_snapshot=prompt,
            request_fingerprint=execution.request_fingerprint,timeout_ms=execution.timeout_ms)
        self.db.add(row)
        try: await self.db.flush()
        except IntegrityError: raise AuthoringConflict() from None
        return row,True

    async def claim(self, attempt_id: UUID) -> bool:
        result=await self.db.execute(update(AuthoringProviderAttempt).where(AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.status=="pending").values(status="running",started_at=func.clock_timestamp()))
        await self.db.flush(); return result.rowcount==1

    async def finalize_success(self, attempt_id: UUID, result: ProviderResult, *, invalid_output: bool=False) -> bool:
        values={"status":"invalid_output" if invalid_output else "succeeded","finished_at":func.clock_timestamp(),"provider_request_id":result.provider_request_id,"response_hash":result.response_hash,"latency_ms":result.latency_ms,"input_tokens":result.usage.input_tokens,"output_tokens":result.usage.output_tokens,"cached_tokens":result.usage.cached_tokens,"cache_read_tokens":result.usage.cache_read_tokens,"cache_write_tokens":result.usage.cache_write_tokens,"cost_amount":result.cost.amount,"currency":result.cost.currency,"pricing_version":result.cost.pricing_version,"pricing_source":result.cost.pricing_source}
        changed=await self.db.execute(update(AuthoringProviderAttempt).where(AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.status=="running").values(**values)); await self.db.flush(); return changed.rowcount==1

    async def finalize_failure(self, attempt_id: UUID, code: FailureCode) -> bool:
        status="failed_retryable" if code in RETRYABLE else "failed_terminal"
        changed=await self.db.execute(update(AuthoringProviderAttempt).where(AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.status=="running").values(status=status,failure_code=code.value,finished_at=func.clock_timestamp())); await self.db.flush(); return changed.rowcount==1

    async def recover_stale(self, attempt_id: UUID, *, grace_ms: int = 5_000) -> bool:
        """CAS a dead provider claim using the database clock."""
        stale=AuthoringProviderAttempt.started_at + func.make_interval(0,0,0,0,0,0,
            (AuthoringProviderAttempt.timeout_ms + grace_ms) / 1000.0)
        changed=await self.db.execute(update(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.status=="running",
            func.clock_timestamp() > stale).values(status="failed_retryable",failure_code=FailureCode.TIMEOUT.value,
            finished_at=func.clock_timestamp()))
        await self.db.flush(); return changed.rowcount==1

    async def configure_pipeline(self, session_id: UUID, identity: str, generator_route, solver_route):
        row=await self.db.scalar(select(AuthoringSession).where(AuthoringSession.id==session_id).with_for_update())
        if row is None: raise AuthoringConflict()
        if row.pipeline_identity is not None and row.pipeline_identity != identity: raise AuthoringConflict()
        from app.application.authoring_pipeline import PipelineResumeState
        if row.pipeline_identity == identity:
            return PipelineResumeState.from_persisted(row.generated_draft,row.generator_attempt_id,
                row.solver_result,row.solver_attempt_id,row.validation_result)
        row.pipeline_identity=identity
        row.generator_route={"provider_id":generator_route.provider_id,"model_id":generator_route.model_id}
        row.solver_route={"provider_id":solver_route.provider_id,"model_id":solver_route.model_id}
        await self.db.flush(); return PipelineResumeState()

    async def configure_extraction_pipeline(self, session_id: UUID, identity: str, extractor_route, solver_route):
        """Configure the new pipeline using legacy columns as a compatibility mapping."""
        row=await self.db.scalar(select(AuthoringSession).where(AuthoringSession.id==session_id).with_for_update())
        if row is None: raise AuthoringConflict()
        if row.pipeline_identity is not None and row.pipeline_identity != identity: raise AuthoringConflict()
        from app.application.extraction_pipeline import ExtractionResumeState
        if row.pipeline_identity == identity:
            return ExtractionResumeState.from_persisted(row.generated_draft,row.generator_attempt_id,
                row.solver_result,row.solver_attempt_id)
        row.pipeline_identity=identity
        # generator_* is the deployed physical schema; application code exposes extractor_*.
        row.generator_route={"provider_id":extractor_route.provider_id,"model_id":extractor_route.model_id}
        row.solver_route={"provider_id":solver_route.provider_id,"model_id":solver_route.model_id}
        await self.db.flush(); return ExtractionResumeState()

    def _result_values(self, result: ProviderResult) -> dict:
        return {"status":"succeeded","finished_at":func.clock_timestamp(),"provider_request_id":result.provider_request_id,
            "response_hash":result.response_hash,"latency_ms":result.latency_ms,"input_tokens":result.usage.input_tokens,
            "output_tokens":result.usage.output_tokens,"cached_tokens":result.usage.cached_tokens,
            "cache_read_tokens":result.usage.cache_read_tokens,"cache_write_tokens":result.usage.cache_write_tokens,
            "cost_amount":result.cost.amount,"currency":result.cost.currency,"pricing_version":result.cost.pricing_version,
            "pricing_source":result.cost.pricing_source}

    async def checkpoint_stage_success(self, session_id: UUID, identity: str, attempt_id: UUID,
                                       role: AuthoringRole, result: ProviderResult, value) -> None:
        """Atomically finalize an attempt and install its semantic checkpoint."""
        attempt=await self.db.scalar(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.session_id==session_id,
            AuthoringProviderAttempt.role==role.value).with_for_update())
        if attempt is None or attempt.status != "running": raise AuthoringConflict()
        field="generated_draft" if role is AuthoringRole.GENERATOR else "solver_result"
        attempt_field="generator_attempt_id" if role is AuthoringRole.GENERATOR else "solver_attempt_id"
        conditions=[AuthoringSession.id==session_id,AuthoringSession.pipeline_identity==identity,
            getattr(AuthoringSession,field).is_(None),getattr(AuthoringSession,attempt_field).is_(None)]
        if role is AuthoringRole.SOLVER:
            conditions.extend((AuthoringSession.generated_draft.is_not(None),AuthoringSession.generator_attempt_id.is_not(None)))
        changed=await self.db.execute(update(AuthoringSession).where(*conditions).values(**{
            field:value.model_dump(mode="json"),attempt_field:attempt_id,"row_version":AuthoringSession.row_version+1}))
        if changed.rowcount != 1: raise AuthoringConflict()
        changed=await self.db.execute(update(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.status=="running").values(**self._result_values(result)))
        if changed.rowcount != 1: raise AuthoringConflict()
        await self.db.flush()

    async def checkpoint_extraction_success(self, session_id, identity, attempt_id, result, value):
        """Persist extraction_result/extractor_attempt_id in legacy physical columns."""
        await self.checkpoint_stage_success(session_id,identity,attempt_id,AuthoringRole.GENERATOR,result,value)

    async def checkpoint_solution_success(self, session_id, identity, attempt_id, result, value):
        await self.checkpoint_stage_success(session_id,identity,attempt_id,AuthoringRole.SOLVER,result,value)

    async def checkpoint_validation(self, session_id: UUID, identity: str, validation) -> None:
        changed=await self.db.execute(update(AuthoringSession).where(AuthoringSession.id==session_id,
            AuthoringSession.pipeline_identity==identity,AuthoringSession.generated_draft.is_not(None),
            AuthoringSession.generator_attempt_id.is_not(None),AuthoringSession.solver_result.is_not(None),
            AuthoringSession.solver_attempt_id.is_not(None),AuthoringSession.validation_result.is_(None)).values(
            validation_result=validation.model_dump(mode="json"),semantic_status=validation.status,
            row_version=AuthoringSession.row_version+1))
        await self.db.flush()
        if changed.rowcount != 1: raise AuthoringConflict()
