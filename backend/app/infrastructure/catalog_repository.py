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
