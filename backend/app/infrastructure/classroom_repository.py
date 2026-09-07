"""SQLAlchemy adapter for the Classroom application port."""
from uuid import UUID
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.classroom_administration import ClassView, StudentView, TeacherView
from app.application.classroom_access import TeacherClassSummary, TeacherStudentSummary
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


class SQLAlchemyClassroomAccessRepository:
    """SQL-level membership enforcement shared by Classroom and Assessment."""
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _membership(statement, actor_id: UUID, unrestricted: bool):
        if unrestricted:
            return statement
        return statement.join(
            ClassGroupTeacher, ClassGroupTeacher.class_group_id == ClassGroup.id
        ).where(ClassGroupTeacher.teacher_user_id == actor_id)

    @staticmethod
    def _status(statement, status: str):
        if status == "active":
            return statement.where(ClassGroup.archived_at.is_(None))
        if status == "archived":
            return statement.where(ClassGroup.archived_at.is_not(None))
        return statement

    @staticmethod
    def _summary(row):
        group, grade_number, grade_name, count = row
        return TeacherClassSummary(group.id, group.name, group.grade_id, grade_number,
                                   grade_name, group.archived_at, count)

    def _class_rows(self):
        students = select(func.count(Student.id)).where(
            Student.class_group_id == ClassGroup.id,
            Student.archived_at.is_(None),
        ).scalar_subquery()
        return select(ClassGroup, Grade.number, Grade.name, students).outerjoin(
            Grade, Grade.id == ClassGroup.grade_id)

    async def list_accessible_classes(self, actor_id, unrestricted, status, offset, limit):
        base = self._status(self._membership(select(ClassGroup.id), actor_id, unrestricted), status)
        total = await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        query = self._status(self._membership(self._class_rows(), actor_id, unrestricted), status)
        rows = (await self.session.execute(query.order_by(ClassGroup.name, ClassGroup.id)
                                           .offset(offset).limit(limit))).all()
        return {"items": [self._summary(row) for row in rows], "total": total,
                "offset": offset, "limit": limit}

    async def get_accessible_class(self, class_group_id, actor_id, unrestricted):
        query = self._membership(self._class_rows(), actor_id, unrestricted).where(
            ClassGroup.id == class_group_id)
        row = (await self.session.execute(query)).first()
        return self._summary(row) if row else None

    async def list_accessible_students(self, class_group_id, actor_id, unrestricted, status):
        query = select(Student).join(ClassGroup, ClassGroup.id == Student.class_group_id).where(
            ClassGroup.id == class_group_id)
        query = self._membership(query, actor_id, unrestricted)
        if status == "active": query = query.where(Student.archived_at.is_(None))
        elif status == "archived": query = query.where(Student.archived_at.is_not(None))
        rows = (await self.session.scalars(query.order_by(Student.display_name, Student.id))).all()
        if not rows and not await self.can_access_class(class_group_id, actor_id, unrestricted):
            return None
        return [TeacherStudentSummary(row.id, row.display_name, row.external_ref, row.archived_at)
                for row in rows]

    async def can_access_class(self, class_group_id, actor_id, unrestricted, *,
                               require_active=False, require_configured=False, lock=False):
        if lock and not unrestricted:
            # Lock both the authoritative membership and its class row. An admin
            # unassignment must wait for the publication transaction to finish.
            query = select(ClassGroupTeacher).join(
                ClassGroup, ClassGroup.id == ClassGroupTeacher.class_group_id
            ).where(ClassGroupTeacher.class_group_id == class_group_id,
                    ClassGroupTeacher.teacher_user_id == actor_id)
            if require_active: query = query.where(ClassGroup.archived_at.is_(None))
            if require_configured: query = query.where(ClassGroup.grade_id.is_not(None))
            return await self.session.scalar(query.with_for_update()) is not None
        query = self._membership(select(ClassGroup), actor_id, unrestricted).where(
            ClassGroup.id == class_group_id)
        if require_active: query = query.where(ClassGroup.archived_at.is_(None))
        if require_configured: query = query.where(ClassGroup.grade_id.is_not(None))
        if lock: query = query.with_for_update(of=ClassGroup)
        return await self.session.scalar(query) is not None

    async def accessible_class_ids(self, actor_id, unrestricted):
        query = self._membership(select(ClassGroup.id), actor_id, unrestricted)
        return tuple((await self.session.scalars(query)).all())

    async def list_assignment_eligible_classes(self, actor_id, unrestricted, offset, limit):
        base = self._membership(select(ClassGroup.id), actor_id, unrestricted).where(
            ClassGroup.archived_at.is_(None), ClassGroup.grade_id.is_not(None))
        total = await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        counts = select(func.count(Student.id)).where(
            Student.class_group_id == ClassGroup.id, Student.archived_at.is_(None)
        ).scalar_subquery()
        query = self._membership(select(ClassGroup, counts), actor_id, unrestricted).where(
            ClassGroup.archived_at.is_(None), ClassGroup.grade_id.is_not(None))
        rows = (await self.session.execute(query.order_by(ClassGroup.name, ClassGroup.id)
                                           .offset(offset).limit(limit))).all()
        return {"items": [(group.id, group.name, count) for group, count in rows],
                "total": total, "offset": offset, "limit": limit}
