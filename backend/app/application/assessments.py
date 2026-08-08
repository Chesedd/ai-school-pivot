"""Application boundary for draft Assessment Core authoring."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.application.content_bank import ActorContext


@dataclass(frozen=True)
class CreateAssessmentCommand:
    title: str
    description: str | None


@dataclass(frozen=True)
class UpdateAssessmentCommand:
    assessment_id: UUID
    expected_updated_at: datetime
    values: dict[str, object]


@dataclass(frozen=True)
class AddAssessmentItemCommand:
    assessment_id: UUID
    variant_id: UUID
    task_version_id: UUID
    points: Decimal


@dataclass(frozen=True)
class ReorderAssessmentItemsCommand:
    assessment_id: UUID
    variant_id: UUID
    item_ids: tuple[UUID, ...]
    expected_updated_at: datetime


@dataclass(frozen=True)
class ChangeAssessmentItemPointsCommand:
    assessment_id: UUID
    variant_id: UUID
    item_id: UUID
    points: Decimal
    expected_updated_at: datetime


@dataclass(frozen=True)
class AssessmentItemRecord:
    id: UUID
    task_version_id: UUID
    position: int
    points: Decimal


@dataclass(frozen=True)
class AssessmentVariantRecord:
    id: UUID
    name: str
    position: int
    items: tuple[AssessmentItemRecord, ...]


@dataclass(frozen=True)
class AssessmentRecord:
    id: UUID
    title: str
    description: str | None
    status: str
    variants: tuple[AssessmentVariantRecord, ...]
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    published_by: UUID | None


class AssessmentError(Exception):
    def __init__(self, code: str, message: str, status: int = 409,
                 details: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or []


class AssessmentRepository(Protocol):
    async def list(self, status: str | None, offset: int, limit: int): ...
    async def get(self, assessment_id: UUID): ...
    async def create(self, command: CreateAssessmentCommand, actor_id: UUID): ...
    async def lock(self, assessment_id: UUID): ...
    async def update_metadata_cas(self, assessment_id: UUID, expected: datetime, values: dict[str, object]): ...
    async def create_variant(self, assessment_id: UUID, name: str): ...
    async def delete_variant(self, assessment_id: UUID, variant_id: UUID): ...
    async def get_variant(self, assessment_id: UUID, variant_id: UUID): ...
    async def create_item(self, variant_id: UUID, task_version_id: UUID, points: Decimal): ...
    async def delete_item(self, variant_id: UUID, item_id: UUID): ...
    async def reorder_items(self, variant_id: UUID, item_ids: tuple[UUID, ...]): ...
    async def update_item_points(self, variant_id: UUID, item_id: UUID, points: Decimal): ...
    async def touch(self, assessment_id: UUID): ...
    async def append_audit(self, assessment_id: UUID, event: str, actor_id: UUID, details: dict[str, object]): ...


class AssessmentUnitOfWork(Protocol):
    repository: AssessmentRepository
    content_bank: ContentBankReadPort
    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc, tb): ...
    async def commit(self): ...


class ContentBankReadPort(Protocol):
    """Minimal same-transaction boundary for new Assessment usage."""

    async def lock_new_usage(self, version_id: UUID) -> bool: ...


def _require_draft(row) -> None:
    if row.status != "draft":
        raise AssessmentError("assessment_immutable", "Опубликованную работу нельзя изменить.")


class AssessmentService:
    def __init__(self, uow: AssessmentUnitOfWork):
        self.uow = uow

    async def list(self, status: str | None, offset: int, limit: int, actor: ActorContext):
        async with self.uow:
            return await self.uow.repository.list(status, offset, limit)

    async def get(self, assessment_id: UUID, actor: ActorContext):
        async with self.uow:
            row = await self.uow.repository.get(assessment_id)
            if row is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            return row

    async def create(self, command: CreateAssessmentCommand, actor: ActorContext):
        async with self.uow:
            row = await self.uow.repository.create(command, actor.actor_id)
            await self.uow.repository.append_audit(row.id, "assessment_created", actor.actor_id, {})
            await self.uow.commit()
            return row

    async def update(self, command: UpdateAssessmentCommand, actor: ActorContext):
        async with self.uow:
            current = await self.uow.repository.lock(command.assessment_id)
            if current is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            _require_draft(current)
            row = await self.uow.repository.update_metadata_cas(
                command.assessment_id, command.expected_updated_at, command.values
            )
            if row is None:
                raise AssessmentError("concurrent_conflict", "Работа уже изменена.")
            await self.uow.repository.append_audit(
                row.id, "assessment_metadata_updated", actor.actor_id,
                {"changed_fields": sorted(command.values)},
            )
            await self.uow.commit()
            return row

    async def create_variant(self, assessment_id: UUID, name: str, actor: ActorContext):
        async with self.uow:
            assessment = await self.uow.repository.lock(assessment_id)
            if assessment is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            _require_draft(assessment)
            variant = await self.uow.repository.create_variant(assessment_id, name)
            if variant is None:
                raise AssessmentError("concurrent_conflict", "Вариант с таким именем уже существует.")
            await self.uow.repository.touch(assessment_id)
            await self.uow.repository.append_audit(
                assessment_id, "variant_created", actor.actor_id,
                {"variant_id": str(variant.id), "position": variant.position},
            )
            await self.uow.commit()
            return variant

    async def delete_variant(self, assessment_id: UUID, variant_id: UUID, actor: ActorContext) -> None:
        async with self.uow:
            assessment = await self.uow.repository.lock(assessment_id)
            if assessment is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            _require_draft(assessment)
            deleted = await self.uow.repository.delete_variant(assessment_id, variant_id)
            if deleted is None:
                raise AssessmentError("variant_not_found", "Вариант не найден.", 404)
            await self.uow.repository.touch(assessment_id)
            await self.uow.repository.append_audit(
                assessment_id, "variant_deleted", actor.actor_id,
                {"variant_id": str(variant_id), "position": deleted},
            )
            await self.uow.commit()

    async def add_item(self, command: AddAssessmentItemCommand, actor: ActorContext):
        async with self.uow:
            assessment = await self.uow.repository.lock(command.assessment_id)
            if assessment is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            _require_draft(assessment)
            if await self.uow.repository.get_variant(command.assessment_id, command.variant_id) is None:
                raise AssessmentError("variant_not_found", "Вариант не найден.", 404)
            if not await self.uow.content_bank.lock_new_usage(command.task_version_id):
                raise AssessmentError("invalid_task_version", "Версию задания нельзя добавить в работу.")
            item = await self.uow.repository.create_item(command.variant_id, command.task_version_id, command.points)
            if item is None:
                raise AssessmentError("concurrent_conflict", "Версия задания уже добавлена в вариант.")
            await self.uow.repository.touch(command.assessment_id)
            await self.uow.repository.append_audit(command.assessment_id, "item_added", actor.actor_id,
                {"variant_id": str(command.variant_id), "item_id": str(item.id),
                 "task_version_id": str(command.task_version_id), "position": item.position})
            await self.uow.commit()
            return item

    async def delete_item(self, assessment_id: UUID, variant_id: UUID, item_id: UUID, actor: ActorContext) -> None:
        async with self.uow:
            assessment = await self.uow.repository.lock(assessment_id)
            if assessment is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            _require_draft(assessment)
            if await self.uow.repository.get_variant(assessment_id, variant_id) is None:
                raise AssessmentError("variant_not_found", "Вариант не найден.", 404)
            position = await self.uow.repository.delete_item(variant_id, item_id)
            if position is None:
                raise AssessmentError("item_not_found", "Элемент варианта не найден.", 404)
            await self.uow.repository.touch(assessment_id)
            await self.uow.repository.append_audit(assessment_id, "item_removed", actor.actor_id,
                {"variant_id": str(variant_id), "item_id": str(item_id), "position": position})
            await self.uow.commit()

    async def reorder_items(self, command: ReorderAssessmentItemsCommand, actor: ActorContext):
        async with self.uow:
            assessment = await self.uow.repository.lock(command.assessment_id)
            if assessment is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            _require_draft(assessment)
            if assessment.updated_at != command.expected_updated_at:
                raise AssessmentError("concurrent_conflict", "Работа уже изменена.")
            if await self.uow.repository.get_variant(command.assessment_id, command.variant_id) is None:
                raise AssessmentError("variant_not_found", "Вариант не найден.", 404)
            variant = await self.uow.repository.reorder_items(command.variant_id, command.item_ids)
            if variant is None:
                raise AssessmentError("validation_error", "item_ids должны быть полной перестановкой элементов варианта.", 422,
                                      [{"field": "item_ids", "code": "invalid_permutation",
                                        "message": "Передайте каждый элемент варианта ровно один раз."}])
            await self.uow.repository.touch(command.assessment_id)
            await self.uow.repository.append_audit(command.assessment_id, "items_reordered", actor.actor_id,
                {"variant_id": str(command.variant_id), "item_ids": [str(value) for value in command.item_ids]})
            await self.uow.commit()
            return variant

    async def change_item_points(self, command: ChangeAssessmentItemPointsCommand, actor: ActorContext):
        async with self.uow:
            assessment = await self.uow.repository.lock(command.assessment_id)
            if assessment is None:
                raise AssessmentError("assessment_not_found", "Работа не найдена.", 404)
            _require_draft(assessment)
            if assessment.updated_at != command.expected_updated_at:
                raise AssessmentError("concurrent_conflict", "Работа уже изменена.")
            if await self.uow.repository.get_variant(command.assessment_id, command.variant_id) is None:
                raise AssessmentError("variant_not_found", "Вариант не найден.", 404)
            item = await self.uow.repository.update_item_points(command.variant_id, command.item_id, command.points)
            if item is None:
                raise AssessmentError("item_not_found", "Элемент варианта не найден.", 404)
            await self.uow.repository.touch(command.assessment_id)
            await self.uow.repository.append_audit(command.assessment_id, "item_points_changed", actor.actor_id,
                {"variant_id": str(command.variant_id), "item_id": str(command.item_id), "changed_fields": ["points"]})
            await self.uow.commit()
            return item
