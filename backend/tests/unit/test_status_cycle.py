from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.content_bank import (
    ActorContext, ArchiveResult, ArchiveTaskService, CreateVersionCommand,
    CreateVersionService, EMPTY_METHODOLOGY, IssuesError, MethodologyDTO,
    SkillLinkDTO, StatusCycleService, VersionState, ExpectedSolutionDTO,
    RubricDTO, RubricItemDTO, ConflictError,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
ACTOR = ActorContext(uuid4())


def state(status="draft", methodology=EMPTY_METHODOLOGY, latest=True, archived=None):
    task, version, skill = uuid4(), uuid4(), uuid4()
    return VersionState(task, version, 1, status, "Statement", "calculation", "number", NOW,
        uuid4(), None, None, archived, latest,
        (SkillLinkDTO(uuid4(), skill, "Skill", Decimal("1.0000"), True),), methodology)


def complete_methodology():
    item = RubricItemDTO(uuid4(), "Criterion", Decimal("1.0000"), True, None, 0)
    return MethodologyDTO(ExpectedSolutionDTO(uuid4(), "Solution", "1", ()),
        RubricDTO(uuid4(), "points", Decimal("1.0000"), None, (item,)), (), (), ())


class Uow:
    def __init__(self, version=None):
        self.repository = AsyncMock()
        self.repository.lock_task_version.return_value = version
        self.committed = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def commit(self): self.committed = True


async def test_soft_warnings_do_not_block_review():
    version = state(); uow = Uow(version)
    result = await StatusCycleService(uow).submit_review(version.task_id, 1, ACTOR)
    assert result.status == "review" and result.validation.valid_for_approval is False
    assert {x.code for x in result.validation.issues} == {"missing_expected_solution", "missing_rubric"}
    assert uow.committed


async def test_structural_damage_blocks_review_with_all_issues():
    version = state(); version = VersionState(**{**version.__dict__, "statement": "", "skills": ()})
    uow = Uow(version)
    with pytest.raises(IssuesError) as caught:
        await StatusCycleService(uow).submit_review(version.task_id, 1, ACTOR)
    assert caught.value.code == "validation_error"
    assert {x.code for x in caught.value.issues} >= {"missing_statement", "missing_skill", "missing_primary_skill", "invalid_skill_weights"}
    assert not uow.committed


async def test_return_to_draft_changes_only_status():
    version = state("review", complete_methodology()); uow = Uow(version)
    result = await StatusCycleService(uow).return_draft(version.task_id, 1, "Reason", ACTOR)
    assert (result.previous_status, result.status) == ("review", "draft")
    uow.repository.set_version_status.assert_awaited_once_with(version.task_version_id, "draft")


@pytest.mark.parametrize("current,operation", [("draft", "approve"), ("approved", "return")])
async def test_forbidden_transitions(current, operation):
    version = state(current, complete_methodology()); uow = Uow(version); service = StatusCycleService(uow)
    with pytest.raises(ConflictError) as caught:
        if operation == "approve": await service.approve(version.task_id, 1, ACTOR)
        else: await service.return_draft(version.task_id, 1, "Reason", ACTOR)
    assert caught.value.code == "invalid_status_transition"


async def test_approve_returns_complete_issue_list():
    version = state("review"); uow = Uow(version)
    with pytest.raises(IssuesError) as caught:
        await StatusCycleService(uow).approve(version.task_id, 1, ACTOR)
    assert caught.value.code == "approval_requirements_not_met"
    assert {x.code for x in caught.value.issues} == {"missing_expected_solution", "missing_rubric"}
    assert not uow.committed


async def test_successful_approve_uses_server_actor():
    version = state("review", complete_methodology()); uow = Uow(version)
    result = await StatusCycleService(uow).approve(version.task_id, 1, ACTOR)
    assert result.status == "approved" and result.approved_by == ACTOR.actor_id and result.approved_at is not None
    args = uow.repository.set_version_status.await_args.args
    assert args[0:2] == (version.task_version_id, "approved") and args[3] == ACTOR.actor_id


async def test_internal_revision_clone_requires_latest_approved_and_commits_clone():
    version = state("approved", complete_methodology()); cloned = VersionState(**{**version.__dict__, "task_version_id": uuid4(), "version_no": 2, "status": "draft", "approved_at": None, "approved_by": None})
    uow = Uow(version); uow.repository.clone_version.return_value = cloned
    result = await CreateVersionService(uow).create(CreateVersionCommand(version.task_id, 1), ACTOR)
    assert result.task_version_id != version.task_version_id and result.status == "draft" and result.approved_at is None
    uow.repository.clone_version.assert_awaited_once_with(version.task_id, 1, ACTOR)


async def test_internal_revision_clone_rejects_non_latest_source():
    version = state("approved", latest=False); uow = Uow(version)
    with pytest.raises(ConflictError) as caught:
        await CreateVersionService(uow).create(CreateVersionCommand(version.task_id, 1), ACTOR)
    assert caught.value.code == "invalid_source_version"


async def test_archive_and_idempotent_archive_results():
    task_id, archived_at = uuid4(), NOW
    uow = Uow(); uow.repository.archive_task_versions.return_value = ArchiveResult(task_id, archived_at, "archived")
    first = await ArchiveTaskService(uow).archive(task_id, ACTOR)
    second = await ArchiveTaskService(uow).archive(task_id, ACTOR)
    assert first.archived_at == second.archived_at == archived_at
    assert uow.repository.archive_task_versions.await_count == 2
