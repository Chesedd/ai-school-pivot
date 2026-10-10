"""PR10 aggregate lifecycle through real PR6–PR9 PostgreSQL adapters."""
import asyncio
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from app.application.capabilities import capabilities_for_roles
from app.application.paper_batch_orchestration import (
    PaperBatchError,
    PaperBatchOrchestrationService,
)
from app.application.paper_checking_review import PaperReviewApproval
from app.application.principal import Principal
from app.db.session import get_session
from app.infrastructure.checking_repository import CheckingRepository
from app.infrastructure.paper_batch_orchestration_repository import (
    PaperBatchOrchestrationRepository,
)
from app.infrastructure.scan_grouping_repository import SqlAlchemyScanGroupingRepository
from app.presentation.auth_dependencies import require_principal
from app.presentation.scan_intake_routes import router
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_paper_checking_execution_database import (
    FakePaperProvider,
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
from tests.integration.test_paper_checking_review_database import (
    call,
    completed,
    edited,
)
from tests.integration.test_paper_result_publication_database import (
    approval,
    publish,
)
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required")]


async def action(context, method, batch_id=None):
    async with context[1]() as db, db.begin():
        service = PaperBatchOrchestrationService(PaperBatchOrchestrationRepository(db))
        batch_id = batch_id or context[2]["batch"]
        if method == "progress":
            return await service.progress(batch_id)
        return await getattr(service, method)(batch_id, context[2]["teacher"])


@pytest_asyncio.fixture
async def unconfirmed_database(tmp_path):
    seed = execution_database.__wrapped__(tmp_path, confirmed=False)
    try:
        yield await anext(seed)
    finally:
        await seed.aclose()


async def test_grouping_through_execution_review_publication_and_finalization(unconfirmed_database):
    c = unconfirmed_database
    ids = c[2]
    ids["matching_run"] = uuid4()
    await c[0].execute(text("""INSERT INTO scan_matching_runs
        (id,batch_id,revision,requested_by_user_id,status,prompt_name,prompt_version,
         prompt_template_hash,output_schema_version,provider_route,request_context_fingerprint,
         assessment_title_snapshot,completed_at)
        VALUES (:matching_run,:batch,1,:teacher,'succeeded','scan-match','1',:source_hash,
        'v1','test/no-network',:source_hash,'Assessment',clock_timestamp())"""), ids)
    for n, paper in enumerate(("a", "b")):
        params = {**ids, "page": ids[f"page_{paper}"], "participant": ids[f"participant_{paper}"],
                  "n": n, "token": f"page-{n}"}
        await c[0].execute(text("""INSERT INTO scan_matching_page_entries
            (matching_run_id,page_token,scan_page_id,source_order) VALUES (:matching_run,:token,:page,:n)"""), params)
        await c[0].execute(text("""INSERT INTO scan_page_match_proposals
            (matching_run_id,scan_page_id,disposition,proposed_assignment_participant_id,
             confidence,evidence_code,evidence_summary,proposed_group_token,proposed_page_order,
             provider_requires_human_review)
            VALUES (:matching_run,:page,'matched',:participant,.8,'visual_identifier','evidence',:token,0,true)"""), params)
    async with c[1]() as db:
        await SqlAlchemyScanGroupingRepository(db).confirm(ids["batch"], 1, 1, ids["teacher"])
    assert (await action(c, "progress")).batch_status == "ready_for_checking"
    for paper in ("a", "b"):
        ids[f"paper_{paper}"] = await scalar(c, "SELECT id FROM paper_submissions WHERE batch_id=:batch AND assignment_participant_id=:participant",
                                             participant=ids[f"participant_{paper}"])
    assert (await action(c, "progress")).not_started == 2
    _, a = await approval(c)
    assert (await action(c, "progress")).batch_status == "checking"
    _, b = await approval(c, "b")
    assert (await action(c, "progress")).batch_status == "checking_completed"
    assert not (await action(c, "progress")).can_finalize  # Approval creates zero publications.
    await publish(c, a.latest_approved_revision_id)
    await publish(c, b.latest_approved_revision_id, "b")
    assert (await action(c, "progress")).batch_status == "checking_completed"
    assert (await action(c, "finalize")).batch_status == "completed"


async def scalar(context, sql, **params):
    return await context[0].scalar(text(sql), {**context[2], **params})


async def settle(context, paper, status):
    run = await start(context, paper)
    if status != "pending":
        async with context[1]() as db, db.begin():
            repo = CheckingRepository(db)
            await repo.transition_run(run.id, 1, "running")
            if status != "running":
                await repo.transition_run(run.id, 2, status,
                                          failure_code="transport" if status.startswith("failed") else None)
    return run


async def test_start_is_explicit_idempotent_and_does_not_create_runs(execution_database):
    c = execution_database
    await freeze(c[1], c[2], "a")
    await freeze(c[1], c[2], "b")
    result = await action(c, "start")
    assert result.batch_status == "checking" and result.not_started == 2
    assert await action(c, "start") == result
    assert await scalar(c, "SELECT row_version FROM assessment_scan_batches WHERE id=:batch") == 2
    assert await scalar(c, "SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type='batch.checking_started'") == 1
    assert await scalar(c, "SELECT count(*) FROM check_runs WHERE paper_submission_id IN (:paper_a,:paper_b)") == 0


@pytest.mark.parametrize("state", ["draft", "grouping_confirmed", "checking_completed", "completed", "processing_failed"])
async def test_start_wrong_state_rejected(execution_database, state):
    c = execution_database
    await c[0].execute(text("UPDATE assessment_scan_batches SET status=:state WHERE id=:batch"), {**c[2], "state": state})
    with pytest.raises(PaperBatchError, match="paper_batch_not_ready_for_checking"):
        await action(c, "start")
    assert (await action(c, "progress")).batch_status == state


async def test_start_requires_policy_prerequisites(execution_database):
    with pytest.raises(PaperBatchError, match="paper_grading_policy_incomplete"):
        await action(execution_database, "start")
    assert (await action(execution_database, "progress")).batch_status == "ready_for_checking"


async def empty_batch(context):
    id_ = uuid4()
    await context[0].execute(text("""INSERT INTO assessment_scan_batches
        (id,assignment_id,class_group_id,created_by_user_id,status,instruction_text,request_key,request_hash)
        VALUES (:id,:assignment,:group,:teacher,'ready_for_checking','empty',:key,:source_hash)"""),
        {**context[2], "id": id_, "key": uuid4().hex})
    return id_


async def test_empty_cannot_start_settle_or_finalize_and_batch_isolation(execution_database):
    c = execution_database
    batch = await empty_batch(c)
    await completed(c)
    assert (await action(c, "progress", batch)).paper_count == 0
    with pytest.raises(PaperBatchError, match="paper_batch_has_no_papers"):
        await action(c, "start", batch)
    await c[0].execute(text("UPDATE assessment_scan_batches SET status='checking' WHERE id=:id"), {"id": batch})
    assert (await action(c, "reconcile", batch)).batch_status == "checking"
    await c[0].execute(text("UPDATE assessment_scan_batches SET status='checking_completed' WHERE id=:id"), {"id": batch})
    with pytest.raises(PaperBatchError, match="paper_batch_has_no_papers"):
        await action(c, "finalize", batch)


async def test_other_batch_paper_run_and_publication_are_excluded(execution_database):
    from app.infrastructure.paper_result_publication_repository import (
        PaperResultPublicationRepository,
    )
    c = execution_database
    ids = c[2]
    batch = await empty_batch(c)
    revision, paper_id = uuid4(), uuid4()
    await c[0].execute(text("""INSERT INTO scan_grouping_revisions
        (id,batch_id,revision,based_on_matching_revision,created_by_user_id,status,confirmed_at,confirmed_by_user_id)
        VALUES (:id,:foreign_batch,1,1,:teacher,'confirmed',clock_timestamp(),:teacher)"""),
        {**ids, "id": revision, "foreign_batch": batch})
    await c[0].execute(text("""INSERT INTO paper_submissions
        (id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,
         assigned_variant_id,status,created_by_user_id)
        VALUES (:id,:foreign_batch,:revision,:assignment,:participant_a,:student_a,
                :variant_a,'ready_for_checking',:teacher)"""),
        {**ids, "id": paper_id, "revision": revision, "foreign_batch": batch})
    # A different batch contains a ready paper; this batch then gets two fully
    # checked and published papers. Neither direction may leak aggregate counts.
    assert (await action(c, "progress")).paper_count == 2
    assert (await action(c, "progress", batch)).paper_count == 1
    _, a = await approval(c)
    _, b = await approval(c, "b")
    await publish(c, a.latest_approved_revision_id)
    await publish(c, b.latest_approved_revision_id, "b")
    foreign = await action(c, "progress", batch)
    assert foreign.not_started == 1 and foreign.active_publication_count == 0
    assert (await action(c, "progress")).active_publication_count == 2
    # The active-pointer composite FK also forbids using the other paper's publication.
    async with c[1]() as db, db.begin():
        history = await PaperResultPublicationRepository(db).history(ids["paper_a"])
        publication_id = history[0]["id"]
    from sqlalchemy.exc import DBAPIError
    savepoint = await c[0].begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await c[0].execute(text("""INSERT INTO paper_result_publication_states
                (paper_submission_id,active_publication_id,version) VALUES (:paper,:publication,1)"""),
                {"paper": paper_id, "publication": publication_id})
    finally:
        await savepoint.rollback()


@pytest.mark.parametrize("status,settled", [
    (None, False), ("pending", False), ("running", False), ("failed_retryable", False),
    ("failed_terminal", True), ("completed", True), ("completed_with_review_required", True),
])
async def test_effective_status_and_reconciliation(execution_database, status, settled):
    c = execution_database
    await settle(c, "a", "completed_with_review_required")
    if status:
        await settle(c, "b", status)
    result = await action(c, "reconcile")
    assert result.batch_status == ("checking_completed" if settled else "checking")
    assert result.settled_count == 1 + int(settled)
    assert getattr(result, status or "not_started") >= 1
    assert result.batch_status != "processing_failed"
    assert await action(c, "reconcile") == result
    assert await scalar(c, "SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type='batch.checking_completed'") == int(settled)
    assert await scalar(c, "SELECT count(*) FROM paper_result_publications WHERE paper_submission_id IN (:paper_a,:paper_b)") == 0


@pytest.mark.parametrize("old,new", [
    ("failed_retryable", "completed_with_review_required"),
    ("completed_with_review_required", "failed_retryable"),
])
async def test_new_attempt_reopens_and_latest_wins(execution_database, old, new):
    c = execution_database
    original = await settle(c, "a", old)
    await settle(c, "b", "completed")
    before = await action(c, "reconcile")
    run = await intake(c[1]).create(request(c[2], key="new-attempt", supersedes=original.id))
    assert (await action(c, "progress")).batch_status == "checking"
    async with c[1]() as db, db.begin():
        repo = CheckingRepository(db)
        await repo.transition_run(run.id, 1, "running")
        await repo.transition_run(run.id, 2, new,
                                  failure_code="transport" if new.startswith("failed") else None)
    after = await action(c, "reconcile")
    assert getattr(after, new) == 1
    assert getattr(after, old) == 0
    assert after.batch_status == ("checking" if new == "failed_retryable" else "checking_completed")
    assert await scalar(c, "SELECT status FROM check_runs WHERE id=:run", run=original.id) == old
    reopened = await scalar(c, "SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type='batch.checking_reopened'")
    assert reopened == int(before.batch_status == "checking_completed")


async def test_run_replay_does_not_reopen_settled_batch(execution_database):
    c = execution_database
    a = await completed(c)
    await completed(c, paper="b")
    assert (await action(c, "progress")).batch_status == "checking_completed"
    assert (await start(c)).id == a.id
    assert (await action(c, "progress")).batch_status == "checking_completed"


@pytest.mark.parametrize("publications", [0, 1, 2])
async def test_finalize_only_active_publications_and_corrections(execution_database, publications):
    c = execution_database
    run, a = await approval(c)
    _, b = await approval(c, "b")
    assert (await action(c, "progress")).batch_status == "checking_completed"
    assert (await action(c, "progress")).active_publication_count == 0
    if publications:
        await publish(c, a.latest_approved_revision_id)
    if publications == 2:
        await publish(c, b.latest_approved_revision_id, "b")
    if publications < 2:
        with pytest.raises(PaperBatchError, match="paper_batch_publications_incomplete"):
            await action(c, "finalize")
        assert (await action(c, "progress")).batch_status == "checking_completed"
        return
    # A newer approval leaves the old active publication sufficient for completion.
    review = await call(c, run, "edit", edited(a, summary="Corrected"), a.version)
    corrected = await call(c, run, "approve", PaperReviewApproval(expected_version=review.version))
    assert (await action(c, "progress")).can_finalize
    result = await action(c, "finalize")
    assert result.batch_status == "completed"
    assert await action(c, "finalize") == result
    assert await scalar(c, "SELECT count(*) FROM paper_result_publications WHERE paper_submission_id IN (:paper_a,:paper_b)") == 2
    assert await scalar(c, "SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type='batch.completed'") == 1
    with pytest.raises(PaperBatchError, match="paper_batch_completed"):
        await intake(c[1]).create(request(c[2], key="after-completion", supersedes=run.id))
    # Existing PR8/PR9 editing, approval and replacement publication stay writable.
    review = await call(c, run, "edit", edited(corrected, summary="After completion"), corrected.version)
    corrected = await call(c, run, "approve", PaperReviewApproval(expected_version=review.version))
    await publish(c, corrected.latest_approved_revision_id)
    assert (await action(c, "progress")).batch_status == "completed"
    assert (await start(c)).id == run.id  # Existing run replay is not new AI work.


async def test_terminal_failure_blocks_finalization_without_publication(execution_database):
    c = execution_database
    _, approved = await approval(c)
    await settle(c, "b", "failed_terminal")
    result = await action(c, "reconcile")
    assert result.batch_status == "checking_completed" and result.failed_terminal == 1
    await publish(c, approved.latest_approved_revision_id)
    with pytest.raises(PaperBatchError, match="paper_batch_publications_incomplete"):
        await action(c, "finalize")


@pytest.mark.parametrize("mode,settled", [("transport", False), ("unknown_item", True)])
async def test_automatic_reconciliation_after_real_execution_failure(execution_database, mode, settled):
    from app.application.paper_checking_execution import PaperCheckingExecutionError
    c = execution_database
    await completed(c)
    run = await start(c, "b")
    provider = FakePaperProvider(mode=mode)
    with pytest.raises(PaperCheckingExecutionError):
        await service(c, provider).execute(c[2]["paper_b"], run.id)
    result = await action(c, "progress")
    assert result.batch_status == ("checking_completed" if settled else "checking")
    assert result.failed_terminal == int(settled)
    assert result.failed_retryable == int(not settled)
    assert len(provider.calls) == (1 if settled else 3)


async def test_failed_auto_reconciliation_does_not_corrupt_success(execution_database, monkeypatch):
    c = execution_database
    await completed(c)
    run = await start(c, "b")
    execution = service(c, FakePaperProvider())

    async def unavailable(paper_id):
        raise RuntimeError("synthetic aggregate outage")

    monkeypatch.setattr(execution.repository, "reconcile_batch", unavailable)
    result = await execution.execute(c[2]["paper_b"], run.id)
    assert result["status"] == "completed_with_review_required" and result["ai_revision_id"]
    assert (await action(c, "progress")).batch_status == "checking"
    assert (await action(c, "reconcile")).batch_status == "checking_completed"


async def test_history_without_active_pointer_does_not_satisfy_finalization(execution_database):
    from app.infrastructure.paper_result_publication_models import (
        PaperResultPublication,
    )
    from app.infrastructure.paper_result_publication_repository import (
        PaperResultPublicationRepository,
    )
    from app.infrastructure.scan_checking_models import PaperSubmission
    c = execution_database
    _, a = await approval(c)
    _, b = await approval(c, "b")
    await publish(c, b.latest_approved_revision_id, "b")
    async with c[1]() as db, db.begin():
        paper = await db.get(PaperSubmission, c[2]["paper_a"])
        snapshot = await PaperResultPublicationRepository(db)._snapshot(paper, a.latest_approved_revision_id)
        # History without the authoritative PR9 pointer is not an active result.
        db.add(PaperResultPublication(paper_submission_id=paper.id, review_id=a.review_id,
            approved_review_revision_id=a.latest_approved_revision_id,
            published_by_user_id=c[2]["teacher"], publication_number=1,
            publication_snapshot=snapshot.model_dump(mode="json")))
    assert (await action(c, "progress")).active_publication_count == 1
    with pytest.raises(PaperBatchError, match="paper_batch_publications_incomplete"):
        await action(c, "finalize")


async def test_cancelled_batch_cannot_start_or_create_new_run(execution_database):
    from app.application.checking_intake import InvalidCheckingInput
    c = execution_database
    await c[0].execute(text("""UPDATE assessment_scan_batches SET status='cancelled',
        cancelled_at=clock_timestamp(),cancelled_by_user_id=:teacher,cancellation_reason='cancelled'
        WHERE id=:batch"""), c[2])
    with pytest.raises(PaperBatchError, match="paper_batch_not_ready_for_checking"):
        await action(c, "start")
    with pytest.raises(InvalidCheckingInput, match="paper_batch_not_checkable"):
        await intake(c[1]).create(request(c[2]))
    assert (await action(c, "reconcile")).batch_status == "cancelled"


@pytest.mark.parametrize("path,method", [
    ("/checking", "get"), ("/checking/start", "post"),
    ("/checking/reconcile", "post"), ("/complete", "post"),
])
@pytest.mark.parametrize("role", ["teacher", "student", "unauthorized"])
async def test_http_authorization_and_aggregate_only(execution_database, path, method, role):
    c = execution_database
    app = FastAPI()
    app.include_router(router)
    identity = {"id": c[2]["teacher"], "role": "teacher"}

    async def session():
        async with c[1]() as db:
            yield db

    def principal():
        roles = frozenset({identity["role"]})
        return Principal(identity["id"], "teacher", "Teacher", roles,
                         capabilities_for_roles(roles), None)

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[require_principal] = principal
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://test", headers={"Origin": "http://localhost:5173"}) as client:
        response = await client.get(f"/api/assessment-core/scan-batches/{c[2]['batch']}/checking")
        assert response.status_code == 200
        assert response.json()["paper_count"] == 2
        assert not any(x in response.text for x in ("Private A", "Private B", "raw_output", "original.pdf"))
        identity.update(id=uuid4(), role=role)
        response = await getattr(client, method)(f"/api/assessment-core/scan-batches/{c[2]['batch']}{path}")
        assert response.status_code == (404 if role == "teacher" else 403)


@pytest.mark.parametrize("operation", ["reconcile", "finalize"])
async def test_real_concurrent_transitions_serialize(concurrent_database, operation):
    c, url = concurrent_database
    _, a = await approval(c)
    _, b = await approval(c, "b")
    if operation == "finalize":
        await publish(c, a.latest_approved_revision_id)
        await publish(c, b.latest_approved_revision_id, "b")
    else:
        # Automatic reconciliation already completed; reset only aggregate state
        # to exercise two contenders for the explicit transition.
        await c[0].execute(text("UPDATE assessment_scan_batches SET status='checking' WHERE id=:batch"), c[2])
        await c[0].execute(text("DELETE FROM scan_checking_events WHERE batch_id=:batch AND event_type='batch.checking_completed'"), c[2])
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second:
            before_version = await first.scalar(text("SELECT row_version FROM assessment_scan_batches WHERE id=:batch"), c[2])
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            # Load stale state before lock acquisition to verify refresh after waiting.
            await PaperBatchOrchestrationRepository(second).load(c[2]["batch"])
            service_a = PaperBatchOrchestrationService(PaperBatchOrchestrationRepository(first))
            a_result = await getattr(service_a, operation)(c[2]["batch"], c[2]["teacher"])

            async def contender():
                service_b = PaperBatchOrchestrationService(PaperBatchOrchestrationRepository(second))
                result = await getattr(service_b, operation)(c[2]["batch"], c[2]["teacher"])
                await second.commit()
                return result

            task = asyncio.create_task(contender())
            for _ in range(100):
                if await first.scalar(text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": second_pid}):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("second transition did not wait on batch lock")
            await first.commit()
            assert await asyncio.wait_for(task, 10) == a_result
            assert await first.scalar(text("SELECT row_version FROM assessment_scan_batches WHERE id=:batch"), c[2]) == before_version + 1
            event = "batch.completed" if operation == "finalize" else "batch.checking_completed"
            assert await first.scalar(text("SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type=:event"), {**c[2], "event": event}) == 1
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
