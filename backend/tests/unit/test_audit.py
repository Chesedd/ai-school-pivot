from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.content_bank import (
    ActorContext, AuditEventRecord, AuditWriter, EMPTY_METHODOLOGY, SkillLinkDTO,
    StatusCycleService, VersionState,
)


def version(status="draft"):
    return VersionState(uuid4(), uuid4(), 1, status, "Safe statement", "calculation", "number",
        datetime.now(timezone.utc), uuid4(), None, None, None, True,
        (SkillLinkDTO(uuid4(), uuid4(), "skill", Decimal("1"), True),), EMPTY_METHODOLOGY)


class Uow:
    def __init__(self, value):
        self.repository = AsyncMock()
        self.repository.lock_task_version.return_value = value
        self.commits = 0
        self.rolled_back = False
    async def __aenter__(self): return self
    async def __aexit__(self, kind, *_): self.rolled_back = kind is not None
    async def commit(self): self.commits += 1


async def test_audit_writer_only_appends_and_never_commits():
    repository = AsyncMock()
    event = AuditEventRecord(uuid4(), uuid4(), 1, "task_created", uuid4(), details={})
    await AuditWriter(repository).write(event)
    repository.append_audit.assert_awaited_once_with(event)
    assert not hasattr(repository, "commit") or repository.commit.await_count == 0


async def test_server_actor_reason_and_safe_transition_details():
    value, actor = version("review"), ActorContext(uuid4())
    uow = Uow(value)
    await StatusCycleService(uow).return_draft(value.task_id, 1, "editorial reason", actor)
    event = uow.repository.append_audit.await_args.args[0]
    assert (event.action, event.actor_id, event.reason) == ("returned_to_draft", actor.actor_id, "editorial reason")
    assert event.details == {"from_status": "review", "to_status": "draft"}
    assert not ({"statement", "solution_text", "accepted_answers", "rubric", "hints"} & event.details.keys())


async def test_audit_failure_rolls_back_business_change_without_commit():
    value = version()
    uow = Uow(value)
    uow.repository.append_audit.side_effect = RuntimeError("audit insert failed")
    with pytest.raises(RuntimeError, match="audit insert failed"):
        await StatusCycleService(uow).submit_review(value.task_id, 1, ActorContext(uuid4()))
    assert uow.rolled_back and uow.commits == 0


async def test_failed_transition_does_not_append_audit():
    value = version("approved")
    uow = Uow(value)
    with pytest.raises(Exception):
        await StatusCycleService(uow).submit_review(value.task_id, 1, ActorContext(uuid4()))
    uow.repository.append_audit.assert_not_awaited()
