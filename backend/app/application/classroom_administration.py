"""Application boundary for transactional administrator-managed classrooms."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Literal
from uuid import UUID

@dataclass(frozen=True)
class ClassView:
    id: UUID; name: str; grade_id: UUID | None; grade_number: int | None; grade_name: str | None; external_ref: str | None; created_at: datetime; archived_at: datetime | None; active_student_count: int = 0; assigned_teacher_count: int = 0
    @property
    def is_configuration_complete(self): return self.grade_id is not None
@dataclass(frozen=True)
class TeacherView:
    user_id: UUID; display_name: str; is_active: bool
@dataclass(frozen=True)
class StudentView:
    id: UUID; display_name: str; external_ref: str | None; class_group_id: UUID; archived_at: datetime | None

class ClassroomError(Exception):
    def __init__(self, code: str, status: int = 409): self.code=code; self.status=status; super().__init__(code.replace('_',' '))

class ClassroomRepository(Protocol):
    async def list_classes(self, status: str) -> list[ClassView]: ...
    async def get_class(self, id: UUID, lock: bool=False) -> ClassView | None: ...
    async def grade_exists(self, id: UUID) -> bool: ...
    async def create_class(self, **values) -> ClassView: ...
    async def update_class(self, id: UUID, values: dict) -> ClassView: ...
    async def list_teachers(self, id: UUID) -> list[TeacherView]: ...
    async def teacher_eligibility(self, id: UUID) -> Literal['eligible','missing','inactive','wrong_role']: ...
    async def membership_exists(self, class_id: UUID, teacher_id: UUID, lock: bool=False) -> bool: ...
    async def assign_teacher(self, class_id: UUID, teacher_id: UUID, actor: UUID): ...
    async def unassign_teacher(self, class_id: UUID, teacher_id: UUID) -> bool: ...
    async def list_students(self, id: UUID, status: str) -> list[StudentView]: ...
    async def get_student(self, id: UUID, lock: bool=False) -> StudentView | None: ...
    async def create_student(self, **values) -> StudentView: ...
    async def update_student(self, id: UUID, values: dict) -> StudentView: ...
    async def audit(self, aggregate_type: str, aggregate_id: UUID, event: str, actor: UUID, details: dict): ...

class ClassroomAdministrationService:
    def __init__(self, repository: ClassroomRepository): self.repository=repository
    async def list_classes(self,status='active'): return await self.repository.list_classes(status)
    async def get_class(self,id):
        value=await self.repository.get_class(id)
        if not value: raise ClassroomError('class_not_found',404)
        return value
    async def create_class(self, *, name, grade_id, external_ref, actor):
        if grade_id is None: raise ClassroomError('class_grade_required')
        if not await self.repository.grade_exists(grade_id): raise ClassroomError('grade_not_found',404)
        try: value=await self.repository.create_class(name=name,grade_id=grade_id,external_ref=external_ref,created_by=actor)
        except ValueError: raise ClassroomError('external_ref_conflict')
        await self.repository.audit('class_group',value.id,'class.created',actor,{'name':name,'grade_id':str(grade_id),'external_ref':external_ref}); return value
    async def update_class(self,id,values,actor):
        current=await self.repository.get_class(id,True)
        if not current: raise ClassroomError('class_not_found',404)
        if current.archived_at: raise ClassroomError('class_archived')
        if 'grade_id' in values:
            if values['grade_id'] is None: raise ClassroomError('class_grade_required')
            if not await self.repository.grade_exists(values['grade_id']): raise ClassroomError('grade_not_found',404)
        changed={k:v for k,v in values.items() if getattr(current,k)!=v}
        if not changed: return current
        try: result=await self.repository.update_class(id,changed)
        except ValueError: raise ClassroomError('external_ref_conflict')
        details={'changed_fields':sorted(changed)}
        if 'grade_id' in changed: details.update(previous_grade_id=str(current.grade_id) if current.grade_id else None,new_grade_id=str(changed['grade_id']))
        await self.repository.audit('class_group',id,'class.updated',actor,details); return result
    async def archive_class(self,id,actor):
        current=await self.repository.get_class(id,True)
        if not current: raise ClassroomError('class_not_found',404)
        if current.archived_at: return current
        result=await self.repository.update_class(id,{'archived_at':datetime.now().astimezone()}); await self.repository.audit('class_group',id,'class.archived',actor,{}); return result
    async def list_teachers(self,id): await self.get_class(id); return await self.repository.list_teachers(id)
    async def assign_teacher(self,class_id,teacher_id,actor):
        group=await self.repository.get_class(class_id,True)
        if not group: raise ClassroomError('class_not_found',404)
        if group.archived_at: raise ClassroomError('class_archived')
        if not group.grade_id: raise ClassroomError('class_grade_required')
        state=await self.repository.teacher_eligibility(teacher_id)
        if state!='eligible': raise ClassroomError({'missing':'teacher_not_found','inactive':'teacher_inactive','wrong_role':'teacher_role_required'}[state],404 if state=='missing' else 409)
        if await self.repository.membership_exists(class_id,teacher_id,True): return
        await self.repository.assign_teacher(class_id,teacher_id,actor); await self.repository.audit('teacher_membership',class_id,'teacher.assigned',actor,{'teacher_user_id':str(teacher_id)})
    async def unassign_teacher(self,class_id,teacher_id,actor):
        if not await self.repository.get_class(class_id,True): raise ClassroomError('class_not_found',404)
        if await self.repository.unassign_teacher(class_id,teacher_id): await self.repository.audit('teacher_membership',class_id,'teacher.unassigned',actor,{'teacher_user_id':str(teacher_id)})
    async def list_students(self,class_id,status='active'): await self.get_class(class_id); return await self.repository.list_students(class_id,status)
    async def create_student(self,class_id,display_name,external_ref,actor):
        group=await self.repository.get_class(class_id,True)
        if not group: raise ClassroomError('class_not_found',404)
        if group.archived_at: raise ClassroomError('class_archived')
        if not group.grade_id: raise ClassroomError('class_grade_required')
        try: value=await self.repository.create_student(class_group_id=class_id,display_name=display_name,external_ref=external_ref)
        except ValueError: raise ClassroomError('external_ref_conflict')
        await self.repository.audit('student',value.id,'student.created',actor,{'class_group_id':str(class_id),'display_name':display_name,'external_ref':external_ref}); return value
    async def move_student(self,student_id,target,expected,actor):
        student=await self.repository.get_student(student_id,True)
        if not student: raise ClassroomError('student_not_found',404)
        if student.archived_at: raise ClassroomError('student_archived')
        if student.class_group_id!=expected: raise ClassroomError('student_class_conflict')
        if target==expected: return student
        group=await self.repository.get_class(target,True)
        if not group: raise ClassroomError('class_not_found',404)
        if group.archived_at: raise ClassroomError('class_archived')
        if not group.grade_id: raise ClassroomError('class_grade_required')
        value=await self.repository.update_student(student_id,{'class_group_id':target}); await self.repository.audit('student',student_id,'student.moved',actor,{'student_id':str(student_id),'source_class_group_id':str(expected),'target_class_group_id':str(target)}); return value
    async def archive_student(self,class_id,student_id,actor):
        student=await self.repository.get_student(student_id,True)
        if not student or student.class_group_id!=class_id: raise ClassroomError('student_not_found',404)
        if student.archived_at: return student
        value=await self.repository.update_student(student_id,{'archived_at':datetime.now().astimezone()}); await self.repository.audit('student',student_id,'student.archived',actor,{'class_group_id':str(class_id)}); return value
