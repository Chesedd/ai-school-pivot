"""SQLAlchemy adapter for the Classroom application port."""
from uuid import UUID
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.classroom_administration import ClassView, StudentView, TeacherView
from app.infrastructure.assessment_models import ClassGroup, Student
from app.infrastructure.auth_models import User, UserRole
from app.infrastructure.classroom_models import ClassGroupTeacher, ClassroomAuditLog
from app.infrastructure.models import Grade

class SQLAlchemyClassroomRepository:
    def __init__(self,session:AsyncSession): self.session=session
    @staticmethod
    def cv(row):
        g,n,name,students,teachers=row
        return ClassView(g.id,g.name,g.grade_id,n,name,g.external_ref,g.created_at,g.archived_at,students,teachers)
    async def list_classes(self,status):
        sc=select(func.count(Student.id)).where(Student.class_group_id==ClassGroup.id,Student.archived_at.is_(None)).scalar_subquery()
        tc=select(func.count()).select_from(ClassGroupTeacher).where(ClassGroupTeacher.class_group_id==ClassGroup.id).scalar_subquery()
        q=select(ClassGroup,Grade.number,Grade.name,sc,tc).outerjoin(Grade,Grade.id==ClassGroup.grade_id).order_by(ClassGroup.name,ClassGroup.id)
        if status=='active': q=q.where(ClassGroup.archived_at.is_(None))
        elif status=='archived': q=q.where(ClassGroup.archived_at.is_not(None))
        return [self.cv(x) for x in (await self.session.execute(q)).all()]
    async def get_class(self,id,lock=False):
        sc=select(func.count(Student.id)).where(Student.class_group_id==id,Student.archived_at.is_(None)).scalar_subquery(); tc=select(func.count()).select_from(ClassGroupTeacher).where(ClassGroupTeacher.class_group_id==id).scalar_subquery()
        q=select(ClassGroup,Grade.number,Grade.name,sc,tc).outerjoin(Grade,Grade.id==ClassGroup.grade_id).where(ClassGroup.id==id)
        if lock: q=q.with_for_update(of=ClassGroup)
        row=(await self.session.execute(q)).first(); return self.cv(row) if row else None
    async def grade_exists(self,id): return bool(await self.session.scalar(select(Grade.id).where(Grade.id==id)))
    async def create_class(self,**values):
        obj=ClassGroup(**values); self.session.add(obj)
        try:
            async with self.session.begin_nested(): await self.session.flush()
        except IntegrityError as e: raise ValueError from e
        return await self.get_class(obj.id)
    async def update_class(self,id,values):
        obj=await self.session.get(ClassGroup,id); [setattr(obj,k,v) for k,v in values.items()]
        try:
            async with self.session.begin_nested(): await self.session.flush()
        except IntegrityError as e: raise ValueError from e
        return await self.get_class(id)
    async def list_teachers(self,id):
        q=select(User.id,User.display_name,User.is_active).join(ClassGroupTeacher,ClassGroupTeacher.teacher_user_id==User.id).where(ClassGroupTeacher.class_group_id==id).order_by(User.display_name,User.id)
        return [TeacherView(*x) for x in (await self.session.execute(q)).all()]
    async def teacher_eligibility(self,id):
        user=await self.session.get(User,id)
        if not user:return 'missing'
        if not user.is_active:return 'inactive'
        role=await self.session.scalar(select(UserRole.user_id).where(UserRole.user_id==id,UserRole.role=='teacher'))
        return 'eligible' if role else 'wrong_role'
    async def membership_exists(self,class_id,teacher_id,lock=False):
        q=select(ClassGroupTeacher).where(ClassGroupTeacher.class_group_id==class_id,ClassGroupTeacher.teacher_user_id==teacher_id)
        if lock:q=q.with_for_update()
        return (await self.session.scalar(q)) is not None
    async def assign_teacher(self,class_id,teacher_id,actor): self.session.add(ClassGroupTeacher(class_group_id=class_id,teacher_user_id=teacher_id,assigned_by=actor)); await self.session.flush()
    async def unassign_teacher(self,class_id,teacher_id):
        result=await self.session.execute(delete(ClassGroupTeacher).where(ClassGroupTeacher.class_group_id==class_id,ClassGroupTeacher.teacher_user_id==teacher_id)); return result.rowcount>0
    @staticmethod
    def sv(x): return StudentView(x.id,x.display_name,x.external_ref,x.class_group_id,x.archived_at)
    async def list_students(self,id,status):
        q=select(Student).where(Student.class_group_id==id).order_by(Student.display_name,Student.id)
        if status=='active':q=q.where(Student.archived_at.is_(None))
        elif status=='archived':q=q.where(Student.archived_at.is_not(None))
        return [self.sv(x) for x in (await self.session.scalars(q)).all()]
    async def get_student(self,id,lock=False):
        q=select(Student).where(Student.id==id)
        if lock:q=q.with_for_update()
        x=await self.session.scalar(q); return self.sv(x) if x else None
    async def create_student(self,**values):
        obj=Student(**values);self.session.add(obj)
        try:
            async with self.session.begin_nested():await self.session.flush()
        except IntegrityError as e:raise ValueError from e
        return self.sv(obj)
    async def update_student(self,id,values):
        obj=await self.session.get(Student,id);[setattr(obj,k,v) for k,v in values.items()];await self.session.flush();return self.sv(obj)
    async def audit(self,aggregate_type,aggregate_id,event,actor,details): self.session.add(ClassroomAuditLog(aggregate_type=aggregate_type,aggregate_id=aggregate_id,event_type=event,actor_user_id=actor,details=details));await self.session.flush()
