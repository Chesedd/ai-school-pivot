"""SQLAlchemy adapters for Content Bank application ports."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import String, and_, bindparam, delete, func, select, update, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.application.content_bank import AcceptedAnswerDTO, ActorContext, ArchiveResult, AuditEventDTO, AuditEventRecord, AuditPage, CatalogRecord, CatalogRef, ConflictError, CreateTaskCommand, DUPLICATE_CANDIDATE_THRESHOLD, DuplicateCandidate, DuplicateCandidateRecord, DuplicateQuery, EMPTY_METHODOLOGY, ExpectedSolutionDTO, HintDTO, LockedVersion, MethodologyDTO, RubricDTO, RubricItemDTO, SaveMethodologyCommand, SkillLinkDTO, TagRefDTO, TaskCard, TaskCardVersion, TaskDTO, TaskListItem, TaskListPage, TaskListQuery, TaskVersionDTO, TaskVersionSummary, TypicalErrorDTO, VersionState
from app.infrastructure.models import AcceptedAnswer, AuditLog, FolderAuditLog, TaskFolder, ExpectedSolution, Grade, Hint, ImportPreview, Rubric, RubricItem, Skill, Subject, Subtopic, Tag, TagCategory, Task, TaskErrorLink, TaskSkillLink, TaskVersion, TaskVersionTag, Topic, TypicalError
from app.application.folders import FolderSummaryDTO, FolderTreeNodeDTO, TaskLocationDTO
from app.application.content_bank import ImportCatalogContext, ImportIssue, ImportPreviewRecord, ImportPreviewRow, SkillLinkInput, VersionContentInput


class SQLAlchemyContentBankRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_duplicate_candidates(self, query: DuplicateQuery) -> tuple[DuplicateCandidateRecord, ...]:
        # SET LOCAL controls the `%` operator and therefore the GIN-indexed
        # candidate scan. All policy filtering and the public limit happen later.
        await self.session.execute(select(func.set_config("pg_trgm.similarity_threshold",str(DUPLICATE_CANDIDATE_THRESHOLD),True)))
        latest_numbers=select(TaskVersion.task_id,func.max(TaskVersion.version_no).label("version_no")).group_by(TaskVersion.task_id).subquery()
        primary=TaskSkillLink.__table__.alias("duplicate_primary_skill")
        normalized=func.regexp_replace(func.lower(func.btrim(TaskVersion.statement)),r"\s+"," ","g")
        score=func.similarity(normalized,query.statement)
        stmt=(select(Task.id,TaskVersion.id,TaskVersion.version_no,TaskVersion.title,TaskVersion.status,
            TaskVersion.statement,score,primary.c.skill_id,ExpectedSolution.final_answer)
            .join(latest_numbers,and_(latest_numbers.c.task_id==Task.id))
            .join(TaskVersion,and_(TaskVersion.task_id==Task.id,TaskVersion.version_no==latest_numbers.c.version_no))
            .outerjoin(primary,and_(primary.c.task_version_id==TaskVersion.id,primary.c.is_primary.is_(True)))
            .outerjoin(ExpectedSolution,ExpectedSolution.task_version_id==TaskVersion.id)
            .where(Task.archived_at.is_(None),TaskVersion.status.in_(("draft","review","approved")),
                TaskVersion.statement.op("%")(query.statement)))
        if query.exclude_task_id is not None: stmt=stmt.where(Task.id!=query.exclude_task_id)
        rows=(await self.session.execute(stmt)).all()
        return tuple(DuplicateCandidateRecord(*row) for row in rows)

    @staticmethod
    def _command_json(c: CreateTaskCommand) -> dict:
        v=c.initial_version
        return {"subject_id":str(c.subject_id),"grade_id":str(c.grade_id),"topic_id":str(c.topic_id),"subtopic_id":str(c.subtopic_id) if c.subtopic_id else None,"initial_version":{"title":v.title,"statement":v.statement,"task_type":v.task_type,"answer_format":v.answer_format,"difficulty":v.difficulty,"source":v.source,"skills":[{"skill_id":str(x.skill_id),"weight":str(x.weight),"is_primary":x.is_primary} for x in v.skills]}}

    @staticmethod
    def _command_from_json(x: dict) -> CreateTaskCommand:
        from decimal import Decimal
        v=x["initial_version"]
        return CreateTaskCommand(UUID(x["subject_id"]),UUID(x["grade_id"]),UUID(x["topic_id"]),UUID(x["subtopic_id"]) if x["subtopic_id"] else None,VersionContentInput(v["title"],v["statement"],v["task_type"],v["answer_format"],v["difficulty"],v["source"],tuple(SkillLinkInput(UUID(s["skill_id"]),Decimal(s["weight"]),s["is_primary"]) for s in v["skills"])))

    async def save_import_preview(self, p: ImportPreviewRecord) -> None:
        def issue_json(i):
            return {"code":i.code,"field":i.field,"message":i.message,"severity":i.severity,
                "duplicate_row_number":i.duplicate_row_number,
                "duplicate_candidates":[{**c.__dict__,"task_id":str(c.task_id),"task_version_id":str(c.task_version_id),"reasons":list(c.reasons)} for c in i.duplicate_candidates]}
        rows=[{"row_number":r.row_number,"status":r.status,"command":self._command_json(r.command),"issues":[issue_json(i) for i in r.issues]} for r in p.rows]
        self.session.add(ImportPreview(import_token=p.import_token,format=p.format,actor_id=p.actor_id,rows=rows,created_at=p.created_at,expires_at=p.expires_at,committed_at=p.committed_at)); await self.session.flush()

    async def get_import_preview_for_update(self, token: UUID) -> ImportPreviewRecord | None:
        m=(await self.session.execute(select(ImportPreview).where(ImportPreview.import_token==token).with_for_update())).scalar_one_or_none()
        if not m:return None
        def issue_from_json(i):
            candidates=tuple(DuplicateCandidate(
                UUID(c["task_id"]),UUID(c["task_version_id"]),c["version_no"],c["title"],c["status"],c["statement"],c["statement_similarity"],c["same_primary_skill"],c["same_final_answer"],tuple(c["reasons"])) for c in i.get("duplicate_candidates",()))
            return ImportIssue(i["code"],i["field"],i["message"],i.get("severity","error"),candidates,i.get("duplicate_row_number"))
        rows=tuple(ImportPreviewRow(x["row_number"],x["status"],self._command_from_json(x["command"]),tuple(issue_from_json(i) for i in x["issues"])) for x in m.rows)
        return ImportPreviewRecord(m.import_token,m.format,m.actor_id,rows,m.created_at,m.expires_at,m.committed_at)

    async def mark_import_preview_committed(self, token: UUID, at: datetime) -> None:
        await self.session.execute(update(ImportPreview).where(ImportPreview.import_token==token).values(committed_at=at))

    async def get_import_catalog_context(self, commands: tuple[CreateTaskCommand, ...]) -> ImportCatalogContext:
        subject_ids={c.subject_id for c in commands}; grade_ids={c.grade_id for c in commands}; topic_ids={c.topic_id for c in commands}
        subtopic_ids={c.subtopic_id for c in commands if c.subtopic_id}; skill_ids={s.skill_id for c in commands for s in c.initial_version.skills}
        subjects=(await self.session.execute(select(Subject).where(Subject.id.in_(subject_ids)))).scalars()
        grades=(await self.session.execute(select(Grade).where(Grade.id.in_(grade_ids)))).scalars()
        topics=(await self.session.execute(select(Topic).where(Topic.id.in_(topic_ids)))).scalars()
        subtopics=(await self.session.execute(select(Subtopic).where(Subtopic.id.in_(subtopic_ids)))).scalars()
        skills=(await self.session.execute(select(Skill).options(selectinload(Skill.subtopic)).where(Skill.id.in_(skill_ids)))).scalars()
        return ImportCatalogContext(
            {x.id:CatalogRecord(x.id,x.name) for x in subjects}, {x.id:CatalogRecord(x.id,x.name) for x in grades},
            {x.id:CatalogRecord(x.id,x.name,subject_id=x.subject_id,grade_id=x.grade_id) for x in topics},
            {x.id:CatalogRecord(x.id,x.name,topic_id=x.topic_id) for x in subtopics},
            {x.id:CatalogRecord(x.id,x.name,topic_id=x.subtopic.topic_id,subtopic_id=x.subtopic_id) for x in skills})

    async def get_subject(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Subject, value)
        return CatalogRecord(row.id, row.name) if row else None

    async def get_grade(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Grade, value)
        return CatalogRecord(row.id, row.name) if row else None

    async def get_topic(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Topic, value)
        return CatalogRecord(row.id, row.name, subject_id=row.subject_id, grade_id=row.grade_id) if row else None

    async def get_subtopic(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Subtopic, value)
        return CatalogRecord(row.id, row.name, topic_id=row.topic_id) if row else None

    async def get_skills(self, values: set[UUID]) -> dict[UUID, CatalogRecord]:
        if not values:
            return {}
        result = await self.session.execute(select(Skill).options(selectinload(Skill.subtopic)).where(Skill.id.in_(values)))
        return {row.id: CatalogRecord(row.id, row.name, topic_id=row.subtopic.topic_id, subtopic_id=row.subtopic_id) for row in result.scalars()}

    async def create_task_with_initial_version(self, command: CreateTaskCommand, actor: ActorContext) -> TaskDTO:
        task = Task(subject_id=command.subject_id, folder_id=command.folder_id, grade_id=command.grade_id, topic_id=command.topic_id, subtopic_id=command.subtopic_id, created_by=actor.actor_id)
        self.session.add(task)
        await self.session.flush()
        content = command.initial_version
        version = TaskVersion(task_id=task.id, version_no=1, title=content.title, statement=content.statement, task_type=content.task_type, answer_format=content.answer_format, difficulty=content.difficulty, source=content.source, status="draft", created_by=actor.actor_id)
        self.session.add(version)
        await self.session.flush()
        skill_rows = await self.get_skills({link.skill_id for link in content.skills})
        links = [TaskSkillLink(task_version_id=version.id, skill_id=item.skill_id, weight=item.weight, is_primary=item.is_primary) for item in content.skills]
        self.session.add_all(links)
        tags=await self._validate_initial_tags(command.tag_ids,command.subject_id)
        self.session.add_all([TaskVersionTag(task_version_id=version.id,tag_id=x.id,attached_by=actor.actor_id) for x in tags])
        occurred=datetime.now().astimezone()
        for tag in sorted(tags,key=lambda x:str(x.id)):
            self.session.add(AuditLog(task_id=task.id,task_version_id=version.id,version_no=1,action="tag_added_to_version",actor_id=actor.actor_id,
                details={"task_id":str(task.id),"version_id":str(version.id),"tag_id":str(tag.id),"canonical_name":tag.name,
                    "category_code":tag.category_code,"subject_id":str(tag.subject_id) if tag.subject_id else None,
                    "actor_id":str(actor.actor_id),"occurred_at":occurred.isoformat()}))
        await self.session.flush()
        await self.session.refresh(task)
        await self.session.refresh(version)
        refs=tuple(self._tag_ref(x) for x in tags)
        return TaskDTO(task.id, task.subject_id, task.grade_id, task.topic_id, task.subtopic_id, task.created_by, task.created_at, TaskVersionDTO(version.id, 1, version.title, version.statement, version.task_type, version.answer_format, version.difficulty, version.source, version.status, version.created_by, version.created_at, tuple(SkillLinkDTO(link.id, link.skill_id, skill_rows[link.skill_id].name, link.weight, link.is_primary) for link in links),refs), folder_id=task.folder_id)

    async def _validate_initial_tags(self, ids, subject_id):
        from app.application.managed_tags import TagError
        if len(ids)!=len(set(ids)): raise TagError("duplicate_tag_assignment","Теги не должны повторяться.",400,"tag_ids")
        if len(ids)>8: raise TagError("tag_limit_exceeded","Можно назначить не более восьми тегов.",422,"tag_ids")
        if not ids:return []
        rows=list((await self.session.execute(select(Tag).options(selectinload(Tag.category),selectinload(Tag.replacement)).where(Tag.id.in_(ids)).order_by(Tag.id).with_for_update(of=Tag))).unique().scalars())
        if len(rows)!=len(ids):raise TagError("tag_not_found","Тег не найден.",404,"tag_ids")
        if any(x.status!="active" for x in rows):raise TagError("tag_deprecated","Устаревший тег нельзя добавить.",409,"tag_ids")
        if any(x.subject_id not in (None,subject_id) for x in rows):raise TagError("tag_subject_mismatch","Тег относится к другому предмету.",422,"tag_ids")
        return sorted(rows,key=lambda x:(0 if x.subject_id==subject_id else 1,x.category.sort_order,x.normalized_name,str(x.id)))

    @staticmethod
    def _tag_ref(x):
        r=x.replacement
        return TagRefDTO(x.id,x.name,x.category_code,x.subject_id,x.status,{"id":r.id,"name":r.name,"category_code":r.category_code,"subject_id":r.subject_id,"status":r.status} if r else None)

    async def list_tasks(self, query: TaskListQuery) -> TaskListPage:
        latest_numbers = select(TaskVersion.task_id, func.max(TaskVersion.version_no).label("version_no")).group_by(TaskVersion.task_id).subquery()
        latest = TaskVersion.__table__.alias("latest_version")
        primary_link = TaskSkillLink.__table__.alias("primary_link")
        primary_skill = Skill.__table__.alias("primary_skill")
        columns = (
            Task.id, Task.subject_id, Subject.name, Task.grade_id, Grade.name,
            Task.topic_id, Topic.name, Task.subtopic_id, Subtopic.name,
            latest.c.id, latest.c.version_no, latest.c.title, latest.c.statement,
            latest.c.task_type, latest.c.answer_format, latest.c.difficulty,
            latest.c.status, primary_skill.c.id, primary_skill.c.name,
            Task.created_at, Task.archived_at, Task.updated_at, Task.folder_id, TaskFolder.name,
        )
        base = (select(*columns).join(Subject, Subject.id == Task.subject_id)
            .join(Grade, Grade.id == Task.grade_id).join(Topic, Topic.id == Task.topic_id)
            .outerjoin(Subtopic, Subtopic.id == Task.subtopic_id)
            .outerjoin(TaskFolder, TaskFolder.id == Task.folder_id)
            .join(latest_numbers, latest_numbers.c.task_id == Task.id)
            .join(latest, and_(latest.c.task_id == Task.id, latest.c.version_no == latest_numbers.c.version_no))
            .outerjoin(primary_link, and_(primary_link.c.task_version_id == latest.c.id, primary_link.c.is_primary.is_(True)))
            .outerjoin(primary_skill, primary_skill.c.id == primary_link.c.skill_id))
        if query.root_only:
            base=base.where(Task.folder_id.is_(None))
        if query.folder_id is not None:
            if query.folder_scope == "subtree":
                scope=text("WITH RECURSIVE s AS (SELECT id FROM task_folders WHERE id=:folder_id UNION ALL SELECT f.id FROM task_folders f JOIN s ON f.parent_id=s.id) SELECT id FROM s")
                base=base.where(Task.folder_id.in_(scope)).params(folder_id=query.folder_id)
            else: base=base.where(Task.folder_id==query.folder_id)
        if query.status == "archived":
            base = base.where(Task.archived_at.is_not(None))
        else:
            base = base.where(Task.archived_at.is_(None))
        filters = ((Task.subject_id, query.subject_id), (Task.grade_id, query.grade_id), (Task.topic_id, query.topic_id), (Task.subtopic_id, query.subtopic_id), (latest.c.task_type, query.task_type), (latest.c.status, query.status))
        for column, value in filters:
            if value is not None:
                base = base.where(column == value)
        if query.difficulty_min is not None:
            base = base.where(latest.c.difficulty >= query.difficulty_min)
        if query.difficulty_max is not None:
            base = base.where(latest.c.difficulty <= query.difficulty_max)
        if query.skill_id is not None:
            skill_match = select(TaskSkillLink.id).where(TaskSkillLink.task_version_id == latest.c.id, TaskSkillLink.skill_id == query.skill_id).exists()
            base = base.where(skill_match)
        for tag_id in query.tag_ids:
            base=base.where(select(TaskVersionTag.tag_id).where(TaskVersionTag.task_version_id==latest.c.id,TaskVersionTag.tag_id==tag_id).exists())
        rank = None
        if query.q is not None:
            # SQLAlchemy binds q; no user text is interpolated into SQL.
            tsquery = func.websearch_to_tsquery("russian", query.q)
            base = base.where(latest.c.search_vector.op("@@")(tsquery))
            rank = func.ts_rank_cd(latest.c.search_vector, tsquery)
        total = int((await self.session.scalar(select(func.count()).select_from(base.order_by(None).subquery()))) or 0)
        sort_columns = {"created_at": Task.created_at, "updated_at": Task.updated_at, "title": latest.c.title, "difficulty": latest.c.difficulty, "status": latest.c.status, "version_no": latest.c.version_no, "relevance": rank}
        sort_column = sort_columns[query.sort_by]
        ordered = sort_column.asc().nulls_last() if query.sort_order == "asc" else sort_column.desc().nulls_last()
        ordering = [ordered]
        if query.sort_by == "relevance":
            ordering.append(Task.updated_at.desc())
        ordering.append(Task.id.asc())
        rows = (await self.session.execute(base.order_by(*ordering).offset(query.offset).limit(query.limit))).all()
        items=[TaskListItem(*row) for row in rows]
        tag_map=await self._tags_for_versions([x.latest_version_id for x in items])
        items=[__import__('dataclasses').replace(x,tags=tag_map.get(x.latest_version_id,())) for x in items]
        return TaskListPage(tuple(items), total, query.offset, query.limit)

    async def _tags_for_versions(self,version_ids):
        if not version_ids:return {}
        rows=(await self.session.execute(select(TaskVersionTag.task_version_id,Tag).join(Tag).options(selectinload(Tag.category),selectinload(Tag.replacement)).where(TaskVersionTag.task_version_id.in_(version_ids)).order_by(TaskVersionTag.task_version_id,Tag.category_code,Tag.normalized_name,Tag.id))).all()
        result={}
        for version_id,tag in rows:result.setdefault(version_id,[]).append(self._tag_ref(tag))
        return {k:tuple(sorted(v,key=lambda x:(0 if x.subject_id is not None else 1,next(t.category.sort_order for version_id,t in rows if t.id==x.id),x.name.casefold(),str(x.id)))) for k,v in result.items()}

    async def get_task_card(self, task_id: UUID) -> TaskCard | None:
        """Load a card in a bounded number of queries, including archived tasks."""
        rows = (await self.session.execute(
            select(Task, Subject, Grade, Topic, Subtopic, TaskVersion)
            .join(Subject, Subject.id == Task.subject_id)
            .join(Grade, Grade.id == Task.grade_id)
            .join(Topic, and_(Topic.id == Task.topic_id, Topic.subject_id == Task.subject_id, Topic.grade_id == Task.grade_id))
            .outerjoin(Subtopic, and_(Subtopic.id == Task.subtopic_id, Subtopic.topic_id == Task.topic_id))
            .join(TaskVersion, TaskVersion.task_id == Task.id)
            .where(Task.id == task_id)
            .order_by(TaskVersion.version_no.desc(), TaskVersion.id.asc())
        )).all()
        if not rows:
            return None
        task, subject, grade, topic, subtopic, latest = rows[0]
        links = (await self.session.execute(
            select(TaskSkillLink, Skill)
            .join(Skill, Skill.id == TaskSkillLink.skill_id)
            .where(TaskSkillLink.task_version_id == latest.id)
            .order_by(TaskSkillLink.is_primary.desc(), Skill.name.asc(), Skill.id.asc())
        )).all()
        # The DB uniqueness constraint prevents duplicates; the guard also makes
        # the adapter robust when reading legacy/inconsistent data.
        seen: set[UUID] = set()
        skills = []
        for link, skill in links:
            if skill.id not in seen:
                seen.add(skill.id)
                skills.append(SkillLinkDTO(link.id, skill.id, skill.name, link.weight, link.is_primary))
        tag_map=await self._tags_for_versions([r[-1].id for r in rows])
        summaries = tuple(TaskVersionSummary(version.id, version.version_no, version.status, version.created_at, version.approved_at,tag_map.get(version.id,())) for *_, version in rows)
        approved = next((summary for summary in summaries if summary.approved_at is not None), None)
        methodology = await self._get_methodology(latest.id)
        return TaskCard(
            task.id, CatalogRef(subject.id, subject.name), CatalogRef(grade.id, grade.name),
            CatalogRef(topic.id, topic.name), CatalogRef(subtopic.id, subtopic.name) if subtopic else None,
            task.created_by, task.created_at, task.archived_at,
            TaskCardVersion(latest.id, latest.version_no, latest.title, latest.statement, latest.task_type,
                latest.answer_format, latest.difficulty, latest.source, latest.status, tuple(skills),
                latest.created_by, latest.created_at, latest.approved_by, latest.approved_at, methodology, latest.updated_at,tag_map.get(latest.id,())),
            approved, summaries, task.updated_at,
        )

    async def lock_version(self, task_version_id: UUID) -> LockedVersion | None:
        version = await self.session.scalar(select(TaskVersion).where(TaskVersion.id == task_version_id).with_for_update())
        if version is None:
            return None
        latest_no = await self.session.scalar(select(func.max(TaskVersion.version_no)).where(TaskVersion.task_id == version.task_id))
        skill_ids = frozenset((await self.session.scalars(select(TaskSkillLink.skill_id).where(TaskSkillLink.task_version_id == version.id))).all())
        return LockedVersion(version.id, version.answer_format, version.status, version.version_no == latest_no, skill_ids, version.task_id, version.version_no)

    async def lock_task_version(self, task_id: UUID, version_no: int) -> VersionState | None:
        task = await self.session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None:
            return None
        version = await self.session.scalar(select(TaskVersion).where(TaskVersion.task_id == task_id, TaskVersion.version_no == version_no).with_for_update())
        if version is None:
            return None
        latest_no = await self.session.scalar(select(func.max(TaskVersion.version_no)).where(TaskVersion.task_id == task_id))
        links = (await self.session.execute(select(TaskSkillLink, Skill).join(Skill).where(TaskSkillLink.task_version_id == version.id).order_by(TaskSkillLink.id))).all()
        topic = await self.session.get(Topic, task.topic_id)
        subtopic = await self.session.get(Subtopic, task.subtopic_id) if task.subtopic_id else None
        skill_subtopics = {row.id: row for row in (await self.session.scalars(
            select(Subtopic).where(Subtopic.id.in_({skill.subtopic_id for _, skill in links}))
        )).all()} if links else {}
        classification_valid = bool(topic and topic.subject_id == task.subject_id and topic.grade_id == task.grade_id)
        classification_valid = classification_valid and (task.subtopic_id is None or bool(subtopic and subtopic.topic_id == task.topic_id))
        classification_valid = classification_valid and all(
            skill.subtopic_id in skill_subtopics
            and skill_subtopics[skill.subtopic_id].topic_id == task.topic_id
            and (task.subtopic_id is None or skill.subtopic_id == task.subtopic_id)
            for _, skill in links
        )
        return VersionState(task.id, version.id, version.version_no, version.status, version.statement, version.task_type,
            version.answer_format, version.created_at, version.created_by, version.approved_at, version.approved_by,
            task.archived_at, version.version_no == latest_no,
            tuple(SkillLinkDTO(link.id, skill.id, skill.name, link.weight, link.is_primary) for link, skill in links),
            await self._get_methodology(version.id), classification_valid)

    async def set_version_status(self, task_version_id: UUID, status: str, approved_at: datetime | None = None, approved_by: UUID | None = None) -> None:
        version = await self.session.get(TaskVersion, task_version_id)
        assert version is not None
        version.status = status
        version.updated_at = func.now()
        await self.session.execute(update(Task).where(Task.id == version.task_id).values(updated_at=func.now()))
        if approved_at is not None:
            version.approved_at, version.approved_by = approved_at, approved_by
        await self.session.flush()

    async def archive_other_approved(self, task_id: UUID, except_version_id: UUID) -> None:
        rows = (await self.session.scalars(select(TaskVersion).where(TaskVersion.task_id == task_id, TaskVersion.status == "approved", TaskVersion.id != except_version_id).with_for_update())).all()
        for row in rows:
            row.status = "archived"
            row.updated_at = func.now()
        await self.session.flush()

    async def clone_version(self, task_id: UUID, source_version_no: int, actor: ActorContext) -> VersionState:
        source = await self.session.scalar(select(TaskVersion).where(TaskVersion.task_id == task_id, TaskVersion.version_no == source_version_no))
        assert source is not None
        unfinished = await self.session.scalar(select(TaskVersion.id).where(TaskVersion.task_id == task_id, TaskVersion.version_no > source_version_no, TaskVersion.status.in_(("draft", "review"))))
        if unfinished is not None:
            raise ConflictError("У карточки уже есть незавершённая версия.")
        next_no = int((await self.session.scalar(select(func.max(TaskVersion.version_no)).where(TaskVersion.task_id == task_id))) or 0) + 1
        target = TaskVersion(task_id=task_id, version_no=next_no, title=source.title, statement=source.statement, task_type=source.task_type, answer_format=source.answer_format, difficulty=source.difficulty, source=source.source, status="draft", created_by=actor.actor_id)
        self.session.add(target); await self.session.flush()
        await self.session.execute(update(Task).where(Task.id == task_id).values(updated_at=func.now()))
        links = (await self.session.scalars(select(TaskSkillLink).where(TaskSkillLink.task_version_id == source.id))).all()
        self.session.add_all([TaskSkillLink(task_version_id=target.id, skill_id=x.skill_id, weight=x.weight, is_primary=x.is_primary) for x in links])
        tag_ids=(await self.session.scalars(select(TaskVersionTag.tag_id).where(TaskVersionTag.task_version_id==source.id))).all()
        self.session.add_all([TaskVersionTag(task_version_id=target.id,tag_id=x,attached_by=actor.actor_id) for x in tag_ids])
        solution = await self.session.scalar(select(ExpectedSolution).where(ExpectedSolution.task_version_id == source.id))
        if solution: self.session.add(ExpectedSolution(task_version_id=target.id, solution_text=solution.solution_text, final_answer=solution.final_answer, solution_steps_json=list(solution.solution_steps_json)))
        rubric = await self.session.scalar(select(Rubric).options(selectinload(Rubric.items)).where(Rubric.task_version_id == source.id))
        if rubric:
            copied = Rubric(task_version_id=target.id, max_score=rubric.max_score, grading_mode=rubric.grading_mode, notes=rubric.notes)
            self.session.add(copied); await self.session.flush()
            self.session.add_all([RubricItem(rubric_id=copied.id, criterion=x.criterion, max_points=x.max_points, required=x.required, common_failure=x.common_failure, order_index=x.order_index) for x in rubric.items])
        answers = (await self.session.scalars(select(AcceptedAnswer).where(AcceptedAnswer.task_version_id == source.id))).all()
        self.session.add_all([AcceptedAnswer(task_version_id=target.id, answer_value=x.answer_value, tolerance=x.tolerance, unit=x.unit, normalization_rule=x.normalization_rule) for x in answers])
        errors = (await self.session.scalars(select(TaskErrorLink).where(TaskErrorLink.task_version_id == source.id))).all()
        self.session.add_all([TaskErrorLink(task_version_id=target.id, typical_error_id=x.typical_error_id, detection_hint=x.detection_hint) for x in errors])
        hints = (await self.session.scalars(select(Hint).where(Hint.task_version_id == source.id))).all()
        self.session.add_all([Hint(task_version_id=target.id, level=x.level, hint_text=x.hint_text) for x in hints])
        await self.session.flush(); await self.session.refresh(target)
        result = await self.lock_task_version(task_id, next_no)
        assert result is not None
        return result

    async def archive_task_versions(self, task_id: UUID, archived_at: datetime) -> ArchiveResult | None:
        task = await self.session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None: return None
        versions = (await self.session.scalars(select(TaskVersion).where(TaskVersion.task_id == task_id).order_by(TaskVersion.version_no.desc()).with_for_update())).all()
        changed = task.archived_at is None
        previous_status = versions[0].status
        if changed:
            task.archived_at = archived_at
            task.updated_at = func.now()
            for version in versions:
                if version.status in {"draft", "review", "approved"}:
                    version.status = "archived"
                    version.updated_at = func.now()
            await self.session.flush()
        return ArchiveResult(task.id, task.archived_at, versions[0].status, versions[0].id, versions[0].version_no, previous_status, changed)

    async def append_audit(self, event: AuditEventRecord) -> None:
        self.session.add(AuditLog(task_id=event.task_id, task_version_id=event.task_version_id,
            version_no=event.version_no, action=event.action, actor_id=event.actor_id,
            reason=event.reason, details=event.details or {}))
        await self.session.flush()

    async def list_audit(self, task_id: UUID, offset: int, limit: int, action: str | None) -> AuditPage | None:
        if await self.session.get(Task, task_id) is None:
            return None
        filtered = select(AuditLog).where(AuditLog.task_id == task_id)
        if action is not None:
            filtered = filtered.where(AuditLog.action == action)
        total = int((await self.session.scalar(select(func.count()).select_from(filtered.subquery()))) or 0)
        rows = (await self.session.scalars(filtered.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).offset(offset).limit(limit))).all()
        return AuditPage(tuple(AuditEventDTO(row.id, row.task_id, row.task_version_id, row.version_no,
            row.action, row.actor_id, row.reason, row.details, row.occurred_at) for row in rows), total, offset, limit)

    async def replace_methodology(self, command: SaveMethodologyCommand) -> MethodologyDTO:
        version_id = command.task_version_id
        await self.session.execute(update(TaskVersion).where(TaskVersion.id == version_id).values(updated_at=func.now()))
        await self.session.execute(update(Task).where(Task.id == select(TaskVersion.task_id).where(TaskVersion.id == version_id).scalar_subquery()).values(updated_at=func.now()))
        rubric_ids = select(Rubric.id).where(Rubric.task_version_id == version_id)
        await self.session.execute(delete(RubricItem).where(RubricItem.rubric_id.in_(rubric_ids)))
        for model in (ExpectedSolution, Rubric, AcceptedAnswer, TaskErrorLink, Hint):
            await self.session.execute(delete(model).where(model.task_version_id == version_id))
        if command.expected_solution:
            value = command.expected_solution
            self.session.add(ExpectedSolution(task_version_id=version_id, solution_text=value.solution_text.strip(), final_answer=value.final_answer, solution_steps_json=[x.strip() for x in value.solution_steps]))
        if command.rubric:
            value = command.rubric
            rubric = Rubric(task_version_id=version_id, max_score=sum((x.max_points for x in value.items), start=0), grading_mode=value.grading_mode, notes=value.notes)
            self.session.add(rubric); await self.session.flush()
            self.session.add_all([RubricItem(rubric_id=rubric.id, criterion=x.criterion.strip(), max_points=x.max_points, required=x.required, common_failure=x.common_failure, order_index=i) for i, x in enumerate(value.items)])
        self.session.add_all([AcceptedAnswer(task_version_id=version_id, answer_value=x.answer_value.strip(), tolerance=x.tolerance, unit=x.unit, normalization_rule=x.normalization_rule) for x in command.accepted_answers])
        for value in command.typical_errors:
            existing = await self.session.scalar(select(TypicalError).where(TypicalError.skill_id == value.skill_id, TypicalError.code == value.code.strip()))
            definition = (value.title.strip(), value.description.strip(), value.severity, value.remediation_hint)
            if existing:
                if (existing.title, existing.description, existing.severity, existing.remediation_hint) != definition:
                    raise ConflictError("Определение типичной ошибки с таким кодом отличается.", "typical_error_definition_conflict")
                typical = existing
            else:
                typical = TypicalError(skill_id=value.skill_id, code=value.code.strip(), title=definition[0], description=definition[1], severity=definition[2], remediation_hint=definition[3])
                self.session.add(typical); await self.session.flush()
            self.session.add(TaskErrorLink(task_version_id=version_id, typical_error_id=typical.id, detection_hint=value.detection_hint))
        self.session.add_all([Hint(task_version_id=version_id, level=x.level, hint_text=x.hint_text.strip()) for x in command.hints])
        await self.session.flush()
        return await self._get_methodology(version_id)

    async def _get_methodology(self, version_id: UUID) -> MethodologyDTO:
        solution = await self.session.scalar(select(ExpectedSolution).where(ExpectedSolution.task_version_id == version_id))
        rubric = await self.session.scalar(select(Rubric).options(selectinload(Rubric.items)).where(Rubric.task_version_id == version_id))
        answers = (await self.session.scalars(select(AcceptedAnswer).where(AcceptedAnswer.task_version_id == version_id).order_by(AcceptedAnswer.id))).all()
        errors = (await self.session.execute(select(TaskErrorLink, TypicalError).join(TypicalError).where(TaskErrorLink.task_version_id == version_id).order_by(TaskErrorLink.id))).all()
        hints = (await self.session.scalars(select(Hint).where(Hint.task_version_id == version_id).order_by(Hint.level))).all()
        if not any((solution, rubric, answers, errors, hints)):
            return EMPTY_METHODOLOGY
        return MethodologyDTO(
            ExpectedSolutionDTO(solution.id, solution.solution_text, solution.final_answer, tuple(solution.solution_steps_json)) if solution else None,
            RubricDTO(rubric.id, rubric.grading_mode, rubric.max_score, rubric.notes, tuple(RubricItemDTO(x.id, x.criterion, x.max_points, x.required, x.common_failure, x.order_index) for x in sorted(rubric.items, key=lambda x: x.order_index))) if rubric else None,
            tuple(AcceptedAnswerDTO(x.id, x.answer_value, x.tolerance, x.unit, x.normalization_rule) for x in answers),
            tuple(TypicalErrorDTO(x.id, x.skill_id, x.code, x.title, x.description, x.severity, x.remediation_hint, link.detection_hint) for link, x in errors),
            tuple(HintDTO(x.id, x.level, x.hint_text) for x in hints),
        )

    async def catalog(self, name: str) -> list[CatalogRecord]:
        if name == "subjects":
            rows = (await self.session.execute(select(Subject).order_by(Subject.name))).scalars()
            return [CatalogRecord(x.id, x.name, code=x.code) for x in rows]
        if name == "grades":
            rows = (await self.session.execute(select(Grade).order_by(Grade.number))).scalars()
            return [CatalogRecord(x.id, x.name, number=x.number) for x in rows]
        if name == "topics":
            rows = (await self.session.execute(select(Topic).order_by(Topic.name))).scalars()
            return [CatalogRecord(x.id, x.name, subject_id=x.subject_id, grade_id=x.grade_id, code=x.code) for x in rows]
        if name == "subtopics":
            rows = (await self.session.execute(select(Subtopic).order_by(Subtopic.name))).scalars()
            return [CatalogRecord(x.id, x.name, topic_id=x.topic_id, code=x.code) for x in rows]
        rows = (await self.session.execute(select(Skill).options(selectinload(Skill.subtopic)).order_by(Skill.name))).scalars()
        return [CatalogRecord(x.id, x.name, topic_id=x.subtopic.topic_id, subtopic_id=x.subtopic_id, code=x.code) for x in rows]


    async def lock_subject_tree(self, subject_id: UUID) -> None:
        # First 64 bits of SHA-256 UUID text, interpreted as signed bigint.
        statement = text("SELECT pg_advisory_xact_lock(('x'||substr(encode(digest(CAST(:id AS text),'sha256'),'hex'),1,16))::bit(64)::bigint)").bindparams(bindparam("id", type_=String()))
        await self.session.execute(statement, {"id": str(subject_id)})
    async def subject_exists(self, subject_id): return bool(await self.session.scalar(select(Subject.id).where(Subject.id==subject_id)))
    async def _folder_dto(self, m):
        depth=int(await self.session.scalar(text("WITH RECURSIVE a AS (SELECT id,parent_id,1 d FROM task_folders WHERE id=:id UNION ALL SELECT f.id,f.parent_id,a.d+1 FROM task_folders f JOIN a ON f.id=a.parent_id) SELECT max(d) FROM a"),{"id":m.id}) or 1)
        return FolderSummaryDTO(m.id,m.subject_id,m.parent_id,m.name,depth,m.created_at,m.updated_at)
    async def get_folder(self,id):
        if id is None:return None
        m=await self.session.get(TaskFolder,id); return await self._folder_dto(m) if m else None
    async def get_folder_for_update(self,id):
        if id is None:return None
        m=await self.session.scalar(select(TaskFolder).where(TaskFolder.id==id).with_for_update()); return await self._folder_dto(m) if m else None
    async def get_folder_subtree_for_update(self,id):
        ids=(await self.session.execute(text("WITH RECURSIVE s AS (SELECT id FROM task_folders WHERE id=:id UNION ALL SELECT f.id FROM task_folders f JOIN s ON f.parent_id=s.id) SELECT id FROM s FOR UPDATE"),{"id":id})).scalars().all()
        return tuple([x for x in [await self.get_folder_for_update(i) for i in ids] if x])
    async def sibling_name_exists(self,subject_id,parent_id,name,exclude_id):
        q=select(TaskFolder.id).where(TaskFolder.subject_id==subject_id,func.lower(TaskFolder.name)==name.lower())
        q=q.where(TaskFolder.parent_id.is_(None) if parent_id is None else TaskFolder.parent_id==parent_id)
        if exclude_id:q=q.where(TaskFolder.id!=exclude_id)
        return bool(await self.session.scalar(q.limit(1)))
    async def create_folder(self,c,name,depth):
        m=TaskFolder(subject_id=c.subject_id,parent_id=c.parent_id,name=name,created_by=c.actor_id,updated_by=c.actor_id);self.session.add(m);await self.session.flush();await self.session.refresh(m);return await self._folder_dto(m)
    async def _mutate_folder(self,current,actor_id,**values):
        values.update(updated_by=actor_id,updated_at=func.clock_timestamp());await self.session.execute(update(TaskFolder).where(TaskFolder.id==current.id).values(**values));await self.session.flush();return await self.get_folder_for_update(current.id)
    async def rename_folder(self,current,name,actor_id):return await self._mutate_folder(current,actor_id,name=name)
    async def move_folder(self,current,parent_id,actor_id):return await self._mutate_folder(current,actor_id,parent_id=parent_id)
    async def folder_nonempty(self,id):
        return bool(await self.session.scalar(select(TaskFolder.id).where(TaskFolder.parent_id==id).limit(1))),bool(await self.session.scalar(select(Task.id).where(Task.folder_id==id).limit(1)))
    async def delete_empty_folder(self,id):await self.session.execute(delete(TaskFolder).where(TaskFolder.id==id))
    @staticmethod
    def _snapshot(x): return None if x is None else {"id":str(x.id),"subject_id":str(x.subject_id),"parent_id":str(x.parent_id) if x.parent_id else None,"name":x.name}
    async def append_folder_audit(self,result,action,actor_id,before,deleted=False):
        self.session.add(FolderAuditLog(folder_id=result.id,subject_id=result.subject_id,action=action,actor_id=actor_id,details={"before":self._snapshot(before),"after":None if deleted else self._snapshot(result)}));await self.session.flush()
    async def lock_task(self,id):return await self.session.scalar(select(Task).where(Task.id==id).with_for_update())
    async def set_task_folder(self,task,folder_id):
        previous=task.folder_id;task.folder_id=folder_id;task.updated_at=func.clock_timestamp();await self.session.flush();await self.session.refresh(task);return TaskLocationDTO(task.id,task.subject_id,task.folder_id,previous,task.updated_at)
    async def task_location(self,task,previous):return TaskLocationDTO(task.id,task.subject_id,task.folder_id,previous,task.updated_at)
    async def append_task_move_audit(self,task,result,old,new,actor_id):
        self.session.add(AuditLog(task_id=task.id,action="task_folder_moved",actor_id=actor_id,details={"before":{"folder_id":str(old.id) if old else None,"folder_name":old.name if old else None},"after":{"folder_id":str(new.id) if new else None,"folder_name":new.name if new else None}}));await self.session.flush()
    async def get_level_contents(self,subject_id,folder_id,query):
        subject=await self.get_subject(subject_id)
        if not subject:return None
        folder=await self.get_folder(folder_id) if folder_id else None
        if folder_id and (not folder or folder.subject_id!=subject_id):return None
        models=(await self.session.scalars(select(TaskFolder).where(TaskFolder.subject_id==subject_id, TaskFolder.parent_id.is_(None) if folder_id is None else TaskFolder.parent_id==folder_id).order_by(func.lower(TaskFolder.name),TaskFolder.name,TaskFolder.id))).all()
        folders=tuple([await self._folder_dto(x) for x in models])
        ancestors=[]; cursor=folder
        while cursor: ancestors.append(cursor);cursor=await self.get_folder(cursor.parent_id)
        direct=await self.list_tasks(__import__('dataclasses').replace(query,subject_id=subject_id,folder_id=folder_id,folder_scope="direct",root_only=folder_id is None))
        subject_total=await self.session.scalar(select(func.count(Task.id)).where(Task.subject_id==subject_id))
        return {"subject":subject,"folder":folder,"breadcrumb":list(reversed(ancestors)),"folders":folders,"tasks":direct,"level_task_total":direct.total,"subject_task_total":int(subject_total or 0)}

    async def list_folder_tree(self,subject_id):
        rows=(await self.session.execute(text("WITH RECURSIVE t AS (SELECT *,1 depth FROM task_folders WHERE subject_id=:s AND parent_id IS NULL UNION ALL SELECT f.*,t.depth+1 FROM task_folders f JOIN t ON f.parent_id=t.id) SELECT id,subject_id,parent_id,name,depth FROM t ORDER BY depth,lower(name),name,id"),{"s":subject_id})).mappings().all()
        children={r.id:[] for r in rows}; roots=[]
        for r in reversed(rows):
            node=FolderTreeNodeDTO(r.id,r.subject_id,r.parent_id,r.name,r.depth,tuple(children[r.id])); (children[r.parent_id] if r.parent_id else roots).insert(0,node)
        def ordered(xs): return tuple(sorted(xs,key=lambda x:(x.name.lower(),x.name,str(x.id))))
        return ordered(roots)

class SQLAlchemyUnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self.session: AsyncSession | None = None
        self.repository: SQLAlchemyContentBankRepository

    async def __aenter__(self) -> SQLAlchemyUnitOfWork:
        self.session = self.factory()
        self.repository = SQLAlchemyContentBankRepository(self.session)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        assert self.session is not None
        if exc_type:
            await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        assert self.session is not None
        await self.session.commit()
