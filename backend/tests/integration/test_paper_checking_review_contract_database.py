"""Current teacher contracts, durable scheduling and real PostgreSQL lock races."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from app.application.paper_batch_orchestration import PaperBatchOrchestrationService
from app.application.paper_checking_review import PaperReviewApproval, PaperReviewError
from app.db.session import get_session
from app.infrastructure.paper_batch_orchestration_repository import (
    PaperBatchOrchestrationRepository,
)
from app.infrastructure.paper_checking_review_repository import (
    PaperCheckingReviewRepository,
)
from app.presentation.auth_dependencies import require_principal
from app.presentation.scan_intake_routes import router
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.auth_helpers import student_principal, teacher_principal
from tests.integration.test_manual_paper_review_database import manual, policies
from tests.integration.test_paper_batch_orchestration_database import (
    action,
    scalar,
    settle,
)
from tests.integration.test_paper_checking_execution_database import (
    FakePaperProvider,
    service,
)
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_intake_database import intake, request
from tests.integration.test_paper_checking_review_database import (
    call,
    completed,
    edited,
)
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)
from tests.integration.test_paper_result_publication_database import (
    publish,
)
from tests.unit.test_paper_checking_review import finding

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]
ROOT = "/api/assessment-core"


def paper(progress, identifier):
    return next(p for p in progress.papers if p["paper_submission_id"] == identifier)


async def add_papers(c, total):
    for number in range(2, total):
        params = {
            **c[2],
            **{
                name: uuid4()
                for name in ("student", "participant", "page", "entry", "paper")
            },
            "position": number,
        }
        for sql in (
            "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'Paper student')",
            "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant,:assignment,:student,:variant_b,clock_timestamp())",
            "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status) VALUES (:page,:batch_artifact,:position,:render_b,1,1,0,'normalized_upright_v1',:hash_b,'ready')",
            "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:entry,:revision,:participant,:position)",
            "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry,:revision,:page,0)",
            "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:paper,:batch,:revision,:assignment,:participant,:student,:variant_b,'ready_for_checking',:teacher)",
            "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper,:page,0)",
        ):
            await c[0].execute(text(sql), params)
        c[2][f"paper_{number}"] = params["paper"]


async def test_five_paper_mixed_orchestration(execution_database):
    c = execution_database
    await add_papers(c, 5)
    await policies(c)
    await settle(c, "b", "running")
    await settle(c, "2", "failed_retryable")
    await completed(c, paper="3")
    await manual(c, paper="4")
    before = await action(c, "progress")
    result = await action(c, "start")
    assert await action(c, "start") == result
    for p in before.papers:
        if p["paper_submission_id"] != c[2]["paper_a"]:
            assert paper(result, p["paper_submission_id"]) == p
    assert (
        result.pending,
        result.running,
        result.failed_retryable,
        result.completed_with_review_required,
        result.manual_reviewing,
    ) == (1, 1, 1, 1, 1)


async def test_thirty_paper_projection_one_query_and_bulk_pending(execution_database):
    from sqlalchemy import event

    c = execution_database
    await add_papers(c, 30)
    await policies(c)
    result = await action(c, "start")
    assert result.pending == 30  # Execution slots never limit durable pending work.
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(c[0].sync_connection, "before_cursor_execute", record)
    try:
        async with c[1]() as db:
            from app.infrastructure.scan_checking_models import AssessmentScanBatch

            batch = await db.get(AssessmentScanBatch, c[2]["batch"])
            queries.clear()
            projected = await PaperBatchOrchestrationRepository(db).progress(batch)
            assert len(projected.papers) == 30 and len(queries) == 1
            assert "JOIN" in queries[0]
    finally:
        event.remove(c[0].sync_connection, "before_cursor_execute", record)


async def test_bounded_batch_execution_http(execution_database, monkeypatch):
    import app.db.session as sessions
    from app.config import get_settings

    c = execution_database
    await add_papers(c, 5)
    await policies(c)
    await action(c, "start")
    monkeypatch.setattr(sessions, "async_session_factory", c[1])
    app = FastAPI()
    app.include_router(router)
    app.state.paper_checking_provider = FakePaperProvider()
    app.dependency_overrides[require_principal] = lambda: teacher_principal(
        c[2]["teacher"]
    )
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"artifact_storage_path": str(c[3].root)}
    )

    async def session():
        async with c[1]() as db:
            yield db

    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"{ROOT}/scan-batches/{c[2]['batch']}/checking/execute"
        first = await client.post(path)
        assert first.status_code == 200, first.text
        assert len(first.json()["outcomes"]) == 3
        assert first.json()["progress"]["pending"] == 2
        second = await client.post(path)
        assert second.status_code == 200, second.text
        assert second.json()["progress"]["completed_with_review_required"] == 5
    assert len(app.state.paper_checking_provider.calls) == 5


@pytest.mark.parametrize("mode", ["unknown_item", "unknown_page"])
async def test_opaque_provider_token_foreign_identity_rejected(
    execution_database, mode
):
    from app.application.paper_checking_execution import PaperCheckingExecutionError

    c = execution_database
    await policies(c)
    progress = await action(c, "start")
    run_id = paper(progress, c[2]["paper_a"])["effective_check_run_id"]
    with pytest.raises(PaperCheckingExecutionError):
        await service(c, FakePaperProvider(mode=mode)).execute(c[2]["paper_a"], run_id)
    assert (
        await scalar(
            c, "SELECT count(*) FROM check_results WHERE check_run_id=:run", run=run_id
        )
        == 0
    )


@pytest.mark.parametrize(
    "state",
    [
        None,
        "pending",
        "running",
        "failed_retryable",
        "failed_terminal",
        "completed_with_review_required",
    ],
)
async def test_effective_projection(execution_database, state):
    c = execution_database
    run = None
    if state == "completed_with_review_required":
        run = await completed(c)
    elif state:
        run = await settle(c, "a", state)
    p = paper(await action(c, "progress"), c[2]["paper_a"])
    assert p["checking_state"] == (state or "not_started")
    assert p["effective_check_run_id"] == (run.id if run else None)
    assert p["ai_review_available"] == (state == "completed_with_review_required")
    assert p["can_execute"] == (state == "pending")
    assert p["can_retry"] == (state in {"failed_retryable", "failed_terminal"})
    assert p["display_name"] == "Private A"


@pytest.mark.parametrize("approved", [False, True])
async def test_manual_projection(execution_database, approved):
    from tests.integration.test_manual_paper_review_database import grade

    c = execution_database
    await policies(c)
    review = await manual(c)
    if approved:
        review = await grade(c, review)
    p = paper(await action(c, "progress"), c[2]["paper_a"])
    assert p["checking_state"] == (
        "manual_approved" if approved else "manual_reviewing"
    )
    assert p["review_id"] == review.review_id
    assert p["latest_approved_revision_id"] == review.latest_approved_revision_id
    assert p["effective_check_run_id"] is None and not p["can_retry"]


async def test_superseded_run_uses_canonical_attempt(execution_database):
    c = execution_database
    old = await settle(c, "a", "failed_retryable")
    new = await intake(c[1]).create(request(c[2], key="retry", supersedes=old.id))
    p = paper(await action(c, "progress"), c[2]["paper_a"])
    assert p["effective_check_run_id"] == new.id and p["checking_state"] == "pending"


@pytest.mark.parametrize(
    "existing",
    [
        "pending",
        "running",
        "failed_retryable",
        "failed_terminal",
        "success",
        "manual",
        "manual_approved",
    ],
)
async def test_start_preserves_existing_lineage(execution_database, existing):
    from tests.integration.test_manual_paper_review_database import grade

    c = execution_database
    await policies(c)
    if existing.startswith("manual"):
        review = await manual(c)
        if existing == "manual_approved":
            await grade(c, review)
    elif existing == "success":
        await completed(c)
    else:
        await settle(c, "a", existing)
    before = paper(await action(c, "progress"), c[2]["paper_a"])
    first = await action(c, "start")
    assert await action(c, "start") == first
    assert paper(first, c[2]["paper_a"]) == before
    assert paper(first, c[2]["paper_b"])["checking_state"] == "pending"
    count = await scalar(
        c,
        "SELECT count(*) FROM check_runs WHERE paper_submission_id IN (:paper_a,:paper_b)",
    )
    assert count == (1 if existing.startswith("manual") else 2)
    assert (
        await scalar(
            c, "SELECT count(*) FROM model_runs WHERE paper_submission_id=:paper_b"
        )
        == 0
    )


async def test_start_missing_input_rolls_back(execution_database):
    from app.application.paper_batch_orchestration import PaperBatchError

    c = execution_database
    await policies(c)
    await c[0].execute(
        text("DELETE FROM paper_submission_pages WHERE paper_submission_id=:paper_b"),
        c[2],
    )
    with pytest.raises(PaperBatchError, match="paper_checking_input_missing"):
        await action(c, "start")
    assert (
        await scalar(
            c,
            "SELECT count(*) FROM check_runs WHERE paper_submission_id IN (:paper_a,:paper_b)",
        )
        == 0
    )
    assert (await action(c, "progress")).batch_status == "ready_for_checking"


async def test_reload_safe_review_and_approval(execution_database):
    c = execution_database
    await policies(c)
    started = await action(c, "start")
    run_id = paper(started, c[2]["paper_a"])["effective_check_run_id"]
    await service(c, FakePaperProvider()).execute(c[2]["paper_a"], run_id)
    # All following reads use fresh sessions, with no remembered response identity.
    reloaded = paper(await action(c, "progress"), c[2]["paper_a"])
    assert reloaded["ai_review_available"]
    async with c[1]() as db, db.begin():
        review = await PaperCheckingReviewRepository(db).start(
            c[2]["paper_a"], reloaded["effective_check_run_id"], c[2]["teacher"]
        )
    assert review.draft.findings[0].assessment_item_id == c[2]["item_a"]
    async with c[1]() as db, db.begin():
        approved = await PaperCheckingReviewRepository(db).approve(
            c[2]["paper_a"],
            reloaded["effective_check_run_id"],
            c[2]["teacher"],
            PaperReviewApproval(expected_version=review.version),
        )
    p = paper(await action(c, "progress"), c[2]["paper_a"])
    assert (
        p["review_state"] == "approved"
        and p["latest_approved_revision_id"] == approved.latest_approved_revision_id
    )
    assert (
        approved.latest_approved_revision.snapshot.findings[0].assessment_item_id
        == c[2]["item_a"]
    )
    pub = await publish(c, approved.latest_approved_revision_id)
    assert pub["result"]["findings"][0]["assessment_item_id"] == str(c[2]["item_a"])


@pytest.mark.parametrize("invalid", ["foreign", "missing", "source_mismatch"])
async def test_finding_membership_rejected(execution_database, invalid):
    c = execution_database
    extra = None
    if invalid == "source_mismatch":
        from decimal import Decimal

        from app.infrastructure.paper_checking_repository import (
            PaperGradingPolicyRepository,
        )
        from tests.integration.test_paper_checking_intake_database import freeze, policy

        extra = uuid4()
        await c[0].execute(
            text(
                "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:extra,:variant_a,:version_b,2,10)"
            ),
            {**c[2], "extra": extra},
        )
        original = policy(c[2])
        supplied = original.model_copy(
            update={
                "assessment_items": original.assessment_items
                + (
                    original.assessment_items[0].model_copy(
                        update={"assessment_item_id": extra}
                    ),
                ),
                "total_max_score": Decimal(20),
            }
        )
        supplied = type(original).model_validate_json(supplied.model_dump_json())
        async with c[1]() as db, db.begin():
            await PaperGradingPolicyRepository(db).freeze(
                c[2]["batch"], c[2]["variant_a"], supplied, c[2]["teacher"]
            )
        await freeze(c[1], c[2], "b")
        run = await intake(c[1]).create(request(c[2]))

        class TwoItemProvider(FakePaperProvider):
            async def check(self, value, content):
                response, telemetry = await super().check(value, content)
                first = response.items[0]
                second = first.model_copy(
                    update={
                        "item_token": value.assessment_items[1].item_token,
                        "findings": (
                            first.findings[0].model_copy(
                                update={"finding_token": "finding-2"}
                            ),
                        ),
                    }
                )
                return response.model_copy(update={"items": (first, second)}), telemetry

        await service(c, TwoItemProvider()).execute(c[2]["paper_a"], run.id)
    else:
        run = await completed(c)
    review = await call(c, run, "start")
    source = next(
        f for f in review.draft.findings if f.assessment_item_id == c[2]["item_a"]
    )
    f = source.model_copy(
        update={"assessment_item_id": None if invalid == "missing" else c[2]["item_b"]}
    )
    if invalid == "source_mismatch":
        # Both items are legal members; immutable source correspondence still forbids reassignment.
        f = f.model_copy(update={"assessment_item_id": extra})
    # Explicit missing AI IDs can be recovered from immutable source; foreign IDs cannot.
    if invalid == "missing":
        changed = await call(
            c, run, "edit", edited(review, findings=(f,)), review.version
        )
        assert changed.draft.findings[0].assessment_item_id == c[2]["item_a"]
    else:
        with pytest.raises(PaperReviewError, match="paper_review_finding_item_invalid"):
            await call(c, run, "edit", edited(review, findings=(f,)), review.version)


@pytest.mark.parametrize("source_kind", ["ai", "teacher"])
async def test_legacy_association_read_without_rewriting_evidence(
    execution_database, source_kind
):
    c = execution_database
    run = await completed(c)
    review = await call(c, run, "start")
    if source_kind == "teacher":
        f = finding(scan_page_id=c[2]["page_a"], assessment_item_id=c[2]["item_a"])
        review = await call(
            c, run, "edit", edited(review, findings=(f,)), review.version
        )
    await c[0].execute(
        text(
            "UPDATE paper_check_reviews SET draft=jsonb_set(draft,'{findings,0}',(draft->'findings'->0)-'assessment_item_id') WHERE id=:id"
        ),
        {"id": review.review_id},
    )
    before = await scalar(
        c, "SELECT draft FROM paper_check_reviews WHERE id=:id", id=review.review_id
    )
    read = await call(c, run, "get")
    assert read.draft.findings[0].assessment_item_id == (
        c[2]["item_a"] if source_kind == "ai" else None
    )
    assert (
        await scalar(
            c, "SELECT draft FROM paper_check_reviews WHERE id=:id", id=review.review_id
        )
        == before
    )
    if source_kind == "teacher":
        with pytest.raises(PaperReviewError, match="paper_review_finding_item_invalid"):
            await call(
                c, run, "approve", PaperReviewApproval(expected_version=read.version)
            )


@pytest.mark.parametrize("manual_source", [False, True])
async def test_teacher_added_finding_item_persisted(execution_database, manual_source):
    c = execution_database
    await policies(c)
    run = None if manual_source else await completed(c)
    review = await manual(c) if manual_source else await call(c, run, "start")
    f = finding(scan_page_id=c[2]["page_a"], assessment_item_id=c[2]["item_a"])
    draft = edited(review, findings=(f,))
    for invalid_item in (None, c[2]["item_b"]):
        invalid_draft = edited(
            review,
            findings=(f.model_copy(update={"assessment_item_id": invalid_item}),),
        )
        with pytest.raises(PaperReviewError, match="paper_review_finding_item_invalid"):
            if manual_source:
                await manual(c, "edit", invalid_draft, review.version)
            else:
                await call(c, run, "edit", invalid_draft, review.version)
    if manual_source:
        result = await manual(c, "edit", draft, review.version)
    else:
        result = await call(c, run, "edit", draft, review.version)
    assert result.draft.findings == (f,)
    assert f.source_check_finding_id is None
    raw = await scalar(
        c,
        "SELECT draft FROM paper_check_reviews WHERE id=:review",
        review=result.review_id,
    )
    import json

    raw = json.loads(raw) if isinstance(raw, str) else raw
    assert raw["findings"][0]["assessment_item_id"] == str(c[2]["item_a"])


@pytest.mark.parametrize("role", ["co_teacher", "unrelated", "revoked", "student"])
async def test_checking_routes_authorization(execution_database, role):
    c = execution_database
    await policies(c)
    teacher = uuid4()
    if role in {"co_teacher", "revoked"}:
        await c[0].execute(
            text(
                "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,:login,:login,'Second teacher','hash')"
            ),
            {"id": teacher, "login": teacher.hex},
        )
        await c[0].execute(
            text(
                "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:id,:teacher)"
            ),
            {**c[2], "id": teacher},
        )
        if role == "revoked":
            await c[0].execute(
                text("DELETE FROM class_group_teachers WHERE teacher_user_id=:id"),
                {"id": teacher},
            )
    actor = (
        student_principal(teacher, c[2]["student_a"])
        if role == "student"
        else teacher_principal(teacher)
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: actor

    async def db_session():
        async with c[1]() as db:
            yield db

    app.dependency_overrides[get_session] = db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"{ROOT}/scan-batches/{c[2]['batch']}/checking"
        for suffix, method in [("", "get"), ("/start", "post")]:
            response = await getattr(client, method)(path + suffix)
            assert response.status_code == (
                200 if role == "co_teacher" else 403 if role == "student" else 404
            ), response.text
        if role == "co_teacher":
            p = next(
                x
                for x in response.json()["papers"]
                if x["paper_submission_id"] == str(c[2]["paper_a"])
            )
            from uuid import UUID

            await service(c, FakePaperProvider()).execute(
                c[2]["paper_a"], UUID(p["effective_check_run_id"])
            )
            response = await client.post(
                f"{ROOT}/paper-submissions/{c[2]['paper_a']}/check-runs/{p['effective_check_run_id']}/review"
            )
            assert response.status_code == 200, response.text


async def test_real_two_teacher_concurrent_start(concurrent_database):
    c, url = concurrent_database
    await policies(c)
    second_teacher = uuid4()
    await c[0].execute(
        text(
            "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,:login,:login,'Second teacher','hash')"
        ),
        {"id": second_teacher, "login": second_teacher.hex},
    )
    await c[0].execute(
        text(
            "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:id,:teacher)"
        ),
        {**c[2], "id": second_teacher},
    )
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second:
            first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            result = await PaperBatchOrchestrationService(
                PaperBatchOrchestrationRepository(first)
            ).start(c[2]["batch"], c[2]["teacher"])
            entered = asyncio.Event()

            async def contender():
                entered.set()
                result = await PaperBatchOrchestrationService(
                    PaperBatchOrchestrationRepository(second)
                ).start(c[2]["batch"], second_teacher)
                await second.commit()
                return result

            task = asyncio.create_task(contender())
            await entered.wait()

            # Query PostgreSQL's lock table as the barrier, with no timing sleeps.
            async def wait_for_lock():
                while first_pid not in await first.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                ):
                    pass

            await asyncio.wait_for(wait_for_lock(), 10)
            await first.commit()
            assert await asyncio.wait_for(task, 10) == result
            assert result.pending == 2
            assert (
                await first.scalar(
                    text(
                        "SELECT count(*) FROM check_runs WHERE paper_submission_id IN (:paper_a,:paper_b)"
                    ),
                    c[2],
                )
                == 2
            )
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
