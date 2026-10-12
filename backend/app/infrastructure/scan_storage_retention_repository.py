"""PostgreSQL eligibility, short claims, and fenced purge finalization."""

from uuid import uuid4

from sqlalchemy import text

from app.application.scan_storage_retention import POLICY_VERSION, RetentionTarget

# Page protection follows the authoritative active pointer and confirmed membership.
# Shared inputs are conservatively excluded, including non-paper consumers.
CANDIDATES = """
WITH completed AS (
    SELECT b.id, min(e.occurred_at) AS completed_at
    FROM assessment_scan_batches b JOIN scan_checking_events e ON e.batch_id=b.id
    WHERE b.status='completed' AND e.event_type='batch.completed'
      AND e.aggregate_type='batch' AND e.aggregate_id=b.id
    GROUP BY b.id
), targets AS (
    SELECT b.id AS batch_id, 'source_artifact' AS target_kind, a.id AS target_id,
           i.id AS input_id, i.storage_reference,
           b.completed_at + make_interval(days => :source_days) AS eligible_at
    FROM completed b JOIN scan_batch_artifacts a ON a.batch_id=b.id
    JOIN input_artifacts i ON i.id=a.input_artifact_id
    WHERE NOT EXISTS (SELECT 1 FROM scan_batch_artifacts other
                      WHERE other.input_artifact_id=i.id AND other.id<>a.id)
      AND NOT EXISTS (SELECT 1 FROM scan_pages p WHERE p.derived_render_artifact_id=i.id)
    UNION ALL
    SELECT b.id, 'page_render', p.id, i.id, i.storage_reference,
           b.completed_at + make_interval(days => :page_days)
    FROM completed b JOIN scan_batch_artifacts a ON a.batch_id=b.id
    JOIN scan_pages p ON p.batch_artifact_id=a.id
    JOIN input_artifacts i ON i.id=p.derived_render_artifact_id
    WHERE NOT EXISTS (
        SELECT 1 FROM paper_submission_pages m
        JOIN paper_submissions paper ON paper.id=m.paper_submission_id
        JOIN scan_grouping_revisions g ON g.id=paper.grouping_revision_id AND g.status='confirmed'
        JOIN paper_result_publication_states s ON s.paper_submission_id=paper.id
        JOIN paper_result_publications pub ON pub.id=s.active_publication_id
          AND pub.paper_submission_id=paper.id
        WHERE m.scan_page_id=p.id)
      AND NOT EXISTS (SELECT 1 FROM scan_pages other
                      WHERE other.derived_render_artifact_id=i.id AND other.id<>p.id)
      AND NOT EXISTS (SELECT 1 FROM scan_batch_artifacts other WHERE other.input_artifact_id=i.id)
)
SELECT t.batch_id, t.target_kind, t.target_id, t.storage_reference, t.eligible_at
FROM targets t LEFT JOIN scan_storage_retention_entries r
  ON (t.target_kind='source_artifact' AND r.batch_artifact_id=t.target_id)
  OR (t.target_kind='page_render' AND r.scan_page_id=t.target_id)
WHERE t.eligible_at <= clock_timestamp()
  AND (r.id IS NULL OR (r.state<>'purged' AND
       (r.claim_expires_at IS NULL OR r.claim_expires_at<=clock_timestamp())))
  AND NOT EXISTS (SELECT 1 FROM authoring_sessions a WHERE a.input_artifact_id=t.input_id)
  AND NOT EXISTS (SELECT 1 FROM image_solving_sessions s WHERE s.input_artifact_id=t.input_id)
"""


def parameters(settings):
    return {
        "source_days": settings.source_retention_days,
        "page_days": settings.unpublished_page_retention_days,
        "maximum": settings.retention_max_targets_per_run,
    }


class ScanStorageRetentionRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def discover(self, settings):
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        CANDIDATES
                        + " ORDER BY eligible_at, target_kind, target_id LIMIT :maximum"
                    ),
                    parameters(settings),
                )
            ).all()
            return [RetentionTarget(*row) for row in rows]

    async def claim(self, target, settings):
        # Lock the actual storage-bearing row before the final eligibility query.
        # Publication takes the same page lock and rejects claimed/purged pages.
        table = (
            "scan_batch_artifacts"
            if target.target_kind == "source_artifact"
            else "scan_pages"
        )
        column = (
            "batch_artifact_id"
            if target.target_kind == "source_artifact"
            else "scan_page_id"
        )
        async with self.session_factory() as db, db.begin():
            locked = await db.scalar(
                text(f"SELECT id FROM {table} WHERE id=:id FOR UPDATE SKIP LOCKED"),
                {"id": target.target_id},
            )
            if locked is None:
                return None
            current = (
                await db.execute(
                    text(CANDIDATES + " AND t.target_kind=:kind AND t.target_id=:id"),
                    {
                        **parameters(settings),
                        "kind": target.target_kind,
                        "id": target.target_id,
                    },
                )
            ).first()
            if (
                current is None
                or current.storage_reference != target.storage_reference
                or current.batch_id != target.batch_id
            ):
                return None
            token, entry_id = uuid4(), uuid4()
            row = (
                await db.execute(
                    text(f"""
                INSERT INTO scan_storage_retention_entries
                  (id, scan_batch_id, target_kind, {column}, policy_version, eligible_at,
                   claimed_at, claim_expires_at, claim_token, attempt_count)
                VALUES (:entry_id, :batch_id, :kind, :id, :policy, :eligible_at,
                        clock_timestamp(), clock_timestamp()+make_interval(secs => :ttl), :token, 1)
                ON CONFLICT ({column}) DO UPDATE SET
                  state='pending', claimed_at=clock_timestamp(),
                  claim_expires_at=clock_timestamp()+make_interval(secs => :ttl),
                  claim_token=:token, attempt_count=scan_storage_retention_entries.attempt_count+1,
                  last_error_code=NULL, updated_at=clock_timestamp()
                WHERE scan_storage_retention_entries.state<>'purged' AND
                  (scan_storage_retention_entries.claim_expires_at IS NULL OR
                   scan_storage_retention_entries.claim_expires_at<=clock_timestamp())
                RETURNING id
            """),
                    {
                        "entry_id": entry_id,
                        "batch_id": target.batch_id,
                        "kind": target.target_kind,
                        "id": target.target_id,
                        "policy": POLICY_VERSION,
                        "eligible_at": current.eligible_at,
                        "ttl": settings.retention_claim_ttl_seconds,
                        "token": token,
                    },
                )
            ).first()
            return (row.id, token) if row else None

    async def finalize(self, claim, error):
        async with self.session_factory() as db, db.begin():
            result = await db.execute(
                text("""
                UPDATE scan_storage_retention_entries SET
                  state=:state, last_error_code=:error,
                  purged_at=CASE WHEN CAST(:error AS varchar) IS NULL THEN clock_timestamp() ELSE NULL END,
                  claimed_at=NULL, claim_expires_at=NULL, claim_token=NULL, updated_at=clock_timestamp()
                WHERE id=:id AND claim_token=:token AND state='pending'
            """),
                {
                    "id": claim[0],
                    "token": claim[1],
                    "error": error,
                    "state": "retryable_failed" if error else "purged",
                },
            )
            return result.rowcount == 1


async def retention_attempt_exists(db, *, batch_artifact_id=None, scan_page_id=None):
    """A durable attempt explains a missing blob even before purge finalization."""
    return bool(
        await db.scalar(
            text("""
        SELECT EXISTS (SELECT 1 FROM scan_storage_retention_entries
        WHERE batch_artifact_id=:artifact OR scan_page_id=:page)
    """),
            {"artifact": batch_artifact_id, "page": scan_page_id},
        )
    )
