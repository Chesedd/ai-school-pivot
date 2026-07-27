from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.content_bank import ApplicationError, ListTasksService, TaskListPage, TaskListQuery


def test_query_defaults():
    query = TaskListQuery()
    assert (query.offset, query.limit, query.sort_by, query.sort_order) == (0, 20, "created_at", "desc")


async def test_query_parameters_are_delegated():
    repository = AsyncMock()
    repository.list_tasks.return_value = TaskListPage((), 0, 5, 10)
    query = TaskListQuery(subject_id=uuid4(), offset=5, limit=10, sort_by="title", sort_order="asc")
    assert await ListTasksService(repository).list_tasks(query) == TaskListPage((), 0, 5, 10)
    repository.list_tasks.assert_awaited_once_with(query)


@pytest.mark.parametrize("query", [TaskListQuery(offset=-1), TaskListQuery(limit=0), TaskListQuery(limit=101), TaskListQuery(sort_by="updated_at"), TaskListQuery(sort_order="sideways")])
async def test_invalid_query(query):
    with pytest.raises(ApplicationError):
        await ListTasksService(AsyncMock()).list_tasks(query)
