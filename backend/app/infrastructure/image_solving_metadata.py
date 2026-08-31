"""Current Content Bank catalog loader for local metadata resolution."""
from sqlalchemy import select

from app.application.image_solving_metadata import (CatalogItemV1,
    MetadataCatalogSnapshotV1, TagCandidateV1)
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Tag, TagCategory, TaskFolder, Topic

class SqlAlchemyMetadataCatalogLoader:
    def __init__(self,db):self.db=db
    async def load(self):
        subjects=(await self.db.scalars(select(Subject).order_by(Subject.name))).all()
        grades=(await self.db.scalars(select(Grade).order_by(Grade.number))).all()
        topics=(await self.db.scalars(select(Topic).order_by(Topic.name))).all()
        subs=(await self.db.scalars(select(Subtopic).order_by(Subtopic.name))).all()
        skills=(await self.db.scalars(select(Skill).order_by(Skill.name))).all()
        folders=(await self.db.scalars(select(TaskFolder).order_by(TaskFolder.name))).all()
        categories=(await self.db.scalars(select(TagCategory.code).order_by(TagCategory.sort_order))).all()
        tags=(await self.db.scalars(select(Tag).where(Tag.status=="active").order_by(Tag.normalized_name))).all()
        topic_by={x.id:x for x in topics}; sub_by={x.id:x for x in subs}
        return MetadataCatalogSnapshotV1(
            subjects=tuple(CatalogItemV1(id=x.id,name=x.name) for x in subjects),
            grades=tuple(CatalogItemV1(id=x.id,name=x.name,grade_number=x.number) for x in grades),
            topics=tuple(CatalogItemV1(id=x.id,name=x.name,subject_id=x.subject_id,grade_id=x.grade_id) for x in topics),
            subtopics=tuple(CatalogItemV1(id=x.id,name=x.name,topic_id=x.topic_id,
                subject_id=topic_by[x.topic_id].subject_id,grade_id=topic_by[x.topic_id].grade_id) for x in subs),
            skills=tuple(CatalogItemV1(id=x.id,name=x.name,subtopic_id=x.subtopic_id,
                topic_id=sub_by[x.subtopic_id].topic_id) for x in skills),
            folders=tuple(CatalogItemV1(id=x.id,name=x.name,subject_id=x.subject_id,parent_id=x.parent_id) for x in folders),
            tag_categories=tuple(categories),tags=tuple(TagCandidateV1(id=x.id,name=x.name,
                category_code=x.category_code,subject_id=x.subject_id) for x in tags))
