"""Add Content Bank folder hierarchy and immutable folder audit."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260806_01"
down_revision = "20260730_01"
branch_labels = depends_on = None
UUID = postgresql.UUID(as_uuid=True)
TS = postgresql.TIMESTAMP(timezone=True)

NEW_ACTIONS = ("task_folder_moved", "folder_created", "folder_renamed", "folder_moved", "folder_deleted")
OLD_ACTIONS = ("task_created", "methodology_updated", "submitted_for_review", "returned_to_draft", "version_approved", "version_created", "task_archived")

def upgrade() -> None:
    context = op.get_context()
    for value in NEW_ACTIONS:
        # PostgreSQL forbids using a newly added enum value before commit.
        with context.autocommit_block():
            op.execute(sa.text(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'"))
    op.create_table("task_folders",
        sa.Column("id", UUID, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("subject_id", UUID, nullable=False), sa.Column("parent_id", UUID),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("created_by", UUID, nullable=False),
        sa.Column("updated_by", UUID, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_task_folders"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], name="fk_task_folders_subject_id_subjects", ondelete="RESTRICT"),
        sa.UniqueConstraint("id", "subject_id", name="uq_task_folders_id_subject_id"),
        sa.ForeignKeyConstraint(["parent_id", "subject_id"], ["task_folders.id", "task_folders.subject_id"], name="fk_task_folders_parent_subject", ondelete="RESTRICT"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_task_folders_parent_not_self"),
        sa.CheckConstraint("name = btrim(name) AND char_length(name) BETWEEN 1 AND 120 AND name NOT IN ('.', '..') AND strpos(name, '/') = 0 AND strpos(name, chr(92)) = 0", name="ck_task_folders_name_valid"))
    op.create_index("uq_task_folders_root_subject_name_ci", "task_folders", ["subject_id", sa.text("lower(name)")], unique=True, postgresql_where=sa.text("parent_id IS NULL"))
    op.create_index("uq_task_folders_parent_name_ci", "task_folders", ["parent_id", sa.text("lower(name)")], unique=True, postgresql_where=sa.text("parent_id IS NOT NULL"))
    op.create_index("ix_task_folders_subject_parent", "task_folders", ["subject_id", "parent_id"])
    op.create_index("ix_task_folders_parent_id", "task_folders", ["parent_id"])
    op.create_index("ix_task_folders_subject_name", "task_folders", ["subject_id", sa.text("lower(name)"), "id"])
    op.add_column("tasks", sa.Column("folder_id", UUID, nullable=True))
    op.create_foreign_key("fk_tasks_folder_subject", "tasks", "task_folders", ["folder_id", "subject_id"], ["id", "subject_id"], ondelete="RESTRICT")
    op.create_index("ix_tasks_subject_folder", "tasks", ["subject_id", "folder_id"])
    action = postgresql.ENUM(name="audit_action", create_type=False)
    op.create_table("folder_audit_log",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("folder_id", UUID), sa.Column("subject_id", UUID, nullable=False),
        sa.Column("action", action, nullable=False), sa.Column("actor_id", UUID, nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", TS, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_folder_audit_folder_occurred_at", "folder_audit_log", ["folder_id", "occurred_at"])
    op.create_index("ix_folder_audit_subject_occurred_at", "folder_audit_log", ["subject_id", "occurred_at"])

def downgrade() -> None:
    op.drop_table("folder_audit_log")
    op.execute("DELETE FROM audit_log WHERE action::text = 'task_folder_moved'")
    op.drop_index("ix_tasks_subject_folder", table_name="tasks")
    op.drop_constraint("fk_tasks_folder_subject", "tasks", type_="foreignkey")
    op.drop_column("tasks", "folder_id")
    op.drop_table("task_folders")
    op.execute("ALTER TYPE audit_action RENAME TO audit_action_new")
    op.execute("CREATE TYPE audit_action AS ENUM (" + ",".join(repr(x) for x in OLD_ACTIONS) + ")")
    op.execute("ALTER TABLE audit_log ALTER COLUMN action TYPE audit_action USING action::text::audit_action")
    op.execute("DROP TYPE audit_action_new")
