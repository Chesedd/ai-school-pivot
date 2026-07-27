from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.content_bank import (AcceptedAnswerInput, ApplicationError, ConflictError,
    ExpectedSolutionInput, HintInput, LockedVersion, RubricInput, RubricItemInput,
    SaveMethodologyCommand, SaveMethodologyService, TypicalErrorInput)


def command(**changes):
    skill = changes.pop("skill", uuid4())
    values = dict(task_version_id=uuid4(),
        expected_solution=ExpectedSolutionInput("Solution", None, ("Step",)),
        rubric=RubricInput("points", None, (RubricItemInput("Criterion", Decimal("1"), True, None),)),
        accepted_answers=(AcceptedAnswerInput("3", None, None, None),),
        typical_errors=(TypicalErrorInput(skill, "code", "Title", "Description", "medium", None, None),),
        hints=(HintInput(1, "Hint"),))
    values.update(changes)
    return SaveMethodologyCommand(**values), skill


class Uow:
    def __init__(self, version):
        self.repository = AsyncMock()
        self.repository.lock_version.return_value = version
        self.repository.replace_methodology.return_value = object()
        self.committed = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def commit(self): self.committed = True


async def test_save_commits_once_for_latest_draft():
    cmd, skill = command()
    uow = Uow(LockedVersion(cmd.task_version_id, "number", "draft", True, frozenset({skill})))
    await SaveMethodologyService(uow).save(cmd)
    assert uow.committed
    uow.repository.replace_methodology.assert_awaited_once_with(cmd)


@pytest.mark.parametrize("status,latest", [("review", True), ("approved", True), ("archived", True), ("draft", False)])
async def test_only_latest_draft_is_editable(status, latest):
    cmd, skill = command(); uow = Uow(LockedVersion(cmd.task_version_id, "number", status, latest, frozenset({skill})))
    with pytest.raises(ConflictError): await SaveMethodologyService(uow).save(cmd)
    assert not uow.committed


@pytest.mark.parametrize("change", [
    {"hints": (HintInput(2, "Hint"),)},
    {"accepted_answers": (AcceptedAnswerInput("3", None, None, None), AcceptedAnswerInput(" 3 ", None, None, None))},
    {"rubric": RubricInput("points", None, (RubricItemInput("Same", Decimal("1"), True, None), RubricItemInput(" same ", Decimal("1"), True, None)))},
    {"expected_solution": ExpectedSolutionInput("Solution", None, (" ",))},
])
async def test_payload_invariants_are_rejected_before_repository(change):
    cmd, skill = command(**change); uow = Uow(LockedVersion(cmd.task_version_id, "number", "draft", True, frozenset({skill})))
    with pytest.raises(ApplicationError): await SaveMethodologyService(uow).save(cmd)
    uow.repository.lock_version.assert_not_awaited()


async def test_tolerance_is_number_only_and_skill_must_be_linked():
    foreign = uuid4()
    cmd, _ = command(accepted_answers=(AcceptedAnswerInput("x", Decimal("0.1"), None, None),), skill=foreign)
    uow = Uow(LockedVersion(cmd.task_version_id, "short_text", "draft", True, frozenset()))
    with pytest.raises(ApplicationError) as caught: await SaveMethodologyService(uow).save(cmd)
    assert {x.code for x in caught.value.details} == {"not_allowed", "invalid_relation"}
    assert not uow.committed
