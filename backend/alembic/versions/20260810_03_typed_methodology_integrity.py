"""Complete typed methodology weighted-policy integrity.

Revision ID: 20260810_03
Revises: 20260810_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260810_03"
down_revision = "20260810_02"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column("choice_option_rules", sa.Column("role", sa.String(16), nullable=True))
    op.execute("""UPDATE choice_option_rules r SET role = CASE WHEN EXISTS (
        SELECT 1 FROM accepted_answer_options ao
        WHERE ao.choice_option_id=r.choice_option_id AND ao.task_version_id=r.task_version_id
    ) THEN 'correct' ELSE 'distractor' END""")
    op.alter_column("choice_option_rules", "role", nullable=False)
    op.create_check_constraint("ck_choice_option_rules_role", "choice_option_rules", "role IN ('correct','distractor')")
    op.execute("""CREATE FUNCTION methodology_validate_version(p_version uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE p_mode text; p_format text;
BEGIN
  SELECT p.mode, v.answer_format::text INTO p_mode, p_format
  FROM choice_scoring_policies p JOIN task_versions v ON v.id=p.task_version_id
  WHERE p.task_version_id=p_version;
  IF p_mode IS NULL THEN RETURN; END IF;
  IF EXISTS (SELECT 1 FROM accepted_answers a WHERE a.task_version_id=p_version AND a.value_kind='choice_set'
             AND NOT EXISTS (SELECT 1 FROM accepted_answer_options ao WHERE ao.accepted_answer_id=a.id)) THEN
    RAISE EXCEPTION 'choice_set cannot be empty';
  END IF;
  IF p_mode='all_or_nothing' AND EXISTS (
      SELECT 1 FROM choice_option_rules r JOIN choice_scoring_policies p ON p.id=r.policy_id WHERE p.task_version_id=p_version) THEN
    RAISE EXCEPTION 'all_or_nothing policy cannot have option rules';
  END IF;
  IF p_mode='per_option' THEN
    IF p_format<>'multiple_choice' THEN RAISE EXCEPTION 'per_option requires multiple_choice'; END IF;
    IF EXISTS (SELECT 1 FROM choice_options o WHERE o.task_version_id=p_version AND NOT EXISTS (
        SELECT 1 FROM choice_option_rules r JOIN choice_scoring_policies p ON p.id=r.policy_id
        WHERE p.task_version_id=p_version AND r.choice_option_id=o.id)) THEN
      RAISE EXCEPTION 'per_option requires one rule per option';
    END IF;
    IF EXISTS (SELECT 1 FROM choice_option_rules r JOIN choice_scoring_policies p ON p.id=r.policy_id
        WHERE p.task_version_id=p_version AND ((r.role='correct') <> EXISTS (
          SELECT 1 FROM accepted_answer_options ao WHERE ao.task_version_id=p_version AND ao.choice_option_id=r.choice_option_id))) THEN
      RAISE EXCEPTION 'option role conflicts with accepted sets';
    END IF;
    IF EXISTS (SELECT 1 FROM choice_option_rules r JOIN choice_scoring_policies p ON p.id=r.policy_id
        WHERE p.task_version_id=p_version AND ((r.role='correct' AND r.weight<=0) OR (r.role='distractor' AND r.weight>=0))) THEN
      RAISE EXCEPTION 'invalid option weight sign';
    END IF;
    IF (SELECT COALESCE(sum(r.weight),0) FROM choice_option_rules r JOIN choice_scoring_policies p ON p.id=r.policy_id
        WHERE p.task_version_id=p_version AND r.role='correct') <> 1.000000 THEN
      RAISE EXCEPTION 'correct option weights must sum to 1.000000';
    END IF;
  END IF;
END $$""")
    op.execute("""CREATE FUNCTION methodology_integrity_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v uuid;
BEGIN
  IF TG_TABLE_NAME='choice_scoring_policies' THEN v=COALESCE(NEW.task_version_id,OLD.task_version_id);
  ELSIF TG_TABLE_NAME='choice_option_rules' THEN v=COALESCE(NEW.task_version_id,OLD.task_version_id);
  ELSIF TG_TABLE_NAME='choice_options' THEN v=COALESCE(NEW.task_version_id,OLD.task_version_id);
  ELSIF TG_TABLE_NAME='accepted_answer_options' THEN v=COALESCE(NEW.task_version_id,OLD.task_version_id);
  ELSE v=COALESCE(NEW.task_version_id,OLD.task_version_id); END IF;
  PERFORM methodology_validate_version(v); RETURN NULL;
END $$""")
    op.execute("""DO $$ DECLARE v uuid; BEGIN FOR v IN SELECT task_version_id FROM choice_scoring_policies LOOP
  PERFORM methodology_validate_version(v);
END LOOP; END $$""")
    for table in ("choice_scoring_policies", "choice_option_rules", "choice_options", "accepted_answer_options", "accepted_answers"):
        op.execute(f"CREATE CONSTRAINT TRIGGER trg_{table}_methodology_integrity AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION methodology_integrity_trigger()")


def downgrade() -> None:
    for table in reversed(("choice_scoring_policies", "choice_option_rules", "choice_options", "accepted_answer_options", "accepted_answers")):
        op.execute(f"DROP TRIGGER trg_{table}_methodology_integrity ON {table}")
    op.execute("DROP FUNCTION methodology_integrity_trigger()")
    op.execute("DROP FUNCTION methodology_validate_version(uuid)")
    op.drop_constraint("ck_choice_option_rules_role", "choice_option_rules", type_="check")
    op.drop_column("choice_option_rules", "role")
