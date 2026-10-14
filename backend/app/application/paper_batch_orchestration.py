"""Durable batch intake and derived progress; provider execution stays separate."""

from collections import Counter
from dataclasses import dataclass, replace
from uuid import UUID


class PaperBatchError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


CHECK_STATUSES = (
    "not_started",
    "pending",
    "running",
    "completed",
    "completed_with_review_required",
    "failed_retryable",
    "failed_terminal",
    "manual_reviewing",
    "manual_approved",
)
SETTLED = frozenset(
    {
        "completed",
        "completed_with_review_required",
        "failed_terminal",
        "manual_approved",
    }
)


@dataclass(frozen=True)
class PaperBatchProgress:
    batch_id: UUID
    batch_status: str
    paper_count: int
    not_started: int
    pending: int
    running: int
    completed: int
    completed_with_review_required: int
    failed_retryable: int
    failed_terminal: int
    manual_reviewing: int
    manual_approved: int
    settled_count: int
    active_publication_count: int
    can_mark_checking_completed: bool
    can_reconcile: bool
    can_finalize: bool
    blocking_reasons: tuple[str, ...]
    papers: tuple = ()


def effective_runs(papers, runs):
    """Canonical attempt numbers are unique per paper; timestamps are irrelevant."""
    paper_ids = set(papers)
    latest = {}
    for run in runs:
        paper_id, attempt_no, *_ = run
        if paper_id in paper_ids and (
            paper_id not in latest or attempt_no > latest[paper_id][1]
        ):
            latest[paper_id] = run
    return latest


def derive_progress(
    batch_id, batch_status, papers, runs, active_paper_ids, manual_reviews=()
):
    """Runs are (paper id, canonical attempt number, status), never model attempts.

    Only paper IDs in this batch and active pointers for those papers contribute.
    The database enforces a unique attempt number per paper.
    """
    paper_ids = set(papers)
    latest = {p: (r[1], r[2]) for p, r in effective_runs(paper_ids, runs).items()}
    for paper_id, state in manual_reviews:
        if paper_id in paper_ids:
            latest[paper_id] = (
                0,
                "manual_approved" if state == "approved" else "manual_reviewing",
            )
    counts = Counter(latest[p][1] if p in latest else "not_started" for p in paper_ids)
    # Unknown future statuses are deliberately unsettled rather than successful.
    settled = sum(counts[s] for s in SETTLED)
    total = len(paper_ids)
    published = len(paper_ids.intersection(active_paper_ids))
    can_settle = total > 0 and settled == total
    reasons = []
    if not total:
        reasons.append("paper_batch_has_no_papers")
    for status in (
        "not_started",
        "pending",
        "running",
        "failed_retryable",
        "manual_reviewing",
    ):
        if counts[status]:
            reasons.append(f"paper_batch_{status}_checks_remaining")
    if settled < total and not any(
        counts[s] for s in CHECK_STATUSES if s not in SETTLED
    ):
        reasons.append("paper_batch_checking_not_settled")
    if published < total:
        reasons.append("paper_batch_publications_incomplete")
    if batch_status != "checking_completed":
        reasons.append("paper_batch_not_checking_completed")
    return PaperBatchProgress(
        batch_id,
        batch_status,
        total,
        *(counts[s] for s in CHECK_STATUSES),
        settled,
        published,
        can_settle,
        batch_status == "checking",
        batch_status == "checking_completed" and total > 0 and published == total,
        tuple(reasons),
    )


class PaperBatchOrchestrationService:
    """Caller owns authorization and transaction; repository locks the batch."""

    def __init__(self, repository):
        self.repository = repository

    async def progress(self, batch_id):
        batch = await self.repository.load(batch_id)
        return await self.repository.progress(batch)

    async def start(self, batch_id, actor_id):
        batch = await self.repository.load(batch_id, lock=True)
        progress = await self.repository.progress(batch)
        if batch.status not in {"ready_for_checking", "checking"}:
            raise PaperBatchError("paper_batch_not_ready_for_checking")
        if not progress.paper_count:
            raise PaperBatchError("paper_batch_has_no_papers")
        await self.repository.validate_prerequisites(batch)
        if batch.status == "ready_for_checking":
            await self.repository.transition(batch, "checking", actor_id, progress)
        await self.repository.schedule_initial_runs(batch, actor_id)
        return await self.repository.progress(batch)

    async def reconcile(self, batch_id, actor_id):
        batch = await self.repository.load(batch_id, lock=True)
        progress = await self.repository.progress(batch)
        if batch.status == "checking" and progress.can_mark_checking_completed:
            await self.repository.transition(
                batch, "checking_completed", actor_id, progress
            )
            return await self.repository.progress(batch)
        return progress

    async def finalize(self, batch_id, actor_id):
        batch = await self.repository.load(batch_id, lock=True)
        progress = await self.repository.progress(batch)
        if batch.status == "completed":
            return progress
        if batch.status != "checking_completed":
            raise PaperBatchError("paper_batch_not_checking_completed")
        if not progress.paper_count:
            raise PaperBatchError("paper_batch_has_no_papers")
        if not progress.can_finalize:
            raise PaperBatchError("paper_batch_publications_incomplete")
        await self.repository.transition(batch, "completed", actor_id, progress)
        return replace(
            progress,
            batch_status="completed",
            can_finalize=False,
            blocking_reasons=("paper_batch_not_checking_completed",),
        )
