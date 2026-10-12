"""Durable PR15 command outcomes on real PostgreSQL and HTTP boundaries."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from app.application.paper_checking_review import PaperReviewApproval
from app.application.paper_result_publication import (
    PaperPublicationError,
    PublicationEntry,
)
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_review_database import call, edited
from tests.integration.test_paper_publication_withdrawal_database import wait_blocked
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


@pytest_asyncio.fixture
async def three_paper_database(tmp_path):
    seed = execution_database.__wrapped__(tmp_path, three_papers=True)
    try:
        yield await anext(seed)
    finally:
        await seed.aclose()


async def state(c):
    return (
        await c[0].execute(
            text(
                "SELECT paper_submission_id,active_publication_id,version,updated_at FROM paper_result_publication_states ORDER BY paper_submission_id"
            )
        )
    ).all()


async def count(c, table):
    assert table in {
        "paper_result_publications",
        "paper_result_publication_commands",
        "paper_result_publication_command_items",
    }
    return await c[0].scalar(text(f"SELECT count(*) FROM {table}"))


async def test_mandatory_single_http_e2e(execution_database):
    c, ids = execution_database, execution_database[2]
    _, revision = await approval(c)
    app, identity = http_app(c)
    endpoint = f"/api/assessment-core/paper-submissions/{ids['paper_a']}/publications"
    result_url = (
        f"/api/assessment-core/student/paper-submissions/{ids['paper_a']}/result"
    )
    body = {"approved_review_revision_id": str(revision.latest_approved_revision_id)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        assert (await client.post(endpoint, json=body)).status_code == 422
        a = await client.post(endpoint, json=body, headers={"Idempotency-Key": "K1"})
        assert a.status_code == 200, a.text
        reuse = await client.post(
            endpoint, json=body, headers={"Idempotency-Key": "reuse"}
        )
        assert reuse.json()["id"] == a.json()["id"]
        assert reuse.json()["outcome"] == "already_current"
        w = await client.post(
            endpoint + f"/{a.json()['id']}/withdraw",
            json={"reason": "Correction"},
            headers={"Idempotency-Key": "withdraw"},
        )
        assert w.status_code == 200, w.text
        identity.update(role="student", student_id=ids["student_a"])
        assert (await client.get(result_url)).status_code == 404
        identity.update(role="teacher", student_id=None)
        for key, original in [("K1", a), ("reuse", reuse)]:
            replay = await client.post(
                endpoint, json=body, headers={"Idempotency-Key": key}
            )
            assert replay.json() == original.json()
        assert await count(c, "paper_result_publications") == 1
        assert (await state(c))[0].active_publication_id is None
        b = await client.post(endpoint, json=body, headers={"Idempotency-Key": "K2"})
        assert b.status_code == 200, b.text
        assert b.json()["id"] != a.json()["id"]
        reuse_b = await client.post(
            endpoint, json=body, headers={"Idempotency-Key": "K3"}
        )
        assert reuse_b.json()["id"] == b.json()["id"]
        assert reuse_b.json()["outcome"] == "already_current"
        before = await state(c)
        for key, original in [("K1", a), ("K2", b), ("reuse", reuse)]:
            assert (
                await client.post(endpoint, json=body, headers={"Idempotency-Key": key})
            ).json() == original.json()
        assert await state(c) == before
        assert str(before[0].active_publication_id) == b.json()["id"]
        assert await count(c, "paper_result_publications") == 2
        # Withdrawal replay retains the audit result and cannot revoke B.
        assert (
            await client.post(
                endpoint + f"/{a.json()['id']}/withdraw",
                json={"reason": "Correction"},
                headers={"Idempotency-Key": "withdraw"},
            )
        ).json() == w.json()
        assert await state(c) == before
        assert (
            await client.post(endpoint, json=body, headers={"Idempotency-Key": "K3"})
        ).json() == reuse_b.json()


@pytest.mark.parametrize(
    "change", ["revision", "paper", "kind", "selection", "batch", "reason"]
)
async def test_same_key_conflicts_are_bounded(execution_database, change):
    c, ids = execution_database, execution_database[2]
    _, review = await approval(c)
    revision = review.latest_approved_revision_id
    entry = PublicationEntry(
        paper_submission_id=ids["paper_a"], approved_review_revision_id=revision
    )
    if change == "selection":
        await repo_call(
            c,
            "publish_selected",
            [entry],
            ids["teacher"],
            ids["batch"],
            request_key="key",
        )
        operation = repo_call(
            c,
            "publish_selected",
            [entry.model_copy(update={"approved_review_revision_id": uuid4()})],
            ids["teacher"],
            ids["batch"],
            request_key="key",
        )
    elif change == "batch":
        # A selected key's batch scope is part of its meaning.
        await repo_call(
            c,
            "publish_selected",
            [entry],
            ids["teacher"],
            ids["batch"],
            request_key="key",
        )
        operation = repo_call(
            c, "publish_selected", [entry], ids["teacher"], uuid4(), request_key="key"
        )
    elif change == "reason":
        a = await publish(c, revision)
        await repo_call(
            c,
            "withdraw",
            ids["paper_a"],
            a["id"],
            "Correction",
            ids["teacher"],
            request_key="key",
        )
        operation = repo_call(
            c,
            "withdraw",
            ids["paper_a"],
            a["id"],
            "Different",
            ids["teacher"],
            request_key="key",
        )
    else:
        await publish(c, revision, request_key="key")
        if change == "kind":
            operation = repo_call(
                c, "publish_selected", [entry], ids["teacher"], request_key="key"
            )
        else:
            operation = repo_call(
                c,
                "publish",
                uuid4() if change == "paper" else ids["paper_a"],
                uuid4() if change == "revision" else revision,
                ids["teacher"],
                request_key="key",
            )
    with pytest.raises(PaperPublicationError) as error:
        await operation
    assert (error.value.code, error.value.status) == (
        "paper_publication_request_key_conflict",
        409,
    )


@pytest.mark.parametrize("mode", ["selected", "bulk"])
async def test_mandatory_bulk_historical_e2e(three_paper_database, mode):
    c, ids = three_paper_database, three_paper_database[2]
    reviews, runs = {}, {}
    for paper in "abc":
        runs[paper], reviews[paper] = await approval(c, paper)
    entries = [
        PublicationEntry(
            paper_submission_id=ids[f"paper_{p}"],
            approved_review_revision_id=reviews[p].latest_approved_revision_id,
        )
        for p in "cba"
    ]
    args = (
        [entries, ids["teacher"], ids["batch"]]
        if mode == "selected"
        else [ids["batch"], ids["teacher"]]
    )
    method = "publish_selected" if mode == "selected" else "bulk"
    original = await repo_call(c, method, *args, request_key="K1")
    assert len(original) == 3
    assert await repo_call(c, method, *args, request_key="K1") == original
    reused = await repo_call(c, method, *args, request_key="reuse")
    assert all(x["outcome"] == "already_current" for x in reused)
    a = next(x for x in original if x["paper_submission_id"] == ids["paper_a"])
    await repo_call(
        c, "withdraw", ids["paper_a"], a["id"], "Correction", ids["teacher"]
    )
    edit = await call(
        c,
        runs["b"],
        "edit",
        edited(reviews["b"], summary="New revision"),
        reviews["b"].version,
        paper="b",
    )
    newer = await call(
        c,
        runs["b"],
        "approve",
        PaperReviewApproval(expected_version=edit.version),
        paper="b",
    )
    b = await publish(c, newer.latest_approved_revision_id, "b", request_key="new-b")
    before, total = await state(c), await count(c, "paper_result_publications")
    for key, expected in [("K1", original), ("reuse", reused)]:
        assert await repo_call(c, method, *args, request_key=key) == expected
    assert await state(c) == before
    assert await count(c, "paper_result_publications") == total == 4
    assert (
        next(
            x for x in before if x.paper_submission_id == ids["paper_b"]
        ).active_publication_id
        == b["id"]
    )
    assert (
        next(
            x for x in before if x.paper_submission_id == ids["paper_a"]
        ).active_publication_id
        is None
    )
    if mode == "selected":
        # Input order is immaterial, output order is preserved by the ledger.
        assert (
            await repo_call(
                c,
                method,
                list(reversed(entries)),
                ids["teacher"],
                ids["batch"],
                request_key="K1",
            )
            == original
        )
    else:
        # A new automatic bulk command resolves today's latest approvals.
        current = await repo_call(c, method, *args, request_key="K2")
        assert (
            next(x for x in current if x["paper_submission_id"] == ids["paper_b"])["id"]
            == b["id"]
        )
        assert await count(c, "paper_result_publications") == 5

    # Replay across the real HTTP contract after withdrawal and supersession.
    app, _ = http_app(c)
    before_http, total_http = (
        await state(c),
        await count(c, "paper_result_publications"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": "K1"},
    ) as client:
        kwargs = (
            {"json": {"entries": [entry.model_dump(mode="json") for entry in entries]}}
            if mode == "selected"
            else {}
        )
        response = await client.post(
            f"/api/assessment-core/scan-batches/{ids['batch']}/publications/{mode}",
            **kwargs,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"items": jsonable_encoder(original)}
    assert await state(c) == before_http
    assert await count(c, "paper_result_publications") == total_http


async def test_command_failure_rolls_back_ledger_and_publications(execution_database):
    c, ids = execution_database, execution_database[2]
    _, review = await approval(c)
    entries = [
        PublicationEntry(
            paper_submission_id=ids["paper_a"],
            approved_review_revision_id=review.latest_approved_revision_id,
        ),
        PublicationEntry(
            paper_submission_id=ids["paper_b"], approved_review_revision_id=uuid4()
        ),
    ]
    with pytest.raises(PaperPublicationError):
        await repo_call(
            c,
            "publish_selected",
            entries,
            ids["teacher"],
            ids["batch"],
            request_key="failed",
        )
    for table in (
        "paper_result_publications",
        "paper_result_publication_commands",
        "paper_result_publication_command_items",
    ):
        assert await count(c, table) == 0
    async with c[1]() as db:
        with pytest.raises(DBAPIError, match="outcomes incomplete"):
            await db.execute(
                text(
                    "INSERT INTO paper_result_publication_commands(actor_user_id,request_key,command_kind,request_fingerprint) VALUES (:teacher,'incomplete','single',repeat('a',64))"
                ),
                ids,
            )
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await db.rollback()


async def test_outcome_failure_rolls_back_effects_and_releases_key(
    execution_database, monkeypatch
):
    c, ids = execution_database, execution_database[2]
    _, review = await approval(c)
    async with c[1]() as db:
        repo = PaperResultPublicationRepository(db, c[3])

        async def fail(*args, **kwargs):
            raise RuntimeError("outcome persistence failed")

        monkeypatch.setattr(repo, "_record_command", fail)
        with pytest.raises(RuntimeError, match="outcome persistence failed"):
            async with db.begin():
                await repo.publish(
                    ids["paper_a"],
                    review.latest_approved_revision_id,
                    ids["teacher"],
                    request_key="retry",
                )
    assert await state(c) == []
    assert await count(c, "paper_result_publications") == 0
    assert await count(c, "paper_result_publication_commands") == 0
    a = await publish(c, review.latest_approved_revision_id, request_key="retry")
    assert a["outcome"] == "published"
    assert await count(c, "paper_result_publication_commands") == 1
    assert await count(c, "paper_result_publication_command_items") == 1


async def test_old_single_replay_leaves_newer_revision_active(execution_database):
    c, ids = execution_database, execution_database[2]
    run, r1 = await approval(c)
    a = await publish(c, r1.latest_approved_revision_id, request_key="K1")
    await repo_call(
        c, "withdraw", ids["paper_a"], a["id"], "Correction", ids["teacher"]
    )
    edit = await call(c, run, "edit", edited(r1, summary="R2"), r1.version)
    r2 = await call(
        c, run, "approve", PaperReviewApproval(expected_version=edit.version)
    )
    b = await publish(c, r2.latest_approved_revision_id, request_key="K2")
    before = await state(c)
    assert await publish(c, r1.latest_approved_revision_id, request_key="K1") == a
    assert await publish(c, r2.latest_approved_revision_id, request_key="K2") == b
    assert await state(c) == before
    assert await count(c, "paper_result_publications") == 2


@pytest.mark.parametrize("mutation", ["command", "item", "append"])
async def test_finalized_command_outcomes_are_immutable(execution_database, mutation):
    c = execution_database
    _, review = await approval(c)
    a = await publish(c, review.latest_approved_revision_id, request_key="immutable")
    sql = {
        "command": "UPDATE paper_result_publication_commands SET request_fingerprint=repeat('b',64) WHERE request_key='immutable'",
        "item": "UPDATE paper_result_publication_command_items SET outcome='already_current'",
        "append": "INSERT INTO paper_result_publication_command_items(command_id,position,publication_id,outcome) SELECT id,1,:pub,'published' FROM paper_result_publication_commands WHERE request_key='immutable'",
    }[mutation]
    async with c[1]() as db:
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(text(sql), {"pub": a["id"]})
        await db.rollback()


@pytest.mark.parametrize("mode", ["selected", "bulk"])
async def test_real_bulk_same_key_concurrency(concurrent_database, mode):
    c, url = concurrent_database
    ids = c[2]
    _, ra = await approval(c)
    _, rb = await approval(c, "b")
    entries = [
        PublicationEntry(
            paper_submission_id=ids[f"paper_{p}"],
            approved_review_revision_id=r.latest_approved_revision_id,
        )
        for p, r in [("a", ra), ("b", rb)]
    ]
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second:
            pid1 = await first.scalar(text("SELECT pg_backend_pid()"))
            pid2 = await second.scalar(text("SELECT pg_backend_pid()"))

            async def operation(db):
                repo = PaperResultPublicationRepository(db, c[3])
                if mode == "bulk":
                    return await repo.bulk(
                        ids["batch"], ids["teacher"], request_key="bulk-key"
                    )
                return await repo.publish_selected(
                    entries, ids["teacher"], ids["batch"], request_key="bulk-key"
                )

            original = await operation(first)

            async def contender():
                result = await operation(second)
                await second.commit()
                return result

            task = asyncio.create_task(contender())
            await wait_blocked(first, pid1, pid2)
            await first.commit()
            assert await asyncio.wait_for(task, 10) == original
            assert (
                await second.scalar(
                    text("SELECT count(*) FROM paper_result_publication_commands")
                )
                == 1
            )
            assert (
                await second.scalar(
                    text("SELECT count(*) FROM paper_result_publication_command_items")
                )
                == 2
            )
            assert (
                await second.scalar(
                    text("SELECT count(*) FROM paper_result_publications")
                )
                == 2
            )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.parametrize("race", ["same", "conflict", "different", "withdrawn"])
async def test_real_command_concurrency_and_restart(concurrent_database, race):
    c, url = concurrent_database
    ids = c[2]
    run, r1 = await approval(c)
    r2 = r1
    if race == "conflict":
        edit = await call(c, run, "edit", edited(r1, summary="Second"), r1.version)
        r2 = await call(
            c, run, "approve", PaperReviewApproval(expected_version=edit.version)
        )
    a = None
    if race == "withdrawn":
        a = await publish(c, r1.latest_approved_revision_id, request_key="K1")
        await repo_call(
            c, "withdraw", ids["paper_a"], a["id"], "Correction", ids["teacher"]
        )
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second:
            pid1 = await first.scalar(text("SELECT pg_backend_pid()"))
            pid2 = await second.scalar(text("SELECT pg_backend_pid()"))
            repo = PaperResultPublicationRepository(first, c[3])
            if race == "withdrawn":
                b = await repo.publish(
                    ids["paper_a"],
                    r1.latest_approved_revision_id,
                    ids["teacher"],
                    request_key="K2",
                )
            else:
                b = await repo.publish(
                    ids["paper_a"],
                    r1.latest_approved_revision_id,
                    ids["teacher"],
                    request_key="K1",
                )

            async def contender():
                try:
                    result = await PaperResultPublicationRepository(
                        second, c[3]
                    ).publish(
                        ids["paper_a"],
                        r2.latest_approved_revision_id,
                        ids["teacher"],
                        request_key="other" if race == "different" else "K1",
                    )
                    await second.commit()
                    return result
                except PaperPublicationError as error:
                    await second.rollback()
                    return error

            task = asyncio.create_task(contender())
            if race == "withdrawn":
                # Historical replay completes even while K2 holds paper locks.
                assert await asyncio.wait_for(task, 10) == a
            else:
                await wait_blocked(first, pid1, pid2)
            await first.commit()
            result = await asyncio.wait_for(task, 10)
            if race == "conflict":
                assert (result.code, result.status) == (
                    "paper_publication_request_key_conflict",
                    409,
                )
            elif race != "withdrawn":
                assert result["id"] == b["id"]
                if race == "same":
                    assert result == b
            # Brand-new service/session sees the same durable historical result.
        async with factory() as restarted:
            replay = await PaperResultPublicationRepository(restarted, c[3]).publish(
                ids["paper_a"],
                r1.latest_approved_revision_id,
                ids["teacher"],
                request_key="K1",
            )
            assert replay == (a if race == "withdrawn" else b)
            assert (
                await restarted.scalar(
                    text(
                        "SELECT active_publication_id FROM paper_result_publication_states WHERE paper_submission_id=:paper_a"
                    ),
                    ids,
                )
                == b["id"]
            )
            assert await restarted.scalar(
                text("SELECT count(*) FROM paper_result_publications")
            ) == (2 if race == "withdrawn" else 1)
            with pytest.raises(DBAPIError):
                await restarted.execute(
                    text(
                        "INSERT INTO paper_result_publication_commands(actor_user_id,request_key,command_kind,request_fingerprint) VALUES (:teacher,'K1','single',repeat('b',64))"
                    ),
                    ids,
                )
            await restarted.rollback()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
