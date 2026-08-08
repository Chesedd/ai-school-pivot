"""Application tests for the isolated draft Assessment contour."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.assessments import AssessmentError, AssessmentService, CreateAssessmentCommand, UpdateAssessmentCommand
from app.application.content_bank import ActorContext


NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self):
        self.rows = {}
        self.variants = {}
        self.audit = []
        self.stale = False

    async def list(self, status, offset, limit):
        rows = [row for row in self.rows.values() if status is None or row.status == status]
        return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit}

    async def get(self, assessment_id): return self.rows.get(assessment_id)
    async def lock(self, assessment_id): return self.rows.get(assessment_id)

    async def create(self, command, actor_id):
        row = SimpleNamespace(id=uuid4(), title=command.title, description=command.description, status="draft",
                              created_by=actor_id, created_at=NOW, updated_at=NOW, published_at=None,
                              published_by=None, variants=[])
        self.rows[row.id] = row
        return row

    async def update_metadata_cas(self, assessment_id, expected, values):
        row = self.rows[assessment_id]
        if self.stale or row.updated_at != expected: return None
        for key, value in values.items(): setattr(row, key, value)
        row.updated_at = NOW.replace(microsecond=1)
        return row

    async def create_variant(self, assessment_id, name):
        if any(value.name == name for value in self.variants.values()): return None
        row = SimpleNamespace(id=uuid4(), assessment_id=assessment_id, name=name,
                              position=len(self.variants) + 1, items=[])
        self.variants[row.id] = row
        return row

    async def delete_variant(self, assessment_id, variant_id):
        row = self.variants.get(variant_id)
        if row is None or row.assessment_id != assessment_id: return None
        del self.variants[variant_id]
        return row.position

    async def touch(self, assessment_id): pass
    async def append_audit(self, assessment_id, event, actor_id, details): self.audit.append((assessment_id, event, actor_id, details))


class FakeUow:
    def __init__(self): self.repository = FakeRepository(); self.commits = 0
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def commit(self): self.commits += 1


async def test_create_uses_server_actor_and_requests_atomic_audit():
    uow = FakeUow(); actor = ActorContext(uuid4())
    row = await AssessmentService(uow).create(CreateAssessmentCommand("Алгебра", None), actor)
    assert row.status == "draft" and row.created_by == actor.actor_id
    assert uow.repository.audit == [(row.id, "assessment_created", actor.actor_id, {})]
    assert uow.commits == 1


async def test_update_draft_and_reject_stale_or_published():
    uow = FakeUow(); actor = ActorContext(uuid4()); service = AssessmentService(uow)
    row = await service.create(CreateAssessmentCommand("Old", None), actor)
    changed = await service.update(UpdateAssessmentCommand(row.id, NOW, {"title": "New"}), actor)
    assert changed.title == "New" and uow.repository.audit[-1][1:] == ("assessment_metadata_updated", actor.actor_id, {"changed_fields": ["title"]})
    with pytest.raises(AssessmentError, match="уже изменена") as stale:
        await service.update(UpdateAssessmentCommand(row.id, NOW, {"title": "Lost"}), actor)
    assert stale.value.code == "concurrent_conflict"
    row.status = "published"
    with pytest.raises(AssessmentError) as immutable:
        await service.update(UpdateAssessmentCommand(row.id, row.updated_at, {"title": "No"}), actor)
    assert immutable.value.code == "assessment_immutable"


async def test_variant_create_delete_ownership_and_published_guards():
    uow = FakeUow(); actor = ActorContext(uuid4()); service = AssessmentService(uow)
    first = await service.create(CreateAssessmentCommand("One", None), actor)
    second = await service.create(CreateAssessmentCommand("Two", None), actor)
    variant = await service.create_variant(first.id, "A", actor)
    assert variant.position == 1 and uow.repository.audit[-1][1] == "variant_created"
    with pytest.raises(AssessmentError) as foreign:
        await service.delete_variant(second.id, variant.id, actor)
    assert foreign.value.code == "variant_not_found" and variant.id in uow.repository.variants
    first.status = "published"
    with pytest.raises(AssessmentError) as create_error:
        await service.create_variant(first.id, "B", actor)
    with pytest.raises(AssessmentError) as delete_error:
        await service.delete_variant(first.id, variant.id, actor)
    assert create_error.value.code == delete_error.value.code == "assessment_immutable"
    first.status = "draft"
    await service.delete_variant(first.id, variant.id, actor)
    assert variant.id not in uow.repository.variants and uow.repository.audit[-1][1] == "variant_deleted"
