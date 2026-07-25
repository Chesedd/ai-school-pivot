"""SQLAlchemy mappings for the schema owned by Alembic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ENUM, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


uuid_type = PGUUID(as_uuid=True)
status_enum = ENUM("draft", "review", "approved", "archived", name="task_version_status", create_type=False)
task_type_enum = ENUM("test", "calculation", "problem", "open_question", "essay", name="task_type", create_type=False)
answer_format_enum = ENUM("single_choice", "multiple_choice", "short_text", "number", "expression", "long_text", name="answer_format", create_type=False)
difficulty_enum = ENUM("basic", "standard", "advanced", name="difficulty_level", create_type=False)


class IdMixin:
    id: Mapped[UUID] = mapped_column(uuid_type, primary_key=True, server_default=text("gen_random_uuid()"))


class Subject(IdMixin, Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("code", name="uq_subjects_code"),)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    topics: Mapped[list[Topic]] = relationship(back_populates="subject")
    tasks: Mapped[list[Task]] = relationship(back_populates="subject")


class Grade(IdMixin, Base):
    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("number", name="uq_grades_number"), CheckConstraint("number BETWEEN 1 AND 11", name="ck_grades_number_range"))
    number: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    topics: Mapped[list[Topic]] = relationship(back_populates="grade")
    tasks: Mapped[list[Task]] = relationship(back_populates="grade")


class Topic(IdMixin, Base):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("subject_id", "grade_id", "code", name="uq_topics_subject_grade_code"), Index("ix_topics_subject_id", "subject_id"), Index("ix_topics_grade_id", "grade_id"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id", ondelete="RESTRICT", name="fk_topics_subject_id_subjects"))
    grade_id: Mapped[UUID] = mapped_column(ForeignKey("grades.id", ondelete="RESTRICT", name="fk_topics_grade_id_grades"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    subject: Mapped[Subject] = relationship(back_populates="topics")
    grade: Mapped[Grade] = relationship(back_populates="topics")
    subtopics: Mapped[list[Subtopic]] = relationship(back_populates="topic")
    tasks: Mapped[list[Task]] = relationship(back_populates="topic")


class Subtopic(IdMixin, Base):
    __tablename__ = "subtopics"
    __table_args__ = (UniqueConstraint("topic_id", "code", name="uq_subtopics_topic_code"), Index("ix_subtopics_topic_id", "topic_id"))
    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id", ondelete="RESTRICT", name="fk_subtopics_topic_id_topics"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    topic: Mapped[Topic] = relationship(back_populates="subtopics")
    skills: Mapped[list[Skill]] = relationship(back_populates="subtopic")
    tasks: Mapped[list[Task]] = relationship(back_populates="subtopic")


class Skill(IdMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("subtopic_id", "code", name="uq_skills_subtopic_code"), Index("ix_skills_subtopic_id", "subtopic_id"))
    subtopic_id: Mapped[UUID] = mapped_column(ForeignKey("subtopics.id", ondelete="RESTRICT", name="fk_skills_subtopic_id_subtopics"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    subtopic: Mapped[Subtopic] = relationship(back_populates="skills")
    task_links: Mapped[list[TaskSkillLink]] = relationship(back_populates="skill")


class Task(IdMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_subject_id", "subject_id"), Index("ix_tasks_grade_id", "grade_id"), Index("ix_tasks_topic_id", "topic_id"), Index("ix_tasks_subtopic_id", "subtopic_id"), Index("ix_tasks_subject_grade_topic_subtopic", "subject_id", "grade_id", "topic_id", "subtopic_id"), Index("ix_tasks_created_at", "created_at"), Index("ix_tasks_archived_at", "archived_at"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id", ondelete="RESTRICT", name="fk_tasks_subject_id_subjects"))
    grade_id: Mapped[UUID] = mapped_column(ForeignKey("grades.id", ondelete="RESTRICT", name="fk_tasks_grade_id_grades"))
    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id", ondelete="RESTRICT", name="fk_tasks_topic_id_topics"))
    subtopic_id: Mapped[UUID | None] = mapped_column(ForeignKey("subtopics.id", ondelete="RESTRICT", name="fk_tasks_subtopic_id_subtopics"), nullable=True)
    created_by: Mapped[UUID] = mapped_column(uuid_type)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subject: Mapped[Subject] = relationship(back_populates="tasks")
    grade: Mapped[Grade] = relationship(back_populates="tasks")
    topic: Mapped[Topic] = relationship(back_populates="tasks")
    subtopic: Mapped[Subtopic | None] = relationship(back_populates="tasks")
    versions: Mapped[list[TaskVersion]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskVersion(IdMixin, Base):
    __tablename__ = "task_versions"
    __table_args__ = (UniqueConstraint("task_id", "version_no", name="uq_task_versions_task_version_no"), CheckConstraint("version_no > 0", name="ck_task_versions_version_no_positive"), CheckConstraint("(approved_at IS NULL) = (approved_by IS NULL)", name="ck_task_versions_approval_pair"), Index("ix_task_versions_task_id_status", "task_id", "status"), Index("ix_task_versions_status", "status"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE", name="fk_task_versions_task_id_tasks"))
    version_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(task_type_enum)
    answer_format: Mapped[str] = mapped_column(answer_format_enum)
    difficulty: Mapped[str] = mapped_column(difficulty_enum)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(status_enum, server_default=text("'draft'::task_version_status"))
    created_by: Mapped[UUID] = mapped_column(uuid_type)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    approved_by: Mapped[UUID | None] = mapped_column(uuid_type, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task: Mapped[Task] = relationship(back_populates="versions")
    skill_links: Mapped[list[TaskSkillLink]] = relationship(back_populates="task_version", cascade="all, delete-orphan")


class TaskSkillLink(IdMixin, Base):
    __tablename__ = "task_skill_links"
    __table_args__ = (UniqueConstraint("task_version_id", "skill_id", name="uq_task_skill_links_version_skill"), CheckConstraint("weight > 0 AND weight <= 1", name="ck_task_skill_links_weight_range"), Index("ix_task_skill_links_skill_id", "skill_id"), Index("uq_task_skill_links_one_primary_per_version", "task_version_id", unique=True, postgresql_where=text("is_primary IS TRUE")))
    task_version_id: Mapped[UUID] = mapped_column(ForeignKey("task_versions.id", ondelete="CASCADE", name="fk_task_skill_links_version_id_versions"))
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT", name="fk_task_skill_links_skill_id_skills"))
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    is_primary: Mapped[bool] = mapped_column(Boolean)
    task_version: Mapped[TaskVersion] = relationship(back_populates="skill_links")
    skill: Mapped[Skill] = relationship(back_populates="task_links")
