"""Preflight the complete downgrade plan before any historical DDL executes.

Accepted migrations remain unchanged. Run online through this repository's Alembic
entry point; copying historical migration files alone bypasses this policy.
"""
from alembic.util import CommandError
from sqlalchemy import inspect, text

# Tables whose provenance cannot be represented below their introduction boundary.
HISTORY = {
    "20261013_01": ("scan_matching_attempts",),
    "20261012_02": ("paper_result_publication_withdrawals", "paper_result_publication_commands", "paper_result_publication_command_items"),
    "20261012_01": ("scan_storage_retention_entries",),
    "20261011_02": ("remediation_plan_paper_item_signals", "remediation_plan_paper_finding_signals"),
    "20261010_02": ("paper_result_publications", "paper_result_publication_states"),
    "20261010_01": ("paper_check_reviews", "paper_check_review_revisions"),
    "20261005_01": ("paper_ai_revisions", "paper_check_finding_regions"),
    "20260923_01": ("scan_grading_policy_revisions",),
    "20260915_02": ("paper_submissions", "paper_submission_pages", "scan_grouping_revisions"),
    "20260915_01": ("scan_matching_runs", "scan_matching_chunks", "scan_page_match_proposals", "scan_page_match_candidates"),
    "20260914_03": ("assessment_scan_batches", "scan_batch_artifacts", "scan_pages", "scan_checking_events"),
}


def preflight_paper_downgrade(connection, revisions):
    if not revisions:
        return
    existing = set(inspect(connection).get_table_names())
    targets = sorted({table for revision in revisions for table in HISTORY.get(revision, ())} & existing)
    extra = set()
    if "20261005_01" in revisions:
        extra.update({"model_runs", "check_runs"} & existing)
    if "20260923_01" in revisions:
        extra.update({"check_runs"} & existing)
    if "20261013_01" in revisions:
        extra.update({"scan_matching_runs"} & existing)
    if "20261011_03" in revisions:
        extra.update({"paper_check_reviews"} & existing)
    if "20261011_02" in revisions:
        extra.update({"remediation_plans"} & existing)
    # Block concurrent writers until all migration steps commit or roll back.
    for table in sorted(set(targets) | extra):
        connection.execute(text(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE'))
    for table in targets:
        if connection.scalar(text(f'SELECT EXISTS (SELECT 1 FROM "{table}")')):
            raise CommandError(f"Paper downgrade blocked: {table} contains immutable history; keep the current revision and restore a separately verified backup for rollback")
    for table in extra:
        column = {"scan_matching_runs": "claim_token", "paper_check_reviews": "source_kind",
                  "remediation_plans": "source_paper_submission_id"}.get(table, "paper_submission_id")
        if column not in {c["name"] for c in inspect(connection).get_columns(table)}:
            continue
        predicate = {"claim_token": "status='running' AND claim_token IS NOT NULL",
                     "source_kind": "source_kind='manual'"}.get(column, f"{column} IS NOT NULL")
        if connection.scalar(text(f'SELECT EXISTS (SELECT 1 FROM "{table}" WHERE {predicate})')):
            raise CommandError(f"Paper downgrade blocked: {table} contains paper execution/claim provenance")
