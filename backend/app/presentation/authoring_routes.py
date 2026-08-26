"""Thin HTTP adapter for Content Bank semantic authoring."""
from functools import lru_cache
from decimal import Decimal
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Header, Query, Response

from app.application.authoring import ModelRoute, Price, PricingCatalog
from app.application.authoring_api import AuthoringApplicationService, AuthoringRouteCatalog
from app.application.authoring_promotion import PromoteAuthoringArtifactService
from app.application.authoring_quality import AuthoringQualityReportV1, AuthoringQualityService
from app.application.authoring_review import AuthoringReviewService
from app.application.authoring_workspace import AuthoringWorkspaceService, AuthoringWorkspaceViewV1
from app.config import Settings, get_settings
from app.db.session import get_session
from app.infrastructure.authoring_providers import production_registry
from app.presentation.authoring_schemas import (AuthoringAcceptanceRequest, AuthoringCreateRequest,
    AuthoringPromotionResponseV1, AuthoringRejectRequest, AuthoringReviewEditRequest,
    AuthoringReviewResponseV1, AuthoringReviewHistoryResponseV1,
    AuthoringReviewDiffResponseV1, RunRequest, SessionResponse)

router=APIRouter(prefix="/api/content-bank/authoring",tags=["authoring"])

@lru_cache
def route_catalog():
    settings=get_settings(); routes=[]
    for item in settings.authoring_routes.split(","):
        provider,model=item.strip().split(":",1); routes.append(ModelRoute(provider,model))
    return AuthoringRouteCatalog(tuple(routes))

def provider_registry(settings:Settings=Depends(get_settings),catalog:AuthoringRouteCatalog=Depends(route_catalog)):
    # Rates are deliberately configuration-versioned placeholders; only allowlisted routes are executable.
    prices={(r.provider_id,r.model_id):Price("USD","api-v1","server-config",Decimal("0"),Decimal("0"),Decimal("0"),Decimal("0")) for r in catalog.routes}
    anthropic_key = (None if settings.anthropic_api_key is None else
        settings.anthropic_api_key.get_secret_value())
    return production_registry(PricingCatalog(prices), openai_api_key=settings.openai_api_key,
        anthropic_api_key=anthropic_key)

def service(db=Depends(get_session),providers=Depends(provider_registry),catalog=Depends(route_catalog)):
    return AuthoringApplicationService(db,providers,catalog)

@router.get("/routes")
async def routes(catalog=Depends(route_catalog)): return {"items":catalog.public()}
@router.post("/sessions",response_model=SessionResponse,status_code=201)
async def create(payload:AuthoringCreateRequest,response:Response,svc=Depends(service),settings:Settings=Depends(get_settings)):
    result=await svc.create(payload,settings.content_bank_dev_actor_id); response.headers["Location"]=f"/api/content-bank/authoring/sessions/{result['id']}"; return result
@router.get("/sessions")
async def list_sessions(offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),svc=Depends(service),settings:Settings=Depends(get_settings)): return await svc.list(settings.content_bank_dev_actor_id,offset,limit)
@router.get("/sessions/{session_id}",response_model=SessionResponse)
async def get(session_id:UUID,svc=Depends(service),settings:Settings=Depends(get_settings)): return await svc.get(session_id,settings.content_bank_dev_actor_id)
@router.post("/sessions/{session_id}/run",response_model=SessionResponse)
async def run(session_id:UUID,payload:RunRequest,idempotency_key:str=Header(...,alias="Idempotency-Key",min_length=1,max_length=80),svc=Depends(service),settings:Settings=Depends(get_settings)):
    return await svc.run(session_id,settings.content_bank_dev_actor_id,payload.generator_route,payload.solver_route,idempotency_key,str(uuid4()))
@router.get("/sessions/{session_id}/preview")
async def preview(session_id:UUID,svc=Depends(service),settings:Settings=Depends(get_settings)): return await svc.preview(session_id,settings.content_bank_dev_actor_id)
@router.get("/sessions/{session_id}/attempts")
async def attempts(session_id:UUID,svc=Depends(service),settings:Settings=Depends(get_settings)): return await svc.attempts(session_id,settings.content_bank_dev_actor_id)

@router.get("/sessions/{session_id}/workspace", response_model=AuthoringWorkspaceViewV1)
async def workspace(session_id:UUID,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    """Return the owned, read-only reviewer projection without invoking the pipeline."""
    return await AuthoringWorkspaceService(db).get(session_id, settings.content_bank_dev_actor_id)

@router.post("/sessions/{session_id}/review",response_model=AuthoringReviewResponseV1)
async def start_review(session_id:UUID,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await AuthoringReviewService(db).start(session_id,settings.content_bank_dev_actor_id)

@router.get("/sessions/{session_id}/review",response_model=AuthoringReviewResponseV1)
async def get_review(session_id:UUID,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await AuthoringReviewService(db).get(session_id,settings.content_bank_dev_actor_id)

@router.get("/sessions/{session_id}/review/history",response_model=AuthoringReviewHistoryResponseV1)
async def review_history(session_id:UUID,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await AuthoringReviewService(db).history(session_id,settings.content_bank_dev_actor_id)

@router.get("/sessions/{session_id}/review/diff",response_model=AuthoringReviewDiffResponseV1)
async def review_diff(session_id:UUID,from_revision:int|None=Query(None,ge=0),
        to_revision:int|None=Query(None,ge=0),db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await AuthoringReviewService(db).diff(session_id,settings.content_bank_dev_actor_id,
        from_revision,to_revision)

@router.get("/sessions/{session_id}/quality",response_model=AuthoringQualityReportV1)
async def get_quality(session_id:UUID,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await AuthoringQualityService(db).get(session_id,settings.content_bank_dev_actor_id)

@router.put("/sessions/{session_id}/review",response_model=AuthoringReviewResponseV1)
async def edit_review(session_id:UUID,payload:AuthoringReviewEditRequest,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await AuthoringReviewService(db).edit(session_id,settings.content_bank_dev_actor_id,payload.draft,payload.version)

@router.post("/sessions/{session_id}/reject",response_model=AuthoringReviewResponseV1)
async def reject(session_id:UUID,payload:AuthoringRejectRequest,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await AuthoringReviewService(db).reject(session_id,settings.content_bank_dev_actor_id,reason=payload.reason)

@router.post("/sessions/{session_id}/accept",response_model=AuthoringPromotionResponseV1)
async def accept(session_id:UUID,payload:AuthoringAcceptanceRequest,db=Depends(get_session),settings:Settings=Depends(get_settings)):
    return await PromoteAuthoringArtifactService(db).accept(session_id,settings.content_bank_dev_actor_id,
        acceptance_note=payload.acceptance_note,confirm_questionable=payload.confirm_questionable,
        warning_override_reason=payload.warning_override_reason,revision_number=payload.revision_number)
