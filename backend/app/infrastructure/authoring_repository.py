"""Transactional repository for Content Bank authoring attempts."""
from uuid import UUID, uuid4
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.authoring import AuthoringConflict, AuthoringRole, ExecutionRequest, FailureCode, FrozenCatalogContext, ProviderResult, RETRYABLE, thaw_json
from app.infrastructure.authoring_models import AuthoringProviderAttempt, AuthoringSession


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
        if execution.role is AuthoringRole.GENERATOR and session.request_fingerprint != execution.request_fingerprint: raise AuthoringConflict()
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

    async def checkpoint_validation(self, session_id: UUID, identity: str, validation) -> None:
        changed=await self.db.execute(update(AuthoringSession).where(AuthoringSession.id==session_id,
            AuthoringSession.pipeline_identity==identity,AuthoringSession.generated_draft.is_not(None),
            AuthoringSession.generator_attempt_id.is_not(None),AuthoringSession.solver_result.is_not(None),
            AuthoringSession.solver_attempt_id.is_not(None),AuthoringSession.validation_result.is_(None)).values(
            validation_result=validation.model_dump(mode="json"),semantic_status=validation.status,
            row_version=AuthoringSession.row_version+1))
        await self.db.flush()
        if changed.rowcount != 1: raise AuthoringConflict()
