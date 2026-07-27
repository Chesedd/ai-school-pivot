from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.content_bank import ApplicationError, ListTasksService, TaskListPage, TaskListQuery


def test_query_defaults():
    query = TaskListQuery()
    assert (query.offset, query.limit, query.sort_by, query.sort_order, query.q) == (0, 20, None, "desc", None)


async def test_query_parameters_are_delegated():
    repository = AsyncMock()
    repository.list_tasks.return_value = TaskListPage((), 0, 5, 10)
    query = TaskListQuery(subject_id=uuid4(), offset=5, limit=10, sort_by="title", sort_order="asc")
    assert await ListTasksService(repository).list_tasks(query) == TaskListPage((), 0, 5, 10)
    repository.list_tasks.assert_awaited_once_with(query)


@pytest.mark.parametrize("query", [TaskListQuery(offset=-1), TaskListQuery(limit=0), TaskListQuery(limit=101), TaskListQuery(sort_by="unknown"), TaskListQuery(sort_order="sideways"), TaskListQuery(sort_by="relevance")])
async def test_invalid_query(query):
    with pytest.raises(ApplicationError):
        await ListTasksService(AsyncMock()).list_tasks(query)


@pytest.mark.parametrize(("raw", "normalized", "sort"), [(None, None, "created_at"), ("   ", None, "created_at"), ("  движение  ", "движение", "relevance")])
async def test_search_normalization_and_default_sort(raw, normalized, sort):
    repository = AsyncMock()
    repository.list_tasks.return_value = TaskListPage((), 0, 0, 20)
    await ListTasksService(repository).list_tasks(TaskListQuery(q=raw))
    delegated = repository.list_tasks.await_args.args[0]
    assert (delegated.q, delegated.sort_by) == (normalized, sort)


async def test_explicit_search_sort_is_preserved():
    repository = AsyncMock()
    repository.list_tasks.return_value = TaskListPage((), 0, 0, 20)
    await ListTasksService(repository).list_tasks(TaskListQuery(q=" задача ", sort_by="updated_at", sort_order="asc"))
    delegated = repository.list_tasks.await_args.args[0]
    assert (delegated.q, delegated.sort_by, delegated.sort_order) == ("задача", "updated_at", "asc")


async def test_search_max_length():
    with pytest.raises(ApplicationError) as error:
        await ListTasksService(AsyncMock()).list_tasks(TaskListQuery(q="я" * 201))
    assert error.value.details[0].field == "q"
