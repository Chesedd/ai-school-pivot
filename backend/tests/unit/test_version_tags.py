from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.managed_tags import ManagedTagService, TagError


async def test_replace_rejects_duplicate_ids_before_database_access():
    tag_id = uuid4()
    service = ManagedTagService(AsyncMock())
    with pytest.raises(TagError) as caught:
        await service.replace_version_tags(uuid4(), [tag_id, tag_id], datetime.now(timezone.utc), uuid4())
    assert (caught.value.code, caught.value.status) == ("duplicate_tag_assignment", 400)


async def test_replace_rejects_more_than_eight_before_database_access():
    service = ManagedTagService(AsyncMock())
    with pytest.raises(TagError) as caught:
        await service.replace_version_tags(uuid4(), [uuid4() for _ in range(9)], datetime.now(timezone.utc), uuid4())
    assert (caught.value.code, caught.value.status) == ("tag_limit_exceeded", 422)
