"""Authoritative teacher publication pairs, real PostgreSQL and HTTP contracts."""

import os
from uuid import uuid4

import httpx
import pytest
from app.application.paper_checking_review import PaperReviewApproval
from app.infrastructure.paper_batch_orchestration_repository import (
    PaperBatchOrchestrationRepository,
)
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from app.infrastructure.scan_checking_models import AssessmentScanBatch
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_manual_paper_review_database import grade, manual, policies
from tests.integration.test_paper_batch_orchestration_database import action
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_review_contract_database import add_papers
from tests.integration.test_paper_checking_review_database import call, edited
from tests.integration.test_paper_publication_withdrawal_database import withdraw
from tests.integration.test_paper_result_publication_database import (
    approval,
    publish,
    repo_call,
)
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)
from tests.integration.test_scan_storage_retention_database import http_app

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]
ROOT = "/api/assessment-core"
ACTIVE = "active_publication_id"
SOURCE = "active_publication_approved_review_revision_id"
APPROVED = "latest_approved_revision_id"


def entry(progress, paper_id):
    return next(p for p in progress.papers if p["paper_submission_id"] == paper_id)


async def projected(c, paper="a"):
    return entry(await action(c, "progress"), c[2][f"paper_{paper}"])


def pair(row):
    return row[ACTIVE], row[SOURCE]


async def correction(c, run, review):
    changed = await call(
        c, run, "edit", edited(review, summary="Corrected review"), review.version
    )
    return await call(
        c, run, "approve", PaperReviewApproval(expected_version=changed.version)
    )


@pytest.mark.parametrize("approved", [False, True])
async def test_no_active_publication(execution_database, approved):
    c = execution_database
    revision = None
    if approved:
        _, review = await approval(c)
        revision = review.latest_approved_revision_id
    row = await projected(c)
    assert pair(row) == (None, None)
    assert row[APPROVED] == revision


@pytest.mark.parametrize("source", ["ai", "manual"])
async def test_exact_active_identity_and_source(
    execution_database, source, monkeypatch
):
    c = execution_database
    if source == "manual":
        await policies(c)
        review = await grade(c, await manual(c))
    else:
        _, review = await approval(c)
    publication = await publish(c, review.latest_approved_revision_id)

    async def forbidden_history(*args, **kwargs):
        pytest.fail("batch projection must not discover identity through history")

    monkeypatch.setattr(PaperResultPublicationRepository, "history", forbidden_history)
    row = await projected(c)
    assert pair(row) == (publication["id"], review.latest_approved_revision_id)
    assert row[APPROVED] == review.latest_approved_revision_id
    assert not {
        "publication_snapshot",
        "result",
        "withdrawal_reason",
        "storage_reference",
        "provider_data",
    }.intersection(row)


async def test_corrected_revision_keeps_old_publication(execution_database):
    c = execution_database
    run, r1 = await approval(c)
    a = await publish(c, r1.latest_approved_revision_id)
    r2 = await correction(c, run, r1)
    row = await projected(c)
    assert row[APPROVED] == r2.latest_approved_revision_id
    assert row[APPROVED] != r1.latest_approved_revision_id
    assert row["review_state"] == "approved"
    assert pair(row) == (a["id"], r1.latest_approved_revision_id)


async def test_withdraw_and_same_revision_republish(execution_database):
    c = execution_database
    _, r1 = await approval(c)
    a = await publish(c, r1.latest_approved_revision_id)
    before = await projected(c)
    await withdraw(c, a)
    row = await projected(c)
    assert pair(row) == (None, None)
    for field in (APPROVED, "review_id", "review_state", "checking_state"):
        assert row[field] == before[field]
    b = await publish(c, r1.latest_approved_revision_id)
    assert a["id"] != b["id"]
    assert pair(await projected(c)) == (b["id"], r1.latest_approved_revision_id)
    history = await repo_call(c, "history", c[2]["paper_a"])
    assert len(history) == 2 and history[0]["is_withdrawn"]


async def test_new_revision_supersedes_active(execution_database):
    c = execution_database
    run, r1 = await approval(c)
    a = await publish(c, r1.latest_approved_revision_id)
    r2 = await correction(c, run, r1)
    b = await publish(c, r2.latest_approved_revision_id)
    row = await projected(c)
    assert a["id"] != b["id"]
    assert row[APPROVED] == r2.latest_approved_revision_id
    assert pair(row) == (b["id"], r2.latest_approved_revision_id)


async def test_completed_batch_withdraw_republish(execution_database):
    c = execution_database
    _, r1 = await approval(c)
    _, other = await approval(c, "b")
    a = await publish(c, r1.latest_approved_revision_id)
    await publish(c, other.latest_approved_revision_id, "b")
    assert (await action(c, "finalize")).batch_status == "completed"
    assert pair(await projected(c)) == (a["id"], r1.latest_approved_revision_id)
    await withdraw(c, a)
    progress = await action(c, "progress")
    assert progress.batch_status == "completed"
    assert pair(entry(progress, c[2]["paper_a"])) == (None, None)
    b = await publish(c, r1.latest_approved_revision_id)
    progress = await action(c, "progress")
    assert progress.batch_status == "completed"
    assert pair(entry(progress, c[2]["paper_a"])) == (
        b["id"],
        r1.latest_approved_revision_id,
    )


async def test_official_bulk_route_projects_distinct_identities(execution_database):
    c = execution_database
    _, a = await approval(c)
    _, b = await approval(c, "b")
    app, _ = http_app(c)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": str(uuid4())},
    ) as client:
        response = await client.post(
            f"{ROOT}/scan-batches/{c[2]['batch']}/publications/bulk"
        )
        assert response.status_code == 200, response.text
        response = await client.get(f"{ROOT}/scan-batches/{c[2]['batch']}/checking")
        assert response.status_code == 200, response.text
        rows = {r["paper_submission_id"]: r for r in response.json()["papers"]}
        active_ids = set()
        for paper, review in (("a", a), ("b", b)):
            row = rows[str(c[2][f"paper_{paper}"])]
            assert (
                row[SOURCE] == row[APPROVED] == str(review.latest_approved_revision_id)
            )
            assert row[ACTIVE]
            active_ids.add(row[ACTIVE])
        assert len(active_ids) == 2


@pytest.mark.parametrize(
    "actor", ["owner", "co_teacher", "admin", "outside", "revoked", "student"]
)
async def test_projection_authorization_and_exact_withdrawal_id(
    execution_database, actor
):
    c, ids = execution_database, execution_database[2]
    _, review = await approval(c)
    publication = await publish(c, review.latest_approved_revision_id)
    app, identity = http_app(c)
    if actor in {"co_teacher", "outside", "revoked"}:
        colleague = uuid4()
        await c[0].execute(
            text(
                "INSERT INTO users(id,login,normalized_login,password_hash,display_name) "
                "VALUES (:id,:name,:name,'hash','Colleague')"
            ),
            {"id": colleague, "name": str(colleague)},
        )
        if actor != "outside":
            await c[0].execute(
                text(
                    "INSERT INTO class_group_teachers(teacher_user_id,class_group_id,assigned_by) "
                    "VALUES (:actor,:group,:teacher)"
                ),
                {**ids, "actor": colleague},
            )
        if actor == "revoked":
            await c[0].execute(
                text("DELETE FROM class_group_teachers WHERE teacher_user_id=:actor"),
                {"actor": colleague},
            )
        identity.update(user_id=colleague)
    elif actor == "admin":
        identity.update(role="admin")
    elif actor == "student":
        identity.update(role="student", user_id=uuid4(), student_id=ids["student_a"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": str(uuid4())},
    ) as client:
        response = await client.get(f"{ROOT}/scan-batches/{ids['batch']}/checking")
        expected = (
            404
            if actor in {"outside", "revoked"}
            else 403
            if actor == "student"
            else 200
        )
        assert response.status_code == expected, response.text
        if expected != 200:
            assert str(publication["id"]) not in response.text
            return
        row = next(
            r
            for r in response.json()["papers"]
            if r["paper_submission_id"] == str(ids["paper_a"])
        )
        assert row[ACTIVE] == str(publication["id"])
        assert row[SOURCE] == row[APPROVED] == str(review.latest_approved_revision_id)
        response = await client.post(
            f"{ROOT}/paper-submissions/{ids['paper_a']}/publications/{row[ACTIVE]}/withdraw",
            json={"reason": "Correction"},
        )
        assert response.status_code == 200, response.text
        response = await client.get(f"{ROOT}/scan-batches/{ids['batch']}/checking")
        row = next(
            r
            for r in response.json()["papers"]
            if r["paper_submission_id"] == str(ids["paper_a"])
        )
        assert pair(row) == (None, None)


async def test_cross_class_batch_and_paper_substitution(execution_database):
    c, ids = execution_database, execution_database[2]
    _, review = await approval(c)
    publication = await publish(c, review.latest_approved_revision_id)
    # An unrelated teacher has class access elsewhere, never in this batch's class.
    actor, group = uuid4(), uuid4()
    await c[0].execute(
        text(
            "INSERT INTO users(id,login,normalized_login,password_hash,display_name) "
            "VALUES (:actor,:name,:name,'hash','Other teacher')"
        ),
        {"actor": actor, "name": str(actor)},
    )
    await c[0].execute(
        text(
            "INSERT INTO class_groups(id,name,created_by) VALUES (:group,:name,:actor)"
        ),
        {"group": group, "name": str(group), "actor": actor},
    )
    await c[0].execute(
        text(
            "INSERT INTO class_group_teachers(teacher_user_id,class_group_id,assigned_by) "
            "VALUES (:actor,:group,:actor)"
        ),
        {"actor": actor, "group": group},
    )
    app, identity = http_app(c)
    identity.update(user_id=actor)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": str(uuid4())},
    ) as client:
        for path in (
            f"/scan-batches/{ids['batch']}/checking",
            f"/paper-submissions/{ids['paper_a']}/publications",
        ):
            response = await client.get(ROOT + path)
            assert response.status_code == 404
            assert str(publication["id"]) not in response.text
        response = await client.post(
            f"{ROOT}/paper-submissions/{ids['paper_a']}/publications/{publication['id']}/withdraw",
            json={"reason": "Wrong class"},
        )
        assert response.status_code == 404
    assert pair(await projected(c)) == (
        publication["id"],
        review.latest_approved_revision_id,
    )


async def test_thirty_papers_mixed_active_states_one_statement(execution_database):
    c = execution_database
    await add_papers(c, 30)
    _, review = await approval(c)
    publication = await publish(c, review.latest_approved_revision_id)
    _, other = await approval(c, "b")
    old = await publish(c, other.latest_approved_revision_id, "b")
    await withdraw(c, old, paper="b")
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    async with c[1]() as db:
        batch = await db.get(AssessmentScanBatch, c[2]["batch"])
        event.listen(c[0].sync_connection, "before_cursor_execute", record)
        try:
            progress = await PaperBatchOrchestrationRepository(db).progress(batch)
        finally:
            event.remove(c[0].sync_connection, "before_cursor_execute", record)
    assert len(progress.papers) == 30 and len(queries) == 1
    assert "publication_snapshot" not in queries[0]
    assert pair(entry(progress, c[2]["paper_a"])) == (
        publication["id"],
        review.latest_approved_revision_id,
    )
    assert all(
        pair(row) == (None, None)
        for row in progress.papers
        if row["paper_submission_id"] != c[2]["paper_a"]
    )


@pytest.mark.parametrize("operation", ["publish", "withdraw"])
async def test_concurrent_projection_pair_before_and_after_commit(
    concurrent_database, operation
):
    c, url = concurrent_database
    run, r1 = await approval(c)
    a = await publish(c, r1.latest_approved_revision_id)
    r2 = await correction(c, run, r1) if operation == "publish" else r1
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as writer, factory() as reader:
            repo = PaperResultPublicationRepository(writer)
            if operation == "publish":
                b = await repo.publish(
                    c[2]["paper_a"],
                    r2.latest_approved_revision_id,
                    c[2]["teacher"],
                    request_key=str(uuid4()),
                )
                expected = (b["id"], r2.latest_approved_revision_id)
            else:
                await repo.withdraw(
                    c[2]["paper_a"],
                    a["id"],
                    "Correction",
                    c[2]["teacher"],
                    request_key=str(uuid4()),
                )
                expected = (None, None)
            batch = await reader.get(AssessmentScanBatch, c[2]["batch"])
            projection = PaperBatchOrchestrationRepository(reader)
            before = entry(await projection.progress(batch), c[2]["paper_a"])
            assert pair(before) == (a["id"], r1.latest_approved_revision_id)
            assert before[APPROVED] == r2.latest_approved_revision_id
            await writer.commit()
            after = entry(await projection.progress(batch), c[2]["paper_a"])
            assert pair(after) == expected
    finally:
        await engine.dispose()
