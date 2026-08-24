import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unit:unit@localhost/unit")
os.environ.setdefault("CONTENT_BANK_DEV_ACTOR_ID", "00000000-0000-4000-8000-000000000001")
os.environ.setdefault("ASSESSMENT_DEV_STUDENT_ID", "00000000-0000-4000-8000-000000000002")

from app.presentation.image_artifact_routes import upload


class Form:
    def __init__(self, entries):
        self.entries = entries

    def __iter__(self):
        return iter(dict(self.entries))

    def get(self, key):
        values = self.getlist(key)
        return values[-1] if values else None

    def getlist(self, key):
        return [value for field, value in self.entries if field == key]


class Request:
    def __init__(self, entries):
        self.entries = entries

    async def form(self):
        return Form(self.entries)


@pytest.mark.asyncio
async def test_upload_rejects_server_owned_metadata_fields():
    upload_service = AsyncMock()
    request = Request([("file", object()), ("owner_id", str(uuid4()))])

    with pytest.raises(HTTPException, match="unexpected upload field") as error:
        await upload(request, upload_service, SimpleNamespace())

    assert error.value.status_code == 422
    upload_service.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_rejects_duplicate_file_fields():
    upload_service = AsyncMock()
    request = Request([("file", object()), ("file", object())])

    with pytest.raises(HTTPException, match="duplicate upload field") as error:
        await upload(request, upload_service, SimpleNamespace())

    assert error.value.status_code == 422
    upload_service.upload.assert_not_awaited()
