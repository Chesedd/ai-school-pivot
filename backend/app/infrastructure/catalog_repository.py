"""Persistence-only primitives for the canonical curriculum catalog."""

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.catalog import CatalogLifecycle, CatalogLifecycleState
from app.infrastructure.models import (
    Grade,
    Skill,
    Subject,
    Subtopic,
    Topic,
    normalize_catalog_name,
)

CatalogKind = Literal["subject", "grade", "topic", "subtopic", "skill"]
MODELS = {
    "subject": Subject,
    "grade": Grade,
    "topic": Topic,
    "subtopic": Subtopic,
    "skill": Skill,
}


class SQLAlchemyCatalogRepository:
    """No policy or HTTP behavior: J1C will compose these race-safe primitives."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, kind: CatalogKind, entity_id: UUID):
        return await self.session.get(MODELS[kind], entity_id)

    async def lock(self, kind: CatalogKind, entity_id: UUID):
        return await self.session.scalar(select(MODELS[kind]).where(MODELS[kind].id == entity_id).with_for_update())

    async def list_provisional(self, kind: CatalogKind | None, offset: int, limit: int):
        kinds = (kind,) if kind else tuple(MODELS)
        rows = []
        for item in kinds:
            model = MODELS[item]
            values = (await self.session.scalars(select(model).where(model.status == "provisional").order_by(model.created_at, model.id))).all()
            rows.extend((item, row) for row in values)
        rows.sort(key=lambda value: (value[1].created_at, value[0], str(value[1].id)))
        return rows[offset:offset + limit]

    async def find_merged_alias(self, kind: CatalogKind, name: str, **parents):
        model = MODELS[kind]
        identity = model.number == parents.get("number") if model is Grade else model.normalized_name == normalize_catalog_name(name)
        query = select(model).where(identity, model.status == "deprecated", model.replacement_id.is_not(None))
        for key in ("subject_id", "grade_id", "topic_id", "subtopic_id"):
            if hasattr(model, key) and parents.get(key) is not None:
                query = query.where(getattr(model, key) == parents[key])
        return await self.session.scalar(query)

    async def find_exact(
        self,
        kind: CatalogKind,
        name: str,
        *,
        status: CatalogLifecycle,
        subject_id: UUID | None = None,
        grade_id: UUID | None = None,
        topic_id: UUID | None = None,
        subtopic_id: UUID | None = None,
        number: int | None = None,
    ):
        model = MODELS[kind]
        identity = model.number == number if model is Grade else model.normalized_name == normalize_catalog_name(name)
        query = select(model).where(identity, model.status == status.value)
        for key, value in (
            ("subject_id", subject_id),
            ("grade_id", grade_id),
            ("topic_id", topic_id),
            ("subtopic_id", subtopic_id),
        ):
            if hasattr(model, key) and value is not None:
                query = query.where(getattr(model, key) == value)
        return await self.session.scalar(query)

    async def create_provisional(
        self,
        kind: CatalogKind,
        *,
        name: str,
        code: str,
        proposed_by: UUID,
        number: int | None = None,
        subject_id: UUID | None = None,
        grade_id: UUID | None = None,
        topic_id: UUID | None = None,
        subtopic_id: UUID | None = None,
    ):
        state = CatalogLifecycleState(CatalogLifecycle.PROVISIONAL, proposed_by)
        values = {
            "name": name,
            "normalized_name": normalize_catalog_name(name),
            "status": state.status.value,
            "proposed_by": state.proposed_by,
        }
        model = MODELS[kind]
        if model is Grade:
            values["number"] = number
        else:
            values["code"] = code
        for key, value in (
            ("subject_id", subject_id),
            ("grade_id", grade_id),
            ("topic_id", topic_id),
            ("subtopic_id", subtopic_id),
        ):
            if hasattr(model, key):
                values[key] = value
        row = model(**values)
        self.session.add(row)
        await self.session.flush()
        return row
