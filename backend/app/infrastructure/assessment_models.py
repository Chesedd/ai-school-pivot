"""Persistence-only mappings for the Assessment Core contract.

Cross-aggregate membership (participant variant and answer item) is deliberately
application-enforced: separate foreign keys cannot prove membership without
duplicating aggregate identifiers.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.models import Base, IdMixin, uuid_type

assessment_status = ENUM("draft", "published", name="assessment_status", create_type=False)
assignment_status = ENUM("open", "closed", name="assignment_status", create_type=False)
submission_status = ENUM("draft", "submitted", name="submission_status", create_type=False)
clock = text("clock_timestamp()")


class ClassGroup(IdMixin, Base):
    __tablename__ = "class_groups"
    __table_args__ = (CheckConstraint("name = btrim(name) AND char_length(name) BETWEEN 1 AND 120", name="ck_class_groups_name_valid"), Index("uq_class_groups_external_ref", "external_ref", unique=True, postgresql_where=text("external_ref IS NOT NULL")), Index("ix_class_groups_active", "archived_at", "id"))
    name: Mapped[str] = mapped_column(String(120)); external_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); created_by: Mapped[UUID] = mapped_column(uuid_type)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Student(IdMixin, Base):
    __tablename__ = "students"
    __table_args__ = (CheckConstraint("display_name = btrim(display_name) AND char_length(display_name) BETWEEN 1 AND 120", name="ck_students_display_name_valid"), Index("uq_students_group_external_ref", "class_group_id", "external_ref", unique=True, postgresql_where=text("external_ref IS NOT NULL")), Index("ix_students_group_active", "class_group_id", "archived_at", "id"))
    class_group_id: Mapped[UUID] = mapped_column(ForeignKey("class_groups.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_students_class_group_id_class_groups"))
    display_name: Mapped[str] = mapped_column(String(120)); external_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Assessment(IdMixin, Base):
    __tablename__ = "assessments"
    __table_args__ = (CheckConstraint("title = btrim(title) AND char_length(title) BETWEEN 1 AND 200", name="ck_assessments_title_valid"), CheckConstraint("description IS NULL OR char_length(description) <= 4000", name="ck_assessments_description_length"), CheckConstraint("(published_at IS NULL) = (published_by IS NULL)", name="ck_assessments_publication_pair"), CheckConstraint("(status = 'draft' AND published_at IS NULL) OR (status = 'published' AND published_at IS NOT NULL)", name="ck_assessments_status_publication"), Index("ix_assessments_status_created", "status", text("created_at DESC"), "id"))
    title: Mapped[str] = mapped_column(String(200)); description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(assessment_status, server_default=text("'draft'::assessment_status")); created_by: Mapped[UUID] = mapped_column(uuid_type)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True); published_by: Mapped[UUID | None] = mapped_column(uuid_type, nullable=True)
    variants: Mapped[list[AssessmentVariant]] = relationship(back_populates="assessment", passive_deletes=True)


class AssessmentVariant(IdMixin, Base):
    __tablename__ = "assessment_variants"
    __table_args__ = (UniqueConstraint("assessment_id", "position", name="uq_assessment_variants_assessment_position"), UniqueConstraint("assessment_id", "name", name="uq_assessment_variants_assessment_name"), CheckConstraint("name = btrim(name) AND char_length(name) BETWEEN 1 AND 80", name="ck_assessment_variants_name_valid"), CheckConstraint("position > 0", name="ck_assessment_variants_position_positive"))
    assessment_id: Mapped[UUID] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE", onupdate="RESTRICT", name="fk_assessment_variants_assessment_id_assessments")); name: Mapped[str] = mapped_column(String(80)); position: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)
    assessment: Mapped[Assessment] = relationship(back_populates="variants"); items: Mapped[list[AssessmentItem]] = relationship(back_populates="variant", passive_deletes=True)


class AssessmentItem(IdMixin, Base):
    __tablename__ = "assessment_items"
    __table_args__ = (UniqueConstraint("variant_id", "position", name="uq_assessment_items_variant_position"), UniqueConstraint("variant_id", "task_version_id", name="uq_assessment_items_variant_task_version"), CheckConstraint("position > 0", name="ck_assessment_items_position_positive"), CheckConstraint("points > 0 AND points <= 999999.99", name="ck_assessment_items_points_range"), Index("ix_assessment_items_task_version_id", "task_version_id"))
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("assessment_variants.id", ondelete="CASCADE", onupdate="RESTRICT", name="fk_assessment_items_variant_id_variants")); task_version_id: Mapped[UUID] = mapped_column(ForeignKey("task_versions.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assessment_items_task_version_id_versions"))
    position: Mapped[int] = mapped_column(); points: Mapped[Decimal] = mapped_column(Numeric(8, 2)); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)
    variant: Mapped[AssessmentVariant] = relationship(back_populates="items")


class Assignment(IdMixin, Base):
    __tablename__ = "assignments"
    __table_args__ = (CheckConstraint("start_at < due_at", name="ck_assignments_window"), CheckConstraint("max_attempts BETWEEN 1 AND 100", name="ck_assignments_max_attempts"), CheckConstraint("(status = 'open' AND closed_at IS NULL AND closed_by IS NULL) OR (status = 'closed' AND closed_at IS NOT NULL AND closed_by IS NOT NULL)", name="ck_assignments_status_closed"), Index("ix_assignments_assessment_id", "assessment_id"), Index("ix_assignments_group_status_window", "class_group_id", "status", "start_at", "due_at"), Index("ix_assignments_status_due_at", "status", "due_at"))
    assessment_id: Mapped[UUID] = mapped_column(ForeignKey("assessments.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assignments_assessment_id_assessments")); class_group_id: Mapped[UUID] = mapped_column(ForeignKey("class_groups.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assignments_class_group_id_class_groups"))
    status: Mapped[str] = mapped_column(assignment_status, server_default=text("'open'::assignment_status")); start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); max_attempts: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); created_by: Mapped[UUID] = mapped_column(uuid_type); closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True); closed_by: Mapped[UUID | None] = mapped_column(uuid_type, nullable=True)


class AssignmentParticipant(IdMixin, Base):
    __tablename__ = "assignment_participants"
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", name="uq_assignment_participants_assignment_student"), CheckConstraint("(assigned_variant_id IS NULL) = (variant_assigned_at IS NULL)", name="ck_assignment_participants_variant_pair"), Index("ix_assignment_participants_student_assignment", "student_id", "assignment_id"), Index("ix_assignment_participants_assigned_variant", "assigned_variant_id"))
    assignment_id: Mapped[UUID] = mapped_column(ForeignKey("assignments.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assignment_participants_assignment_id_assignments")); student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assignment_participants_student_id_students")); assigned_variant_id: Mapped[UUID | None] = mapped_column(ForeignKey("assessment_variants.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assignment_participants_variant_id_variants"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); variant_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudentSubmission(IdMixin, Base):
    __tablename__ = "student_submissions"
    __table_args__ = (UniqueConstraint("assignment_participant_id", "attempt_no", name="uq_student_submissions_participant_attempt"), CheckConstraint("attempt_no > 0", name="ck_student_submissions_attempt_positive"), CheckConstraint("(status = 'draft' AND submitted_at IS NULL) OR (status = 'submitted' AND submitted_at IS NOT NULL)", name="ck_student_submissions_status_submitted"), Index("uq_student_submissions_one_draft", "assignment_participant_id", unique=True, postgresql_where=text("status = 'draft'")), Index("ix_student_submissions_participant_status", "assignment_participant_id", "status"), Index("ix_student_submissions_status_started", "status", "started_at"))
    assignment_participant_id: Mapped[UUID] = mapped_column(ForeignKey("assignment_participants.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_student_submissions_participant_id_participants")); attempt_no: Mapped[int] = mapped_column(SmallInteger); status: Mapped[str] = mapped_column(submission_status, server_default=text("'draft'::submission_status"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudentAnswer(IdMixin, Base):
    __tablename__ = "student_answers"
    __table_args__ = (UniqueConstraint("submission_id", "assessment_item_id", name="uq_student_answers_submission_item"), Index("ix_student_answers_assessment_item_id", "assessment_item_id"))
    submission_id: Mapped[UUID] = mapped_column(ForeignKey("student_submissions.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_student_answers_submission_id_submissions")); assessment_item_id: Mapped[UUID] = mapped_column(ForeignKey("assessment_items.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_student_answers_item_id_items"))
    raw_answer: Mapped[object] = mapped_column(JSONB); normalized_answer: Mapped[object] = mapped_column(JSONB); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)


class StudentAnswerAttachment(Base):
    __tablename__ = "student_answer_attachments"
    student_answer_id: Mapped[UUID] = mapped_column(ForeignKey("student_answers.id", ondelete="CASCADE"), primary_key=True)
    attachment_id: Mapped[UUID] = mapped_column(ForeignKey("attachments.id", ondelete="CASCADE"), primary_key=True)


class AssessmentIdempotencyKey(IdMixin, Base):
    __tablename__ = "assessment_idempotency_keys"
    __table_args__ = (UniqueConstraint("assignment_participant_id", "key", name="uq_assessment_idempotency_participant_key"), CheckConstraint("operation IN ('start','submit')", name="ck_assessment_idempotency_operation"), CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_assessment_idempotency_request_hash"), CheckConstraint("http_status IN (200,201)", name="ck_assessment_idempotency_http_status"), Index("ix_assessment_idempotency_submission_id", "submission_id"))
    assignment_participant_id: Mapped[UUID] = mapped_column(ForeignKey("assignment_participants.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assessment_idempotency_participant_id_participants")); key: Mapped[str] = mapped_column(String(128)); operation: Mapped[str] = mapped_column(String(16)); request_hash: Mapped[str] = mapped_column(String(64)); submission_id: Mapped[UUID] = mapped_column(ForeignKey("student_submissions.id", ondelete="RESTRICT", onupdate="RESTRICT", name="fk_assessment_idempotency_submission_id_submissions")); http_status: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)


class AssessmentAuditLog(IdMixin, Base):
    __tablename__ = "assessment_audit_log"
    __table_args__ = (CheckConstraint("aggregate_type IN ('assessment','assignment','submission')", name="ck_assessment_audit_aggregate_type"), CheckConstraint("actor_type IN ('teacher','student','system')", name="ck_assessment_audit_actor_type"), CheckConstraint("event_type IN ('assessment_created','assessment_metadata_updated','variant_created','variant_deleted','item_added','item_removed','items_reordered','item_points_changed','assessment_published','assignment_created','assignment_closed','variant_assigned','submission_started','answer_saved','answer_deleted','submission_submitted')", name="ck_assessment_audit_event_type"), Index("ix_assessment_audit_aggregate_occurred", "aggregate_type", "aggregate_id", text("occurred_at DESC"), text("id DESC")))
    aggregate_type: Mapped[str] = mapped_column(String(32)); aggregate_id: Mapped[UUID] = mapped_column(uuid_type); event_type: Mapped[str] = mapped_column(String(64)); actor_type: Mapped[str] = mapped_column(String(16)); actor_id: Mapped[UUID] = mapped_column(uuid_type); occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock); details: Mapped[object] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
