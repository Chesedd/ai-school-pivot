"""Application facade and safe read models for semantic authoring."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.application.authoring import (AuthoringError, AuthoringRequestV1, FrozenCatalogContext,
    ModelRoute, ProviderFailure, ProviderRegistry, RETRYABLE, validate_authoring_request)
from app.application.authoring_pipeline import SemanticPipelineService
from app.infrastructure.authoring_models import AuthoringProviderAttempt, AuthoringSession
from app.infrastructure.authoring_repository import AuthoringRepository
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Topic


class AuthoringApiError(Exception):
    def __init__(self, code: str, status: int, message: str | None = None, *, retryable: bool | None = None):
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(message or code)


@dataclass(frozen=True)
class AuthoringRouteCatalog:
    """Immutable exact-match public execution allowlist."""
    routes: tuple[ModelRoute, ...]
    def __post_init__(self): object.__setattr__(self, "_index", MappingProxyType({(r.provider_id,r.model_id):r for r in self.routes}))
    def get(self, provider_id: str, model_id: str) -> ModelRoute:
        try: return self._index[(provider_id,model_id)]
        except KeyError: raise AuthoringApiError("authoring_route_not_allowed",422) from None
    def public(self):
        return [{"provider_id":r.provider_id,"model_id":r.model_id,"supported_roles":["generator","solver"],"structured_output":True} for r in self.routes]


def execution_status(row, attempts) -> str:
    if row.validation_result is not None: return "completed"
    running=next((a for a in attempts if a.status=="running"),None)
    if running: return f"{running.role}_running"
    failure=next((a for a in reversed(attempts) if a.status in {"failed_retryable","failed_terminal","invalid_output"}),None)
    if failure:
        suffix="retryable_failure" if failure.status=="failed_retryable" else "invalid" if failure.status=="invalid_output" else "failed"
        return f"{failure.role}_{suffix}"
    if row.solver_result is not None: return "validation_pending"
    if row.generated_draft is not None: return "solver_pending"
    return "created"


def attempt_dto(a):
    return {"id":a.id,"role":a.role,"attempt_number":a.attempt_number,"provider_id":a.provider_id,"model_id":a.model_id,
        "status":a.status,"failure_code":a.failure_code,"retryable":a.failure_code in {x.value for x in RETRYABLE} if a.failure_code else None,
        "started_at":a.started_at,"finished_at":a.finished_at,"latency_ms":a.latency_ms,
        "usage":{"input_tokens":a.input_tokens,"cache_read_tokens":a.cache_read_tokens,"cache_write_tokens":a.cache_write_tokens,"output_tokens":a.output_tokens},
        "cost_amount":a.cost_amount,"currency":a.currency,"pricing_version":a.pricing_version,"provider_request_id":a.provider_request_id}


def session_dto(row, attempts):
    totals=defaultdict(lambda:Decimal("0"))
    for a in attempts:
        if a.cost_amount is not None and a.currency: totals[a.currency]+=a.cost_amount
    return {"id":row.id,"schema_version":"authoring_session_response.v1","created_at":row.created_at,
        "request":row.frozen_request,"request_fingerprint":row.request_fingerprint,"execution_status":execution_status(row,attempts),
        "semantic_status":row.semantic_status,"generator_route":row.generator_route,"solver_route":row.solver_route,
        "preview_available":row.generated_draft is not None,"attempt_count":len(attempts),
        "cost_totals":[{"currency":k,"amount":v} for k,v in sorted(totals.items())]}


class AuthoringApplicationService:
    def __init__(self, session, providers: ProviderRegistry, routes: AuthoringRouteCatalog):
        self.db,self.repo,self.providers,self.routes=session,AuthoringRepository(session),providers,routes
    async def _owned(self, session_id: UUID, owner_id: UUID):
        row=await self.db.scalar(select(AuthoringSession).where(AuthoringSession.id==session_id,AuthoringSession.owner_id==owner_id))
        if row is None: raise AuthoringApiError("authoring_session_not_found",404)
        return row
    async def _attempts(self,sid):
        return list((await self.db.scalars(select(AuthoringProviderAttempt).where(AuthoringProviderAttempt.session_id==sid).order_by(AuthoringProviderAttempt.created_at,AuthoringProviderAttempt.attempt_number))).all())
    async def create(self, payload, owner_id):
        subject=await self.db.get(Subject,payload.subject_id); grade=await self.db.get(Grade,payload.grade_id)
        topic=await self.db.scalar(select(Topic).where(Topic.id==payload.topic_id,Topic.subject_id==payload.subject_id,Topic.grade_id==payload.grade_id))
        subtopic=None
        if payload.subtopic_id: subtopic=await self.db.scalar(select(Subtopic).where(Subtopic.id==payload.subtopic_id,Subtopic.topic_id==payload.topic_id))
        skills=list((await self.db.scalars(select(Skill).where(Skill.id.in_(payload.skill_ids),Skill.subtopic_id==payload.subtopic_id))).all()) if payload.subtopic_id else []
        if not subject or not grade or not topic or (payload.subtopic_id and not subtopic) or len(skills)!=len(set(payload.skill_ids)):
            raise AuthoringApiError("catalog_reference_not_allowed",422)
        values=payload.model_dump(exclude={"subject_id","grade_id","topic_id","subtopic_id","skill_ids"})
        values.update(schema_version="authoring-request.v1",subject=subject.code,grade=f"g{grade.number}",topic=topic.code,
                      subtopic=subtopic.code if subtopic else None,skills=tuple(sorted(x.code for x in skills)),policy_version="authoring-v1")
        request=validate_authoring_request(values); catalog=FrozenCatalogContext(request.subject,request.grade,request.topic,request.subtopic,request.skills)
        row=await self.repo.create_session(owner_id,request,catalog); await self.repo.commit()
        return session_dto(row,[])
    async def get(self,sid,owner):
        row=await self._owned(sid,owner); return session_dto(row,await self._attempts(sid))
    async def list(self,owner,offset,limit):
        rows=list((await self.db.scalars(select(AuthoringSession).where(AuthoringSession.owner_id==owner).order_by(AuthoringSession.created_at.desc()).offset(offset).limit(limit))).all())
        return {"offset":offset,"limit":limit,"items":[session_dto(r,await self._attempts(r.id)) for r in rows]}
    async def run(self,sid,owner,generator,solver,key,correlation):
        row=await self._owned(sid,owner); g=self.routes.get(generator.provider_id,generator.model_id); s=self.routes.get(solver.provider_id,solver.model_id)
        request=AuthoringRequestV1.model_validate(row.frozen_request)
        try: await SemanticPipelineService(self.repo,self.providers).run(sid,request,g,s,correlation_id=correlation,idempotency_key=key)
        except ProviderFailure as e: raise AuthoringApiError(e.code.value,503 if e.code in RETRYABLE else 422,retryable=e.code in RETRYABLE) from None
        except AuthoringError as e: raise AuthoringApiError(e.code,409 if e.code in {"pipeline_in_progress","request_conflict"} else 422) from None
        await self.db.refresh(row); return session_dto(row,await self._attempts(sid))
    async def preview(self,sid,owner):
        row=await self._owned(sid,owner)
        if row.generated_draft is None: raise AuthoringApiError("authoring_preview_not_ready",409)
        return {"schema_version":"authoring_preview_response.v1","session_id":sid,"semantic_status":row.semantic_status,
            "generated_draft":row.generated_draft,"solver_result":row.solver_result,"validation_result":row.validation_result}
    async def attempts(self,sid,owner):
        await self._owned(sid,owner); return {"items":[attempt_dto(a) for a in await self._attempts(sid)]}
