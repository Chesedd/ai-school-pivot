"""Managed-tag normalization and transactional catalog operations."""
from __future__ import annotations
from datetime import datetime
import unicodedata
from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.infrastructure.models import AuditLog, Subject, Tag, TagAuditLog, TagCategory, Task, TaskVersion, TaskVersionTag


class TagError(Exception):
    def __init__(self, code: str, message: str, status: int, field: str | None = None):
        super().__init__(message); self.code=code; self.status=status; self.field=field


def canonicalize_tag_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(ch) in {"Cc", "Cf"} for ch in value):
        raise TagError("tag_name_invalid", "Имя содержит недопустимые управляющие символы.", 422, "name")
    value = " ".join(value.split())
    if not value or len(value) > 80 or ";" in value or not any(ch.isalpha() or ch.isdigit() for ch in value):
        raise TagError("tag_name_invalid", "Имя тега должно содержать букву или цифру и иметь длину от 1 до 80.", 422, "name")
    return value


def normalize_tag_name(value: str) -> str:
    return canonicalize_tag_name(value).casefold().replace("ё", "е")


def snapshot(tag: Tag) -> dict[str, object]:
    def iso(x: datetime | None): return x.isoformat() if x else None
    return {"id":str(tag.id),"name":tag.name,"normalized_name":tag.normalized_name,"category_code":tag.category_code,
        "subject_id":str(tag.subject_id) if tag.subject_id else None,"status":tag.status,
        "replacement_tag_id":str(tag.replacement_tag_id) if tag.replacement_tag_id else None,
        "created_at":iso(tag.created_at),"created_by":str(tag.created_by),"updated_at":iso(tag.updated_at),"updated_by":str(tag.updated_by)}


def serialize(tag: Tag) -> dict:
    return {"id":tag.id,"category":{"code":tag.category.code,"name":tag.category.display_name,"sort_order":tag.category.sort_order},
        "subject":({"id":tag.subject.id,"code":tag.subject.code,"name":tag.subject.name} if tag.subject else None),
        "name":tag.name,"normalized_name":tag.normalized_name,"status":tag.status,
        "replacement":({"id":tag.replacement.id,"name":tag.replacement.name,"category_code":tag.replacement.category_code,"subject_id":tag.replacement.subject_id,"status":tag.replacement.status} if tag.replacement else None),
        "created_at":tag.created_at,"created_by":tag.created_by,"updated_at":tag.updated_at,"updated_by":tag.updated_by}

def tag_ref(tag: Tag) -> dict:
    replacement = tag.replacement
    return {"id":tag.id,"name":tag.name,"category_code":tag.category_code,"subject_id":tag.subject_id,
        "status":tag.status,"replacement":({"id":replacement.id,"name":replacement.name,
        "category_code":replacement.category_code,"subject_id":replacement.subject_id,"status":replacement.status}
        if replacement else None)}


class ManagedTagService:
    def __init__(self, session: AsyncSession): self.session=session
    def _loaded(self): return (joinedload(Tag.category), joinedload(Tag.subject), joinedload(Tag.replacement))
    async def categories(self):
        rows=(await self.session.execute(select(TagCategory).order_by(TagCategory.sort_order))).scalars()
        return {"items":[{"code":x.code,"name":x.display_name,"sort_order":x.sort_order} for x in rows]}
    async def validate_filter(self, ids):
        if len(ids)!=len(set(ids)): raise TagError("duplicate_tag_assignment","Теги не должны повторяться.",400,"tag_id")
        if ids:
            found=set((await self.session.scalars(select(Tag.id).where(Tag.id.in_(ids)))).all())
            if found!=set(ids): raise TagError("tag_not_found","Тег не найден.",404,"tag_id")
    async def get(self, tag_id: UUID, lock: bool=False) -> Tag:
        stmt=select(Tag).options(*self._loaded()).where(Tag.id==tag_id)
        if lock: stmt=stmt.with_for_update()
        tag=(await self.session.execute(stmt)).unique().scalar_one_or_none()
        if not tag: raise TagError("tag_not_found","Тег не найден.",404,"tag_id")
        return tag
    async def list(self,q,subject_id,category_code,status,offset,limit):
        if subject_id and not await self.session.get(Subject,subject_id): raise TagError("subject_not_found","Предмет не найден.",404,"subject_id")
        if category_code and not await self.session.get(TagCategory,category_code): raise TagError("tag_category_not_found","Категория не найдена.",404,"category_code")
        stmt=select(Tag).options(*self._loaded()).join(TagCategory)
        if status!="all": stmt=stmt.where(Tag.status==status)
        if subject_id: stmt=stmt.where(or_(Tag.subject_id==subject_id,Tag.subject_id.is_(None)))
        if category_code: stmt=stmt.where(Tag.category_code==category_code)
        if q:
            nq=normalize_tag_name(q); stmt=stmt.where(Tag.normalized_name.contains(nq))
        total=await self.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
        scope=case((Tag.subject_id==subject_id,0),else_=1) if subject_id else case((Tag.subject_id.is_(None),0),else_=1)
        rows=(await self.session.execute(stmt.order_by(scope,TagCategory.sort_order,Tag.normalized_name,Tag.id).offset(offset).limit(limit))).unique().scalars()
        return {"items":[serialize(x) for x in rows],"total":total,"offset":offset,"limit":limit}
    async def similar(self,name,exclude,limit):
        normalized=normalize_tag_name(name); score=func.similarity(Tag.normalized_name,normalized)
        stmt=select(Tag,score.label("score")).where(or_(Tag.normalized_name==normalized,score>=.30))
        if exclude: stmt=stmt.where(Tag.id!=exclude)
        rows=(await self.session.execute(stmt.order_by(score.desc(),Tag.normalized_name,Tag.id).limit(limit))).all()
        return {"normalized_query":normalized,"items":[{"tag":{"id":t.id,"name":t.name,"category_code":t.category_code,"subject_id":t.subject_id,"status":t.status},"similarity":float(s),"exact_match":t.normalized_name==normalized} for t,s in rows]}
    async def _validate_refs(self,category,subject):
        if not await self.session.get(TagCategory,category): raise TagError("tag_category_not_found","Категория не найдена.",404,"category_code")
        if subject and not await self.session.get(Subject,subject): raise TagError("subject_not_found","Предмет не найден.",404,"subject_id")
    async def create(self,category,subject,name,actor):
        await self._validate_refs(category,subject); display=canonicalize_tag_name(name); normalized=normalize_tag_name(name)
        if await self.session.scalar(select(Tag.id).where(Tag.normalized_name==normalized)): raise TagError("tag_name_conflict","Имя тега уже занято.",409,"name")
        tag=Tag(category_code=category,subject_id=subject,name=display,normalized_name=normalized,created_by=actor,updated_by=actor)
        self.session.add(tag)
        try: await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback(); raise TagError("tag_name_conflict","Имя тега уже занято.",409,"name") from exc
        self.session.add(TagAuditLog(tag_id=tag.id,action="tag_created",actor_id=actor,after_snapshot=snapshot(tag))); await self.session.commit()
        return serialize(await self.get(tag.id))
    async def _replacement(self,source:Tag,target_id:UUID|None):
        if target_id is None:return None
        if target_id==source.id: raise TagError("tag_replacement_invalid","Тег не может заменять сам себя.",422,"replacement_tag_id")
        target=await self.get(target_id,True)
        if target.status!="active" or (source.subject_id is None and target.subject_id is not None) or (source.subject_id and target.subject_id not in (None,source.subject_id)):
            raise TagError("tag_replacement_invalid","Замена неактивна или несовместима по предмету.",422,"replacement_tag_id")
        seen={source.id}; node=target
        while node:
            if node.id in seen: raise TagError("tag_replacement_cycle","Обнаружен цикл замен.",409,"replacement_tag_id")
            seen.add(node.id); node=await self.session.get(Tag,node.replacement_tag_id) if node.replacement_tag_id else None
        return target
    async def patch(self,tag_id,expected,actor,values,fields):
        tag=await self.get(tag_id,True)
        if tag.updated_at != expected: raise TagError("tag_concurrent_modification","Тег уже изменён.",409,"expected_updated_at")
        before=snapshot(tag); actions=[]
        new_category=values.get("category_code",tag.category_code); new_subject=values.get("subject_id",tag.subject_id)
        await self._validate_refs(new_category,new_subject)
        if "subject_id" in fields and new_subject!=tag.subject_id:
            incompatible=await self.session.scalar(select(func.count()).select_from(TaskVersionTag).join(TaskVersion).join(Task).where(TaskVersionTag.tag_id==tag.id, Task.subject_id!=new_subject)) if new_subject else 0
            if incompatible: raise TagError("tag_subject_mismatch","Scope несовместим с историческим использованием.",422,"subject_id")
            incoming=(await self.session.execute(select(Tag).where(Tag.replacement_tag_id==tag.id))).scalars()
            if any((x.subject_id is None and new_subject is not None) or (x.subject_id and new_subject not in (None,x.subject_id)) for x in incoming): raise TagError("tag_replacement_invalid","Scope нарушает входящие замены.",422,"subject_id")
            tag.subject_id=new_subject; actions.append("tag_scope_changed")
        if "category_code" in fields: tag.category_code=new_category
        if "name" in fields:
            display=canonicalize_tag_name(values["name"]); normalized=normalize_tag_name(values["name"])
            conflict=await self.session.scalar(select(Tag.id).where(Tag.normalized_name==normalized,Tag.id!=tag.id))
            if conflict: raise TagError("tag_name_conflict","Имя тега уже занято.",409,"name")
            if display!=tag.name: tag.name=display;tag.normalized_name=normalized;actions.append("tag_renamed")
        if "replacement_tag_id" in fields:
            if tag.status!="deprecated": raise TagError("tag_replacement_invalid","Замена задаётся только устаревшему тегу.",422,"replacement_tag_id")
            await self._replacement(tag,values["replacement_tag_id"]); tag.replacement_tag_id=values["replacement_tag_id"];actions.append("tag_replacement_changed")
        tag.updated_by=actor; tag.updated_at=func.clock_timestamp(); await self.session.flush(); after=snapshot(tag)
        for action in dict.fromkeys(actions): self.session.add(TagAuditLog(tag_id=tag.id,action=action,actor_id=actor,before_snapshot=before,after_snapshot=after))
        try: await self.session.commit()
        except IntegrityError as exc: await self.session.rollback(); raise TagError("tag_name_conflict","Имя тега уже занято.",409,"name") from exc
        return serialize(await self.get(tag.id))
    async def deprecate(self,tag_id,replacement,expected,actor):
        tag=await self.get(tag_id,True)
        if tag.updated_at!=expected: raise TagError("tag_concurrent_modification","Тег уже изменён.",409,"expected_updated_at")
        if tag.status=="deprecated": return serialize(tag)
        if await self.session.scalar(select(Tag.id).where(Tag.replacement_tag_id==tag.id)): raise TagError("tag_replacement_invalid","Сначала измените входящие ссылки замены.",422,"replacement_tag_id")
        await self._replacement(tag,replacement); before=snapshot(tag); tag.status="deprecated";tag.replacement_tag_id=replacement;tag.updated_by=actor;tag.updated_at=func.clock_timestamp();await self.session.flush();after=snapshot(tag)
        self.session.add(TagAuditLog(tag_id=tag.id,action="tag_deprecated",actor_id=actor,before_snapshot=before,after_snapshot=after));await self.session.commit()
        return serialize(await self.get(tag.id))
    async def usage(self,tag_id):
        tag=await self.get(tag_id)
        latest=select(TaskVersion.task_id,func.max(TaskVersion.version_no).label("n")).group_by(TaskVersion.task_id).subquery()
        base=select(TaskVersion.status,TaskVersion.task_id,TaskVersion.id).join(TaskVersionTag).where(TaskVersionTag.tag_id==tag_id).subquery()
        rows=(await self.session.execute(select(base.c.status,func.count()).group_by(base.c.status))).all(); counts={x:0 for x in ("draft","review","approved","archived")};counts.update(dict(rows))
        latest_base=select(TaskVersion.status).join(TaskVersionTag).join(latest,and_(latest.c.task_id==TaskVersion.task_id,latest.c.n==TaskVersion.version_no)).where(TaskVersionTag.tag_id==tag_id).subquery()
        lrows=(await self.session.execute(select(latest_base.c.status,func.count()).group_by(latest_base.c.status))).all(); lcounts={x:0 for x in counts};lcounts.update(dict(lrows))
        historical=await self.session.scalar(select(func.count()).select_from(base));distinct=await self.session.scalar(select(func.count(func.distinct(base.c.task_id))).select_from(base))
        return {"tag_id":tag_id,"historical_version_count":historical,"distinct_task_count":distinct,"latest_version_count":sum(lcounts.values()),"status_counts":counts,"latest_status_counts":lcounts}

    async def replace_version_tags(self, version_id: UUID, tag_ids: list[UUID], expected: datetime, actor: UUID, access=None):
        if len(tag_ids) != len(set(tag_ids)):
            raise TagError("duplicate_tag_assignment","Теги не должны повторяться.",400,"tag_ids")
        if len(tag_ids) > 8:
            raise TagError("tag_limit_exceeded","Можно назначить не более восьми тегов.",422,"tag_ids")
        row=(await self.session.execute(select(TaskVersion,Task).join(Task).where(TaskVersion.id==version_id).with_for_update())).one_or_none()
        if row is None: raise TagError("task_version_not_found","Версия задания не найдена.",404,"version_id")
        version,task=row
        if access is not None and not access.owns(task.created_by):
            raise TagError("task_version_not_found","Версия задания не найдена.",404,"version_id")
        latest=await self.session.scalar(select(func.max(TaskVersion.version_no)).where(TaskVersion.task_id==task.id))
        if version.status!="draft" or version.version_no!=latest or task.archived_at is not None:
            raise TagError("task_version_not_editable","Изменять теги можно только у последней draft-версии.",409,"version_id")
        if version.updated_at != expected:
            raise TagError("tag_concurrent_modification","Версия уже изменена.",409,"expected_updated_at")
        current=set((await self.session.scalars(select(TaskVersionTag.tag_id).where(TaskVersionTag.task_version_id==version.id))).all())
        requested=set(tag_ids); added=requested-current; removed=current-requested
        tags=[]
        if requested:
            tags=list((await self.session.execute(select(Tag).options(*self._loaded()).where(Tag.id.in_(requested)).order_by(Tag.id).with_for_update(of=Tag))).unique().scalars())
        by_id={x.id:x for x in tags}
        missing=requested-by_id.keys()
        if missing: raise TagError("tag_not_found","Тег не найден.",404,"tag_ids")
        for tag in tags:
            if tag.subject_id not in (None,task.subject_id): raise TagError("tag_subject_mismatch","Тег относится к другому предмету.",422,"tag_ids")
            if tag.id in added and tag.status!="active": raise TagError("tag_deprecated","Устаревший тег нельзя добавить.",409,"tag_ids")
        if not added and not removed:
            return {"task_id":task.id,"task_version_id":version.id,"version_no":version.version_no,"updated_at":version.updated_at,"tags":self._ordered_refs(tags,task.subject_id)}
        snapshots={x.id:x for x in tags}
        if removed:
            old=list((await self.session.execute(select(Tag).options(*self._loaded()).where(Tag.id.in_(removed)))).unique().scalars())
            snapshots.update({x.id:x for x in old})
            await self.session.execute(delete(TaskVersionTag).where(TaskVersionTag.task_version_id==version.id,TaskVersionTag.tag_id.in_(removed)))
        self.session.add_all([TaskVersionTag(task_version_id=version.id,tag_id=x,attached_by=actor) for x in sorted(added,key=str)])
        version.updated_at=func.clock_timestamp(); task.updated_at=func.clock_timestamp(); await self.session.flush(); await self.session.refresh(version)
        occurred=datetime.now().astimezone()
        for action,ids in (("tag_added_to_version",added),("tag_removed_from_version",removed)):
            for tag_id in sorted(ids,key=str):
                tag=snapshots[tag_id]
                details={"task_id":str(task.id),"version_id":str(version.id),"tag_id":str(tag.id),"canonical_name":tag.name,
                    "category_code":tag.category_code,"subject_id":str(tag.subject_id) if tag.subject_id else None,
                    "actor_id":str(actor),"occurred_at":occurred.isoformat()}
                self.session.add(AuditLog(task_id=task.id,task_version_id=version.id,version_no=version.version_no,action=action,actor_id=actor,details=details))
        await self.session.commit()
        refreshed=list((await self.session.execute(select(Tag).options(*self._loaded()).where(Tag.id.in_(requested)))).unique().scalars()) if requested else []
        return {"task_id":task.id,"task_version_id":version.id,"version_no":version.version_no,"updated_at":version.updated_at,"tags":self._ordered_refs(refreshed,task.subject_id)}

    @staticmethod
    def _ordered_refs(tags, subject_id):
        return [tag_ref(x) for x in sorted(tags,key=lambda x:(0 if x.subject_id==subject_id else 1,x.category.sort_order,x.normalized_name,str(x.id)))]
