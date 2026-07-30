"""Boundary tests for the numeric difficulty HTTP and list contracts."""
import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock

from app.application.content_bank import ApplicationError, ListTasksService, TaskListQuery
from app.presentation.schemas import InitialVersionCreate

BASE = dict(title=None, statement="x", task_type="calculation", answer_format="number", source=None,
            skills=[{"skill_id": "00000000-0000-0000-0000-000000000001", "weight": "1", "is_primary": True}])

@pytest.mark.parametrize("value", [1, 25, 50, 75, 100])
def test_difficulty_accepts_integer_boundaries_and_examples(value):
    assert InitialVersionCreate(**BASE, difficulty=value).difficulty == value

@pytest.mark.parametrize("value", [0, 101, -1, 1.5, True, "25", "basic", None])
def test_difficulty_rejects_invalid_or_non_strict_values(value):
    with pytest.raises(ValidationError):
        InitialVersionCreate(**BASE, difficulty=value)

def test_difficulty_is_required():
    with pytest.raises(ValidationError):
        InitialVersionCreate(**BASE)

@pytest.mark.asyncio
async def test_difficulty_range_is_forwarded():
    repository = AsyncMock()
    repository.list_tasks.return_value = object()
    await ListTasksService(repository).list_tasks(TaskListQuery(difficulty_min=9, difficulty_max=100))
    sent = repository.list_tasks.await_args.args[0]
    assert (sent.difficulty_min, sent.difficulty_max) == (9, 100)

@pytest.mark.asyncio
@pytest.mark.parametrize("query", [TaskListQuery(difficulty_min=0), TaskListQuery(difficulty_max=101), TaskListQuery(difficulty_min=75, difficulty_max=25)])
async def test_difficulty_range_rejects_invalid_bounds(query):
    with pytest.raises(ApplicationError):
        await ListTasksService(AsyncMock()).list_tasks(query)
