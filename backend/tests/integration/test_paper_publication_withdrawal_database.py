"""PR15 real PostgreSQL withdrawal, visibility, storage and lock acceptance."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from app.application.paper_checking_review import PaperReviewApproval
from app.application.paper_result_publication import (
    PaperPublicationError,
    WithdrawalEntry,
)
from app.application.scan_storage_retention import PaperRetentionSettings
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_paper_batch_orchestration_database import action
from tests.integration.test_paper_checking_execution_database import PNG
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_review_database import call, edited, evidence
from tests.integration.test_paper_result_publication_database import (
    approval,
    publish,
    repo_call,
)
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)
from tests.integration.test_scan_storage_retention_database import (
    historical_completion,
    http_app,
    ledger,
    sweeper,
    targets,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


async def withdraw(c, pub, reason="Correction", paper="a"):
    return await repo_call(
        c, "withdraw", c[2][f"paper_{paper}"], pub["id"], reason, c[2]["teacher"]
    )


async def seed_historical_withdrawal(c, pub, days):
    # Insert historical immutable records; never UPDATE audit times.
    async with c[1]() as db, db.begin():
        await db.execute(
            text("""INSERT INTO paper_result_publication_withdrawals
          (publication_id,withdrawn_by_user_id,reason,created_at)
          VALUES (:pub,:teacher,'Historical correction',clock_timestamp()-make_interval(days => :days))"""),
            {**c[2], "pub": pub["id"], "days": days},
        )
        await db.execute(
            text("""UPDATE paper_result_publication_states
          SET active_publication_id=NULL WHERE paper_submission_id=:paper_a"""),
            c[2],
        )


async def test_withdraw_republish_history_evidence_and_student_pages(
    execution_database,
):
    c, ids = execution_database, execution_database[2]
    run, review = await approval(c)
    a = await publish(c, review.latest_approved_revision_id)
    before = await evidence(c, run)
    assert (
        await repo_call(
            c, "student_page", ids["paper_a"], ids["page_a"], ids["student_a"], c[3]
        )
        == PNG
    )
    w = await withdraw(c, a, " \n Correction \t")
    assert w["reason"] == "Correction"
    assert await withdraw(c, a) == w
    with pytest.raises(PaperPublicationError, match="already_withdrawn"):
        await withdraw(c, a, "Different")
    for method, args in [
        ("student_result", (ids["paper_a"], ids["student_a"])),
        ("student_page", (ids["paper_a"], ids["page_a"], ids["student_a"], c[3])),
    ]:
        with pytest.raises(PaperPublicationError, match="paper_result_not_found"):
            await repo_call(c, method, *args)
    assert await repo_call(c, "student_list", ids["student_a"]) == []
    history = await repo_call(c, "history", ids["paper_a"])
    assert history[0]["is_withdrawn"] and not history[0]["is_active"]
    assert history[0]["withdrawal_reason"] == "Correction"
    assert history[0]["withdrawn_by_user_id"] == ids["teacher"]
    assert history[0]["result"] == a["result"]
    app, _ = http_app(c)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.get(f"/api/assessment-core/scan-pages/{ids['page_a']}/content")
        ).content == PNG
    b = await publish(c, review.latest_approved_revision_id)
    assert b["id"] != a["id"] and b["publication_number"] == 2
    assert b["result"] == a["result"]
    assert (await publish(c, review.latest_approved_revision_id))["id"] == b["id"]
    assert await withdraw(c, a) == w  # Old exact identity never touches B.
    result = await repo_call(c, "student_result", ids["paper_a"], ids["student_a"])
    assert result["id"] == b["id"] and "withdrawal_reason" not in result
    assert (
        await repo_call(
            c, "student_page", ids["paper_a"], ids["page_a"], ids["student_a"], c[3]
        )
        == PNG
    )
    assert await evidence(c, run) == before
    await withdraw(c, b)
    third = await publish(c, review.latest_approved_revision_id)
    assert third["publication_number"] == 3
    history = await repo_call(c, "history", ids["paper_a"])
    assert [h["is_withdrawn"] for h in history] == [True, True, False]
    assert sum(h["active"] for h in history) == 1


@pytest.mark.parametrize("reason", ["", " \n\t", "x" * 501])
async def test_reason_validation_is_atomic(execution_database, reason):
    c = execution_database
    _, review = await approval(c)
    pub = await publish(c, review.latest_approved_revision_id)
    with pytest.raises(PaperPublicationError, match="reason"):
        await withdraw(c, pub, reason)
    assert (await repo_call(c, "history", c[2]["paper_a"]))[0]["active"]


async def test_superseded_cross_paper_and_newer_revision(execution_database):
    c = execution_database
    run, review = await approval(c)
    a = await publish(c, review.latest_approved_revision_id)
    edit = await call(
        c, run, "edit", edited(review, summary="Correction"), review.version
    )
    next_review = await call(
        c, run, "approve", PaperReviewApproval(expected_version=edit.version)
    )
    b = await publish(c, next_review.latest_approved_revision_id)
    with pytest.raises(PaperPublicationError, match="not_active"):
        await withdraw(c, a)
    with pytest.raises(PaperPublicationError, match="selection_mismatch"):
        await withdraw(c, b, paper="b")
    await withdraw(c, b)
    assert (await publish(c, next_review.latest_approved_revision_id))[
        "publication_number"
    ] == 3


@pytest.mark.parametrize("role", ["student", "outside", "same_class", "admin"])
async def test_http_authorization_and_reason_privacy(execution_database, role):
    c, ids = execution_database, execution_database[2]
    _, review = await approval(c)
    pub = await publish(c, review.latest_approved_revision_id)
    app, identity = http_app(c)
    if role == "student":
        identity.update(role="student", user_id=uuid4(), student_id=ids["student_a"])
    elif role == "admin":
        identity.update(role="admin")
    else:
        actor = uuid4()
        await c[0].execute(
            text(
                "INSERT INTO users(id,login,normalized_login,password_hash,display_name) VALUES (:id,:name,:name,'hash','Colleague')"
            ),
            {"id": actor, "name": f"teacher-{actor}"},
        )
        if role == "same_class":
            await c[0].execute(
                text(
                    "INSERT INTO class_group_teachers(teacher_user_id,class_group_id,assigned_by) VALUES (:actor,:group,:teacher)"
                ),
                {**ids, "actor": actor},
            )
        identity.update(user_id=actor)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": "withdraw"},
    ) as client:
        response = await client.post(
            f"/api/assessment-core/paper-submissions/{ids['paper_a']}/publications/{pub['id']}/withdraw",
            json={"reason": "Private teacher reason"},
        )
        assert response.status_code == (
            200
            if role in {"same_class", "admin"}
            else 404
            if role == "outside"
            else 403
        )
        if response.status_code == 200:
            identity.update(
                role="student", user_id=uuid4(), student_id=ids["student_a"]
            )
            for suffix in ("result", f"pages/{ids['page_a']}"):
                hidden = await client.get(
                    f"/api/assessment-core/student/paper-submissions/{ids['paper_a']}/{suffix}"
                )
                assert hidden.status_code == 404 and hidden.json() == {
                    "detail": "paper_result_not_found"
                }


async def test_checking_completed_and_completed_finalize_replay(execution_database):
    c = execution_database
    _, a = await approval(c)
    _, b = await approval(c, "b")
    pub = await publish(c, a.latest_approved_revision_id)
    await publish(c, b.latest_approved_revision_id, "b")
    await withdraw(c, pub)
    progress = await action(c, "progress")
    assert progress.batch_status == "checking_completed" and progress.settled_count == 2
    from app.application.paper_batch_orchestration import PaperBatchError

    with pytest.raises(PaperBatchError, match="publications_incomplete"):
        await action(c, "finalize")
    new = await publish(c, a.latest_approved_revision_id)
    assert (await action(c, "finalize")).batch_status == "completed"
    await withdraw(c, new)
    assert (await action(c, "finalize")).batch_status == "completed"


@pytest.mark.parametrize(
    "days,protected", [(0, True), (29, True), (30, False), (31, False)]
)
async def test_withdrawal_grace_and_independent_source_retention(
    execution_database, days, protected
):
    c = execution_database
    _, review = await approval(c)
    pub = await publish(c, review.latest_approved_revision_id)
    await historical_completion(c, days=90)
    await seed_historical_withdrawal(c, pub, days)
    found = await targets(c)
    assert any(t.target_kind == "source_artifact" for t in found)
    assert (c[2]["page_a"] not in {t.target_id for t in found}) == protected
    async with c[1]() as db:
        from app.infrastructure.scan_storage_retention_repository import (
            CANDIDATES,
            parameters,
        )

        now = await db.scalar(
            text(
                "SELECT created_at+interval '30 days' FROM paper_result_publication_withdrawals WHERE publication_id=:pub"
            ),
            {"pub": pub["id"]},
        )
        boundary = (
            await db.execute(
                text(
                    CANDIDATES.replace("clock_timestamp()", "CAST(:now AS timestamptz)")
                ),
                {**parameters(PaperRetentionSettings()), "now": now},
            )
        ).all()
        assert c[2]["page_a"] in {r.target_id for r in boundary}  # Strict > boundary.


async def test_physical_purge_blocks_republish_and_teacher_gets_410(execution_database):
    c, ids = execution_database, execution_database[2]
    run, review = await approval(c)
    pub = await publish(c, review.latest_approved_revision_id)
    before = await evidence(c, run)
    await historical_completion(c, days=90)
    await seed_historical_withdrawal(c, pub, 31)
    assert (c[3].root / ids["storage_a"]).exists()
    assert (await sweeper(c).run(dry_run=False))["purged"] == 3
    assert not (c[3].root / ids["storage_a"]).exists()
    assert all(
        r["policy_version"] == "paper_storage_retention.v2" for r in await ledger(c)
    )
    with pytest.raises(
        PaperPublicationError, match="paper_publication_pages_unavailable"
    ):
        await publish(c, review.latest_approved_revision_id)
    assert await evidence(c, run) == before
    history = await repo_call(c, "history", ids["paper_a"])
    assert len(history) == 1 and history[0]["is_withdrawn"] and not history[0]["active"]
    app, _ = http_app(c)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        gone = await client.get(
            f"/api/assessment-core/scan-pages/{ids['page_a']}/content"
        )
        assert gone.status_code == 410


@pytest.mark.parametrize("days", [10, 31])
async def test_republish_before_purge_protects_pages(execution_database, days):
    c = execution_database
    _, review = await approval(c)
    a = await publish(c, review.latest_approved_revision_id)
    await historical_completion(c, days=90)
    await seed_historical_withdrawal(c, a, days)
    b = await publish(c, review.latest_approved_revision_id)
    assert b["id"] != a["id"]
    assert c[2]["page_a"] not in {t.target_id for t in await targets(c)}
    await withdraw(c, b)
    assert c[2]["page_a"] not in {
        t.target_id for t in await targets(c)
    }  # Latest withdrawal extends grace.


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
async def test_actual_bytes_required_without_ledger(execution_database, damage):
    c = execution_database
    _, review = await approval(c)
    if damage == "missing":
        await c[3].delete(c[2]["storage_a"])
    else:
        (c[3].root / c[2]["storage_a"]).write_bytes(PNG + b"bad")
    with pytest.raises(PaperPublicationError, match="pages_unavailable"):
        await publish(c, review.latest_approved_revision_id)
    assert await repo_call(c, "history", c[2]["paper_a"]) == []


@pytest.mark.parametrize("bad", [False, True])
async def test_bulk_all_or_none_and_exact_replay(execution_database, bad):
    c, ids = execution_database, execution_database[2]
    _, ra = await approval(c)
    _, rb = await approval(c, "b")
    a = await publish(c, ra.latest_approved_revision_id)
    b = await publish(c, rb.latest_approved_revision_id, "b")
    entries = [
        WithdrawalEntry(paper_submission_id=ids["paper_a"], publication_id=a["id"]),
        WithdrawalEntry(
            paper_submission_id=ids["paper_b"],
            publication_id=a["id"] if bad else b["id"],
        ),
    ]
    if bad:
        with pytest.raises(PaperPublicationError, match="selection_mismatch"):
            await repo_call(
                c,
                "withdraw_selected",
                entries,
                "Correction",
                ids["teacher"],
                ids["batch"],
            )
        assert (await repo_call(c, "history", ids["paper_a"]))[0]["active"]
    else:
        rows = await repo_call(
            c, "withdraw_selected", entries, "Correction", ids["teacher"], ids["batch"]
        )
        new = await publish(c, ra.latest_approved_revision_id)
        assert (
            await repo_call(
                c,
                "withdraw_selected",
                entries,
                "Correction",
                ids["teacher"],
                ids["batch"],
            )
            == rows
        )
        assert (await repo_call(c, "student_result", ids["paper_a"], ids["student_a"]))[
            "id"
        ] == new["id"]


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "blank",
        "blank_tab",
        "foreign_pub",
        "foreign_actor",
        "immutable",
        "publication_immutable",
        "withdrawal_delete",
        "publication_delete",
        "pointer",
        "uncleared",
    ],
)
async def test_database_guards(execution_database, case):
    c, ids = execution_database, execution_database[2]
    _, review = await approval(c)
    a = await publish(c, review.latest_approved_revision_id)
    if case not in {"blank", "blank_tab", "foreign_pub", "foreign_actor", "uncleared"}:
        await withdraw(c, a)
    sql = {
        "duplicate": "INSERT INTO paper_result_publication_withdrawals(publication_id,withdrawn_by_user_id,reason) VALUES (:pub,:teacher,'Correction')",
        "blank": "INSERT INTO paper_result_publication_withdrawals(publication_id,withdrawn_by_user_id,reason) VALUES (:pub,:teacher,'   ')",
        "blank_tab": "INSERT INTO paper_result_publication_withdrawals(publication_id,withdrawn_by_user_id,reason) VALUES (:pub,:teacher,E'\\t\\n')",
        "foreign_pub": "INSERT INTO paper_result_publication_withdrawals(publication_id,withdrawn_by_user_id,reason) VALUES (:foreign,:teacher,'Correction')",
        "foreign_actor": "INSERT INTO paper_result_publication_withdrawals(publication_id,withdrawn_by_user_id,reason) VALUES (:pub,:foreign,'Correction')",
        "immutable": "UPDATE paper_result_publication_withdrawals SET reason='rewrite' WHERE publication_id=:pub",
        "publication_immutable": "UPDATE paper_result_publications SET publication_number=2 WHERE id=:pub",
        "withdrawal_delete": "DELETE FROM paper_result_publication_withdrawals WHERE publication_id=:pub",
        "publication_delete": "DELETE FROM paper_result_publications WHERE id=:pub",
        "pointer": "UPDATE paper_result_publication_states SET active_publication_id=:pub,version=version+1 WHERE paper_submission_id=:paper_a",
        "uncleared": "INSERT INTO paper_result_publication_withdrawals(publication_id,withdrawn_by_user_id,reason) VALUES (:pub,:teacher,'Correction')",
    }[case]
    with pytest.raises(DBAPIError):
        async with c[0].begin_nested():
            await c[0].execute(text(sql), {**ids, "pub": a["id"], "foreign": uuid4()})
            await c[0].execute(
                text("SET CONSTRAINTS trg_paper_withdrawal_committed IMMEDIATE")
            )


async def test_withdrawal_database_constraint_contract(execution_database):
    c = execution_database
    constraints = (
        await c[0].execute(
            text("""SELECT contype::text,pg_get_constraintdef(oid)
      FROM pg_constraint WHERE conrelid='paper_result_publication_withdrawals'::regclass""")
        )
    ).all()
    assert any(
        kind == "u" and definition == "UNIQUE (publication_id)"
        for kind, definition in constraints
    )
    assert sum(kind == "f" for kind, _ in constraints) == 2
    assert any(kind == "c" and "500" in definition for kind, definition in constraints)
    _, review = await approval(c)
    pub = await publish(c, review.latest_approved_revision_id)
    assert (await withdraw(c, pub, "verify"))["reason"] == "verify"


async def test_v1_ledger_history_remains_readable_and_unchanged(execution_database):
    c = execution_database
    await historical_completion(c, days=90)
    await c[0].execute(
        text("""INSERT INTO scan_storage_retention_entries
      (id,scan_batch_id,target_kind,batch_artifact_id,policy_version,eligible_at)
      VALUES (:id,:batch,'source_artifact',:batch_artifact,'paper_storage_retention.v1',clock_timestamp()-interval '1 day')"""),
        {**c[2], "id": uuid4()},
    )
    assert (await sweeper(c).run(dry_run=False))["purged"] == 3
    entries = await ledger(c)
    assert (
        next(e for e in entries if e["target_kind"] == "source_artifact")[
            "policy_version"
        ]
        == "paper_storage_retention.v1"
    )
    assert all(
        e["policy_version"] == "paper_storage_retention.v2"
        for e in entries
        if e["target_kind"] == "page_render"
    )


async def wait_blocked(observer, blocker, waiter):
    async with asyncio.timeout(10):
        while blocker not in await observer.scalar(
            text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter}
        ):
            await asyncio.sleep(
                0
            )  # Yield only; correctness depends on PostgreSQL lock state.


@pytest.mark.parametrize("winner", ["withdraw", "publish", "finalize", "duplicate"])
async def test_real_withdraw_publish_finalize_duplicate_races(
    concurrent_database, winner
):
    c, url = concurrent_database
    ids = c[2]
    run, ra = await approval(c)
    a = await publish(c, ra.latest_approved_revision_id)
    edit = await call(c, run, "edit", edited(ra, summary="Next"), ra.version)
    rb = await call(
        c, run, "approve", PaperReviewApproval(expected_version=edit.version)
    )
    _, other = await approval(c, "b")
    await publish(c, other.latest_approved_revision_id, "b")
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second:
            p1 = await first.scalar(text("SELECT pg_backend_pid()"))
            p2 = await second.scalar(text("SELECT pg_backend_pid()"))
            r1, r2 = (
                PaperResultPublicationRepository(first),
                PaperResultPublicationRepository(second),
            )
            if winner in {"withdraw", "duplicate"}:
                await r1.withdraw(
                    ids["paper_a"],
                    a["id"],
                    "Correction",
                    ids["teacher"],
                    request_key=str(uuid4()),
                )
            elif winner == "publish":
                await r1.publish(
                    ids["paper_a"],
                    rb.latest_approved_revision_id,
                    ids["teacher"],
                    request_key=str(uuid4()),
                )
            else:
                from app.application.paper_batch_orchestration import (
                    PaperBatchOrchestrationService,
                )
                from app.infrastructure.paper_batch_orchestration_repository import (
                    PaperBatchOrchestrationRepository,
                )

                await PaperBatchOrchestrationService(
                    PaperBatchOrchestrationRepository(first)
                ).finalize(ids["batch"], ids["teacher"])

            async def contender():
                try:
                    result = (
                        (
                            await r2.publish(
                                ids["paper_a"],
                                rb.latest_approved_revision_id,
                                ids["teacher"],
                                request_key=str(uuid4()),
                            )
                        )
                        if winner == "withdraw"
                        else (
                            await r2.withdraw(
                                ids["paper_a"],
                                a["id"],
                                "Correction",
                                ids["teacher"],
                                request_key=str(uuid4()),
                            )
                        )
                    )
                    await second.commit()
                    return result
                except PaperPublicationError as exc:
                    await second.rollback()
                    return exc.code

            task = asyncio.create_task(contender())
            await wait_blocked(first, p1, p2)
            await first.commit()
            result = await asyncio.wait_for(task, 10)
            history = await r2.history(ids["paper_a"])
            assert sum(h["active"] for h in history) == (
                0 if winner in {"duplicate", "finalize"} else 1
            )
            assert all(not (h["active"] and h["is_withdrawn"]) for h in history)
            if winner == "publish":
                assert result == "paper_publication_not_active"
            else:
                assert history[0]["is_withdrawn"]
            if winner == "finalize":
                assert (
                    await second.scalar(
                        text(
                            "SELECT status FROM assessment_scan_batches WHERE id=:batch"
                        ),
                        ids,
                    )
                    == "completed"
                )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
