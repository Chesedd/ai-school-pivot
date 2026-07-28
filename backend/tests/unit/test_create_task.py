from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.content_bank import ActorContext, ApplicationError, CatalogRecord, CreateTaskCommand, CreateTaskService, SkillLinkDTO, SkillLinkInput, TaskDTO, TaskVersionDTO, VersionContentInput

pytestmark = pytest.mark.asyncio


class Repo:
    def __init__(self):
        self.subject, self.grade, self.topic, self.subtopic, self.skill = [uuid4() for _ in range(5)]
        self.fail = False
        self.audit_events = []
    async def append_audit(self, event): self.audit_events.append(event)
    async def get_subject(self, value): return CatalogRecord(value, "s") if value == self.subject else None
    async def get_grade(self, value): return CatalogRecord(value, "g") if value == self.grade else None
    async def get_topic(self, value): return CatalogRecord(value, "t", subject_id=self.subject, grade_id=self.grade) if value == self.topic else None
    async def get_subtopic(self, value): return CatalogRecord(value, "st", topic_id=self.topic) if value == self.subtopic else None
    async def get_skills(self, values): return {x: CatalogRecord(x, "skill", topic_id=self.topic, subtopic_id=self.subtopic) for x in values if x == self.skill}
    async def create_task_with_initial_version(self, command, actor):
        if self.fail: raise RuntimeError("database failure")
        now, task_id, version_id, link_id = datetime.now(UTC), uuid4(), uuid4(), uuid4()
        link = command.initial_version.skills[0]
        version = TaskVersionDTO(version_id, 1, None, "text", "calculation", "number", "basic", None, "draft", actor.actor_id, now, (SkillLinkDTO(link_id, link.skill_id, "skill", link.weight, link.is_primary),))
        return TaskDTO(task_id, command.subject_id, command.grade_id, command.topic_id, command.subtopic_id, actor.actor_id, now, version)


class Uow:
    def __init__(self, repo): self.repository, self.committed, self.rolled_back = repo, False, False
    async def __aenter__(self): return self
    async def __aexit__(self, kind, exc, tb): self.rolled_back = kind is not None
    async def commit(self): self.committed = True


def command(repo, links=None, task_type="calculation", answer_format="number", **ids):
    links = links if links is not None else (SkillLinkInput(repo.skill, Decimal("1.0000"), True),)
    return CreateTaskCommand(ids.get("subject", repo.subject), ids.get("grade", repo.grade), ids.get("topic", repo.topic), ids.get("subtopic", repo.subtopic), VersionContentInput(None, "text", task_type, answer_format, "basic", None, tuple(links)))


async def test_success():
    repo, actor = Repo(), ActorContext(uuid4()); uow = Uow(repo)
    result = await CreateTaskService(uow).create_task(command(repo), actor)
    assert result.initial_version.version_no == 1 and result.initial_version.status == "draft" and uow.committed
    assert [event.action for event in repo.audit_events] == ["task_created"]


@pytest.mark.parametrize("links,code", [([], "primary_count"), ([False, False], "primary_count"), ([True, True], "primary_count"), ([True, False], "duplicate"), ([True], "weight_sum")])
async def test_skill_invariants(links, code):
    repo = Repo()
    if links == []: values = ()
    elif code == "duplicate": values = tuple(SkillLinkInput(repo.skill, Decimal("0.5"), x) for x in links)
    elif code == "weight_sum": values = (SkillLinkInput(repo.skill, Decimal("0.9"), True),)
    else: values = tuple(SkillLinkInput(uuid4(), Decimal("0.5"), x) for x in links)
    with pytest.raises(ApplicationError) as error: await CreateTaskService(Uow(repo)).create_task(command(repo, values), ActorContext(uuid4()))
    assert any(x.code == code for x in error.value.details)


async def test_incompatible_format():
    repo = Repo()
    with pytest.raises(ApplicationError) as error: await CreateTaskService(Uow(repo)).create_task(command(repo, task_type="essay", answer_format="number"), ActorContext(uuid4()))
    assert any(x.code == "incompatible" for x in error.value.details)


@pytest.mark.parametrize("field", ["subject", "grade", "topic", "subtopic"])
async def test_missing_catalog(field):
    repo = Repo(); values = {field: uuid4()}
    with pytest.raises(ApplicationError): await CreateTaskService(Uow(repo)).create_task(command(repo, **values), ActorContext(uuid4()))


async def test_repository_failure_rolls_back():
    repo = Repo(); repo.fail = True; uow = Uow(repo)
    with pytest.raises(RuntimeError): await CreateTaskService(uow).create_task(command(repo), ActorContext(uuid4()))
    assert uow.rolled_back and not uow.committed
