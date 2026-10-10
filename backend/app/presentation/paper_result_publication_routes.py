"""Teacher publication and ownership-scoped student result/page boundaries."""
# ruff: noqa: B008

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.application.capabilities import STUDENT_RESULTS_READ
from app.application.paper_result_publication import (
    PaperPublicationError,
    PublishPaper,
    SelectedPublications,
)
from app.application.principal import Principal
from app.config import Settings, get_settings
from app.db.session import get_session
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from app.infrastructure.scan_checking_models import PaperSubmission
from app.presentation.auth_dependencies import (
    require_capability,
    require_student_identity,
    require_trusted_origin,
)
from app.presentation.scan_intake_routes import PrincipalDep, _authorized_batch

router = APIRouter(
    prefix="/api/assessment-core",
    tags=["paper-publication"],
    dependencies=[Depends(require_trusted_origin)],
)


def _student(
    principal: Principal = Depends(require_capability(STUDENT_RESULTS_READ)),
    student_id: UUID = Depends(require_student_identity),
):
    if "student" not in principal.roles:
        raise HTTPException(403, "student_identity_required")
    return student_id


async def _teacher_paper(db, paper_id, principal):
    paper = await db.get(PaperSubmission, paper_id)
    if paper is None:
        raise HTTPException(404, "paper_submission_not_found")
    await _authorized_batch(db, paper.batch_id, principal)


async def _action(db, operation, *, commit=False):
    try:
        result = await operation
        if commit:
            await db.commit()
        return result
    except PaperPublicationError as exc:
        await db.rollback()
        raise HTTPException(exc.status, exc.code) from None


@router.post("/paper-submissions/{paper_submission_id}/publications")
async def publish_paper(
    paper_submission_id: UUID,
    payload: PublishPaper,
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    await _teacher_paper(db, paper_submission_id, principal)
    return await _action(
        db,
        PaperResultPublicationRepository(db).publish(
            paper_submission_id, payload.approved_review_revision_id, principal.user_id
        ),
        commit=True,
    )


@router.get("/paper-submissions/{paper_submission_id}/publications")
async def publication_history(
    paper_submission_id: UUID,
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    await _teacher_paper(db, paper_submission_id, principal)
    return {
        "items": await _action(
            db, PaperResultPublicationRepository(db).history(paper_submission_id)
        )
    }


@router.post("/scan-batches/{batch_id}/publications/selected")
async def publish_selected(
    batch_id: UUID,
    payload: SelectedPublications,
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    return {
        "items": await _action(
            db,
            PaperResultPublicationRepository(db).publish_selected(
                payload.entries, principal.user_id, batch_id
            ),
            commit=True,
        )
    }


@router.post("/scan-batches/{batch_id}/publications/bulk")
async def publish_bulk(
    batch_id: UUID, db=Depends(get_session), principal: Principal = PrincipalDep
):
    await _authorized_batch(db, batch_id, principal)
    return {
        "items": await _action(
            db,
            PaperResultPublicationRepository(db).bulk(batch_id, principal.user_id),
            commit=True,
        )
    }


@router.get("/student/paper-submissions/{paper_submission_id}/result")
async def student_result(
    paper_submission_id: UUID,
    db=Depends(get_session),
    student_id: UUID = Depends(_student),
):
    return await _action(
        db,
        PaperResultPublicationRepository(db).student_result(
            paper_submission_id, student_id
        ),
    )


@router.get("/student/paper-results")
async def student_results(
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    db=Depends(get_session),
    student_id: UUID = Depends(_student),
):
    return {
        "items": await _action(
            db,
            PaperResultPublicationRepository(db).student_list(
                student_id, offset, limit
            ),
        )
    }


@router.get("/student/paper-submissions/{paper_submission_id}/pages/{scan_page_id}")
async def student_page(
    paper_submission_id: UUID,
    scan_page_id: UUID,
    db=Depends(get_session),
    student_id: UUID = Depends(_student),
    settings: Settings = Depends(get_settings),
):
    content = await _action(
        db,
        PaperResultPublicationRepository(db).student_page(
            paper_submission_id,
            scan_page_id,
            student_id,
            FilesystemArtifactStorage(settings.artifact_storage_path),
        ),
    )
    return Response(
        content,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="scan-page-{scan_page_id}.png"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
