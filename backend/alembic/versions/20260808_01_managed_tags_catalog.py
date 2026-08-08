"""Add the managed tag catalog foundation.

Downgrade intentionally destroys all tag definitions, associations, and catalog
audit records. It is intended only for disposable ``*_test`` databases.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260808_01"
down_revision = "20260806_01"
branch_labels = depends_on = None
UUID = postgresql.UUID(as_uuid=True)
TS = postgresql.TIMESTAMP(timezone=True)
OLD_AUDIT_ACTIONS = ("task_created", "methodology_updated", "submitted_for_review", "returned_to_draft", "version_approved", "version_created", "task_archived", "task_folder_moved", "folder_created", "folder_renamed", "folder_moved", "folder_deleted")
NEW_AUDIT_ACTIONS = ("tag_added_to_version", "tag_removed_from_version")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("active", "deprecated", name="tag_status").create(bind)
    postgresql.ENUM("tag_created", "tag_renamed", "tag_scope_changed", "tag_deprecated", "tag_replacement_changed", name="tag_audit_action").create(bind)
    context = op.get_context()
    for value in NEW_AUDIT_ACTIONS:
        with context.autocommit_block():
            op.execute(sa.text(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'"))
    op.create_table("tag_categories",
        sa.Column("code", sa.String(32), nullable=False), sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_tag_categories"),
        sa.UniqueConstraint("sort_order", name="uq_tag_categories_sort_order"),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="ck_tag_categories_code"),
        sa.CheckConstraint("sort_order >= 0", name="ck_tag_categories_sort_order_nonnegative"))
    op.bulk_insert(sa.table("tag_categories", sa.column("code"), sa.column("display_name"), sa.column("sort_order")), [
        {"code":"exam","display_name":"Экзамен","sort_order":10}, {"code":"purpose","display_name":"Назначение","sort_order":20},
        {"code":"methodology","display_name":"Методика","sort_order":30}, {"code":"task_feature","display_name":"Особенность задания","sort_order":40},
        {"code":"usage_level","display_name":"Уровень использования","sort_order":50}])
    status = postgresql.ENUM(name="tag_status", create_type=False)
    op.create_table("tags",
        sa.Column("id", UUID, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("category_code", sa.String(32), nullable=False), sa.Column("subject_id", UUID),
        sa.Column("name", sa.String(80), nullable=False), sa.Column("normalized_name", sa.String(80), nullable=False),
        sa.Column("status", status, nullable=False, server_default="active"), sa.Column("replacement_tag_id", UUID),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("clock_timestamp()")), sa.Column("created_by", UUID, nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("clock_timestamp()")), sa.Column("updated_by", UUID, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.ForeignKeyConstraint(["category_code"],["tag_categories.code"],name="fk_tags_category_code_tag_categories",onupdate="RESTRICT",ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subject_id"],["subjects.id"],name="fk_tags_subject_id_subjects",ondelete="RESTRICT"),
        sa.UniqueConstraint("normalized_name", name="uq_tags_normalized_name"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="ck_tags_name_length"),
        sa.CheckConstraint("char_length(normalized_name) BETWEEN 1 AND 80", name="ck_tags_normalized_name_length"),
        sa.CheckConstraint("replacement_tag_id IS NULL OR replacement_tag_id <> id", name="ck_tags_replacement_not_self"),
        sa.CheckConstraint("status = 'deprecated' OR replacement_tag_id IS NULL", name="ck_tags_active_without_replacement"))
    op.create_foreign_key("fk_tags_replacement_tag_id_tags", "tags", "tags", ["replacement_tag_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_tags_catalog_order", "tags", ["category_code","normalized_name","id"])
    op.create_index("ix_tags_subject_catalog", "tags", ["subject_id","category_code","normalized_name","id"])
    op.create_index("ix_tags_status", "tags", ["status"]); op.create_index("ix_tags_replacement_tag_id", "tags", ["replacement_tag_id"])
    op.create_index("ix_tags_normalized_name_trgm", "tags", ["normalized_name"], postgresql_using="gin", postgresql_ops={"normalized_name":"gin_trgm_ops"})
    op.create_table("task_version_tags",
        sa.Column("task_version_id", UUID, nullable=False), sa.Column("tag_id", UUID, nullable=False),
        sa.Column("attached_at", TS, nullable=False, server_default=sa.text("clock_timestamp()")), sa.Column("attached_by", UUID, nullable=False),
        sa.PrimaryKeyConstraint("task_version_id","tag_id",name="pk_task_version_tags"),
        sa.ForeignKeyConstraint(["task_version_id"],["task_versions.id"],name="fk_task_version_tags_task_version_id_task_versions",ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"],["tags.id"],name="fk_task_version_tags_tag_id_tags",ondelete="RESTRICT"))
    op.create_index("ix_task_version_tags_tag_version", "task_version_tags", ["tag_id","task_version_id"])
    action = postgresql.ENUM(name="tag_audit_action", create_type=False)
    op.create_table("tag_audit_log",
        sa.Column("id", UUID, nullable=False, server_default=sa.text("gen_random_uuid()")), sa.Column("tag_id", UUID, nullable=False),
        sa.Column("action", action, nullable=False), sa.Column("actor_id", UUID, nullable=False),
        sa.Column("occurred_at", TS, nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("before_snapshot", postgresql.JSONB()), sa.Column("after_snapshot", postgresql.JSONB()),
        sa.PrimaryKeyConstraint("id",name="pk_tag_audit_log"),
        sa.ForeignKeyConstraint(["tag_id"],["tags.id"],name="fk_tag_audit_log_tag_id_tags",ondelete="RESTRICT"),
        sa.CheckConstraint("before_snapshot IS NOT NULL OR after_snapshot IS NOT NULL",name="ck_tag_audit_log_snapshot_present"))
    op.create_index("ix_tag_audit_log_tag_occurred", "tag_audit_log", ["tag_id",sa.text("occurred_at DESC"),sa.text("id DESC")])


def downgrade() -> None:
    op.drop_table("tag_audit_log"); op.drop_table("task_version_tags"); op.drop_table("tags"); op.drop_table("tag_categories")
    postgresql.ENUM(name="tag_audit_action").drop(op.get_bind()); postgresql.ENUM(name="tag_status").drop(op.get_bind())
    op.execute("DELETE FROM audit_log WHERE action::text IN ('tag_added_to_version','tag_removed_from_version')")
    op.execute("ALTER TYPE audit_action RENAME TO audit_action_with_tags")
    op.execute("CREATE TYPE audit_action AS ENUM (" + ",".join(repr(x) for x in OLD_AUDIT_ACTIONS) + ")")
    op.execute("ALTER TABLE audit_log ALTER COLUMN action TYPE audit_action USING action::text::audit_action")
    op.execute("ALTER TABLE folder_audit_log ALTER COLUMN action TYPE audit_action USING action::text::audit_action")
    op.execute("DROP TYPE audit_action_with_tags")
