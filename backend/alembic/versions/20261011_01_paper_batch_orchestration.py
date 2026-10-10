"""Bounded batch checking audit events.

Revision ID: 20261011_01
Revises: 20261010_02
"""
from alembic import op

revision = "20261011_01"
down_revision = "20261010_02"
branch_labels = None
depends_on = None

OLD_EVENTS = (
    "batch.created", "batch.cancelled", "artifact.attached",
    "artifact.extraction_status_changed", "page.created", "page.status_changed",
    "grouping.draft_created", "grouping.updated", "grouping.confirmed",
    "paper_submission.created",
)
NEW_EVENTS = ("batch.checking_started", "batch.checking_completed",
              "batch.checking_reopened", "batch.completed")


def _constraint(events):
    values = ",".join(f"'{event}'" for event in events)
    op.execute("ALTER TABLE scan_checking_events DROP CONSTRAINT ck_scan_checking_events_type")
    op.execute("ALTER TABLE scan_checking_events ADD CONSTRAINT ck_scan_checking_events_type "
               f"CHECK (event_type IN ({values}))")


def upgrade():
    _constraint(OLD_EVENTS + NEW_EVENTS)


def downgrade():
    # Retain audit history on populated databases. The old application cannot
    # write these event types; NOT VALID preserves existing rows while checking
    # every new write against the old vocabulary.
    values = ",".join(f"'{event}'" for event in OLD_EVENTS)
    op.execute("ALTER TABLE scan_checking_events DROP CONSTRAINT ck_scan_checking_events_type")
    op.execute("ALTER TABLE scan_checking_events ADD CONSTRAINT ck_scan_checking_events_type "
               f"CHECK (event_type IN ({values})) NOT VALID")
