"""Application tests for the isolated draft Assessment contour."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from decimal import Decimal

from app.application.assessments import (AddAssessmentItemCommand, AssessmentError,
    AssessmentItemRecord, AssessmentRecord, AssessmentService, AssessmentVariantRecord,
    AssignmentRecord, CreateAssessmentCommand, PublicationRecord, PublishAssessmentCommand,
    UpdateAssessmentCommand)
from app.application.content_bank import ActorContext
from app.presentation.assessment_schemas import AssessmentResponse


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

    async def get_variant(self, assessment_id, variant_id):
        row = self.variants.get(variant_id)
        return row if row is not None and row.assessment_id == assessment_id else None

    async def touch(self, assessment_id): pass
    async def append_audit(self, assessment_id, event, actor_id, details): self.audit.append((assessment_id, event, actor_id, details))


class FakeUow:
    def __init__(self):
        self.repository = FakeRepository(); self.commits = 0
        self.content_bank = SimpleNamespace(lock_new_usage=self.lock_new_usage)
        self.eligible = True; self.validated = []
    async def lock_new_usage(self, version_id):
        self.validated.append(version_id); return self.eligible
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


def test_created_assessment_projection_serializes_without_an_orm_session():
    record = AssessmentRecord(uuid4(), "Алгебра", None, "draft", (), NOW, NOW, None, None)
    response = AssessmentResponse.model_validate(record)
    assert response.status == "draft" and response.variants == []


async def test_add_item_uses_content_bank_port_and_failed_validation_has_no_audit():
    uow = FakeUow(); actor = ActorContext(uuid4()); service = AssessmentService(uow)
    assessment = await service.create(CreateAssessmentCommand("One", None), actor)
    variant = await service.create_variant(assessment.id, "A", actor)
    version_id = uuid4(); uow.eligible = False
    before = list(uow.repository.audit)
    with pytest.raises(AssessmentError) as error:
        await service.add_item(AddAssessmentItemCommand(
            assessment.id, variant.id, version_id, Decimal("2.50")), actor)
    assert error.value.code == "invalid_task_version"
    assert uow.validated == [version_id]
    assert uow.repository.audit == before


class PublicationRepository:
    def __init__(self, *, status="draft", students=None):
        self.assessment_id = uuid4(); self.group_id = uuid4(); self.actor_id = uuid4()
        item = AssessmentItemRecord(uuid4(), uuid4(), 1, Decimal("2.00"))
        self.composition = (AssessmentVariantRecord(uuid4(), "A", 1, (item,)),)
        self.row = SimpleNamespace(id=self.assessment_id, title="Ready", status=status,
            created_at=NOW, updated_at=NOW, description=None, published_at=None,
            published_by=None, variants=self.composition)
        self.students = tuple(students if students is not None else (uuid4(), uuid4()))
        self.audit = []; self.assignment = None; self.closed = False

    async def lock(self, value): return self.row if value == self.assessment_id else None
    async def lock_composition(self, value): return self.composition
    async def lock_group_students(self, value): return self.students if value == self.group_id else None
    async def database_clock(self): return NOW
    async def mark_published(self, value, now, actor_id):
        self.row.status = "published"; self.row.published_at = now; self.row.published_by = actor_id
    async def create_assignment(self, command, actor_id, students):
        self.assignment = AssignmentRecord(uuid4(), command.assessment_id, command.class_group_id,
            "open", command.start_at, command.due_at, command.max_attempts, NOW, None,
            len(students), tuple(students))
        return self.assignment
    async def get(self, value):
        return AssessmentRecord(self.row.id, self.row.title, None, self.row.status,
            self.composition, NOW, NOW, self.row.published_at, self.row.published_by)
    async def append_audit(self, aggregate_id, event, actor_id, details, aggregate_type="assessment"):
        self.audit.append((aggregate_type, aggregate_id, event, actor_id, details))
    async def lock_assignment(self, value):
        if self.assignment is None or self.assignment.id != value: return None
        return SimpleNamespace(status="closed" if self.closed else "open")
    async def close_assignment(self, value, now, actor_id): self.closed = True
    async def get_assignment(self, value):
        if self.assignment is None or self.assignment.id != value: return None
        if not self.closed: return self.assignment
        return AssignmentRecord(
            self.assignment.id, self.assignment.assessment_id, self.assignment.class_group_id, "closed",
            self.assignment.start_at, self.assignment.due_at, self.assignment.max_attempts,
            self.assignment.created_at, NOW, self.assignment.participant_count, self.assignment.participant_ids)


class PublicationUow:
    def __init__(self, *, status="draft", students=None, eligible=True):
        self.repository = PublicationRepository(status=status, students=students)
        self.eligible = eligible; self.validated = []; self.commits = 0
        self.content_bank = SimpleNamespace(lock_publication_usage=self.lock_publication_usage)
    async def lock_publication_usage(self, ids): self.validated.append(ids); return self.eligible
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def commit(self): self.commits += 1


def publication_command(repository):
    return PublishAssessmentCommand(repository.assessment_id, repository.group_id,
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2027, 8, 1, tzinfo=timezone.utc), 2)


async def test_publish_orchestrates_concrete_revalidation_snapshot_and_audits():
    uow = PublicationUow(); actor = ActorContext(uow.repository.actor_id)
    result = await AssessmentService(uow).publish_and_assign(publication_command(uow.repository), actor)
    concrete = uow.repository.composition[0].items[0].task_version_id
    assert isinstance(result, PublicationRecord) and result.assessment.status == "published"
    assert uow.validated == [(concrete,)]
    assert result.assignment.participant_ids == uow.repository.students
    assert [entry[2] for entry in uow.repository.audit] == ["assessment_published", "assignment_created"]
    assert all(entry[3] == actor.actor_id for entry in uow.repository.audit) and uow.commits == 1


@pytest.mark.parametrize("status,eligible,students,code", [
    ("published", True, (uuid4(),), "assessment_immutable"),
    ("draft", False, (uuid4(),), "invalid_task_version"),
    ("draft", True, (), "publication_requirements_not_met"),
])
async def test_publish_failures_have_no_audit_or_commit(status, eligible, students, code):
    uow = PublicationUow(status=status, students=students, eligible=eligible)
    with pytest.raises(AssessmentError) as error:
        await AssessmentService(uow).publish_and_assign(
            publication_command(uow.repository), ActorContext(uow.repository.actor_id))
    assert error.value.code == code and uow.repository.audit == [] and uow.commits == 0


async def test_close_open_audits_and_repeated_close_is_rejected():
    uow = PublicationUow(); actor = ActorContext(uow.repository.actor_id)
    published = await AssessmentService(uow).publish_and_assign(publication_command(uow.repository), actor)
    closed = await AssessmentService(uow).close_assignment(published.assignment.id, actor)
    assert closed.status == "closed" and uow.repository.audit[-1][2] == "assignment_closed"
    with pytest.raises(AssessmentError) as error:
        await AssessmentService(uow).close_assignment(published.assignment.id, actor)
    assert error.value.code == "invalid_status_transition"
