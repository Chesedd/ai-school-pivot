"""Resolve retention/read races before constructing any binary response."""

from fastapi import HTTPException
from sqlalchemy import text

from app.infrastructure.scan_storage_retention_repository import (
    retention_attempt_exists,
)


async def read_scan_content(
    db, storage, reference, *, batch_artifact_id=None, scan_page_id=None
):
    identity = {"batch_artifact_id": batch_artifact_id, "scan_page_id": scan_page_id}
    code = (
        "scan_page_content_purged" if scan_page_id else "scan_artifact_content_purged"
    )
    # Only finalized purges should hide bytes when a backend attempt failed.
    column = "scan_page_id" if scan_page_id else "batch_artifact_id"
    if await db.scalar(
        text(
            f"SELECT EXISTS (SELECT 1 FROM scan_storage_retention_entries WHERE {column}=:id AND state='purged')"
        ),
        {"id": scan_page_id or batch_artifact_id},
    ):
        raise HTTPException(410, code)
    try:
        return await storage.read(reference)
    except FileNotFoundError:
        # Claim was committed before deletion. A new READ COMMITTED query sees it
        # even when this request authorized/read metadata before the claim.
        if await retention_attempt_exists(db, **identity):
            raise HTTPException(410, code) from None
        raise HTTPException(404, "scan_content_missing") from None
    except (OSError, ValueError):
        raise HTTPException(503, "scan_content_unavailable") from None
