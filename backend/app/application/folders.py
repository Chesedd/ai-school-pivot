"""Application contract and transactional use cases for Content Bank folders."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

MAX_FOLDER_DEPTH = 8

@dataclass(frozen=True)
class FolderSummaryDTO:
    id: UUID; subject_id: UUID; parent_id: UUID | None; name: str; depth: int; created_at: datetime; updated_at: datetime
@dataclass(frozen=True)
class FolderTreeNodeDTO:
    id: UUID; subject_id: UUID; parent_id: UUID | None; name: str; depth: int; children: tuple["FolderTreeNodeDTO", ...]
@dataclass(frozen=True)
class BreadcrumbDTO:
    subject: object; folders: tuple[FolderSummaryDTO, ...]
@dataclass(frozen=True)
class TaskLocationDTO:
    task_id: UUID; subject_id: UUID; folder_id: UUID | None; previous_folder_id: UUID | None; updated_at: datetime
@dataclass(frozen=True)
class CreateFolderCommand:
    subject_id: UUID; parent_id: UUID | None; name: str; actor_id: UUID
@dataclass(frozen=True)
class RenameFolderCommand:
    folder_id: UUID; name: str; expected_updated_at: datetime; actor_id: UUID
@dataclass(frozen=True)
class MoveFolderCommand:
    folder_id: UUID; parent_id: UUID | None; expected_updated_at: datetime; actor_id: UUID
@dataclass(frozen=True)
class DeleteFolderCommand:
    folder_id: UUID; expected_updated_at: datetime; actor_id: UUID
@dataclass(frozen=True)
class MoveTaskCommand:
    task_id: UUID; folder_id: UUID | None; expected_folder_id: UUID | None; actor_id: UUID
@dataclass(frozen=True)
class FolderTreeQuery: subject_id: UUID

class FolderDomainError(Exception):
    def __init__(self, code: str, message: str, details: dict, status: int = 409):
        self.code, self.details, self.status = code, details, status; super().__init__(message)

def normalize_folder_name(value: str) -> str:
    name = value.strip()
    if not 1 <= len(name) <= 120 or name in {".", ".."} or "/" in name or "\\" in name:
        reason = "length" if not 1 <= len(name) <= 120 else "reserved_or_separator"
        raise FolderDomainError("folder_name_invalid", "Некорректное имя папки.", {"field":"name","reason":reason,"min_length":1,"max_length":120}, 422)
    return name

class FolderService:
    def __init__(self, uow): self.uow=uow
    async def create(self, c: CreateFolderCommand):
        name=normalize_folder_name(c.name)
        async with self.uow:
            r=self.uow.repository; await r.lock_subject_tree(c.subject_id)
            if not await r.subject_exists(c.subject_id): raise FolderDomainError("subject_not_found","Предмет не найден.",{"subject_id":str(c.subject_id)},404)
            parent=await r.get_folder_for_update(c.parent_id) if c.parent_id else None
            if c.parent_id and not parent: raise FolderDomainError("folder_not_found","Папка не найдена.",{"folder_id":str(c.parent_id)},404)
            if parent and parent.subject_id != c.subject_id: raise FolderDomainError("folder_subject_mismatch","Предметы папок не совпадают.",{"folder_id":str(parent.id),"folder_subject_id":str(parent.subject_id),"subject_id":str(c.subject_id)})
            depth=(parent.depth+1) if parent else 1
            if depth>MAX_FOLDER_DEPTH: raise FolderDomainError("folder_max_depth_exceeded","Превышена максимальная глубина.",{"folder_id":None,"parent_id":str(c.parent_id) if c.parent_id else None,"max_depth":8,"resulting_depth":depth})
            if await r.sibling_name_exists(c.subject_id,c.parent_id,name,None): raise FolderDomainError("folder_name_conflict","Имя уже занято.",{"subject_id":str(c.subject_id),"parent_id":str(c.parent_id) if c.parent_id else None,"name":name})
            result=await r.create_folder(c,name,depth); await r.append_folder_audit(result,"folder_created",c.actor_id,None); await self.uow.commit(); return result
    async def rename(self,c): return await self._change(c,"rename")
    async def move(self,c): return await self._change(c,"move")
    async def _change(self,c,kind):
        async with self.uow:
            r=self.uow.repository; current=await r.get_folder(c.folder_id)
            if not current: raise FolderDomainError("folder_not_found","Папка не найдена.",{"folder_id":str(c.folder_id)},404)
            await r.lock_subject_tree(current.subject_id); current=await r.get_folder_for_update(c.folder_id)
            if current.updated_at != c.expected_updated_at: raise FolderDomainError("folder_concurrent_modification","Папка была изменена.",{"resource_type":"folder","resource_id":str(c.folder_id),"expected_updated_at":c.expected_updated_at.isoformat(),"actual_updated_at":current.updated_at.isoformat()})
            before=current
            if kind=="rename":
                name=normalize_folder_name(c.name)
                if name==current.name: return current
                if await r.sibling_name_exists(current.subject_id,current.parent_id,name,current.id): raise FolderDomainError("folder_name_conflict","Имя уже занято.",{"subject_id":str(current.subject_id),"parent_id":str(current.parent_id) if current.parent_id else None,"name":name})
                result=await r.rename_folder(current,name,c.actor_id)
            else:
                subtree=await r.get_folder_subtree_for_update(current.id)
                if c.parent_id==current.id or c.parent_id in {x.id for x in subtree}: raise FolderDomainError("folder_cycle","Папку нельзя переместить в её поддерево.",{"folder_id":str(current.id),"parent_id":str(c.parent_id)})
                parent=await r.get_folder_for_update(c.parent_id) if c.parent_id else None
                if c.parent_id and not parent: raise FolderDomainError("folder_not_found","Папка не найдена.",{"folder_id":str(c.parent_id)},404)
                if parent and parent.subject_id!=current.subject_id: raise FolderDomainError("folder_subject_mismatch","Предметы папок не совпадают.",{"folder_id":str(parent.id),"folder_subject_id":str(parent.subject_id),"subject_id":str(current.subject_id)})
                height=max(x.depth-current.depth for x in subtree)+1
                resulting=(parent.depth if parent else 0)+height
                if resulting>8: raise FolderDomainError("folder_max_depth_exceeded","Превышена максимальная глубина.",{"folder_id":str(current.id),"parent_id":str(c.parent_id) if c.parent_id else None,"max_depth":8,"resulting_depth":resulting})
                if await r.sibling_name_exists(current.subject_id,c.parent_id,current.name,current.id): raise FolderDomainError("folder_name_conflict","Имя уже занято.",{"subject_id":str(current.subject_id),"parent_id":str(c.parent_id) if c.parent_id else None,"name":current.name})
                if c.parent_id==current.parent_id:return current
                result=await r.move_folder(current,c.parent_id,c.actor_id)
            await r.append_folder_audit(result,"folder_renamed" if kind=="rename" else "folder_moved",c.actor_id,before); await self.uow.commit(); return result
    async def delete(self,c):
        async with self.uow:
            r=self.uow.repository; current=await r.get_folder(c.folder_id)
            if not current: raise FolderDomainError("folder_not_found","Папка не найдена.",{"folder_id":str(c.folder_id)},404)
            await r.lock_subject_tree(current.subject_id); current=await r.get_folder_for_update(c.folder_id)
            if current.updated_at!=c.expected_updated_at: raise FolderDomainError("folder_concurrent_modification","Папка была изменена.",{"resource_type":"folder","resource_id":str(c.folder_id),"expected_updated_at":c.expected_updated_at.isoformat(),"actual_updated_at":current.updated_at.isoformat()})
            children,tasks=await r.folder_nonempty(current.id)
            if children or tasks: raise FolderDomainError("folder_not_empty","Папка не пуста.",{"folder_id":str(current.id),"has_child_folders":children,"has_tasks":tasks})
            await r.append_folder_audit(current,"folder_deleted",c.actor_id,current,deleted=True); await r.delete_empty_folder(current.id); await self.uow.commit()
    async def move_task(self,c):
        async with self.uow:
            r=self.uow.repository; task=await r.lock_task(c.task_id)
            if not task: raise FolderDomainError("task_not_found","Задание не найдено.",{"task_id":str(c.task_id)},404)
            if task.folder_id!=c.expected_folder_id: raise FolderDomainError("folder_concurrent_modification","Размещение было изменено.",{"resource_type":"task","resource_id":str(c.task_id),"expected_folder_id":str(c.expected_folder_id) if c.expected_folder_id else None,"actual_folder_id":str(task.folder_id) if task.folder_id else None})
            target=await r.get_folder_for_update(c.folder_id) if c.folder_id else None
            if c.folder_id and not target: raise FolderDomainError("folder_not_found","Папка не найдена.",{"folder_id":str(c.folder_id)},404)
            if target and target.subject_id!=task.subject_id: raise FolderDomainError("task_folder_subject_mismatch","Предмет задания и папки не совпадает.",{"task_id":str(task.id),"task_subject_id":str(task.subject_id),"folder_id":str(target.id),"folder_subject_id":str(target.subject_id)})
            if task.folder_id==c.folder_id:return await r.task_location(task,task.folder_id)
            old=await r.get_folder(task.folder_id) if task.folder_id else None
            result=await r.set_task_folder(task,c.folder_id)
            await r.append_task_move_audit(task,result,old,target,c.actor_id); await self.uow.commit(); return result

class GetFolderTreeService:
    def __init__(self, repository): self.repository=repository
    async def get(self,subject_id):
        subject=await self.repository.get_subject(subject_id)
        if not subject: raise FolderDomainError("subject_not_found","Предмет не найден.",{"subject_id":str(subject_id)},404)
        return {"subject":subject,"folders":await self.repository.list_folder_tree(subject_id)}
class GetLevelContentsService:
    def __init__(self, repository): self.repository=repository
    async def get(self,subject_id,folder_id,query): return await self.repository.get_level_contents(subject_id,folder_id,query)
