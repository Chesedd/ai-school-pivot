from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.models import Base, IdMixin, uuid_type

class ClassGroupTeacher(Base):
    __tablename__ = "class_group_teachers"
    __table_args__ = (PrimaryKeyConstraint("class_group_id", "teacher_user_id", name="pk_class_group_teachers"), Index("ix_class_group_teachers_teacher_class", "teacher_user_id", "class_group_id"))
    class_group_id: Mapped[UUID] = mapped_column(ForeignKey("class_groups.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_class_group_teachers_class_group_id"))
    teacher_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_class_group_teachers_teacher_user_id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    assigned_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_class_group_teachers_assigned_by"))

class ClassroomAuditLog(IdMixin, Base):
    __tablename__ = "classroom_audit_log"
    __table_args__ = (CheckConstraint("aggregate_type IN ('class_group','student','teacher_membership')", name="ck_classroom_audit_aggregate_type"), CheckConstraint("event_type IN ('class.created','class.updated','class.archived','teacher.assigned','teacher.unassigned','student.created','student.moved','student.archived')", name="ck_classroom_audit_event_type"), Index("ix_classroom_audit_aggregate", "aggregate_type", "aggregate_id", "occurred_at", "id"), Index("ix_classroom_audit_actor", "actor_user_id", "occurred_at"))
    aggregate_type: Mapped[str] = mapped_column(String(32)); aggregate_id: Mapped[UUID] = mapped_column(uuid_type)
    event_type: Mapped[str] = mapped_column(String(64)); actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_classroom_audit_actor_user_id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()")); details: Mapped[object] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

class ClassNote(IdMixin, Base):
    __tablename__ = "class_notes"
    __table_args__ = (
        CheckConstraint("char_length(body) BETWEEN 1 AND 12000", name="ck_class_notes_body_length"),
        Index("ix_class_notes_class_teacher_created", "class_group_id", "teacher_user_id", text("created_at DESC"), "id"),
        Index("ix_class_notes_teacher_created", "teacher_user_id", text("created_at DESC"), "id"),
    )
    class_group_id: Mapped[UUID] = mapped_column(ForeignKey("class_groups.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_class_notes_class_group_id"))
    teacher_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_class_notes_teacher_user_id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))

class StudentNote(IdMixin, Base):
    __tablename__ = "student_notes"
    __table_args__ = (
        CheckConstraint("char_length(body) BETWEEN 1 AND 12000", name="ck_student_notes_body_length"),
        Index("ix_student_notes_class_student_teacher_created", "class_group_id", "student_id", "teacher_user_id", text("created_at DESC"), "id"),
        Index("ix_student_notes_student_teacher_created", "student_id", "teacher_user_id", text("created_at DESC"), "id"),
    )
    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_student_notes_student_id"))
    class_group_id: Mapped[UUID] = mapped_column(ForeignKey("class_groups.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_student_notes_class_group_id"))
    teacher_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_student_notes_teacher_user_id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
