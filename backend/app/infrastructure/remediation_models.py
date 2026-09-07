"""Persistence mappings for teacher-controlled personal remediation."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.models import Base, IdMixin

clock=text("clock_timestamp()")
class RemediationPlan(IdMixin, Base):
    __tablename__="remediation_plans"
    __table_args__=(
      UniqueConstraint("owner_user_id","creation_key",name="uq_remediation_plans_owner_creation_key"),
      CheckConstraint("status IN ('draft','assigned','cancelled')",name="ck_remediation_plans_status"),
      CheckConstraint("title=btrim(title) AND char_length(title) BETWEEN 1 AND 200",name="ck_remediation_plans_title"),
      CheckConstraint("instructions IS NULL OR char_length(instructions)<=4000",name="ck_remediation_plans_instructions"),
      CheckConstraint("creation_fingerprint ~ '^[0-9a-f]{64}$'",name="ck_remediation_plans_fingerprint"),
      CheckConstraint("(status='draft' AND assigned_at IS NULL AND cancelled_at IS NULL) OR (status='assigned' AND assigned_at IS NOT NULL AND cancelled_at IS NULL) OR (status='cancelled' AND cancelled_at IS NOT NULL)",name="ck_remediation_plans_lifecycle"),
      Index("ix_remediation_plans_owner_class_student_created","owner_user_id","class_group_id","student_id",text("created_at DESC"),"id"),
      Index("ix_remediation_plans_student_status_assigned","student_id","status",text("assigned_at DESC"),"id"),
      Index("ix_remediation_plans_source_run_student","source_check_run_id","student_id"),
      Index("ix_remediation_plans_source_assignment_student","source_assignment_id","student_id"),)
    owner_user_id:Mapped[UUID]=mapped_column(ForeignKey("users.id",ondelete="RESTRICT",onupdate="RESTRICT"))
    student_id:Mapped[UUID]=mapped_column(ForeignKey("students.id",ondelete="RESTRICT",onupdate="RESTRICT"))
    class_group_id:Mapped[UUID]=mapped_column(ForeignKey("class_groups.id",ondelete="RESTRICT",onupdate="RESTRICT"))
    source_assignment_id:Mapped[UUID]=mapped_column(ForeignKey("assignments.id",ondelete="RESTRICT",onupdate="RESTRICT"))
    source_assignment_participant_id:Mapped[UUID]=mapped_column(ForeignKey("assignment_participants.id",ondelete="RESTRICT",onupdate="RESTRICT"))
    source_submission_id:Mapped[UUID]=mapped_column(ForeignKey("student_submissions.id",ondelete="RESTRICT",onupdate="RESTRICT"))
    source_check_run_id:Mapped[UUID]=mapped_column(ForeignKey("check_runs.id",ondelete="RESTRICT",onupdate="RESTRICT"))
    status:Mapped[str]=mapped_column(String(16),server_default="draft"); title:Mapped[str]=mapped_column(String(200)); instructions:Mapped[str|None]=mapped_column(Text)
    due_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); review_acknowledged_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    creation_key:Mapped[str]=mapped_column(String(128)); creation_fingerprint:Mapped[str]=mapped_column(String(64))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=clock); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=clock)
    assigned_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); cancelled_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
class RemediationPlanSignal(IdMixin,Base):
    __tablename__="remediation_plan_signals"; __table_args__=(UniqueConstraint("remediation_plan_id","check_finding_id"),)
    remediation_plan_id:Mapped[UUID]=mapped_column(ForeignKey("remediation_plans.id",ondelete="CASCADE")); check_finding_id:Mapped[UUID]=mapped_column(ForeignKey("check_findings.id",ondelete="RESTRICT",onupdate="RESTRICT")); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=clock)
class RemediationPlanItem(IdMixin,Base):
    __tablename__="remediation_plan_items"; __table_args__=(CheckConstraint("position>=0"),CheckConstraint("selection_source IN ('suggested','manual')"),UniqueConstraint("remediation_plan_id","position"),UniqueConstraint("remediation_plan_id","task_version_id"))
    remediation_plan_id:Mapped[UUID]=mapped_column(ForeignKey("remediation_plans.id",ondelete="CASCADE")); position:Mapped[int]=mapped_column(Integer); task_version_id:Mapped[UUID]=mapped_column(ForeignKey("task_versions.id",ondelete="RESTRICT",onupdate="RESTRICT")); selection_source:Mapped[str]=mapped_column(String(16)); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=clock)
class RemediationEvent(IdMixin,Base):
    __tablename__="remediation_events"; __table_args__=(CheckConstraint("event_type IN ('plan_created','plan_updated','plan_assigned','plan_cancelled')"),Index("ix_remediation_events_plan_time","remediation_plan_id","occurred_at","id"))
    remediation_plan_id:Mapped[UUID]=mapped_column(ForeignKey("remediation_plans.id",ondelete="RESTRICT")); event_type:Mapped[str]=mapped_column(String(32)); actor_user_id:Mapped[UUID]=mapped_column(ForeignKey("users.id",ondelete="RESTRICT")); occurred_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=clock); details:Mapped[object]=mapped_column(JSONB,server_default=text("'{}'::jsonb"))
