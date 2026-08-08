"""Add the Phase 3.1 Assessment Core PostgreSQL foundation.

Revision ID: 20260808_02
Revises: 20260808_01
"""
from alembic import op
from sqlalchemy.dialects import postgresql

from app.infrastructure.assessment_models import (
    Assessment, AssessmentAuditLog, AssessmentIdempotencyKey, AssessmentItem,
    AssessmentVariant, Assignment, AssignmentParticipant, ClassGroup, Student,
    StudentAnswer, StudentSubmission,
)

revision = "20260808_02"
down_revision = "20260808_01"
branch_labels = depends_on = None

TABLES = (
    ClassGroup.__table__, Student.__table__, Assessment.__table__,
    AssessmentVariant.__table__, AssessmentItem.__table__, Assignment.__table__,
    AssignmentParticipant.__table__, StudentSubmission.__table__,
    StudentAnswer.__table__, AssessmentIdempotencyKey.__table__,
    AssessmentAuditLog.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("draft", "published", name="assessment_status").create(bind)
    postgresql.ENUM("open", "closed", name="assignment_status").create(bind)
    postgresql.ENUM("draft", "submitted", name="submission_status").create(bind)
    for table in TABLES:
        table.create(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind)
    postgresql.ENUM(name="submission_status").drop(bind)
    postgresql.ENUM(name="assignment_status").drop(bind)
    postgresql.ENUM(name="assessment_status").drop(bind)
