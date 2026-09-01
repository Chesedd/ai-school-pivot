"""Capability-protected explicit curriculum catalog proposal API."""

from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response

from app.application.capabilities import CATALOG_MANAGE, CATALOG_PROPOSE
from app.application.catalog_resolution import CatalogResolutionService
from app.application.catalog_proposals import CatalogProposalCommand, CatalogProposalService
from app.application.principal import Principal
from app.db.session import async_session_factory
from app.infrastructure.catalog_repository import SQLAlchemyCatalogRepository
from app.presentation.auth_dependencies import require_capability, require_trusted_origin
from app.presentation.catalog_proposal_schemas import AdminProposalResponse, CatalogProposalRequest, CatalogProposalResponse, MergeProposalRequest, RejectProposalRequest

router = APIRouter(prefix="/api/catalog", tags=["catalog"], dependencies=[Depends(require_trusted_origin)])


@router.post("/proposals", response_model=CatalogProposalResponse, status_code=201)
async def propose_catalog_value(
    payload: CatalogProposalRequest,
    response: Response,
    principal: Principal = Depends(require_capability(CATALOG_PROPOSE)),
) -> CatalogProposalResponse:
    values = payload.model_dump()
    command = CatalogProposalCommand(**values)
    async with async_session_factory() as session:
        async with session.begin():
            result = await CatalogProposalService(SQLAlchemyCatalogRepository(session)).propose_catalog_value(
                principal.user_id, command
            )
    response.status_code = 201 if result.outcome == "created_provisional" else 200
    return CatalogProposalResponse.model_validate(result)


@router.get("/proposals", response_model=list[AdminProposalResponse])
async def list_catalog_proposals(
    kind: Literal["subject", "grade", "topic", "subtopic", "skill"] | None = None,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    _: Principal = Depends(require_capability(CATALOG_MANAGE)),
) -> list[AdminProposalResponse]:
    async with async_session_factory() as session:
        rows = await CatalogResolutionService(SQLAlchemyCatalogRepository(session)).list_proposals(kind, offset, limit)
    return [AdminProposalResponse.model_validate(row) for row in rows]


@router.post("/proposals/{kind}/{proposal_id}/confirm", response_model=AdminProposalResponse)
async def confirm_catalog_proposal(kind: Literal["subject", "grade", "topic", "subtopic", "skill"], proposal_id: UUID,
    principal: Principal = Depends(require_capability(CATALOG_MANAGE))) -> AdminProposalResponse:
    async with async_session_factory() as session, session.begin():
        row = await CatalogResolutionService(SQLAlchemyCatalogRepository(session)).confirm(kind, proposal_id, principal.user_id)
    return AdminProposalResponse.model_validate(row)


@router.post("/proposals/{kind}/{proposal_id}/merge", response_model=AdminProposalResponse)
async def merge_catalog_proposal(kind: Literal["subject", "grade", "topic", "subtopic", "skill"], proposal_id: UUID, payload: MergeProposalRequest,
    principal: Principal = Depends(require_capability(CATALOG_MANAGE))) -> AdminProposalResponse:
    async with async_session_factory() as session, session.begin():
        row = await CatalogResolutionService(SQLAlchemyCatalogRepository(session)).merge(kind, proposal_id, payload.target_id, payload.reason, principal.user_id)
    return AdminProposalResponse.model_validate(row)


@router.post("/proposals/{kind}/{proposal_id}/reject", response_model=AdminProposalResponse)
async def reject_catalog_proposal(kind: Literal["subject", "grade", "topic", "subtopic", "skill"], proposal_id: UUID, payload: RejectProposalRequest,
    principal: Principal = Depends(require_capability(CATALOG_MANAGE))) -> AdminProposalResponse:
    async with async_session_factory() as session, session.begin():
        row = await CatalogResolutionService(SQLAlchemyCatalogRepository(session)).reject(kind, proposal_id, payload.reason, principal.user_id)
    return AdminProposalResponse.model_validate(row)
