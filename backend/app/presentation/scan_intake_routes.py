"""Dedicated, teacher-only scanned-paper intake HTTP boundary."""

from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from starlette.datastructures import UploadFile
from sqlalchemy import select

from app.application.capabilities import ASSESSMENT_SCAN_CHECK_MANAGE
from app.application.input_artifacts import ArtifactError, MAX_ARTIFACT_SIZE_BYTES
from app.application.principal import Principal
from app.application.scan_intake import ScanIntakeError, ScanIntakeService
from app.config import Settings, get_settings
from app.db.session import get_session
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.classroom_repository import SQLAlchemyClassroomAccessRepository
from app.infrastructure.scan_checking_models import (
    AssessmentScanBatch,
    ScanBatchArtifact,
    ScanPage,
)
from app.infrastructure.scan_intake_repository import SqlAlchemyScanIntakeRepository
from app.infrastructure.scan_page_pipeline import ScanPagePipeline
from app.application.scan_matching_service import ScanMatchingError, ScanMatchingService
from app.infrastructure.scan_matching_providers import AnthropicScanPageMatchingProvider
from app.presentation.auth_dependencies import (
    require_capability,
    require_trusted_origin,
)
from app.presentation.scan_intake_schemas import (
    ScanBatchCreate,
    CancelScanBatch,
    GroupingPageAssignment,
    GroupingPageOrder,
    GroupingConfirmation,
)
from app.application.scan_grouping import ScanGroupingError, ScanGroupingService
from app.infrastructure.scan_grouping_repository import SqlAlchemyScanGroupingRepository

router = APIRouter(
    prefix="/api/assessment-core",
    tags=["scan-intake"],
    dependencies=[Depends(require_trusted_origin)],
)
PrincipalDep = Depends(require_capability(ASSESSMENT_SCAN_CHECK_MANAGE))


class Access:
    def __init__(self, db, unrestricted):
        self.repo = SQLAlchemyClassroomAccessRepository(db)
        self.unrestricted = unrestricted

    async def can_access_class(self, class_group_id, actor_id):
        return await self.repo.can_access_class(
            class_group_id, actor_id, self.unrestricted
        )


def _service(db, principal):
    return ScanIntakeService(
        SqlAlchemyScanIntakeRepository(db), Access(db, "admin" in principal.roles)
    )


def _batch(row):
    return {
        "id": row.id,
        "assignment_id": row.assignment_id,
        "status": row.status.value if hasattr(row.status, "value") else row.status,
        "instruction": row.instruction_text,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def _authorized_batch(db, batch_id, principal):
    row = await db.get(AssessmentScanBatch, batch_id)
    if row is None or not await Access(db, "admin" in principal.roles).can_access_class(
        row.class_group_id, principal.user_id
    ):
        raise HTTPException(404, "scan_batch_not_found")
    return row


def _artifact(row, source):
    return {
        "id": row.id,
        "upload_position": row.upload_position,
        "original_filename": row.original_filename,
        "mime_type": source.mime_type,
        "size_bytes": source.size_bytes,
        "extraction_status": row.extraction_status,
        "page_count": row.page_count,
        "failure_code": row.failure_code,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("/assignments/{assignment_id}/scan-batches", status_code=201)
async def create_batch(
    assignment_id: UUID,
    payload: ScanBatchCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    try:
        return _batch(
            await _service(db, principal).create_batch(
                assignment_id=assignment_id,
                instruction_text=payload.instruction,
                request_key=idempotency_key,
                actor_user_id=principal.user_id,
            )
        )
    except ScanIntakeError as exc:
        raise _error(exc) from None


@router.get("/assignments/{assignment_id}/scan-batches")
async def list_batches(
    assignment_id: UUID, db=Depends(get_session), principal: Principal = PrincipalDep
):
    try:
        return {
            "items": [
                _batch(x)
                for x in await _service(db, principal).list_batches(
                    assignment_id, principal.user_id
                )
            ]
        }
    except ScanIntakeError as exc:
        raise _error(exc) from None


@router.get("/scan-batches/{batch_id}")
async def get_batch(
    batch_id: UUID, db=Depends(get_session), principal: Principal = PrincipalDep
):
    try:
        return _batch(
            await _service(db, principal).get_batch(batch_id, principal.user_id)
        )
    except ScanIntakeError as exc:
        raise _error(exc) from None


@router.post("/scan-batches/{batch_id}/cancel")
async def cancel(
    batch_id: UUID,
    payload: CancelScanBatch,
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    try:
        return _batch(
            await _service(db, principal).cancel_batch(
                batch_id=batch_id,
                actor_user_id=principal.user_id,
                reason=payload.reason,
            )
        )
    except ScanIntakeError as exc:
        raise _error(exc) from None


@router.post("/scan-batches/{batch_id}/artifacts", status_code=201)
async def upload(
    batch_id: UUID,
    request: Request,
    db=Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    form = await request.form()
    if (
        set(form.keys()) != {"file", "upload_position"}
        or len(form.getlist("file")) != 1
    ):
        raise HTTPException(422, "invalid_upload_form")
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise HTTPException(422, "file_required")
    try:
        position = int(form.get("upload_position"))
        content = await file.read(MAX_ARTIFACT_SIZE_BYTES + 1)
    except (TypeError, ValueError):
        raise HTTPException(422, "invalid_upload_position") from None
    try:
        row, source = await ScanPagePipeline(
            db, FilesystemArtifactStorage(settings.artifact_storage_path)
        ).upload(
            batch_id=batch_id,
            actor_id=principal.user_id,
            content=content,
            claimed_mime_type=file.content_type or "",
            upload_position=position,
            filename=file.filename or "upload",
        )
        return _artifact(row, source)
    except (ArtifactError, ScanIntakeError) as exc:
        raise _error(exc) from None
    finally:
        await file.close()


@router.get("/scan-batches/{batch_id}/artifacts")
async def artifacts(
    batch_id: UUID, db=Depends(get_session), principal: Principal = PrincipalDep
):
    await _authorized_batch(db, batch_id, principal)
    rows = (
        await db.execute(
            select(ScanBatchArtifact, InputArtifact)
            .join(
                InputArtifact, InputArtifact.id == ScanBatchArtifact.input_artifact_id
            )
            .where(ScanBatchArtifact.batch_id == batch_id)
            .order_by(ScanBatchArtifact.upload_position)
        )
    ).all()
    return {"items": [_artifact(a, i) for a, i in rows]}


@router.get("/scan-batches/{batch_id}/pages")
async def pages(
    batch_id: UUID, db=Depends(get_session), principal: Principal = PrincipalDep
):
    await _authorized_batch(db, batch_id, principal)
    rows = (
        await db.scalars(
            select(ScanPage)
            .join(ScanBatchArtifact)
            .where(ScanBatchArtifact.batch_id == batch_id)
            .order_by(ScanBatchArtifact.upload_position, ScanPage.source_page_index)
        )
    ).all()
    return {
        "items": [
            {
                "id": p.id,
                "source_artifact_id": p.batch_artifact_id,
                "source_page_index": p.source_page_index,
                "width_px": p.width_px,
                "height_px": p.height_px,
                "status": p.status,
                "failure_code": p.failure_code,
                "content_fingerprint": p.content_fingerprint,
                "content_url": f"/api/assessment-core/scan-pages/{p.id}/content"
                if p.status == "ready"
                else None,
            }
            for p in rows
        ]
    }


@router.post("/scan-batches/{batch_id}/extract")
async def extract(
    batch_id: UUID,
    db=Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    try:
        return await ScanPagePipeline(
            db, FilesystemArtifactStorage(settings.artifact_storage_path)
        ).extract(batch_id=batch_id, actor_id=principal.user_id)
    except ScanIntakeError as exc:
        raise _error(exc) from None


@router.get("/scan-pages/{page_id}/content")
async def page_content(
    page_id: UUID,
    db=Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: Principal = PrincipalDep,
):
    result = (
        await db.execute(
            select(ScanPage, ScanBatchArtifact, AssessmentScanBatch)
            .join(ScanBatchArtifact, ScanBatchArtifact.id == ScanPage.batch_artifact_id)
            .join(
                AssessmentScanBatch,
                AssessmentScanBatch.id == ScanBatchArtifact.batch_id,
            )
            .where(ScanPage.id == page_id)
        )
    ).first()
    if result is None or not await Access(
        db, "admin" in principal.roles
    ).can_access_class(result[2].class_group_id, principal.user_id):
        raise HTTPException(404, "scan_page_not_found")
    page = result[0]
    if page.status != "ready" or page.derived_render_artifact_id is None:
        raise HTTPException(404, "scan_page_not_ready")
    derived = await db.get(InputArtifact, page.derived_render_artifact_id)
    if derived is None or derived.mime_type != "image/png":
        raise HTTPException(404, "scan_page_not_ready")
    content = await FilesystemArtifactStorage(settings.artifact_storage_path).read(
        derived.storage_reference
    )
    return Response(
        content,
        media_type="image/png",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="scan-page-{page.id}.png"',
            "Cache-Control": "private, no-store",
        },
    )


def _error(exc):
    code = exc.code
    status = (
        404
        if code.endswith("not_found")
        else 413
        if code in {"artifact_too_large", "artifact_too_small"}
        else 415
        if code in {"invalid_artifact_signature", "unsupported_artifact_type"}
        else 409
        if code
        in {
            "duplicate_upload_position",
            "idempotency_conflict",
            "artifact_attachment_not_allowed",
            "extraction_not_allowed",
        }
        else 422
    )
    return HTTPException(status, code)


def _matching_provider(request: Request, settings: Settings):
    injected = getattr(request.app.state, "scan_matching_provider", None)
    if injected is not None:
        return injected
    if (
        settings.scan_matching_provider != "anthropic"
        or not settings.anthropic_credential
    ):
        raise HTTPException(503, "scan_matching_provider_not_configured")
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(
        api_key=settings.anthropic_credential, base_url=settings.anthropic_base_url
    )
    return AnthropicScanPageMatchingProvider(client, settings.scan_matching_model)


@router.post("/scan-batches/{batch_id}/match")
async def match_pages(
    batch_id: UUID,
    request: Request,
    db=Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    try:
        return await ScanMatchingService(
            db,
            FilesystemArtifactStorage(settings.artifact_storage_path),
            _matching_provider(request, settings),
        ).run(batch_id, principal.user_id)
    except ScanMatchingError as exc:
        raise HTTPException(
            409
            if exc.code.startswith("matching_") or exc.code.startswith("scan_")
            else 422,
            exc.code,
        ) from None


@router.get("/scan-batches/{batch_id}/matching")
async def matching_results(
    batch_id: UUID,
    request: Request,
    db=Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    try:
        # Reading never invokes this provider; a placeholder avoids requiring credentials.
        provider = (
            getattr(request.app.state, "scan_matching_provider", None)
            or type(
                "ReadOnlyProvider", (), {"provider_id": "none", "model_id": "none"}
            )()
        )
        return await ScanMatchingService(
            db, FilesystemArtifactStorage(settings.artifact_storage_path), provider
        ).read(batch_id)
    except ScanMatchingError as exc:
        raise HTTPException(404, exc.code) from None


def _grouping_service(db):
    return ScanGroupingService(SqlAlchemyScanGroupingRepository(db))


def _grouping_error(exc):
    if exc.code.endswith("not_found"):
        return HTTPException(404, exc.code)
    if exc.code in {
        "grouping_revision_conflict",
        "grouping_based_on_stale_matching",
        "grouping_immutable",
        "grouping_initialization_not_allowed",
        "grouping_confirmation_not_allowed",
    }:
        return HTTPException(409, exc.code)
    return HTTPException(422, exc.code)


@router.post("/scan-batches/{batch_id}/grouping", status_code=201)
async def initialize_grouping(
    batch_id: UUID, db=Depends(get_session), principal: Principal = PrincipalDep
):
    await _authorized_batch(db, batch_id, principal)
    try:
        return await _grouping_service(db).initialize(batch_id, principal.user_id)
    except ScanGroupingError as exc:
        raise _grouping_error(exc) from None


@router.get("/scan-batches/{batch_id}/grouping")
async def get_grouping(
    batch_id: UUID, db=Depends(get_session), principal: Principal = PrincipalDep
):
    await _authorized_batch(db, batch_id, principal)
    try:
        return await _grouping_service(db).read(batch_id)
    except ScanGroupingError as exc:
        raise _grouping_error(exc) from None


@router.patch("/scan-batches/{batch_id}/grouping/pages/{page_id}")
async def assign_grouping_page(
    batch_id: UUID,
    page_id: UUID,
    payload: GroupingPageAssignment,
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    try:
        return await _grouping_service(db).assign(
            batch_id,
            page_id,
            payload.assignment_participant_id,
            payload.expected_revision,
            payload.expected_row_version,
            principal.user_id,
        )
    except ScanGroupingError as exc:
        raise _grouping_error(exc) from None


@router.put("/scan-batches/{batch_id}/grouping/groups/{participant_id}/page-order")
async def reorder_grouping_pages(
    batch_id: UUID,
    participant_id: UUID,
    payload: GroupingPageOrder,
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    try:
        return await _grouping_service(db).reorder(
            batch_id,
            participant_id,
            payload.page_ids,
            payload.expected_revision,
            payload.expected_row_version,
            principal.user_id,
        )
    except ScanGroupingError as exc:
        raise _grouping_error(exc) from None


@router.post("/scan-batches/{batch_id}/grouping/confirm")
async def confirm_grouping(
    batch_id: UUID,
    payload: GroupingConfirmation,
    db=Depends(get_session),
    principal: Principal = PrincipalDep,
):
    await _authorized_batch(db, batch_id, principal)
    try:
        return await _grouping_service(db).confirm(
            batch_id,
            payload.expected_revision,
            payload.expected_row_version,
            principal.user_id,
        )
    except ScanGroupingError as exc:
        raise _grouping_error(exc) from None
