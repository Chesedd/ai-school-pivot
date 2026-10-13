"""Production audit regressions: committed bytes and private failure boundaries."""

import os
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text

from app.application.paper_checking_intake import PaperCheckingIntakeService
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.scan_page_pipeline import ScanPagePipeline
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_operational_limits_database import (
    scan_intake as scan_intake,  # noqa: PLC0414
)
from tests.integration.test_paper_operational_limits_database import (
    upload,
)
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)
from tests.integration.test_scan_intake_database import create_batch
from tests.integration.test_scan_storage_retention_database import http_app

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


async def test_response_refresh_failure_preserves_committed_source(
    scan_intake, tmp_path, monkeypatch
):
    connection, session, _, svc, ids = scan_intake
    batch = await create_batch(svc, ids)
    storage = FilesystemArtifactStorage(str(tmp_path))
    monkeypatch.setattr(
        session,
        "refresh",
        AsyncMock(side_effect=RuntimeError("response read interrupted")),
    )
    with pytest.raises(RuntimeError, match="response read interrupted"):
        await upload(ScanPagePipeline(session, storage), ids, batch.id)
    reference = await connection.scalar(
        text(
            "SELECT i.storage_reference FROM scan_batch_artifacts a "
            "JOIN input_artifacts i ON i.id=a.input_artifact_id WHERE a.batch_id=:batch"
        ),
        {"batch": batch.id},
    )
    assert reference is not None
    assert await storage.read(reference)


async def test_partial_storage_write_cleans_unknown_reference(tmp_path, monkeypatch):
    original = Path.write_bytes

    def interrupted(path, content):
        original(path, content[:3])
        raise OSError("disk write failed")

    monkeypatch.setattr(Path, "write_bytes", interrupted)
    with pytest.raises(OSError, match="disk write failed"):
        await FilesystemArtifactStorage(str(tmp_path)).store(
            b"private-paper", "image/png"
        )
    assert not list(tmp_path.iterdir())


async def test_unexpected_intake_failure_does_not_serialize_sql(
    execution_database, monkeypatch
):
    app, _ = http_app(execution_database)
    monkeypatch.setattr(
        PaperCheckingIntakeService,
        "create",
        AsyncMock(side_effect=RuntimeError("SQL parameters: private-storage-ref")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        response = await client.post(
            f"/api/assessment-core/paper-submissions/{execution_database[2]['paper_a']}/check-runs",
            json={
                "request_key": "audit",
                "routing_version": "1",
                "checker_set_version": "1",
                "threshold_policy_version": "1",
                "prompt_model_policy_version": "1",
            },
        )
    assert response.status_code == 500
    assert "private-storage-ref" not in response.text
    assert "SQL parameters" not in response.text


async def matching_service(c, provider):
    from uuid import uuid4

    from app.application.scan_intake import ScanIntakeService
    from app.application.scan_matching_service import ScanMatchingService
    from app.infrastructure.scan_intake_repository import SqlAlchemyScanIntakeRepository
    from app.presentation.scan_intake_routes import Access

    db = c[1]()
    batch = await ScanIntakeService(
        SqlAlchemyScanIntakeRepository(db), Access(db, False)
    ).create_batch(
        assignment_id=c[2]["assignment"],
        instruction_text="Audit marking",
        request_key=uuid4().hex,
        actor_user_id=c[2]["teacher"],
    )
    from tests.integration.test_paper_checking_execution_database import PNG

    await ScanPagePipeline(db, c[3]).upload(
        batch_id=batch.id,
        actor_id=c[2]["teacher"],
        content=PNG,
        claimed_mime_type="image/png",
        upload_position=0,
        filename="paper.png",
    )
    await ScanPagePipeline(db, c[3]).extract(
        batch_id=batch.id, actor_id=c[2]["teacher"]
    )
    return db, batch, ScanMatchingService(db, c[3], provider)


async def test_matching_provider_failure_settles_without_open_transaction(
    execution_database,
):
    from app.application.authoring import FailureCode, ProviderFailure
    from app.application.scan_matching_service import ScanMatchingError

    class Provider:
        provider_id = "audit"
        model_id = "audit"

        async def match(self, request, content):
            assert not db.in_transaction()
            raise ProviderFailure(FailureCode.TIMEOUT)

    db, batch, service = await matching_service(execution_database, Provider())
    try:
        with pytest.raises(ScanMatchingError, match="timeout"):
            await service.run(batch.id, execution_database[2]["teacher"])
        rows = (
            await db.execute(
                text(
                    "SELECT r.status,c.status FROM scan_matching_runs r JOIN scan_matching_chunks c "
                    "ON c.matching_run_id=r.id WHERE r.batch_id=:batch"
                ),
                {"batch": batch.id},
            )
        ).all()
        assert rows == [("failed_retryable", "failed_retryable")]
    finally:
        await db.close()


async def test_five_paper_failure_isolation_with_manual_republish_remediation_and_retention(
    tmp_path,
):
    from decimal import Decimal
    from uuid import uuid4

    from app.application.paper_checking_execution import PaperCheckingExecutionError
    from app.application.paper_checking_review import PaperReviewApproval
    from app.presentation.remediation_schemas import ItemInput
    from tests.integration.test_manual_paper_review_database import grade, manual
    from tests.integration.test_paper_batch_orchestration_database import action
    from tests.integration.test_paper_checking_execution_database import (
        PNG,
        FakePaperProvider,
        execution_database,
        service,
        start,
    )
    from tests.integration.test_paper_checking_review_database import call
    from tests.integration.test_paper_publication_withdrawal_database import withdraw
    from tests.integration.test_paper_remediation_database import create, invoke
    from tests.integration.test_paper_result_publication_database import (
        approval,
        publish,
        repo_call,
    )
    from tests.integration.test_scan_storage_retention_database import (
        historical_completion,
        sweeper,
    )

    # Build a genuine five-page source PDF for this confirmed fixture.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R 6 0 R 7 0 R] /Count 5 >>",
    ]
    objects.extend([b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1 1] >>"] * 5)
    body, offsets = b"%PDF-1.4\n", [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + value + b"\nendobj\n"
    startxref = len(body)
    body += b"xref\n0 8\n0000000000 65535 f \n"
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    body += (
        f"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode()
    )
    seed = execution_database.__wrapped__(
        tmp_path, three_papers=True, source_content=body
    )
    c = await anext(seed)
    try:
        ids = c[2]
        for letter, index in (("d", 3), ("e", 4)):
            for key in ("student", "participant", "paper", "page", "render", "entry"):
                ids[f"{key}_{letter}"] = uuid4()
            ids[f"variant_{letter}"] = ids["variant_b"]
            ids[f"item_{letter}"] = ids["item_b"]
            ids[f"version_{letter}"] = ids["version_b"]
            reference = await c[3].store(PNG, "image/png")
            values = {
                **ids,
                "student": ids[f"student_{letter}"],
                "participant": ids[f"participant_{letter}"],
                "paper": ids[f"paper_{letter}"],
                "page": ids[f"page_{letter}"],
                "render": ids[f"render_{letter}"],
                "entry": ids[f"entry_{letter}"],
                "reference": reference,
                "index": index,
                "name": f"Private {letter}",
            }
            for statement in (
                "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,:name)",
                "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant,:assignment,:student,:variant_b,clock_timestamp())",
                "INSERT INTO input_artifacts(id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference) VALUES (:render,:teacher,'image/png',:hash_b,:png_size,:reference)",
                "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status) VALUES (:page,:batch_artifact,:index,:render,1,1,0,'normalized_upright_v1',:hash_b,'ready')",
                "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:entry,:revision,:participant,:index)",
                "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry,:revision,:page,0)",
                "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:paper,:batch,:revision,:assignment,:participant,:student,:variant_b,'ready_for_checking',:teacher)",
                "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper,:page,0)",
            ):
                await c[0].execute(text(statement), values)
        await c[0].execute(
            text(
                "UPDATE scan_batch_artifacts SET page_count=5 WHERE id=:batch_artifact"
            ),
            ids,
        )
        _, a = await approval(c)
        pa = await publish(c, a.latest_approved_revision_id)

        class RetryOnce(FakePaperProvider):
            attempts = 0

            async def check(self, request, content):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError("retry once")
                return await super().check(request, content)

        rb = await start(c, "b")
        provider = RetryOnce()
        await service(c, provider).execute(ids["paper_b"], rb.id)
        assert provider.attempts == 2
        br = await call(c, rb, "start", paper="b")
        br = await call(
            c,
            rb,
            "approve",
            PaperReviewApproval(expected_version=br.version),
            paper="b",
        )
        pb = await publish(c, br.latest_approved_revision_id, "b")
        rc = await start(c, "c")
        with pytest.raises(PaperCheckingExecutionError):
            await service(c, FakePaperProvider(mode="unknown_page")).execute(
                ids["paper_c"], rc.id
            )
        cr = await grade(c, await manual(c, paper="c"), score=Decimal(10), paper="c")
        pc = await publish(c, cr.latest_approved_revision_id, "c")
        _, dr = await approval(c, "d")
        pd = await publish(c, dr.latest_approved_revision_id, "d")
        await withdraw(c, pd, paper="d")
        pd2 = await publish(c, dr.latest_approved_revision_id, "d")
        assert pd2["id"] != pd["id"]
        re = await start(c, "e")
        await service(c, FakePaperProvider(status="partially_correct")).execute(
            ids["paper_e"], re.id
        )
        er = await call(c, re, "start", paper="e")
        er = await call(
            c,
            re,
            "approve",
            PaperReviewApproval(expected_version=er.version),
            paper="e",
        )
        pe = await publish(c, er.latest_approved_revision_id, "e")
        ec = (
            c[0],
            c[1],
            {
                **ids,
                "paper_a": ids["paper_e"],
                "student_a": ids["student_e"],
                "item_a": ids["item_e"],
            },
            c[3],
        )
        plan = await create(
            ec,
            pe,
            items=[
                ItemInput(task_version_id=ids["version_a"], selection_source="manual")
            ],
        )
        assigned = await invoke(c, "assign", plan["id"], ids["teacher"], False, False)
        assert assigned["status"] == "assigned"
        await action(c, "finalize")
        await historical_completion(c)
        assert (await sweeper(c).run(dry_run=False))["purged"] == 1
        for letter, publication in zip("abcde", (pa, pb, pc, pd2, pe), strict=True):
            result = await repo_call(
                c, "student_result", ids[f"paper_{letter}"], ids[f"student_{letter}"]
            )
            assert result["id"] == publication["id"]
        assert await c[0].scalar(text("SELECT count(*) FROM paper_ai_revisions")) == 4
    finally:
        await seed.aclose()


async def test_matching_completion_cannot_reactivate_cancelled_batch(
    execution_database,
):
    from decimal import Decimal

    from app.application.scan_checking_contracts import (
        PageMatchingResponse,
        PageMatchResult,
    )
    from app.application.scan_matching import MatchingTelemetry
    from app.application.scan_matching_service import ScanMatchingError

    class Provider:
        provider_id = "audit"
        model_id = "audit"

        async def match(self, request, content):
            # Separate service transaction commits the teacher cancellation while
            # the provider response is in flight.
            async with execution_database[1]() as cancel_db:
                from app.application.scan_intake import ScanIntakeService
                from app.infrastructure.scan_intake_repository import (
                    SqlAlchemyScanIntakeRepository,
                )
                from app.presentation.scan_intake_routes import Access

                await ScanIntakeService(
                    SqlAlchemyScanIntakeRepository(cancel_db), Access(cancel_db, False)
                ).cancel_batch(
                    batch_id=batch.id,
                    actor_user_id=execution_database[2]["teacher"],
                    reason="Stop",
                )
            return PageMatchingResponse(
                pages=tuple(
                    PageMatchResult(
                        page_token=p.page_token,
                        disposition="unmatched",
                        confidence=Decimal(0),
                        evidence_code="insufficient_evidence",
                        evidence_summary="No identity",
                        requires_human_review=True,
                    )
                    for p in request.pages
                )
            ), MatchingTelemetry(None)

    db, batch, service = await matching_service(execution_database, Provider())
    try:
        with pytest.raises(
            ScanMatchingError, match="scan_batch_not_ready_for_matching"
        ):
            await service.run(batch.id, execution_database[2]["teacher"])
        assert (
            await db.scalar(
                text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
                {"id": batch.id},
            )
            == "cancelled"
        )
    finally:
        await db.close()


async def test_default_global_twelve_and_batch_three_concurrent_claims(
    concurrent_database,
):
    import asyncio
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.paper_checking_execution import PaperCheckingExecutionError
    from app.infrastructure.paper_checking_execution_repository import (
        SQLAlchemyPaperCheckingExecutionRepository,
    )
    from tests.integration.test_paper_checking_execution_database import start
    from tests.integration.test_paper_operational_limits_database import new_batch

    c, url = concurrent_database
    contexts = [c]
    # Two papers per batch: seven independent batches compete for twelve slots.
    for _ in range(6):
        batch_id = await new_batch(c)
        ids = {
            **c[2],
            "batch": batch_id,
            "revision": uuid4(),
            "batch_artifact": uuid4(),
        }
        for letter in ("a", "b"):
            for name in ("paper", "entry", "page"):
                ids[f"{name}_{letter}"] = uuid4()
        await c[0].execute(
            text(
                "UPDATE assessment_scan_batches SET status='ready_for_checking',matching_revision=1 WHERE id=:batch"
            ),
            ids,
        )
        await c[0].execute(
            text(
                "INSERT INTO scan_batch_artifacts(id,batch_id,input_artifact_id,upload_position,original_filename,extraction_status,page_count) VALUES (:batch_artifact,:batch,:source,0,'paper.pdf','completed',2)"
            ),
            ids,
        )
        await c[0].execute(
            text(
                "INSERT INTO scan_grouping_revisions(id,batch_id,revision,based_on_matching_revision,created_by_user_id,status,confirmed_at,confirmed_by_user_id) VALUES (:revision,:batch,1,1,:teacher,'confirmed',clock_timestamp(),:teacher)"
            ),
            ids,
        )
        for letter, index in (("a", 0), ("b", 1)):
            params = {
                **ids,
                "paper": ids[f"paper_{letter}"],
                "entry": ids[f"entry_{letter}"],
                "page": ids[f"page_{letter}"],
                "participant": ids[f"participant_{letter}"],
                "student": ids[f"student_{letter}"],
                "variant": ids[f"variant_{letter}"],
                "render": ids[f"render_{letter}"],
                "hash": ids[f"hash_{letter}"],
                "index": index,
            }
            for statement in (
                "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,coordinate_space_version,content_fingerprint,status) VALUES (:page,:batch_artifact,:index,:render,1,1,'normalized_upright_v1',:hash,'ready')",
                "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:entry,:revision,:participant,:index)",
                "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry,:revision,:page,0)",
                "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,created_by_user_id) VALUES (:paper,:batch,:revision,:assignment,:participant,:student,:variant,:teacher)",
                "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper,:page,0)",
            ):
                await c[0].execute(text(statement), params)
        contexts.append((c[0], c[1], ids, c[3]))
    runs = [
        (ctx, letter, await start(ctx, letter))
        for ctx in contexts
        for letter in ("a", "b")
    ]
    await c[0].commit()
    engine = create_async_engine(url, pool_size=16)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SQLAlchemyPaperCheckingExecutionRepository(factory)
    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                *(
                    repo.prepare(ctx[2][f"paper_{letter}"], run.id, "audit", "audit")
                    for ctx, letter, run in runs
                ),
                return_exceptions=True,
            ),
            30,
        )
        winners = [o for o in outcomes if not isinstance(o, Exception)]
        losers = [o for o in outcomes if isinstance(o, Exception)]
        assert len(winners) == 12 and len(losers) == 2
        assert all(
            isinstance(o, PaperCheckingExecutionError)
            and o.code == "paper_check_capacity_unavailable"
            for o in losers
        )
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM model_runs WHERE status='running'")
                )
                == 12
            )
            assert await db.scalar(text("SELECT sum(retry_count) FROM check_runs")) == 0
            assert (
                await db.scalar(
                    text(
                        "SELECT max(n) FROM (SELECT count(*) n FROM model_runs m JOIN paper_submissions p ON p.id=m.paper_submission_id WHERE m.status='running' GROUP BY p.batch_id) x"
                    )
                )
                <= 3
            )
        for winner in winners:
            await repo.fail(winner, "semantic_invalid", None)
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM model_runs WHERE status='running'")
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_provider_success_then_finalize_interruption_reclaims_one_academic_result(
    execution_database,
):
    from unittest.mock import AsyncMock

    from app.infrastructure.paper_checking_execution_repository import (
        SQLAlchemyPaperCheckingExecutionRepository,
    )
    from tests.integration.test_paper_checking_execution_database import (
        FakePaperProvider,
        start,
    )

    c = execution_database
    run = await start(c)
    repo = SQLAlchemyPaperCheckingExecutionRepository(c[1])
    # Existing stale-claim fixture pattern ages the interrupted attempt's insert.
    await c[0].execute(
        text(
            "ALTER TABLE model_runs ALTER COLUMN started_at SET DEFAULT (clock_timestamp() - interval '5 minutes')"
        )
    )
    old = await repo.prepare(
        c[2]["paper_a"], run.id, "acceptance-fake", "deterministic-vision"
    )
    await c[0].execute(
        text(
            "ALTER TABLE model_runs ALTER COLUMN started_at SET DEFAULT clock_timestamp()"
        )
    )
    provider = FakePaperProvider()
    response, telemetry = await provider.check(old.compiled.request, {})
    original_succeed = repo.succeed
    repo.succeed = AsyncMock(side_effect=RuntimeError("finalize interrupted"))
    with pytest.raises(RuntimeError, match="finalize interrupted"):
        await repo.succeed(old, response, telemetry)
    repo.succeed = original_succeed
    assert (
        await c[0].scalar(
            text("SELECT count(*) FROM paper_ai_revisions WHERE check_run_id=:id"),
            {"id": run.id},
        )
        == 0
    )
    new = await repo.prepare(
        c[2]["paper_a"], run.id, provider.provider_id, provider.model_id
    )
    response, telemetry = await provider.check(new.compiled.request, {})
    result = await repo.succeed(new, response, telemetry)
    replay = await repo.prepare(
        c[2]["paper_a"], run.id, provider.provider_id, provider.model_id
    )
    assert replay.replay == result
    assert len(provider.calls) == 2
    assert (
        await c[0].scalar(
            text("SELECT count(*) FROM paper_ai_revisions WHERE check_run_id=:id"),
            {"id": run.id},
        )
        == 1
    )
    assert (
        await c[0].scalar(
            text("SELECT count(*) FROM check_results WHERE check_run_id=:id"),
            {"id": run.id},
        )
        == 1
    )


async def test_retention_claim_then_publication_has_no_fk_lock_cycle(
    concurrent_database,
):
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.application.paper_result_publication import PaperPublicationError
    from app.application.scan_storage_retention import PaperRetentionSettings
    from app.infrastructure.paper_result_publication_repository import (
        PaperResultPublicationRepository,
    )
    from app.infrastructure.scan_storage_retention_repository import (
        ScanStorageRetentionRepository,
    )
    from tests.integration.test_paper_publication_withdrawal_database import (
        wait_blocked,
    )
    from tests.integration.test_paper_result_publication_database import approval
    from tests.integration.test_scan_storage_retention_database import (
        historical_completion,
    )

    c, url = concurrent_database
    _, review = await approval(c)
    await historical_completion(c)
    await c[0].commit()
    engine = create_async_engine(url)
    page_locked, release, publisher_started = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    pids = {}

    class PausedSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            result = await super().scalar(statement, *args, **kwargs)
            if (
                str(statement).startswith("SELECT id FROM scan_pages WHERE")
                and result is not None
            ):
                pids["retention"] = await super().scalar(
                    text("SELECT pg_backend_pid()")
                )
                page_locked.set()
                await asyncio.wait_for(release.wait(), 10)
            return result

    ordinary = async_sessionmaker(engine, expire_on_commit=False)
    paused = async_sessionmaker(engine, class_=PausedSession, expire_on_commit=False)
    repo = ScanStorageRetentionRepository(paused)
    settings = PaperRetentionSettings()
    target = next(
        t for t in await repo.discover(settings) if t.target_id == c[2]["page_a"]
    )
    retaining = asyncio.create_task(repo.claim(target, settings))

    async def publish():
        async with ordinary() as db, db.begin():
            pids["publication"] = await db.scalar(text("SELECT pg_backend_pid()"))
            publisher_started.set()
            try:
                await PaperResultPublicationRepository(db, c[3]).publish(
                    c[2]["paper_a"],
                    review.latest_approved_revision_id,
                    c[2]["teacher"],
                    request_key="lock-cycle",
                )
            except PaperPublicationError as exc:
                await db.rollback()
                return exc.code
            return "published"

    publishing = None
    try:
        await asyncio.wait_for(page_locked.wait(), 10)
        publishing = asyncio.create_task(publish())
        await asyncio.wait_for(publisher_started.wait(), 10)
        async with engine.connect() as observer:
            await wait_blocked(observer, pids["retention"], pids["publication"])
        release.set()
        claim, outcome = await asyncio.wait_for(
            asyncio.gather(retaining, publishing), 15
        )
        assert claim is not None
        assert outcome == "paper_publication_pages_unavailable"
    finally:
        release.set()
        await asyncio.gather(
            retaining, *([publishing] if publishing else []), return_exceptions=True
        )
        await engine.dispose()


async def test_execute_route_releases_authorization_transaction(
    execution_database, monkeypatch
):
    from uuid import uuid4

    from app.application.paper_checking_execution import PaperCheckingExecutionService
    from app.db.session import get_session
    from tests.integration.test_paper_checking_execution_database import (
        FakePaperProvider,
    )

    app, _ = http_app(execution_database)
    app.state.paper_checking_provider = FakePaperProvider()
    captured = {}

    async def session():
        async with execution_database[1]() as db:
            captured["db"] = db
            yield db

    async def execute(self, paper_id, run_id):
        assert not captured["db"].in_transaction()
        return {"status": "completed_with_review_required"}

    app.dependency_overrides[get_session] = session
    monkeypatch.setattr(PaperCheckingExecutionService, "execute", execute)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        response = await client.post(
            f"/api/assessment-core/paper-submissions/{execution_database[2]['paper_a']}/check-runs/{uuid4()}/execute"
        )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "table,expected",
    [
        (
            "paper_ai_revisions",
            {"ck_paper_ai_revisions_review", "ck_paper_ai_revisions_hashes"},
        ),
        ("paper_check_finding_regions", {"ck_paper_finding_region_coordinate_space"}),
        ("paper_submission_pages", {"ck_paper_submission_pages_order"}),
        (
            "scan_storage_retention_entries",
            {"ck_scan_retention_target", "ck_scan_retention_state"},
        ),
    ],
)
async def test_orm_declares_existing_postgres_paper_checks(
    execution_database, table, expected
):
    from sqlalchemy import CheckConstraint

    from app.infrastructure.models import Base

    orm = {
        c.name
        for c in Base.metadata.tables[table].constraints
        if isinstance(c, CheckConstraint)
    }
    actual = set(
        (
            await execution_database[0].execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid=CAST(:table AS regclass) AND contype='c'"
                ),
                {"table": table},
            )
        ).scalars()
    )
    assert expected <= orm
    assert expected <= actual
    if table == "scan_storage_retention_entries":
        assert Base.metadata.tables[table].c.id.server_default is None
        assert (
            await execution_database[0].scalar(
                text(
                    "SELECT column_default FROM information_schema.columns WHERE table_schema='public' AND table_name=:table AND column_name='id'"
                ),
                {"table": table},
            )
            is None
        )


async def test_batch_create_replay_requires_current_class_membership(scan_intake):
    from uuid import uuid4

    from app.application.scan_intake import ScanIntakeError

    connection, _, _, svc, ids = scan_intake
    key = uuid4().hex
    batch = await create_batch(svc, ids, key=key)
    await connection.execute(
        text(
            "DELETE FROM class_group_teachers WHERE class_group_id=:group AND teacher_user_id=:teacher"
        ),
        ids,
    )
    with pytest.raises(ScanIntakeError, match="scan_batch_not_found"):
        await create_batch(svc, ids, key=key)
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM assessment_scan_batches WHERE id=:id"),
            {"id": batch.id},
        )
        == 1
    )


@pytest.mark.parametrize("stage", ["upload", "extraction"])
async def test_lost_commit_acknowledgement_preserves_database_referenced_bytes(
    scan_intake, tmp_path, monkeypatch, stage
):
    connection, session, _, svc, ids = scan_intake
    b = await create_batch(svc, ids)
    storage = FilesystemArtifactStorage(str(tmp_path))
    pipeline = ScanPagePipeline(session, storage)
    if stage == "extraction":
        await upload(pipeline, ids, b.id)
    commit = session.commit
    commits = 0

    async def acknowledged_lost():
        nonlocal commits
        await commit()
        commits += 1
        # Extraction first commits the batch transition, then its rendered pages.
        if stage == "upload" or commits == 2:
            raise RuntimeError("commit acknowledgement lost")

    monkeypatch.setattr(session, "commit", acknowledged_lost)
    with pytest.raises(RuntimeError, match="commit acknowledgement lost"):
        if stage == "upload":
            await upload(pipeline, ids, b.id)
        else:
            await pipeline.extract(batch_id=b.id, actor_id=ids["teacher"])
    references = list(
        (
            await connection.execute(
                text(
                    "SELECT i.storage_reference FROM input_artifacts i WHERE i.id IN "
                    "(SELECT input_artifact_id FROM scan_batch_artifacts WHERE batch_id=:batch "
                    "UNION ALL SELECT derived_render_artifact_id FROM scan_pages p JOIN scan_batch_artifacts a ON a.id=p.batch_artifact_id WHERE a.batch_id=:batch)"
                ),
                {"batch": b.id},
            )
        ).scalars()
    )
    assert len(references) == (1 if stage == "upload" else 2)
    assert all([await storage.read(ref) for ref in references])


async def test_active_publication_protects_pages_independent_of_grouping_status(
    execution_database,
):
    from tests.integration.test_paper_result_publication_database import (
        approval,
        publish,
    )
    from tests.integration.test_scan_storage_retention_database import (
        historical_completion,
        sweeper,
        targets,
    )

    c = execution_database
    _, review = await approval(c)
    publication = await publish(c, review.latest_approved_revision_id)
    await historical_completion(c)
    # PostgreSQL currently permits this historical state; an active pointer must
    # still protect its paper's bytes even if grouping metadata is inconsistent.
    await c[0].execute(
        text(
            "UPDATE scan_grouping_revisions SET status='superseded',confirmed_at=NULL,confirmed_by_user_id=NULL WHERE id=:revision"
        ),
        c[2],
    )
    assert c[2]["page_a"] not in {t.target_id for t in await targets(c)}
    assert (await sweeper(c).run(dry_run=False))["purged"] == 2
    from tests.integration.test_paper_result_publication_database import repo_call

    result = await repo_call(c, "student_result", c[2]["paper_a"], c[2]["student_a"])
    assert result["id"] == publication["id"]
    assert await repo_call(
        c, "student_page", c[2]["paper_a"], c[2]["page_a"], c[2]["student_a"], c[3]
    )
