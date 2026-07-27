from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.content_bank import GetTaskCardService, NotFoundError


async def test_task_id_is_delegated_to_repository():
    task_id, card = uuid4(), object()
    repository = AsyncMock()
    repository.get_task_card.return_value = card
    assert await GetTaskCardService(repository).get_task_card(task_id) is card
    repository.get_task_card.assert_awaited_once_with(task_id)


async def test_missing_task_raises_application_not_found():
    repository = AsyncMock()
    repository.get_task_card.return_value = None
    with pytest.raises(NotFoundError):
        await GetTaskCardService(repository).get_task_card(uuid4())


async def test_read_only_query_does_not_commit():
    repository = AsyncMock()
    repository.get_task_card.return_value = object()
    await GetTaskCardService(repository).get_task_card(uuid4())
    repository.commit.assert_not_called()
