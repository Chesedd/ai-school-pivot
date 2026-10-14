"""PR12 acceptance against real PostgreSQL, including committed lock races."""

import asyncio
import json
import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from app.application.capabilities import capabilities_for_roles
from app.application.checking import InvalidPersistenceCommand
from app.application.paper_batch_orchestration import PaperBatchOrchestrationService
from app.application.paper_checking_review import (
    PaperReviewApproval,
    PaperReviewError,
    PaperReviewOverride,
)
from app.application.principal import Principal
from app.db.session import get_session
from app.infrastructure.checking_repository import CheckingRepository
from app.infrastructure.paper_batch_orchestration_repository import (
    PaperBatchOrchestrationRepository,
)
from app.infrastructure.paper_checking_review_repository import (
    PaperCheckingReviewRepository,
)
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from app.presentation.auth_dependencies import require_principal
from app.presentation.remediation_schemas import ItemInput
from app.presentation.scan_intake_routes import router
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_paper_checking_execution_database import (
    FakePaperProvider,
    rejected,
    service,
    start,
)
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_intake_database import (
    freeze,
    intake,
    request,
)
from tests.integration.test_paper_checking_review_database import completed, evidence
from tests.integration.test_paper_remediation_database import (
    create,
    eligibility,
    invoke,
)
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)
from tests.integration.test_paper_result_publication_database import (
    publish,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]
D = Decimal
HALF = D(5)


async def policies(context):
    for paper in ("a", "b"):
        await freeze(context[1], context[2], paper)


async def paper_evidence_counts(context, paper="a"):
    """Committed evidence for other papers may precede this suite."""
    sources = {
        "paper_result_publications": "paper_result_publications WHERE paper_submission_id=:id",
        "remediation_plans": "remediation_plans WHERE source_paper_submission_id=:id",
        "check_runs": "check_runs WHERE paper_submission_id=:id",
        "model_runs": "model_runs WHERE paper_submission_id=:id",
        "paper_ai_revisions": "paper_ai_revisions WHERE paper_submission_id=:id",
        "check_results": "check_results x JOIN check_runs r ON r.id=x.check_run_id WHERE r.paper_submission_id=:id",
        "check_findings": "check_findings f JOIN check_results x ON x.id=f.check_result_id JOIN check_runs r ON r.id=x.check_run_id WHERE r.paper_submission_id=:id",
        "checker_events": "checker_events x JOIN check_runs r ON r.id=x.check_run_id WHERE r.paper_submission_id=:id",
    }
    return {
        table: await context[0].scalar(
            text(f"SELECT count(*) FROM {source}"),
            {"id": context[2][f"paper_{paper}"]},
        )
        for table, source in sources.items()
    }


async def manual(context, method="start_manual", *args, paper="a"):
    async with context[1]() as db, db.begin():
        repo = PaperCheckingReviewRepository(db)
        arguments = (context[2][f"paper_{paper}"],)
        if method != "start_manual":
            arguments += (None,)
        if method in {"start_manual", "edit", "approve"}:
            arguments += (context[2]["teacher"],)
        return await getattr(repo, method)(*arguments, *args)


async def grade(
    context, review, status="partially_correct", score=HALF, *, paper="a", override=None
):
    draft = review.draft.model_copy(
        update={
            "summary": "Teacher checked this paper",
            "items": tuple(
                x.model_copy(update={"status": status, "accepted_score": score})
                for x in review.draft.items
            ),
        }
    )
    edited = await manual(context, "edit", draft, review.version, paper=paper)
    return await manual(
        context,
        "approve",
        PaperReviewApproval(expected_version=edited.version, grade_override=override),
        paper=paper,
    )


async def batch(context, method="progress"):
    async with context[1]() as db, db.begin():
        svc = PaperBatchOrchestrationService(PaperBatchOrchestrationRepository(db))
        args = (context[2]["batch"],)
        if method != "progress":
            args += (context[2]["teacher"],)
        return await getattr(svc, method)(*args)


async def test_offline_seed_idempotence_and_complete_pipeline(execution_database):
    c = execution_database
    await policies(c)
    first = await manual(c)
    assert await manual(c) == first
    assert first.source_kind == first.draft.source_kind == "manual"
    assert first.check_run_id is first.ai_revision_id is None
    assert first.draft.findings == () and first.draft.summary == ""
    assert [
        (
            x.assessment_item_id,
            x.max_score,
            x.status,
            x.accepted_score,
            x.source_check_result_id,
        )
        for x in first.draft.items
    ] == [(c[2]["item_a"], D(10), None, None, None)]
    history = (await manual(c, "history"))["revisions"]
    assert len(history) == 1 and history[0].revision_kind == "manual_seed"
    assert history[0].source_kind == "manual" and history[0].grading_policy_fingerprint
    progress = await batch(c)
    assert progress.batch_status == "checking" and progress.manual_reviewing == 1
    assert progress.settled_count == 0
    approved = await grade(c, first)
    assert approved.latest_approved_revision.grade_result.calculated_total_score == D(5)
    assert (await batch(c)).manual_approved == 1
    assert set((await paper_evidence_counts(c)).values()) == {0}
    pub = await publish(c, approved.latest_approved_revision_id)
    async with c[1]() as db:
        student = await PaperResultPublicationRepository(db).student_result(
            c[2]["paper_a"], c[2]["student_a"]
        )
        assert student["result"] == pub["result"]
        assert "source_kind" not in json.dumps(student, default=str)
        assert await PaperResultPublicationRepository(db).student_page(
            c[2]["paper_a"], c[2]["page_a"], c[2]["student_a"], c[3]
        )
        with pytest.raises(Exception, match="paper_result_not_found"):
            await PaperResultPublicationRepository(db).student_page(
                c[2]["paper_a"], c[2]["source"], c[2]["student_a"], c[3]
            )
    other = await grade(c, await manual(c, paper="b"), "correct", D(20), paper="b")
    assert (await batch(c)).batch_status == "checking_completed"
    await publish(c, other.latest_approved_revision_id, "b")
    assert (await batch(c)).batch_status == "checking_completed"
    assert (await batch(c, "finalize")).batch_status == "completed"
    for paper in ("a", "b"):
        counts = await paper_evidence_counts(c, paper)
        assert counts.pop("paper_result_publications") == 1
        assert set(counts.values()) == {0}


@pytest.mark.parametrize(
    "status", ["pending", "running", "completed", "completed_with_review_required"]
)
async def test_ai_active_or_successful_rejects_manual(execution_database, status):
    c = execution_database
    if status == "completed_with_review_required":
        await completed(c)
    else:
        run = await start(c)
        if status != "pending":
            async with c[1]() as db, db.begin():
                repo = CheckingRepository(db)
                await repo.transition_run(run.id, 1, "running")
                if status == "completed":
                    await repo.transition_run(run.id, 2, "completed")
    code = (
        "check_in_progress"
        if status in {"pending", "running"}
        else "ai_result_available"
    )
    with pytest.raises(PaperReviewError, match=code):
        await manual(c)


@pytest.mark.parametrize("failure", ["failed_retryable", "failed_terminal"])
async def test_failed_ai_takeover_history_and_gap_recovery(execution_database, failure):
    c = execution_database
    run = await start(c)
    provider = FakePaperProvider(
        mode="transport" if failure == "failed_retryable" else "unknown_item"
    )
    from app.application.paper_checking_execution import PaperCheckingExecutionError

    with pytest.raises(PaperCheckingExecutionError):
        await service(c, provider).execute(c[2]["paper_a"], run.id)
    assert (
        await c[0].scalar(
            text("SELECT status FROM check_runs WHERE id=:id"), {"id": run.id}
        )
        == failure
    )
    other_run = await completed(c, paper="b")
    from tests.integration.test_paper_checking_review_database import call

    other = await call(c, other_run, "start", paper="b")
    other = await call(
        c, other_run, "approve", PaperReviewApproval(expected_version=1), paper="b"
    )
    await publish(c, other.latest_approved_revision_id, "b")
    await batch(c, "reconcile")
    if failure == "failed_terminal":
        assert (await batch(c)).batch_status == "checking_completed"
    before = await evidence(c, run)
    review = await manual(c)
    progress = await batch(c)
    assert progress.batch_status == "checking" and progress.manual_reviewing == 1
    assert progress.failed_retryable == progress.failed_terminal == 0
    with pytest.raises(InvalidPersistenceCommand, match="paper_manual_review_active"):
        await intake(c[1]).create(request(c[2], "a", "new-ai-after-manual"))
    approved = await grade(c, review)
    assert (await batch(c)).batch_status == "checking_completed"
    assert (await batch(c, "reconcile")).batch_status == "checking_completed"
    assert await evidence(c, run) == before
    assert await c[0].scalar(text("SELECT count(*) FROM remediation_plans")) == 0
    pub = await publish(c, approved.latest_approved_revision_id)
    assert (await eligibility(c))["eligible"]
    plan = await create(
        c,
        pub,
        items=[ItemInput(task_version_id=c[2]["version_b"], selection_source="manual")],
    )
    assert plan["source_check_run_id"] is None
    await invoke(c, "assign", plan["id"], c[2]["teacher"], False, False)
    assert (await invoke(c, "student_detail", plan["id"], c[2]["student_a"]))[
        "status"
    ] == "assigned"
    from app.application.remediation_execution import RemediationExecutionService

    execution = RemediationExecutionService(c[1])
    rubric = uuid4()
    await c[0].execute(
        text(
            "INSERT INTO rubrics(id,task_version_id,max_score,grading_mode,notes) VALUES (:rubric,:version_b,1,'points','Practice rubric')"
        ),
        {**c[2], "rubric": rubric},
    )
    await c[0].execute(
        text(
            "INSERT INTO rubric_items(rubric_id,criterion,max_points,required,order_index) VALUES (:rubric,'Criterion',1,true,0)"
        ),
        {"rubric": rubric},
    )
    await c[0].execute(
        text(
            "INSERT INTO accepted_answers (task_version_id,answer_value,value_kind,canonical_text,normalization_policy_code,normalization_policy_version) VALUES (:version_b,'answer','text','answer','exact_text_v1',1)"
        ),
        c[2],
    )
    workspace, status = await execution.start(plan["id"], c[2]["student_a"])
    assert status == 201
    item = workspace["items"][0]["remediation_plan_item_id"]
    await execution.save_answer(plan["id"], item, c[2]["student_a"], "answer")
    submitted = await execution.submit(plan["id"], c[2]["student_a"])
    run_row = (
        (
            await c[0].execute(
                text("SELECT * FROM check_runs WHERE submission_id=:id"),
                {"id": submitted["submission_id"]},
            )
        )
        .mappings()
        .one()
    )
    from tests.integration.test_remediation_checking_database import _run_checkers

    await _run_checkers(c[1], run_row)
    from app.application.remediation_results import RemediationResultsService

    async with c[1]() as db:
        result = await RemediationResultsService(db).student_execution(
            plan["id"], c[2]["student_a"]
        )
        assert result["execution_status"] == "checked"
        assert result["items"][0]["score_suggested"] == D(1)
    assert (await batch(c, "finalize")).batch_status == "completed"
    assert await evidence(c, run) == before


@pytest.mark.parametrize(
    "status,score",
    [
        (None, None),
        (None, D(0)),
        ("correct", None),
        ("correct", D(5)),
        ("partially_correct", D(0)),
        ("partially_correct", D(10)),
        ("incorrect", D(1)),
        ("not_attempted", None),
        ("not_attempted", D(1)),
        ("unreadable", D(0)),
        ("insufficient_evidence", D(0)),
    ],
)
async def test_unresolved_or_inconsistent_approval_rejected(
    execution_database, status, score
):
    await policies(execution_database)
    review = await manual(execution_database)
    with pytest.raises(PaperReviewError):
        await grade(execution_database, review, status, score)


@pytest.mark.parametrize(
    "status,score",
    [
        ("correct", D(10)),
        ("partially_correct", D(5)),
        ("incorrect", D(0)),
        ("not_attempted", D(0)),
    ],
)
async def test_ordinary_status_rules_and_remediation_eligibility(
    execution_database, status, score
):
    c = execution_database
    await policies(c)
    review = await grade(c, await manual(c), status, score)
    await publish(c, review.latest_approved_revision_id)
    assert (await eligibility(c))["eligible"] == (score < 10)


async def test_manual_correction_publication_and_override(execution_database):
    c = execution_database
    await policies(c)
    first = await grade(
        c,
        await manual(c),
        override=PaperReviewOverride(override_grade="teacher", reason="Reason"),
    )
    assert first.latest_approved_revision.grade_result.calculated_grade == "graded"
    assert first.latest_approved_revision.grade_result.final_grade == "teacher"
    a = await publish(c, first.latest_approved_revision_id)
    draft = first.draft.model_copy(update={"summary": "Correction"})
    edit = await manual(c, "edit", draft, first.version)
    async with c[1]() as db:
        assert (await PaperResultPublicationRepository(db).history(c[2]["paper_a"]))[
            -1
        ]["id"] == a["id"]
    second = await manual(
        c, "approve", PaperReviewApproval(expected_version=edit.version)
    )
    async with c[1]() as db:
        assert (
            len(await PaperResultPublicationRepository(db).history(c[2]["paper_a"]))
            == 1
        )
    b = await publish(c, second.latest_approved_revision_id)
    async with c[1]() as db:
        history = await PaperResultPublicationRepository(db).history(c[2]["paper_a"])
    assert [x["id"] for x in history] == [a["id"], b["id"]]
    assert [x["active"] for x in history] == [False, True]


@pytest.mark.parametrize(
    "mutation",
    ["foreign_item", "max_score", "foreign_page", "source_result", "source_finding"],
)
async def test_manual_edit_lineage_and_policy_validation(execution_database, mutation):
    from tests.unit.test_paper_checking_review import finding

    c = execution_database
    await policies(c)
    review = await manual(c)
    changes = {}
    if mutation in {"foreign_item", "max_score", "source_result"}:
        key, value = {
            "foreign_item": ("assessment_item_id", c[2]["item_b"]),
            "max_score": ("max_score", D(11)),
            "source_result": ("source_check_result_id", uuid4()),
        }[mutation]
        changes["items"] = (review.draft.items[0].model_copy(update={key: value}),)
    else:
        changes["findings"] = (
            finding(
                scan_page_id=c[2]["page_b"]
                if mutation == "foreign_page"
                else c[2]["page_a"],
                source_check_finding_id=uuid4()
                if mutation == "source_finding"
                else None,
            ),
        )
    with pytest.raises((PaperReviewError, ValueError)):
        await manual(c, "edit", review.draft.model_copy(update=changes), review.version)


async def test_teacher_owned_manual_finding_publishes(execution_database):
    from tests.unit.test_paper_checking_review import finding

    c = execution_database
    await policies(c)
    review = await manual(c)
    f = finding(scan_page_id=c[2]["page_a"], assessment_item_id=c[2]["item_a"])
    review = await manual(
        c, "edit", review.draft.model_copy(update={"findings": (f,)}), 1
    )
    approved = await grade(c, review)
    pub = await publish(c, approved.latest_approved_revision_id)
    assert pub["result"]["findings"][0]["finding_id"] == str(f.finding_id)
    assert (await paper_evidence_counts(c))["check_findings"] == 0


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE paper_check_review_revisions SET snapshot=snapshot WHERE review_id=:id",
        "DELETE FROM paper_check_review_revisions WHERE review_id=:id",
        "UPDATE paper_check_reviews SET source_kind='ai' WHERE id=:id",
    ],
)
async def test_manual_provenance_immutable(execution_database, sql):
    await policies(execution_database)
    review = await manual(execution_database)
    await rejected(
        execution_database[0], sql, {"id": review.review_id}, match="immutable"
    )


@pytest.mark.parametrize(
    "kind,run,ai",
    [
        ("ai", None, None),
        ("ai", "run", None),
        ("ai", None, "ai"),
        ("manual", "run", None),
        ("manual", None, "ai"),
        ("manual", "run", "ai"),
    ],
)
async def test_database_source_combinations_rejected(execution_database, kind, run, ai):
    c = execution_database
    await policies(c)
    review = await manual(c)
    await rejected(
        c[0],
        """INSERT INTO paper_check_reviews
        (id,paper_submission_id,source_kind,check_run_id,ai_revision_id,draft,created_by_user_id,updated_by_user_id)
        VALUES (:id,:paper,:kind,:run,:ai,'{}',:actor,:actor)""",
        {
            "id": uuid4(),
            "paper": c[2]["paper_b"],
            "kind": kind,
            "run": uuid4() if run else None,
            "ai": uuid4() if ai else None,
            "actor": c[2]["teacher"],
        },
    )
    assert review.source_kind == "manual"


async def test_duplicate_manual_database_rejected(execution_database):
    c = execution_database
    await policies(c)
    await manual(c)
    await rejected(
        c[0],
        """INSERT INTO paper_check_reviews
        (id,paper_submission_id,source_kind,draft,created_by_user_id,updated_by_user_id)
        VALUES (:id,:paper,'manual','{}',:actor,:actor)""",
        {"id": uuid4(), "paper": c[2]["paper_a"], "actor": c[2]["teacher"]},
    )


@pytest.mark.parametrize("role", ["teacher", "outsider", "student"])
async def test_manual_http_authorization_and_shared_aliases(execution_database, role):
    c = execution_database
    await policies(c)
    app = FastAPI()
    app.include_router(router)

    async def session():
        async with c[1]() as db:
            yield db

    roles = frozenset({"student" if role == "student" else "teacher"})
    actor = c[2]["teacher"] if role == "teacher" else uuid4()
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[require_principal] = lambda: Principal(
        actor,
        "User",
        "User",
        roles,
        capabilities_for_roles(roles),
        c[2]["student_a"] if role == "student" else None,
    )
    path = f"/api/assessment-core/paper-submissions/{c[2]['paper_a']}/manual-review"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        response = await client.post(path)
        if role != "teacher":
            assert response.status_code in {403, 404}
            return
        assert response.status_code == 200, response.text
        assert (await client.get(path)).json() == response.json()
        assert len((await client.get(path + "/history")).json()["revisions"]) == 1
        draft = response.json()["draft"]
        draft["summary"] = "Checked"
        draft["items"][0].update(status="correct", accepted_score="10")
        result = await client.put(path, json={"expected_version": 1, "draft": draft})
        assert result.status_code == 200, result.text
        result = await client.post(path + "/approve", json={"expected_version": 2})
        assert result.status_code == 200, result.text
        assert result.json()["source_kind"] == "manual"


@pytest.mark.parametrize("winner", ["manual", "ai", "manual_replay"])
async def test_real_postgres_start_races_have_one_lineage(concurrent_database, winner):
    c, url = concurrent_database
    await policies(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second:
            first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            if winner == "ai":
                from app.application.checking import CreatePaperRunCommand
                from app.application.checking_intake import sha256_hex
                from app.application.paper_checking_intake import (
                    build_paper_snapshot,
                    canonical_paper_run_request,
                )
                from app.infrastructure.paper_checking_repository import (
                    SQLAlchemyPaperCheckingIntakeUnitOfWork,
                )

                uow = SQLAlchemyPaperCheckingIntakeUnitOfWork(lambda: first)
                uow.session = first
                handoff = await uow.load_locked_handoff(c[2]["paper_a"])
                methods = await uow.load_methodologies(
                    tuple(x.task_version_id for x in handoff.items)
                )
                snapshot = build_paper_snapshot(handoff, methods)
                req = request(c[2], "a", "race-ai")
                fingerprint = sha256_hex(snapshot)
                command = CreatePaperRunCommand(
                    req.paper_submission_id,
                    req.request_key,
                    sha256_hex(canonical_paper_run_request(req, fingerprint)),
                    1,
                    snapshot,
                    fingerprint,
                    "checking_input_paper_v1",
                    req.routing_version,
                    req.checker_set_version,
                    req.threshold_policy_version,
                    req.prompt_model_policy_version,
                )
                await CheckingRepository(first).create_paper_run(command)
            else:
                await PaperCheckingReviewRepository(first).start_manual(
                    c[2]["paper_a"], c[2]["teacher"]
                )

            async def contender():
                if winner in {"ai", "manual_replay"}:
                    result = await PaperCheckingReviewRepository(second).start_manual(
                        c[2]["paper_a"], c[2]["teacher"]
                    )
                    await second.commit()
                    return result
                # Read-only snapshot compilation then shared creation lock.
                from app.infrastructure.paper_checking_repository import (
                    SQLAlchemyPaperCheckingIntakeUnitOfWork,
                )

                class Uow(SQLAlchemyPaperCheckingIntakeUnitOfWork):
                    def __init__(self):
                        self.session = second

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *args):
                        pass

                from app.application.paper_checking_intake import (
                    PaperCheckingIntakeService,
                )

                return await PaperCheckingIntakeService(lambda: Uow()).create(
                    request(c[2], "a", "race-ai")
                )

            task = asyncio.create_task(contender())
            for _ in range(100):
                if first_pid in await first.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("contender did not wait on the shared source lock")
            await first.commit()
            if winner == "manual_replay":
                await asyncio.wait_for(task, 10)
                assert (
                    await second.scalar(
                        text("SELECT count(*) FROM paper_check_reviews")
                    )
                    == 1
                )
                assert (
                    await second.scalar(
                        text("SELECT count(*) FROM paper_check_review_revisions")
                    )
                    == 1
                )
                assert (
                    await second.scalar(
                        text(
                            "SELECT count(*) FROM scan_checking_events WHERE event_type='paper.manual_review_started'"
                        )
                    )
                    == 1
                )
            else:
                with pytest.raises((PaperReviewError, InvalidPersistenceCommand)):
                    await asyncio.wait_for(task, 10)
            await second.rollback()
            async with factory() as db:
                reviews = await db.scalar(
                    text(
                        "SELECT count(*) FROM paper_check_reviews WHERE source_kind='manual'"
                    )
                )
                runs = await db.scalar(
                    text(
                        "SELECT count(*) FROM check_runs WHERE paper_submission_id=:id"
                    ),
                    {"id": c[2]["paper_a"]},
                )
                assert (reviews, runs) == ((0, 1) if winner == "ai" else (1, 0))
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


async def test_completed_batch_forbids_new_lineage_but_allows_correction(
    execution_database,
):
    c = execution_database
    await policies(c)
    # Persisted completed batch is authoritative even for inconsistent legacy rows.
    await c[0].execute(
        text("UPDATE assessment_scan_batches SET status='completed' WHERE id=:batch"),
        c[2],
    )
    with pytest.raises(PaperReviewError, match="paper_batch_completed"):
        await manual(c)
    await c[0].execute(
        text(
            "UPDATE assessment_scan_batches SET status='ready_for_checking' WHERE id=:batch"
        ),
        c[2],
    )
    a = await grade(c, await manual(c))
    b = await grade(c, await manual(c, paper="b"), "correct", D(20), paper="b")
    await publish(c, a.latest_approved_revision_id)
    await publish(c, b.latest_approved_revision_id, "b")
    await batch(c, "finalize")
    assert await manual(c) == a
    correction = await grade(c, a, "correct", D(10))
    await publish(c, correction.latest_approved_revision_id)
    assert (await batch(c)).batch_status == "completed"
    assert not (await eligibility(c))["eligible"]


async def test_missing_policy_cannot_start(execution_database):
    with pytest.raises(PaperReviewError, match="paper_grading_policy_incomplete"):
        await manual(execution_database)


async def test_manual_uses_frozen_maxima_after_live_assessment_changes(
    execution_database,
):
    c = execution_database
    await policies(c)
    await c[0].execute(
        text("UPDATE assessment_items SET points=99 WHERE id=:item_a"), c[2]
    )
    review = await manual(c)
    assert review.draft.items[0].max_score == D(10)
    approved = await grade(c, review, "correct", D(10))
    assert approved.latest_approved_revision.grade_result.total_max_score == D(10)
    assert (await publish(c, approved.latest_approved_revision_id))["result"]["items"][
        0
    ]["max_score"] == "10"


@pytest.mark.parametrize("operation", ["edit", "approve"])
async def test_manual_cas_rejects_stale_version(execution_database, operation):
    c = execution_database
    await policies(c)
    review = await manual(c)
    await manual(
        c, "edit", review.draft.model_copy(update={"summary": "First writer"}), 1
    )
    with pytest.raises(PaperReviewError, match="version_conflict"):
        if operation == "edit":
            await manual(c, "edit", review.draft, 1)
        else:
            await manual(c, "approve", PaperReviewApproval(expected_version=1))


async def test_incomplete_coverage_cannot_edit(execution_database):
    c = execution_database
    await policies(c)
    review = await manual(c)
    with pytest.raises(ValueError):
        await manual(c, "edit", review.draft.model_copy(update={"items": ()}), 1)


async def test_retry_of_historical_failed_run_forbidden_after_takeover(
    execution_database,
):
    c = execution_database
    run = await start(c)
    from app.application.paper_checking_execution import PaperCheckingExecutionError

    with pytest.raises(PaperCheckingExecutionError):
        await service(c, FakePaperProvider(mode="transport")).execute(
            c[2]["paper_a"], run.id
        )
    assert (
        await c[0].scalar(
            text("SELECT status FROM check_runs WHERE id=:id"), {"id": run.id}
        )
        == "failed_retryable"
    )
    await manual(c)
    before = await evidence(c, run)
    async with c[1]() as db, db.begin():
        with pytest.raises(
            InvalidPersistenceCommand, match="paper_manual_review_active"
        ):
            await CheckingRepository(db).transition_run(run.id, 3, "pending")
    assert before == await evidence(c, run)


async def test_digital_plan_still_requires_check_run(execution_database):
    c = execution_database
    from tests.integration.c9ab_fixtures import seed_execution_world

    ids = await seed_execution_world(c[0])
    await rejected(
        c[0],
        "UPDATE remediation_plans SET source_check_run_id=NULL WHERE id=:id",
        {"id": ids["plan_0"]},
    )


@pytest.mark.parametrize(
    "field,value",
    [("review_id", "other"), ("source_kind", "ai"), ("paper_submission_id", "other")],
)
async def test_revision_foreign_source_rejected(execution_database, field, value):
    c = execution_database
    await policies(c)
    review = await manual(c)
    source = "'ai'" if field == "source_kind" else ":other"
    columns = ["review_id", "paper_submission_id", "source_kind"]
    values = {
        "review_id": ":review",
        "paper_submission_id": ":paper",
        "source_kind": "'manual'",
    }
    values[field] = source
    await rejected(
        c[0],
        f"""INSERT INTO paper_check_review_revisions
        (id,{",".join(columns)},revision_number,revision_kind,snapshot,actor_user_id,
            grading_policy_revision,grading_policy_fingerprint,change_summary)
        SELECT :id,{",".join(values[x] for x in columns)},1,'human_edit','{{}}',:actor,
            grading_policy_revision,grading_policy_fingerprint,'[]'
        FROM paper_check_review_revisions WHERE review_id=:review AND revision_number=0""",
        {
            "id": uuid4(),
            "review": review.review_id,
            "paper": c[2]["paper_a"],
            "other": c[2]["paper_b"],
            "actor": c[2]["teacher"],
        },
    )


async def test_multi_item_seed_exact_coverage_and_frozen_policy(execution_database):
    c = execution_database
    extra = uuid4()
    await c[0].execute(
        text(
            "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:extra,:variant_a,:version_b,2,10)"
        ),
        {**c[2], "extra": extra},
    )
    from app.infrastructure.paper_checking_repository import (
        PaperGradingPolicyRepository,
    )
    from tests.integration.test_paper_checking_intake_database import policy

    original = policy(c[2])
    supplied = original.model_copy(
        update={
            "assessment_items": original.assessment_items
            + (
                original.assessment_items[0].model_copy(
                    update={"assessment_item_id": extra}
                ),
            ),
            "total_max_score": D(20),
        }
    )
    supplied = type(original).model_validate_json(supplied.model_dump_json())
    async with c[1]() as db, db.begin():
        await PaperGradingPolicyRepository(db).freeze(
            c[2]["batch"], c[2]["variant_a"], supplied, c[2]["teacher"]
        )
    await freeze(c[1], c[2], "b")
    review = await manual(c)
    assert {x.assessment_item_id for x in review.draft.items} == {extra, c[2]["item_a"]}
    assert all(x.max_score == D(10) for x in review.draft.items)
    with pytest.raises(PaperReviewError, match="item_coverage"):
        await manual(
            c,
            "edit",
            review.draft.model_copy(update={"items": review.draft.items[:1]}),
            1,
        )
    approved = await grade(c, review)
    assert approved.latest_approved_revision.grade_result.calculated_total_score == D(
        10
    )
    assert approved.latest_approved_revision.grade_result.total_max_score == D(20)


async def test_ten_paper_failed_terminal_publication_gap(execution_database):
    """Nine genuine PR7 AI publications plus one failure recovered manually."""
    c = execution_database
    ids = c[2]
    from tests.integration.test_paper_checking_review_database import call

    for number in range(2, 10):
        label = str(number)
        student, participant, page, entry, paper = (uuid4() for _ in range(5))
        parameters = {
            **ids,
            "student": student,
            "participant": participant,
            "page": page,
            "entry": entry,
            "paper": paper,
            "position": number,
        }
        statements = (
            "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'Paper student')",
            "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant,:assignment,:student,:variant_b,clock_timestamp())",
            "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status) VALUES (:page,:batch_artifact,:position,:render_b,1,1,0,'normalized_upright_v1',:hash_b,'ready')",
            "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:entry,:revision,:participant,:position)",
            "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry,:revision,:page,0)",
            "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:paper,:batch,:revision,:assignment,:participant,:student,:variant_b,'ready_for_checking',:teacher)",
            "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper,:page,0)",
        )
        for sql in statements:
            await c[0].execute(text(sql), parameters)
        ids[f"paper_{label}"] = paper
    failed = await start(c)
    from app.application.paper_checking_execution import PaperCheckingExecutionError

    with pytest.raises(PaperCheckingExecutionError):
        await service(c, FakePaperProvider(mode="unknown_item")).execute(
            ids["paper_a"], failed.id
        )
    before = await evidence(c, failed)
    for label in ("b", *(str(n) for n in range(2, 10))):
        run = await completed(c, paper=label)
        review = await call(c, run, "start", paper=label)
        approved = await call(
            c, run, "approve", PaperReviewApproval(expected_version=1), paper=label
        )
        await publish(c, approved.latest_approved_revision_id, label)
    progress = await batch(c)
    assert progress.paper_count == progress.settled_count == 10
    assert (
        progress.batch_status == "checking_completed"
        and progress.active_publication_count == 9
    )
    with pytest.raises(Exception, match="publications_incomplete"):
        await batch(c, "finalize")
    review = await manual(c)
    assert (await batch(c)).batch_status == "checking"
    approved = await grade(c, review)
    assert (await batch(c)).batch_status == "checking_completed"
    await publish(c, approved.latest_approved_revision_id)
    assert (await batch(c)).active_publication_count == 10
    assert (await batch(c)).batch_status == "checking_completed"
    assert (await batch(c, "finalize")).batch_status == "completed"
    assert await evidence(c, failed) == before
    assert (
        await c[0].scalar(
            text("SELECT count(*) FROM check_runs WHERE paper_submission_id=:paper_a"),
            ids,
        )
        == 1
    )
