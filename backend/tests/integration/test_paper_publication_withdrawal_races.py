"""Additional PR15 real connection races, manual and remediation acceptance."""

import asyncio
import os
import subprocess
from uuid import uuid4

import httpx
import pytest
from app.application.paper_batch_orchestration import (
    PaperBatchError,
    PaperBatchOrchestrationService,
)
from app.application.paper_result_publication import PaperPublicationError
from app.application.remediation import RemediationError
from app.application.scan_storage_retention import PaperRetentionSettings
from app.infrastructure.paper_batch_orchestration_repository import (
    PaperBatchOrchestrationRepository,
)
from app.infrastructure.paper_remediation_repository import PaperRemediationRepository
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from app.infrastructure.remediation_repository import RemediationRepository
from app.infrastructure.scan_storage_retention_repository import (
    ScanStorageRetentionRepository,
)
from app.presentation.remediation_schemas import ItemInput
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_paper_batch_orchestration_database import (
    unconfirmed_database as unconfirmed_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_publication_withdrawal_database import (
    approval,
    historical_completion,
    publish,
    repo_call,
    seed_historical_withdrawal,
    wait_blocked,
    withdraw,
)
from tests.integration.test_paper_remediation_database import (
    create,
    invoke,
    payload,
    published,
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


@pytest.mark.parametrize("endpoint", ["selected", "bulk"])
@pytest.mark.parametrize("case", ["valid", "cross_paper", "outside_teacher", "blank"])
async def test_bulk_http_atomic_authorization(execution_database, endpoint, case):
    c, ids = execution_database, execution_database[2]
    _, ra = await approval(c)
    _, rb = await approval(c, "b")
    a = await publish(c, ra.latest_approved_revision_id)
    b = await publish(c, rb.latest_approved_revision_id, "b")
    app, identity = http_app(c)
    if case == "outside_teacher":
        identity.update(user_id=uuid4())
    body = {
        "reason": " " if case == "blank" else "Correction",
        "entries": [
            {
                "paper_submission_id": str(ids["paper_a"]),
                "publication_id": str(a["id"]),
            },
            {
                "paper_submission_id": str(ids["paper_b"]),
                "publication_id": str(a["id"] if case == "cross_paper" else b["id"]),
            },
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": "withdraw"},
    ) as client:
        response = await client.post(
            f"/api/assessment-core/scan-batches/{ids['batch']}/publications/withdraw/{endpoint}",
            json=body,
        )
        expected = {
            "valid": 200,
            "cross_paper": 404,
            "outside_teacher": 404,
            "blank": 422,
        }[case]
        assert response.status_code == expected
        assert await c[0].scalar(
            text("SELECT count(*) FROM paper_result_publication_withdrawals")
        ) == (2 if case == "valid" else 0)
        for paper in ("a", "b"):
            assert (await repo_call(c, "history", ids[f"paper_{paper}"]))[0][
                "active"
            ] == (case != "valid")


async def test_group_confirmation_through_withdrawal_republish(unconfirmed_database):
    from tests.integration.test_paper_batch_orchestration_database import (
        test_grouping_through_execution_review_publication_and_finalization,
    )

    c = unconfirmed_database
    await test_grouping_through_execution_review_publication_and_finalization(c)
    a = (await repo_call(c, "history", c[2]["paper_a"]))[-1]
    await withdraw(c, a)
    with pytest.raises(PaperPublicationError, match="paper_result_not_found"):
        await repo_call(c, "student_result", c[2]["paper_a"], c[2]["student_a"])
    b = await publish(c, a["approved_review_revision_id"])
    assert b["id"] != a["id"]
    assert await repo_call(
        c, "student_page", c[2]["paper_a"], c[2]["page_a"], c[2]["student_a"], c[3]
    )


async def test_manual_withdrawal_and_same_revision_republish(execution_database):
    from tests.integration.test_manual_paper_review_database import (
        grade,
        manual,
        policies,
    )

    c = execution_database
    await policies(c)
    review = await grade(c, await manual(c))
    a = await publish(c, review.latest_approved_revision_id)
    await withdraw(c, a)
    b = await publish(c, review.latest_approved_revision_id)
    assert a["id"] != b["id"] and a["result"] == b["result"]


async def test_draft_stale_and_new_plan_uses_B(execution_database):
    c, ids = execution_database, execution_database[2]
    _, review, a, _ = await published(c)
    draft = await create(
        c,
        a,
        items=[ItemInput(task_version_id=ids["version_b"], selection_source="manual")],
    )
    await withdraw(c, a)
    with pytest.raises(RemediationError, match="publication_stale"):
        await invoke(c, "assign", draft["id"], ids["teacher"], False, False)
    with pytest.raises(RemediationError, match="publication_stale"):
        async with c[1]() as db, db.begin():
            await PaperRemediationRepository(db).create(
                ids["teacher"],
                False,
                ids["paper_a"],
                payload(c, a).model_copy(update={"creation_key": "new-old-source"}),
                RemediationRepository(db),
            )
    b = await publish(c, review.latest_approved_revision_id)
    async with c[1]() as db, db.begin():
        new = await PaperRemediationRepository(db).create(
            ids["teacher"],
            False,
            ids["paper_a"],
            payload(c, b).model_copy(update={"creation_key": "new-B-source"}),
            RemediationRepository(db),
        )
    assert new["source_paper_publication_id"] == b["id"]
    assert (
        await c[0].scalar(
            text(
                "SELECT source_paper_publication_id FROM remediation_plans WHERE id=:id"
            ),
            {"id": draft["id"]},
        )
        == a["id"]
    )


async def test_assigned_plan_survives_withdrawal_and_checks(
    execution_database, monkeypatch
):
    # Reuse the full PR11 execution/checking acceptance, changing its lifecycle
    # action from correction publication to withdrawal. All assertions remain.
    from tests.integration import test_paper_remediation_database as pr11

    async def withdrawal_replacement(c, run, review):
        history = await repo_call(c, "history", c[2]["paper_a"])
        await withdraw(c, history[-1])

    monkeypatch.setattr(pr11, "replacement", withdrawal_replacement)
    await pr11.test_assigned_plan_survives_republication_and_executes(
        execution_database
    )


@pytest.mark.parametrize("operation", ["create", "assign"])
@pytest.mark.parametrize("withdraw_first", [True, False])
async def test_real_remediation_withdrawal_races(
    concurrent_database, operation, withdraw_first
):
    c, url = concurrent_database
    ids = c[2]
    _, _, a, _ = await published(c)
    draft = (
        await create(
            c,
            a,
            items=[
                ItemInput(task_version_id=ids["version_b"], selection_source="manual")
            ],
        )
        if operation == "assign"
        else None
    )
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second:
            p1 = await first.scalar(text("SELECT pg_backend_pid()"))
            p2 = await second.scalar(text("SELECT pg_backend_pid()"))

            async def remediate(db):
                repo = RemediationRepository(db)
                if operation == "assign":
                    return await repo.assign(draft["id"], ids["teacher"], False, False)
                return await PaperRemediationRepository(db).create(
                    ids["teacher"], False, ids["paper_a"], payload(c, a), repo
                )

            async def do_withdraw(db):
                return await PaperResultPublicationRepository(db).withdraw(
                    ids["paper_a"],
                    a["id"],
                    "Correction",
                    ids["teacher"],
                    request_key=str(uuid4()),
                )

            await (do_withdraw(first) if withdraw_first else remediate(first))

            async def contender():
                try:
                    result = await (
                        remediate(second) if withdraw_first else do_withdraw(second)
                    )
                    await second.commit()
                    return result
                except RemediationError as exc:
                    await second.rollback()
                    return exc.code

            task = asyncio.create_task(contender())
            await wait_blocked(first, p1, p2)
            await first.commit()
            result = await asyncio.wait_for(task, 10)
            if withdraw_first:
                assert result == "remediation_source_publication_stale"
            elif operation == "assign":
                assert (
                    await RemediationRepository(second).student_detail(
                        draft["id"], ids["student_a"]
                    )
                )["status"] == "assigned"
                # Assigned provenance remains A; execution does not consult active state.
                assert (
                    await second.scalar(
                        text(
                            "SELECT source_paper_publication_id FROM remediation_plans WHERE id=:id"
                        ),
                        {"id": draft["id"]},
                    )
                    == a["id"]
                )
            else:
                assert (
                    await second.scalar(
                        text(
                            "SELECT count(*) FROM remediation_plans WHERE source_paper_publication_id=:pub AND status='draft'"
                        ),
                        {"pub": a["id"]},
                    )
                    == 1
                )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


async def test_real_withdraw_first_blocks_finalize_and_student_read_is_atomic(
    concurrent_database,
):
    c, url = concurrent_database
    ids = c[2]
    _, ra = await approval(c)
    _, rb = await approval(c, "b")
    a = await publish(c, ra.latest_approved_revision_id)
    await publish(c, rb.latest_approved_revision_id, "b")
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with factory() as first, factory() as second, factory() as reader:
            p1 = await first.scalar(text("SELECT pg_backend_pid()"))
            p2 = await second.scalar(text("SELECT pg_backend_pid()"))
            await PaperResultPublicationRepository(first).withdraw(
                ids["paper_a"],
                a["id"],
                "Secret",
                ids["teacher"],
                request_key=str(uuid4()),
            )
            repo = PaperResultPublicationRepository(reader)
            assert (await repo.student_result(ids["paper_a"], ids["student_a"]))[
                "id"
            ] == a["id"]
            assert await repo.student_page(
                ids["paper_a"], ids["page_a"], ids["student_a"], c[3]
            )

            async def finalize():
                try:
                    await PaperBatchOrchestrationService(
                        PaperBatchOrchestrationRepository(second)
                    ).finalize(ids["batch"], ids["teacher"])
                except PaperBatchError as exc:
                    await second.rollback()
                    return exc.code
                pytest.fail("finalization should fail after withdrawal")

            task = asyncio.create_task(finalize())
            await wait_blocked(first, p1, p2)
            await first.commit()
            assert (
                await asyncio.wait_for(task, 10)
                == "paper_batch_publications_incomplete"
            )
            with pytest.raises(PaperPublicationError, match="paper_result_not_found"):
                await repo.student_result(ids["paper_a"], ids["student_a"])
            with pytest.raises(PaperPublicationError, match="paper_result_not_found"):
                await repo.student_page(
                    ids["paper_a"], ids["page_a"], ids["student_a"], c[3]
                )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.parametrize("winner", ["publish", "retention", "withdraw"])
async def test_real_storage_retention_races(concurrent_database, winner):
    c, url = concurrent_database
    ids = c[2]
    _, review = await approval(c)
    a = await publish(c, review.latest_approved_revision_id)
    await historical_completion(c, days=90)
    if winner != "withdraw":
        await seed_historical_withdrawal(c, a, 31)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    retention = ScanStorageRetentionRepository(factory)
    settings = PaperRetentionSettings()
    try:
        if winner == "withdraw":
            async with factory() as db:
                await PaperResultPublicationRepository(db).withdraw(
                    ids["paper_a"],
                    a["id"],
                    "Correction",
                    ids["teacher"],
                    request_key=str(uuid4()),
                )
                assert ids["page_a"] not in {
                    t.target_id for t in await retention.discover(settings)
                }
                await db.commit()
                assert ids["page_a"] not in {
                    t.target_id for t in await retention.discover(settings)
                }
                assert (
                    await db.scalar(
                        text(
                            "SELECT count(*) FROM scan_storage_retention_entries WHERE scan_page_id=:page_a"
                        ),
                        ids,
                    )
                    == 0
                )
        else:
            target = next(
                t
                for t in await retention.discover(settings)
                if t.target_id == ids["page_a"]
            )
            if winner == "publish":
                async with factory() as db:
                    b = await PaperResultPublicationRepository(db).publish(
                        ids["paper_a"],
                        review.latest_approved_revision_id,
                        ids["teacher"],
                        request_key=str(uuid4()),
                    )
                    assert (
                        await retention.claim(target, settings) is None
                    )  # Publication holds the page lock.
                    await db.commit()
                    assert (
                        await retention.claim(target, settings) is None
                    )  # Final SQL recheck sees B.
                    assert (
                        b["id"] != a["id"] and (c[3].root / ids["storage_a"]).exists()
                    )
            else:
                claim = await retention.claim(target, settings)
                assert claim
                await c[3].delete(target.storage_reference)
                assert await retention.finalize(claim, None)
                async with factory() as db:
                    with pytest.raises(
                        PaperPublicationError, match="pages_unavailable"
                    ):
                        await PaperResultPublicationRepository(db).publish(
                            ids["paper_a"],
                            review.latest_approved_revision_id,
                            ids["teacher"],
                            request_key=str(uuid4()),
                        )
                    await db.rollback()
                    assert (
                        await db.scalar(
                            text(
                                "SELECT active_publication_id FROM paper_result_publication_states WHERE paper_submission_id=:paper_a"
                            ),
                            ids,
                        )
                        is None
                    )
    finally:
        await engine.dispose()


async def test_downgrade_refuses_null_pointer_and_preserves_republish_history(
    concurrent_database,
):
    from tests.pr14_migration_acceptance import command

    c, url = concurrent_database
    _, review = await approval(c)
    a = await publish(c, review.latest_approved_revision_id)
    await withdraw(c, a)
    await c[0].commit()
    with pytest.raises(subprocess.CalledProcessError):
        await asyncio.to_thread(
            command,
            url.render_as_string(hide_password=False),
            "downgrade",
            "20261012_01",
        )
    assert (
        await c[0].scalar(text("SELECT version_num FROM alembic_version"))
        == "20261012_02"
    )
    await c[0].rollback()
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            b = await PaperResultPublicationRepository(db).publish(
                c[2]["paper_a"],
                review.latest_approved_revision_id,
                c[2]["teacher"],
                request_key=str(uuid4()),
            )
            await db.commit()
        # PR9 already represents duplicate revision publications. With an active
        # pointer downgrade is supported, dropping only withdrawal audit data.
        await asyncio.to_thread(
            command,
            url.render_as_string(hide_password=False),
            "downgrade",
            "20261012_01",
        )
        async with engine.connect() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM paper_result_publications WHERE paper_submission_id=:paper_a"
                    ),
                    c[2],
                )
                == 2
            )
            assert (
                await db.scalar(
                    text(
                        "SELECT active_publication_id FROM paper_result_publication_states WHERE paper_submission_id=:paper_a"
                    ),
                    c[2],
                )
                == b["id"]
            )
        await asyncio.to_thread(
            command, url.render_as_string(hide_password=False), "upgrade", "20261012_02"
        )
    finally:
        await engine.dispose()
