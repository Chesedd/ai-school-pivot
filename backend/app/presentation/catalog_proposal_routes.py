"""Capability-protected explicit curriculum catalog proposal API."""

from fastapi import APIRouter, Depends, Response

from app.application.capabilities import CATALOG_PROPOSE
from app.application.catalog_proposals import CatalogProposalCommand, CatalogProposalService
from app.application.principal import Principal
from app.db.session import async_session_factory
from app.infrastructure.catalog_repository import SQLAlchemyCatalogRepository
from app.presentation.auth_dependencies import require_capability, require_trusted_origin
from app.presentation.catalog_proposal_schemas import CatalogProposalRequest, CatalogProposalResponse

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
