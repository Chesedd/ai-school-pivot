"""Transactional repository for Content Bank authoring attempts."""
from uuid import UUID, uuid4
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.authoring import AuthoringConflict, ExecutionRequest, FailureCode, FrozenCatalogContext, ProviderResult, RETRYABLE, thaw_json
from app.infrastructure.authoring_models import AuthoringProviderAttempt, AuthoringSession


class AuthoringRepository:
    def __init__(self, db: AsyncSession): self.db=db

    async def create_session(self, owner_id: UUID, request, catalog: FrozenCatalogContext) -> AuthoringSession:
        catalog.validate_request(request)
        row=AuthoringSession(id=uuid4(),owner_id=owner_id,schema_version=request.schema_version,policy_version=request.policy_version,
            frozen_request=request.model_dump(mode="json"),request_fingerprint=request.fingerprint,frozen_allowlist=catalog.as_json())
        self.db.add(row); await self.db.flush(); return row

    async def create_attempt(self, session_id: UUID, execution: ExecutionRequest) -> tuple[AuthoringProviderAttempt,bool]:
        existing=await self.db.scalar(select(AuthoringProviderAttempt).where(AuthoringProviderAttempt.session_id==session_id,AuthoringProviderAttempt.idempotency_key==execution.idempotency_key))
        if existing:
            if existing.request_fingerprint != execution.request_fingerprint: raise AuthoringConflict()
            return existing,False
        # Lock aggregate: serializes retry numbering and creation for a session.
        session=await self.db.scalar(select(AuthoringSession).where(AuthoringSession.id==session_id).with_for_update())
        if session is None: raise AuthoringConflict()
        if session.request_fingerprint != execution.request_fingerprint: raise AuthoringConflict()
        existing=await self.db.scalar(select(AuthoringProviderAttempt).where(AuthoringProviderAttempt.session_id==session_id,AuthoringProviderAttempt.idempotency_key==execution.idempotency_key))
        if existing:
            if existing.request_fingerprint != execution.request_fingerprint: raise AuthoringConflict()
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
        values={"status":"invalid_output" if invalid_output else "succeeded","finished_at":func.clock_timestamp(),"provider_request_id":result.provider_request_id,"response_hash":result.response_hash,"latency_ms":result.latency_ms,"input_tokens":result.usage.input_tokens,"output_tokens":result.usage.output_tokens,"cached_tokens":result.usage.cached_tokens,"cost_amount":result.cost.amount,"currency":result.cost.currency,"pricing_version":result.cost.pricing_version,"pricing_source":result.cost.pricing_source}
        changed=await self.db.execute(update(AuthoringProviderAttempt).where(AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.status=="running").values(**values)); await self.db.flush(); return changed.rowcount==1

    async def finalize_failure(self, attempt_id: UUID, code: FailureCode) -> bool:
        status="failed_retryable" if code in RETRYABLE else "failed_terminal"
        changed=await self.db.execute(update(AuthoringProviderAttempt).where(AuthoringProviderAttempt.id==attempt_id,AuthoringProviderAttempt.status=="running").values(status=status,failure_code=code.value,finished_at=func.clock_timestamp())); await self.db.flush(); return changed.rowcount==1
