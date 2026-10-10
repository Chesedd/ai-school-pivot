"""Real PR7 execution -> rollback-isolated teacher review acceptance."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.paper_checking_review import (
    PaperReviewApproval,
    PaperReviewError,
    PaperReviewFinding,
    PaperReviewOverride,
)
from app.infrastructure.paper_checking_review_repository import (
    PaperCheckingReviewRepository,
)
from tests.integration.test_paper_checking_execution_database import (
    FakePaperProvider,
    rejected,
    rows,
    service,
    start,
)
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]
D = Decimal


async def completed(context, status="correct", paper="a"):
    run = await start(context, paper)
    await service(context, FakePaperProvider(status=status)).execute(
        context[2][f"paper_{paper}"], run.id
    )
    return run


async def call(context, run, method, *args, paper="a"):
    _, factory, ids, _ = context
    async with factory() as db, db.begin():
        repo = PaperCheckingReviewRepository(db)
        arguments = (ids[f"paper_{paper}"], run.id)
        if method in {"start", "edit", "approve"}:
            arguments += (ids["teacher"],)
        return await getattr(repo, method)(*arguments, *args)


def edited(review, **changes):
    return review.draft.model_copy(update=changes)


async def evidence(context, run):
    connection = context[0]
    result = []
    for sql in (
        "SELECT to_jsonb(t) FROM paper_ai_revisions t WHERE check_run_id=:run",
        "SELECT to_jsonb(t) FROM check_results t WHERE check_run_id=:run ORDER BY id",
        "SELECT to_jsonb(f) FROM check_findings f JOIN check_results r ON r.id=f.check_result_id WHERE r.check_run_id=:run ORDER BY f.id",
        "SELECT to_jsonb(f) FROM paper_check_finding_regions f JOIN paper_ai_revisions r ON r.id=f.paper_ai_revision_id WHERE r.check_run_id=:run ORDER BY f.id",
        "SELECT to_jsonb(t) FROM model_runs t WHERE check_run_id=:run ORDER BY id",
        "SELECT to_jsonb(t) FROM check_runs t WHERE id=:run",
    ):
        result.append(
            (await connection.execute(text(sql), {"run": run.id})).scalars().all()
        )
    return result


@pytest.mark.parametrize(
    "status,score",
    [
        ("correct", D(10)),
        ("partially_correct", D(5)),
        ("incorrect", D(0)),
        ("not_attempted", None),
        ("unreadable", None),
        ("insufficient_evidence", None),
    ],
)
async def test_seed_copies_complete_ai_evidence_without_fabricated_scores(
    execution_database, status, score
):
    run = await completed(execution_database, status)
    before = await evidence(execution_database, run)
    review = await call(execution_database, run, "start")
    replay = await call(execution_database, run, "start")
    assert replay == review and review.version == 1 and review.state == "reviewing"
    assert review.draft.items[0].accepted_score == score
    assert review.draft.findings[0].source_check_finding_id is not None
    history = await call(execution_database, run, "history")
    assert [(x.revision_number, x.revision_kind) for x in history["revisions"]] == [
        (0, "ai_seed")
    ]
    assert history["revisions"][0].actor_user_id == execution_database[2]["teacher"]
    assert before == await evidence(execution_database, run)


async def test_edit_approve_correct_and_reapprove_preserves_exact_history(
    execution_database,
):
    run = await completed(execution_database, "partially_correct")
    before = await evidence(execution_database, run)
    review = await call(execution_database, run, "start")
    review = await call(
        execution_database, run, "edit", edited(review, summary="Teacher corrected"), 1
    )
    assert review.version == 2
    first = await call(
        execution_database, run, "approve", PaperReviewApproval(expected_version=2)
    )
    approved_a = first.latest_approved_revision
    grade = approved_a.grade_result
    assert (
        grade.calculated_total_score,
        grade.total_max_score,
        grade.calculated_percentage,
    ) == (D(5), D(10), D("50.0000"))
    assert grade.final_grade == grade.calculated_grade == "graded"
    assert first.state == "approved" and first.version == 3
    item = first.draft.items[0].model_copy(
        update={"status": "correct", "accepted_score": D(10)}
    )
    correction = await call(
        execution_database, run, "edit", edited(first, items=(item,)), 3
    )
    assert correction.state == "reviewing" and correction.version == 4
    assert correction.latest_approved_revision_id == approved_a.id
    assert correction.latest_approved_revision == approved_a
    second = await call(
        execution_database, run, "approve", PaperReviewApproval(expected_version=4)
    )
    assert second.latest_approved_revision_id != approved_a.id
    assert second.latest_approved_revision.grade_result.calculated_total_score == D(10)
    history = (await call(execution_database, run, "history"))["revisions"]
    assert [x.revision_number for x in history] == list(range(5))
    assert [x.revision_kind for x in history] == [
        "ai_seed",
        "human_edit",
        "approved",
        "human_edit",
        "approved",
    ]
    assert history[2] == approved_a
    assert history[1].change_summary == ("summary",)
    assert history[3].change_summary == ("item_score_status",)
    assert before == await evidence(execution_database, run)
    connection, _, ids, _ = execution_database
    assert (
        await connection.scalar(
            text("SELECT status FROM paper_submissions WHERE id=:id"),
            {"id": ids["paper_a"]},
        )
        == "ready_for_checking"
    )
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "checking"
    )
    assert (
        await connection.scalar(
            text("SELECT status FROM check_runs WHERE id=:id"), {"id": run.id}
        )
        == "completed_with_review_required"
    )
    assert not await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM paper_result_publications WHERE paper_submission_id=:id)"),
        {"id": ids["paper_a"]},
    )


@pytest.mark.parametrize("operation", ["edit", "approve"])
async def test_stale_concurrent_workspace_version_is_rejected(
    execution_database, operation
):
    run = await completed(execution_database)
    # Two independent sessions read the same version; only the first writer wins.
    first = await call(execution_database, run, "start")
    second = await call(execution_database, run, "get")
    await call(
        execution_database,
        run,
        "edit",
        edited(first, summary="First writer"),
        first.version,
    )
    with pytest.raises(PaperReviewError, match="paper_review_version_conflict"):
        if operation == "edit":
            await call(
                execution_database,
                run,
                "edit",
                edited(second, summary="Stale writer"),
                second.version,
            )
        else:
            await call(
                execution_database,
                run,
                "approve",
                PaperReviewApproval(expected_version=second.version),
            )
    history = await call(execution_database, run, "history")
    assert len(history["revisions"]) == 2


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE paper_check_review_revisions SET snapshot=snapshot WHERE id=:id",
        "DELETE FROM paper_check_review_revisions WHERE id=:id",
    ],
)
async def test_revision_immutability_in_postgresql(execution_database, sql):
    run = await completed(execution_database)
    await call(execution_database, run, "start")
    approved = await call(
        execution_database, run, "approve", PaperReviewApproval(expected_version=1)
    )
    await rejected(
        execution_database[0],
        sql,
        {"id": approved.latest_approved_revision_id},
        match="immutable",
    )


async def test_override_preserves_application_grade_and_reason(execution_database):
    run = await completed(execution_database)
    await call(execution_database, run, "start")
    review = await call(
        execution_database,
        run,
        "approve",
        PaperReviewApproval(
            expected_version=1,
            grade_override=PaperReviewOverride(
                override_grade="moderated-label", reason="Teacher moderation"
            ),
        ),
    )
    grade = review.latest_approved_revision.grade_result
    assert grade.calculated_grade == "graded" and grade.final_grade == "moderated-label"
    assert grade.grade_override_reason == "Teacher moderation"
    assert review.latest_approved_revision.change_summary == ("grade_override_intent",)


@pytest.mark.parametrize(
    "status", ["not_attempted", "unreadable", "insufficient_evidence"]
)
async def test_unresolved_approval_atomic_and_teacher_resolution(
    execution_database, status
):
    run = await completed(execution_database, status)
    review = await call(execution_database, run, "start")
    with pytest.raises(PaperReviewError, match="unresolved"):
        await call(
            execution_database, run, "approve", PaperReviewApproval(expected_version=1)
        )
    current = await call(execution_database, run, "get")
    assert current == review
    assert len((await call(execution_database, run, "history"))["revisions"]) == 1
    item = review.draft.items[0].model_copy(
        update={
            "status": status if status == "not_attempted" else "incorrect",
            "accepted_score": D(0),
        }
    )
    resolved = await call(
        execution_database, run, "edit", edited(review, items=(item,)), 1
    )
    result = await call(
        execution_database,
        run,
        "approve",
        PaperReviewApproval(expected_version=resolved.version),
    )
    assert result.latest_approved_revision.grade_result.calculated_total_score == D(0)


@pytest.mark.parametrize(
    "tamper,error",
    [
        ("maximum", "policy_mismatch"),
        ("extra_item", "coverage_mismatch"),
        ("foreign_result", "result_lineage_mismatch"),
        ("foreign_page", "foreign_page"),
        ("foreign_finding", "finding_lineage_mismatch"),
        ("provenance", "provenance_mismatch"),
    ],
)
async def test_edit_rejects_foreign_and_tampered_provenance(
    execution_database, tamper, error
):
    run = await completed(execution_database)
    other_run = await completed(execution_database, paper="b")
    review = await call(execution_database, run, "start")
    other = await call(execution_database, other_run, "start", paper="b")
    item, finding = review.draft.items[0], review.draft.findings[0]
    value = {
        "maximum": edited(
            review, items=(item.model_copy(update={"max_score": D(11)}),)
        ),
        "extra_item": edited(review, items=(item, other.draft.items[0])),
        "foreign_result": edited(
            review,
            items=(
                item.model_copy(
                    update={
                        "source_check_result_id": other.draft.items[
                            0
                        ].source_check_result_id
                    }
                ),
            ),
        ),
        "foreign_page": edited(
            review,
            findings=(
                finding.model_copy(
                    update={"scan_page_id": execution_database[2]["page_b"]}
                ),
            ),
        ),
        "foreign_finding": edited(
            review,
            findings=(
                finding.model_copy(
                    update={
                        "source_check_finding_id": other.draft.findings[
                            0
                        ].source_check_finding_id
                    }
                ),
            ),
        ),
        "provenance": edited(review, ai_revision_id=other.ai_revision_id),
    }[tamper]
    with pytest.raises(PaperReviewError, match=error):
        await call(execution_database, run, "edit", value, 1)
    assert await call(execution_database, run, "get") == review
    assert await call(execution_database, other_run, "get", paper="b") == other


async def test_finding_full_replacement_changes_only_human_snapshot(execution_database):
    run = await completed(execution_database)
    before = await evidence(execution_database, run)
    review = await call(execution_database, run, "start")
    original = review.draft.findings[0]
    modified = original.model_copy(
        update={
            "short_explanation": "Teacher correction",
            "region": original.region.model_copy(update={"x": D("0.75")}),
            "category": "arithmetic",
            "severity": "error",
        }
    )
    review = await call(
        execution_database, run, "edit", edited(review, findings=(modified,)), 1
    )
    assert (
        review.draft.findings[0].source_check_finding_id
        == original.source_check_finding_id
    )
    added = PaperReviewFinding(
        finding_id=uuid4(),
        scan_page_id=original.scan_page_id,
        category="method",
        severity="info",
        short_explanation="Teacher added",
        region=original.region,
    )
    review = await call(
        execution_database, run, "edit", edited(review, findings=(added,)), 2
    )
    assert review.draft.findings == (added,) and added.source_check_finding_id is None
    assert before == await evidence(execution_database, run)


async def test_reordered_identical_draft_does_not_create_meaningless_revision(
    execution_database,
):
    run = await completed(execution_database)
    review = await call(execution_database, run, "start")
    result = await call(execution_database, run, "edit", review.draft, 1)
    assert result == review
    assert len((await call(execution_database, run, "history"))["revisions"]) == 1


async def test_not_ready_and_mismatched_run(execution_database):
    run = await start(execution_database)
    with pytest.raises(PaperReviewError, match="not_ready"):
        await call(execution_database, run, "start")
    with pytest.raises(PaperReviewError, match="not_found"):
        await call(execution_database, run, "start", paper="b")


async def test_exact_policy_provenance_fails_closed(execution_database):
    from app.infrastructure.checking_models import CheckRun, PaperAIRevision
    from app.infrastructure.scan_checking_models import PaperSubmission

    run = await completed(execution_database)
    _, factory, ids, _ = execution_database
    async with factory() as db:
        repo = PaperCheckingReviewRepository(db)
        paper = await db.get(PaperSubmission, ids["paper_a"])
        persisted = await db.get(CheckRun, run.id)
        from sqlalchemy import select

        ai = await db.scalar(
            select(PaperAIRevision).where(PaperAIRevision.check_run_id == run.id)
        )
        # Test corrupt source in memory without ever mutating a persisted AI row.
        db.expunge(persisted)
        persisted.input_snapshot = json.loads(json.dumps(persisted.input_snapshot))
        persisted.input_snapshot["grading_policy"]["revision"] += 1
        with pytest.raises(PaperReviewError, match="policy_mismatch"):
            await repo._policy(paper, persisted, ai)


async def test_database_rejects_foreign_pointer_and_unapproved_pointer(
    execution_database,
):
    run = await completed(execution_database)
    review = await call(execution_database, run, "start")
    seed = (await call(execution_database, run, "history"))["revisions"][0]
    await rejected(
        execution_database[0],
        "UPDATE paper_check_reviews SET latest_approved_revision_id=:revision WHERE id=:id",
        {"id": review.review_id, "revision": seed.id},
        match="requires approved",
    )
    other_run = await completed(execution_database, paper="b")
    await call(execution_database, other_run, "start", paper="b")
    other = await call(
        execution_database,
        other_run,
        "approve",
        PaperReviewApproval(expected_version=1),
        paper="b",
    )
    await rejected(
        execution_database[0],
        "UPDATE paper_check_reviews SET latest_approved_revision_id=:revision WHERE id=:id",
        {"id": review.review_id, "revision": other.latest_approved_revision_id},
        match="requires approved",
    )


async def test_migrated_review_schema_and_unique_workspace(execution_database):
    from tests.integration.test_migrations import repository_head

    connection = execution_database[0]
    assert (
        await connection.scalar(text("SELECT version_num FROM alembic_version"))
        == repository_head()
    )
    run = await completed(execution_database)
    review = await call(execution_database, run, "start")
    from sqlalchemy.exc import IntegrityError

    from tests.integration.test_paper_checking_execution_database import clone

    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await clone(connection, "paper_check_reviews", review.review_id)
    indexes = await rows(
        connection,
        "SELECT indexname FROM pg_indexes WHERE tablename='paper_check_review_revisions'",
    )
    assert "ix_paper_check_review_revisions_history" in {
        x["indexname"] for x in indexes
    }


@pytest.mark.parametrize(
    "status,score,accepted",
    [
        ("correct", "10", True),
        ("correct", "9", False),
        ("partially_correct", "5", True),
        ("partially_correct", "0", False),
        ("partially_correct", "10", False),
        ("incorrect", "0", True),
        ("incorrect", "1", False),
        ("not_attempted", "0", True),
        ("not_attempted", "1", False),
        ("unreadable", "0", False),
        ("insufficient_evidence", "10", False),
    ],
)
async def test_persisted_approval_score_rules(
    execution_database, status, score, accepted
):
    run = await completed(execution_database)
    review = await call(execution_database, run, "start")
    item = review.draft.items[0].model_copy(
        update={"status": status, "accepted_score": D(score)}
    )
    review = await call(
        execution_database, run, "edit", edited(review, items=(item,)), 1
    )
    if accepted:
        approved = await call(
            execution_database,
            run,
            "approve",
            PaperReviewApproval(expected_version=review.version),
        )
        assert (
            approved.latest_approved_revision.grade_result.calculated_total_score
            == D(score)
        )
    else:
        with pytest.raises(PaperReviewError):
            await call(
                execution_database,
                run,
                "approve",
                PaperReviewApproval(expected_version=review.version),
            )
        assert await call(execution_database, run, "get") == review
        assert all(
            x.revision_kind != "approved"
            for x in (await call(execution_database, run, "history"))["revisions"]
        )


@pytest.mark.parametrize("basis", ["points", "percent"])
async def test_exact_policy_scale_and_display_rounding_survive_database(
    execution_database, basis
):
    from app.application.scan_grading_policy import GradeScale, GradeThreshold
    from app.infrastructure.paper_checking_repository import (
        PaperGradingPolicyRepository,
    )
    from tests.integration.test_paper_checking_intake_database import (
        freeze,
        intake,
        policy,
        request,
    )

    _, factory, ids, _ = execution_database
    await freeze(factory, ids, "b")
    value = policy(ids).model_copy(
        update={
            "grade_scale": GradeScale(
                basis=basis,
                thresholds=(
                    GradeThreshold(
                        minimum=D(7 if basis == "points" else 70), grade_label="pass"
                    ),
                    GradeThreshold(minimum=D(0), grade_label="retry"),
                ),
            )
        }
    )
    async with factory() as db, db.begin():
        await PaperGradingPolicyRepository(db).freeze(
            ids["batch"], ids["variant_a"], value, ids["teacher"]
        )
    run = await intake(factory).create(request(ids))
    await service(execution_database, FakePaperProvider()).execute(
        ids["paper_a"], run.id
    )
    review = await call(execution_database, run, "start")
    item = review.draft.items[0].model_copy(
        update={"status": "partially_correct", "accepted_score": D("6.9999999")}
    )
    review = await call(
        execution_database, run, "edit", edited(review, items=(item,)), 1
    )
    approved = await call(
        execution_database,
        run,
        "approve",
        PaperReviewApproval(expected_version=review.version),
    )
    grade = approved.latest_approved_revision.grade_result
    assert grade.calculated_total_score == D("6.9999999")
    assert grade.calculated_percentage == D("70.0000")
    assert grade.calculated_grade == "retry"
    assert (
        approved.latest_approved_revision.grading_policy_fingerprint
        == value.fingerprint
    )


async def test_failed_correction_approval_keeps_previous_approval(execution_database):
    run = await completed(execution_database)
    await call(execution_database, run, "start")
    approved = await call(
        execution_database, run, "approve", PaperReviewApproval(expected_version=1)
    )
    unresolved = approved.draft.items[0].model_copy(
        update={"status": "unreadable", "accepted_score": None}
    )
    changed = await call(
        execution_database, run, "edit", edited(approved, items=(unresolved,)), 2
    )
    with pytest.raises(PaperReviewError, match="unresolved"):
        await call(
            execution_database, run, "approve", PaperReviewApproval(expected_version=3)
        )
    assert await call(execution_database, run, "get") == changed
    assert changed.latest_approved_revision == approved.latest_approved_revision


@pytest.mark.parametrize(
    "changes",
    [
        {"final_grade": "other-without-reason"},
        {"grade_override_reason": ""},
        {"calculated_grade": None},
        {"calculated_total_score": -1},
        {"calculated_percentage": 101},
        {"total_max_score": 0},
        {"revision_kind": "human_edit"},
    ],
)
async def test_database_grade_constraints_reject_invalid_approved_snapshots(
    execution_database, changes
):
    from sqlalchemy.exc import IntegrityError

    from tests.integration.test_paper_checking_execution_database import clone

    run = await completed(execution_database)
    await call(execution_database, run, "start")
    approved = await call(
        execution_database, run, "approve", PaperReviewApproval(expected_version=1)
    )
    connection = execution_database[0]
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await clone(
                connection,
                "paper_check_review_revisions",
                approved.latest_approved_revision_id,
                revision_number=99,
                **changes,
            )
