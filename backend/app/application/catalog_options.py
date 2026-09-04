"""Bounded, hierarchy-safe catalog options for human metadata review."""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select

from app.infrastructure.models import (CurriculumCatalogAlias, Skill, Subject,
    Subtopic, Topic, normalize_catalog_name)


@dataclass(frozen=True)
class CatalogOptionQuery:
    kind: str
    q: str = ""
    limit: int = 20
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None


class CatalogOptionService:
    """Search live rows locally; fuzzy results are never interpreted as bindings."""
    models = {"subjects": Subject, "topics": Topic, "subtopics": Subtopic, "skills": Skill}

    def __init__(self, db):
        self.db = db

    async def search(self, query: CatalogOptionQuery) -> dict:
        model = self.models[query.kind]
        normalized = normalize_catalog_name(query.q) if query.q.strip() else ""
        statement = select(model).where(model.status.in_(("active", "provisional")))
        if model is Topic:
            statement = statement.where(model.subject_id == query.subject_id,
                                        model.grade_id == query.grade_id)
        elif model is Subtopic:
            statement = statement.where(model.topic_id == query.topic_id)
        elif model is Skill:
            statement = statement.where(model.subtopic_id == query.subtopic_id)
        alias_ids: set[UUID] = set()
        if normalized:
            target = {Subject: CurriculumCatalogAlias.subject_target_id,
                      Topic: CurriculumCatalogAlias.topic_target_id,
                      Subtopic: CurriculumCatalogAlias.subtopic_target_id,
                      Skill: CurriculumCatalogAlias.skill_target_id}[model]
            alias = select(target).where(CurriculumCatalogAlias.kind == query.kind[:-1],
                CurriculumCatalogAlias.normalized_alias == normalized)
            if model is Topic:
                alias = alias.where(CurriculumCatalogAlias.subject_id == query.subject_id,
                                    CurriculumCatalogAlias.grade_id == query.grade_id)
            elif model is Subtopic:
                alias = alias.where(CurriculumCatalogAlias.topic_id == query.topic_id)
            elif model is Skill:
                alias = alias.where(CurriculumCatalogAlias.subtopic_id == query.subtopic_id)
            alias_ids = set((await self.db.scalars(alias)).all())
            statement = statement.where(or_(
                model.normalized_name.contains(normalized),
                func.similarity(model.normalized_name, normalized) >= 0.2,
                model.id.in_(alias_ids) if alias_ids else False,
            ))
        # A defensive over-fetch allows deterministic application ranking without
        # ever transferring the whole catalog to the browser.
        rows = (await self.db.scalars(statement.order_by(model.name, model.id).limit(min(query.limit * 8, 160)))).all()
        def rank(row):
            name = row.normalized_name
            if normalized and name == normalized: bucket = 0
            elif row.id in alias_ids: bucket = 1
            elif normalized and name.startswith(normalized): bucket = 2
            elif normalized and normalized in name: bucket = 3
            else: bucket = 4
            return bucket, row.name.casefold(), str(row.id)
        rows = sorted(rows, key=rank)[:query.limit]
        return {"items": [{"id": str(row.id), "name": row.name, "status": row.status,
            "match": "alias" if row.id in alias_ids else ("exact" if normalized and row.normalized_name == normalized else "search")}
            for row in rows], "limit": query.limit}
