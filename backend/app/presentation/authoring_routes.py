"""Thin HTTP adapter for Content Bank semantic authoring."""
from functools import lru_cache
from decimal import Decimal
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Header, Query, Response

from app.application.authoring import ModelRoute, Price, PricingCatalog
from app.application.authoring_api import AuthoringApplicationService, AuthoringRouteCatalog
from app.config import Settings, get_settings
from app.db.session import get_session
from app.infrastructure.authoring_providers import production_registry
from app.presentation.authoring_schemas import AuthoringCreateRequest, RunRequest, SessionResponse

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
    return production_registry(PricingCatalog(prices),openai_api_key=settings.openai_api_key,anthropic_api_key=settings.anthropic_api_key)

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
