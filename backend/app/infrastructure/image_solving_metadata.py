"""Current Content Bank catalog loader for local metadata resolution."""
from sqlalchemy import select

from app.application.image_solving_metadata import (CatalogItemV1,
    MetadataCatalogSnapshotV1, TagCandidateV1)
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Tag, TagCategory, TaskFolder, Topic

class MetadataCatalogConsistencyError(RuntimeError):
    def __init__(self, entity_type, entity_id, relation):
        self.entity_type = entity_type
        super().__init__(f"inconsistent_{entity_type}:{entity_id}:{relation}")

class SqlAlchemyMetadataCatalogLoader:
    def __init__(self,db):self.db=db
    async def load(self):
        live=("active","provisional")
        subjects=(await self.db.scalars(select(Subject).where(Subject.status.in_(live)).order_by(Subject.name))).all()
        grades=(await self.db.scalars(select(Grade).where(Grade.status.in_(live)).order_by(Grade.number))).all()
        topics=(await self.db.scalars(select(Topic).where(Topic.status.in_(live)).order_by(Topic.name))).all()
        subs=(await self.db.scalars(select(Subtopic).where(Subtopic.status.in_(live)).order_by(Subtopic.name))).all()
        skills=(await self.db.scalars(select(Skill).where(Skill.status.in_(live)).order_by(Skill.name))).all()
        folders=(await self.db.scalars(select(TaskFolder).order_by(TaskFolder.name))).all()
        categories=(await self.db.scalars(select(TagCategory.code).order_by(TagCategory.sort_order))).all()
        tags=(await self.db.scalars(select(Tag).where(Tag.status=="active").order_by(Tag.normalized_name))).all()
        topic_by={x.id:x for x in topics}; sub_by={x.id:x for x in subs}
        subject_ids={x.id for x in subjects}; grade_ids={x.id for x in grades}
        category_codes=set(categories); folder_ids={x.id for x in folders}
        folder_by={x.id:x for x in folders}
        def require(entity, entity_id, relation, valid):
            if not valid:
                raise MetadataCatalogConsistencyError(entity, entity_id, relation)
        for x in topics:
            require("topic",x.id,"subject",x.subject_id in subject_ids)
            require("topic",x.id,"grade",x.grade_id in grade_ids)
        for x in subs: require("subtopic",x.id,"topic",x.topic_id in topic_by)
        for x in skills: require("skill",x.id,"subtopic",x.subtopic_id in sub_by)
        for x in folders:
            require("folder",x.id,"subject",x.subject_id in subject_ids)
            require("folder",x.id,"parent",x.parent_id is None or x.parent_id in folder_ids)
            require("folder",x.id,"parent_subject",x.parent_id is None or folder_by[x.parent_id].subject_id==x.subject_id)
        for x in tags:
            require("tag",x.id,"category",x.category_code in category_codes)
            require("tag",x.id,"subject",x.subject_id is None or x.subject_id in subject_ids)
        return MetadataCatalogSnapshotV1(
            subjects=tuple(CatalogItemV1(id=x.id,name=x.name,catalog_status=x.status) for x in subjects),
            grades=tuple(CatalogItemV1(id=x.id,name=x.name,grade_number=x.number,catalog_status=x.status) for x in grades),
            topics=tuple(CatalogItemV1(id=x.id,name=x.name,subject_id=x.subject_id,grade_id=x.grade_id,catalog_status=x.status) for x in topics),
            subtopics=tuple(CatalogItemV1(id=x.id,name=x.name,topic_id=x.topic_id,
                subject_id=topic_by[x.topic_id].subject_id,grade_id=topic_by[x.topic_id].grade_id,catalog_status=x.status) for x in subs),
            skills=tuple(CatalogItemV1(id=x.id,name=x.name,subtopic_id=x.subtopic_id,
                topic_id=sub_by[x.subtopic_id].topic_id,catalog_status=x.status) for x in skills),
            folders=tuple(CatalogItemV1(id=x.id,name=x.name,subject_id=x.subject_id,parent_id=x.parent_id) for x in folders),
            tag_categories=tuple(categories),tags=tuple(TagCandidateV1(id=x.id,name=x.name,
                category_code=x.category_code,subject_id=x.subject_id) for x in tags))
