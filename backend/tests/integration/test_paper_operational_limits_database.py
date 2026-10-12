"""PR13 real PostgreSQL boundaries and committed multi-connection races."""
import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.paper_checking_execution import (
    PaperCheckingExecutionError,
    PaperCheckingExecutionService,
)
from app.application.paper_operational_limits import PaperOperationalLimits
from app.application.scan_grouping import ScanGroupingError, ScanGroupingService
from app.application.scan_intake import ScanIntakeError
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.paper_checking_execution_repository import (
    SQLAlchemyPaperCheckingExecutionRepository,
)
from app.infrastructure.scan_grouping_repository import SqlAlchemyScanGroupingRepository
from app.infrastructure.scan_page_pipeline import ScanPagePipeline
from tests.integration import test_scan_intake_database as scan_intake_tests
from tests.integration.test_manual_paper_review_database import (
    batch,
    grade,
    manual,
    policies,
)
from tests.integration.test_paper_batch_orchestration_database import action
from tests.integration.test_paper_checking_execution_database import (
    PNG,
    FakePaperProvider,
    source_pdf,
    start,
)
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_intake_database import intake, request
from tests.integration.test_paper_checking_review_database import call
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)
from tests.integration.test_paper_result_publication_database import publish
from tests.integration.test_scan_grouping_database import (
    grouping_database as grouping_database,  # noqa: PLC0414
)
from tests.integration.test_scan_intake_database import create_batch


@pytest_asyncio.fixture
async def scan_intake():
    seed = scan_intake_tests.intake.__wrapped__()
    try:
        yield await anext(seed)
    finally:
        await seed.aclose()

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required")]


def limits(**changes):
    return PaperOperationalLimits(**changes)


async def upload(pipeline, ids, batch_id, position=0, content=PNG, mime="image/png"):
    return await pipeline.upload(batch_id=batch_id, actor_id=ids["teacher"], content=content,
        claimed_mime_type=mime, upload_position=position, filename="paper")


@pytest.mark.parametrize("field,code", [
    ("max_artifacts_per_batch", "scan_batch_artifact_limit_exceeded"),
    ("max_total_upload_bytes_per_batch", "scan_batch_byte_limit_exceeded"),
])
async def test_exact_intake_limits_reject_next_without_orphans(scan_intake, tmp_path, field, code):
    connection, session, _, svc, ids = scan_intake
    b = await create_batch(svc, ids)
    settings = {"max_single_artifact_bytes": len(PNG), "max_total_upload_bytes_per_batch": 2 * len(PNG), "max_artifacts_per_batch": 2}
    settings[field] = 1 if field == "max_artifacts_per_batch" else len(PNG)
    storage = FilesystemArtifactStorage(str(tmp_path))
    pipeline = ScanPagePipeline(session, storage, limits=limits(**settings))
    before = await connection.scalar(text("SELECT count(*) FROM input_artifacts"))
    await upload(pipeline, ids, b.id)
    with pytest.raises(ScanIntakeError, match=code):
        await upload(pipeline, ids, b.id, 1)
    assert await connection.scalar(text("SELECT count(*) FROM input_artifacts")) == before + 1
    assert await connection.scalar(text("SELECT count(*) FROM scan_batch_artifacts WHERE batch_id=:id"), {"id": b.id}) == 1
    assert len(list(tmp_path.iterdir())) == 1


async def test_single_byte_exact_and_plus_one_rejection_then_valid_batch(scan_intake, tmp_path):
    _connection, session, _, svc, ids = scan_intake
    b = await create_batch(svc, ids)
    storage = FilesystemArtifactStorage(str(tmp_path))
    pipeline = ScanPagePipeline(session, storage, limits=limits(max_single_artifact_bytes=len(PNG)))
    with pytest.raises(ScanIntakeError, match="scan_artifact_too_large"):
        await upload(pipeline, ids, b.id, content=PNG + b"x")
    assert not tmp_path.exists() or not list(tmp_path.iterdir())
    await upload(pipeline, ids, b.id)
    summary = await pipeline.extract(batch_id=b.id, actor_id=ids["teacher"])
    assert summary.pages["ready"] == 1


@pytest.mark.parametrize("kind", ["pdf", "batch", "pixels"])
async def test_extraction_rejects_whole_artifact_before_render_persistence(scan_intake, tmp_path, kind):
    connection, session, _, svc, ids = scan_intake
    b = await create_batch(svc, ids)
    config = {"max_pages_per_artifact": 1} if kind == "pdf" else (
        {"max_pages_per_artifact": 1, "max_pages_per_batch": 1} if kind == "batch" else {"max_pixels_per_page": 1})
    content, mime = source_pdf(), "application/pdf"
    if kind == "pixels":
        from io import BytesIO

        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (2, 1)).save(image, format="PNG")
        content, mime = image.getvalue(), "image/png"
    storage = FilesystemArtifactStorage(str(tmp_path))
    pipeline = ScanPagePipeline(session, storage, limits=limits(**config))
    if kind == "batch":
        await upload(pipeline, ids, b.id)
        await pipeline.extract(batch_id=b.id, actor_id=ids["teacher"])
        # Return to intake in this fixture only to test a historical full batch.
        await connection.execute(text("UPDATE assessment_scan_batches SET status='uploading' WHERE id=:id"), {"id": b.id})
        session.expire_all()
        content, mime = PNG, "image/png"
    count_before = await connection.scalar(text("SELECT count(*) FROM scan_pages"))
    await upload(pipeline, ids, b.id, 1 if kind == "batch" else 0, content, mime)
    await pipeline.extract(batch_id=b.id, actor_id=ids["teacher"])
    assert await connection.scalar(text("SELECT count(*) FROM scan_pages")) == count_before
    code = {"pdf": "scan_artifact_page_limit_exceeded", "batch": "scan_batch_page_limit_exceeded", "pixels": "image_too_large"}[kind]
    assert await connection.scalar(text("SELECT failure_code FROM scan_batch_artifacts WHERE batch_id=:id ORDER BY upload_position DESC LIMIT 1"), {"id": b.id}) == code
    assert len(list(tmp_path.iterdir())) == (3 if kind == "batch" else 1)


@pytest.mark.parametrize("field,maximum,code", [
    ("max_paper_submissions_per_batch", 1, "scan_batch_paper_limit_exceeded"),
    ("max_pages_per_paper", 2, "paper_submission_page_limit_exceeded"),
])
async def test_grouping_rejection_atomic_and_exact_boundary_accepts(grouping_database, field, maximum, code):
    connection, session, _, ids = grouping_database
    svc = ScanGroupingService(SqlAlchemyScanGroupingRepository(session, limits(**{field: maximum, "max_provider_pages_per_paper": min(maximum, 20)})))
    view = await svc.initialize(ids["batch"], ids["teacher"])
    for index, page in enumerate(ids["pages"]):
        view = await svc.assign(ids["batch"], page, ids["participant_a" if index < 3 else "participant_b"], 1, view["row_version"], ids["teacher"])
    with pytest.raises(ScanGroupingError, match=code):
        await svc.confirm(ids["batch"], 1, view["row_version"], ids["teacher"])
    assert await connection.scalar(text("SELECT count(*) FROM paper_submissions WHERE batch_id=:batch"), ids) == 0
    session.expire_all()
    accepted = {field: maximum + 1, "max_provider_pages_per_paper": min(maximum + 1, 20)}
    svc = ScanGroupingService(SqlAlchemyScanGroupingRepository(session, limits(**accepted)))
    result = await svc.confirm(ids["batch"], 1, view["row_version"], ids["teacher"])
    assert result["batch_status"] == "ready_for_checking"
    assert await connection.scalar(text("SELECT count(*) FROM paper_submissions WHERE batch_id=:batch"), ids) == 2


@pytest.mark.parametrize("size,accepted", [(len(PNG), True), (len(PNG) - 1, False)])
async def test_provider_bytes_checked_before_storage_read_and_attempt(execution_database, size, accepted):
    c = execution_database
    run = await start(c)
    provider = FakePaperProvider()
    config = limits(max_provider_image_bytes_per_paper=size)
    repo = SQLAlchemyPaperCheckingExecutionRepository(c[1], config)
    class Reader:
        reads = 0
        async def read(self, reference):
            self.reads += 1
            return await c[3].read(reference)
    reader = Reader()
    svc = PaperCheckingExecutionService(repo, reader, provider, config)
    if accepted:
        await svc.execute(c[2]["paper_a"], run.id)
        assert len(provider.calls) == reader.reads == 1
    else:
        with pytest.raises(PaperCheckingExecutionError, match="paper_check_provider_payload_too_large"):
            await svc.execute(c[2]["paper_a"], run.id)
        assert len(provider.calls) == reader.reads == 0
        assert await c[0].scalar(text("SELECT count(*) FROM model_runs WHERE check_run_id=:id"), {"id": run.id}) == 0
        assert await c[0].scalar(text("SELECT retry_count FROM check_runs WHERE id=:id"), {"id": run.id}) == 0
        review = await manual(c)
        approved = await grade(c, review)
        await publish(c, approved.latest_approved_revision_id)
        from decimal import Decimal
        other = await grade(c, await manual(c, paper="b"), "correct", Decimal(20), paper="b")
        await publish(c, other.latest_approved_revision_id, "b")
        assert (await batch(c, "finalize")).batch_status == "completed"


async def test_capacity_denial_no_retry_mutation_manual_unrelated_paper_allowed(execution_database):
    c = execution_database
    run = await start(c)
    repo = SQLAlchemyPaperCheckingExecutionRepository(c[1], limits(max_parallel_paper_checks_per_batch=1))
    prepared = await repo.prepare(c[2]["paper_a"], run.id, "acceptance-fake", "deterministic-vision")
    second = await start(c, "b")
    with pytest.raises(PaperCheckingExecutionError, match="paper_check_capacity_unavailable"):
        await repo.prepare(c[2]["paper_b"], second.id, "acceptance-fake", "deterministic-vision")
    assert (await c[0].execute(text("SELECT status,retry_count FROM check_runs WHERE id=:id"), {"id": second.id})).one() == ("pending", 0)
    assert await c[0].scalar(text("SELECT count(*) FROM model_runs WHERE check_run_id=:id"), {"id": second.id}) == 0
    assert (await batch(c)).batch_status == "checking"
    await repo.fail(prepared, "semantic_invalid", None)
    assert (await manual(c)).source_kind == "manual"
    assert (await repo.prepare(c[2]["paper_b"], second.id, "acceptance-fake", "deterministic-vision")).model_run_id


async def test_stale_attempt_recovery_fences_late_result_without_consuming_budget(execution_database):
    c = execution_database
    run = await start(c)
    repo = SQLAlchemyPaperCheckingExecutionRepository(c[1])
    # Claim using database time in the past; production always calls clock_timestamp.
    original = repo.prepare
    # A clock offset on the insert default simulates an interrupted old process.
    await c[0].execute(text("ALTER TABLE model_runs ALTER COLUMN started_at SET DEFAULT (clock_timestamp() - interval '5 minutes')"))
    old = await original(c[2]["paper_a"], run.id, "acceptance-fake", "deterministic-vision")
    await c[0].execute(text("ALTER TABLE model_runs ALTER COLUMN started_at SET DEFAULT clock_timestamp()"))
    new = await original(c[2]["paper_a"], run.id, "acceptance-fake", "deterministic-vision")
    assert old.model_run_id != new.model_run_id
    provider = FakePaperProvider()
    response, telemetry = await provider.check(new.compiled.request, {})
    with pytest.raises(PaperCheckingExecutionError, match="paper_check_execution_claim_expired"):
        await repo.succeed(old, response, telemetry)
    await repo.succeed(new, response, telemetry)
    assert await c[0].scalar(text("SELECT retry_count FROM check_runs WHERE id=:id"), {"id": run.id}) == 0
    assert await c[0].scalar(text("SELECT count(*) FROM paper_ai_revisions WHERE check_run_id=:id"), {"id": run.id}) == 1


class GateProvider(FakePaperProvider):
    def __init__(self, capacity):
        super().__init__()
        self.capacity = capacity
        self.active = self.maximum = self.entered = 0
        self.full = asyncio.Event()
        self.release = asyncio.Event()

    async def check(self, value, content):
        self.active += 1
        self.entered += 1
        self.maximum = max(self.maximum, self.active)
        if self.active == self.capacity:
            self.full.set()
        try:
            await self.release.wait()
            return await super().check(value, content)
        finally:
            self.active -= 1


async def seed_ten(c):
    ids = c[2]
    for number in range(2, 10):
        params = {**ids, **{name: uuid4() for name in ("student", "participant", "page", "entry", "paper")}, "position": number}
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
            await c[0].execute(text(sql), params)
        ids[f"paper_{number}"] = params["paper"]


async def test_ten_concurrent_starts_and_execution_cap_three_then_all_publish(concurrent_database):
    c, url = concurrent_database
    await full_intake_ten(c)
    await policies(c)
    await c[0].commit()
    engine = create_async_engine(url, pool_size=12)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    live = (c[0], factory, c[2], c[3])
    labels = ["a", "b", *(str(n) for n in range(2, 10))]
    provider = GateProvider(3)
    repo = SQLAlchemyPaperCheckingExecutionRepository(factory, limits(max_parallel_paper_checks_per_batch=3))
    svc = PaperCheckingExecutionService(repo, c[3], provider)
    tasks = []
    try:
        await asyncio.wait_for(asyncio.gather(*(action(live, "start") for _ in range(10))), 20)
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type='batch.checking_started'"), c[2]) == 1
            assert await db.scalar(text("SELECT count(*) FROM check_runs r JOIN paper_submissions p ON p.id=r.paper_submission_id WHERE p.batch_id=:batch"), c[2]) == 0
        runs = [await intake(factory).create(request(c[2], label, f"pr13-{label}")) for label in labels]
        async def execute(index):
            return await svc.execute(c[2][f"paper_{labels[index]}"], runs[index].id)
        tasks = [asyncio.create_task(execute(index)) for index in range(10)]
        await asyncio.wait_for(provider.full.wait(), 20)
        # The seven denied calls must finish while three provider calls are held.
        finished = set()
        while len(finished) < 7:
            newly_done, _ = await asyncio.wait([task for task in tasks if task not in finished], timeout=20, return_when=asyncio.FIRST_COMPLETED)
            assert newly_done
            finished.update(newly_done)
        assert len(finished) == 7
        assert all(isinstance(task.exception(), PaperCheckingExecutionError) and task.exception().code == "paper_check_capacity_unavailable" for task in finished)
        assert provider.active == provider.maximum == provider.entered == 3
        async with factory() as db:
            assert await db.scalar(text("SELECT sum(retry_count) FROM check_runs r JOIN paper_submissions p ON p.id=r.paper_submission_id WHERE p.batch_id=:batch"), c[2]) == 0
        provider.release.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for index, outcome in enumerate(outcomes):
            if isinstance(outcome, Exception):
                await execute(index)
        assert len(provider.calls) == 10 and provider.maximum == 3
        for label, run in zip(labels, runs, strict=True):
            await call(live, run, "start", paper=label)
            from app.application.paper_checking_review import PaperReviewApproval
            approved = await call(live, run, "approve", PaperReviewApproval(expected_version=1), paper=label)
            await publish(live, approved.latest_approved_revision_id, label)
        assert (await action(live, "reconcile")).settled_count == 10
        assert (await action(live, "finalize")).batch_status == "completed"
    finally:
        provider.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


async def test_same_run_concurrent_execution_one_provider_winner(concurrent_database):
    c, url = concurrent_database
    run = await start(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = GateProvider(1)
    svc = PaperCheckingExecutionService(SQLAlchemyPaperCheckingExecutionRepository(factory), c[3], provider)
    first = asyncio.create_task(svc.execute(c[2]["paper_a"], run.id))
    try:
        await asyncio.wait_for(provider.full.wait(), 20)
        with pytest.raises(PaperCheckingExecutionError, match="paper_check_in_progress"):
            await svc.execute(c[2]["paper_a"], run.id)
        provider.release.set()
        result = await first
        assert await svc.execute(c[2]["paper_a"], run.id) == result
        assert len(provider.calls) == 1
    finally:
        provider.release.set()
        await asyncio.gather(first, return_exceptions=True)
        await engine.dispose()


async def new_batch(c):
    batch_id = uuid4()
    await c[0].execute(text("""INSERT INTO assessment_scan_batches
        (id,assignment_id,class_group_id,created_by_user_id,status,instruction_text,request_key,request_hash)
        VALUES (:id,:assignment,:group,:teacher,'draft','Mark exactly',:key,:source_hash)"""),
        {**c[2], "id": batch_id, "key": uuid4().hex})
    return batch_id


async def full_intake_ten(c):
    """Actual upload → PDFium/Pillow extraction → matching → grouping handoff."""
    from decimal import Decimal
    from types import SimpleNamespace

    from app.application.scan_checking_contracts import (
        PageMatchingResponse,
        PageMatchResult,
    )
    from app.application.scan_matching_service import ScanMatchingService
    await seed_ten(c)  # FK-valid class roster of ten, sharing two approved variants.
    batch_id = await new_batch(c)
    async with c[1]() as db:
        pipeline = ScanPagePipeline(db, c[3])
        for n in range(10):
            await upload(pipeline, c[2], batch_id, n)
        summary = await pipeline.extract(batch_id=batch_id, actor_id=c[2]["teacher"])
        assert summary.pages["ready"] == 10
        class Provider:
            provider_id, model_id = "matching-fake", "matching-fake"
            async def match(self, request, content):
                assert len(content) == len(request.pages)
                return PageMatchingResponse(pages=tuple(PageMatchResult(page_token=page.page_token,
                    disposition="matched", proposed_roster_token=request.roster[page.source_order].roster_token,
                    confidence=Decimal("0.9"), evidence_code="visual_identifier", evidence_summary="Visible identifier",
                    proposed_page_order=0, requires_human_review=True) for page in request.pages)), SimpleNamespace(
                        provider_request_id=None, input_tokens=0, output_tokens=0, cached_tokens=0, cache_write_tokens=0, latency_ms=0)
        result = await ScanMatchingService(db, c[3], Provider()).run(batch_id, c[2]["teacher"])
        assert result["pages_summary"]["matched"] == 10
        grouping = ScanGroupingService(SqlAlchemyScanGroupingRepository(db))
        view = await grouping.initialize(batch_id, c[2]["teacher"])
        assert len(view["groups"]) == 10
        view = await grouping.confirm(batch_id, view["grouping_revision"], view["row_version"], c[2]["teacher"])
        labels = ["a", "b", *(str(n) for n in range(2, 10))]
        assert len(view["paper_submissions"]) == 10
        for label, paper in zip(labels, view["paper_submissions"], strict=True):
            c[2][f"paper_{label}"] = paper["id"]
    c[2]["batch"] = batch_id


@pytest.mark.parametrize("field,code", [
    ("max_artifacts_per_batch", "scan_batch_artifact_limit_exceeded"),
    ("max_total_upload_bytes_per_batch", "scan_batch_byte_limit_exceeded"),
])
async def test_ten_concurrent_uploads_cannot_bypass_aggregate_limit(concurrent_database, field, code):
    c, url = concurrent_database
    batch_id = await new_batch(c)
    await c[0].commit()
    engine = create_async_engine(url, pool_size=12)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = limits(max_single_artifact_bytes=len(PNG), **{field: 3 if field == "max_artifacts_per_batch" else 3 * len(PNG)})
    before_files = set(c[3].root.iterdir())
    async def attempt(position):
        async with factory() as db:
            return await upload(ScanPagePipeline(db, c[3], limits=config), c[2], batch_id, position)
    try:
        outcomes = await asyncio.wait_for(asyncio.gather(*(attempt(n) for n in range(10)), return_exceptions=True), 20)
        failures = [value for value in outcomes if isinstance(value, Exception)]
        assert len(failures) == 7 and all(isinstance(value, ScanIntakeError) and value.code == code for value in failures)
        async with factory() as db:
            assert (await db.execute(text("""SELECT count(*),sum(i.size_bytes) FROM scan_batch_artifacts a
                JOIN input_artifacts i ON i.id=a.input_artifact_id WHERE a.batch_id=:id"""), {"id": batch_id})).one() == (3, 3 * len(PNG))
        assert len(set(c[3].root.iterdir()) - before_files) == 3
    finally:
        await engine.dispose()


async def test_concurrent_pdf_extraction_whole_artifacts_remain_bounded(concurrent_database):
    c, url = concurrent_database
    batch_id = await new_batch(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = limits(max_pages_per_artifact=2, max_pages_per_batch=3)
    async with factory() as db:
        pipeline = ScanPagePipeline(db, c[3], limits=config)
        for n in range(2):
            await upload(pipeline, c[2], batch_id, n, source_pdf(), "application/pdf")
    before_files = set(c[3].root.iterdir())
    async def extract():
        async with factory() as db:
            return await ScanPagePipeline(db, c[3], limits=config).extract(batch_id=batch_id, actor_id=c[2]["teacher"])
    try:
        await asyncio.wait_for(asyncio.gather(extract(), extract()), 20)
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM scan_pages p JOIN scan_batch_artifacts a ON a.id=p.batch_artifact_id WHERE a.batch_id=:id"), {"id": batch_id}) == 2
            rows = (await db.execute(text("SELECT extraction_status,failure_code FROM scan_batch_artifacts WHERE batch_id=:id ORDER BY upload_position"), {"id": batch_id})).all()
            assert sorted(status for status, _ in rows) == ["completed", "failed_terminal"]
            assert [code for _, code in rows if code] == ["scan_batch_page_limit_exceeded"]
        assert len(set(c[3].root.iterdir()) - before_files) == 2
    finally:
        await engine.dispose()


async def test_provider_page_cap_no_call_or_retry_and_full_manual_fallback(execution_database):
    c = execution_database
    params = {**c[2], "page": uuid4()}
    await c[0].execute(text("""INSERT INTO scan_pages
        (id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status)
        VALUES (:page,:batch_artifact,2,:render_a,1,1,0,'normalized_upright_v1',:hash_a,'ready')"""), params)
    await c[0].execute(text("INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry_a,:revision,:page,1)"), params)
    await c[0].execute(text("INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper_a,:page,1)"), params)
    run = await start(c)
    provider = FakePaperProvider()
    config = limits(max_provider_pages_per_paper=1)
    svc = PaperCheckingExecutionService(SQLAlchemyPaperCheckingExecutionRepository(c[1], config), c[3], provider, config)
    with pytest.raises(PaperCheckingExecutionError, match="paper_check_provider_page_limit_exceeded"):
        await svc.execute(c[2]["paper_a"], run.id)
    assert not provider.calls
    assert await c[0].scalar(text("SELECT count(*) FROM model_runs WHERE check_run_id=:id"), {"id": run.id}) == 0
    for label, score in (("a", 10), ("b", 20)):
        from decimal import Decimal
        approved = await grade(c, await manual(c, paper=label), "correct", Decimal(score), paper=label)
        await publish(c, approved.latest_approved_revision_id, label)
    assert (await batch(c, "finalize")).batch_status == "completed"


async def test_historical_batch_read_and_completed_replay_ignore_new_limits(execution_database):
    c = execution_database
    restrictive = limits(max_pages_per_artifact=1, max_pages_per_batch=1, max_paper_submissions_per_batch=1)
    async with c[1]() as db:
        from app.infrastructure.scan_intake_repository import (
            SqlAlchemyScanIntakeRepository,
        )
        view = await SqlAlchemyScanIntakeRepository(db, restrictive).get_batch(c[2]["batch"])
        assert view.id == c[2]["batch"]
    run = await start(c)
    provider = FakePaperProvider()
    svc = PaperCheckingExecutionService(SQLAlchemyPaperCheckingExecutionRepository(c[1]), c[3], provider)
    result = await svc.execute(c[2]["paper_a"], run.id)
    strict = limits(max_provider_image_bytes_per_paper=1)
    assert await PaperCheckingExecutionService(SQLAlchemyPaperCheckingExecutionRepository(c[1], strict), c[3], provider, strict).execute(c[2]["paper_a"], run.id) == result
    assert len(provider.calls) == 1


@pytest.mark.parametrize("mode,status", [("unknown_item", "failed_terminal"), ("transport", "failed_retryable")])
async def test_provider_failure_releases_capacity_and_retains_retry_semantics(execution_database, mode, status):
    c = execution_database
    first = await start(c)
    second = await start(c, "b")
    config = limits(max_parallel_paper_checks_per_batch=1)
    repo = SQLAlchemyPaperCheckingExecutionRepository(c[1], config)
    provider = FakePaperProvider(mode=mode)
    with pytest.raises(PaperCheckingExecutionError):
        await PaperCheckingExecutionService(repo, c[3], provider, config).execute(c[2]["paper_a"], first.id)
    assert await c[0].scalar(text("SELECT status FROM check_runs WHERE id=:id"), {"id": first.id}) == status
    assert len(provider.calls) == (3 if mode == "transport" else 1)
    successful = FakePaperProvider()
    await PaperCheckingExecutionService(repo, c[3], successful, config).execute(c[2]["paper_b"], second.id)
    assert len(successful.calls) == 1


async def test_provider_finalize_manual_approval_and_reconcile_race(concurrent_database):
    from decimal import Decimal

    from app.application.paper_checking_review import PaperReviewApproval
    c, url = concurrent_database
    run = await start(c)
    review = await manual(c, paper="b")
    draft = review.draft.model_copy(update={"summary": "Teacher checked", "items": tuple(
        item.model_copy(update={"status": "correct", "accepted_score": Decimal(20)}) for item in review.draft.items)})
    edited = await manual(c, "edit", draft, review.version, paper="b")
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    live = (c[0], factory, c[2], c[3])
    provider = GateProvider(1)
    config = limits(max_parallel_paper_checks_per_batch=1)
    svc = PaperCheckingExecutionService(SQLAlchemyPaperCheckingExecutionRepository(factory, config), c[3], provider, config)
    first = asyncio.create_task(svc.execute(c[2]["paper_a"], run.id))
    try:
        await asyncio.wait_for(provider.full.wait(), 20)
        assert (await action(live, "progress")).batch_status == "checking"
        provider.release.set()
        result, approved, *_ = await asyncio.wait_for(asyncio.gather(first,
            manual(live, "approve", PaperReviewApproval(expected_version=edited.version), paper="b"),
            action(live, "reconcile"), action(live, "reconcile")), 20)
        assert result["status"] == "completed_with_review_required"
        assert (await action(live, "reconcile")).settled_count == 2
        assert (await action(live, "progress")).batch_status == "checking_completed"
        await call(live, run, "start")
        ai_approved = await call(live, run, "approve", PaperReviewApproval(expected_version=1))
        await publish(live, ai_approved.latest_approved_revision_id)
        await publish(live, approved.latest_approved_revision_id, "b")
        await asyncio.wait_for(asyncio.gather(action(live, "finalize"), action(live, "reconcile")), 20)
        assert (await action(live, "progress")).batch_status == "completed"
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM model_runs WHERE paper_submission_id=:paper_b"), c[2]) == 0
    finally:
        provider.release.set()
        await asyncio.gather(first, return_exceptions=True)
        await engine.dispose()


async def test_matching_double_click_with_preloaded_rows_calls_provider_once(concurrent_database):
    from decimal import Decimal
    from types import SimpleNamespace

    from app.application.scan_checking_contracts import (
        PageMatchingResponse,
        PageMatchResult,
    )
    from app.application.scan_matching_service import (
        ScanMatchingError,
        ScanMatchingService,
    )
    from app.infrastructure.scan_checking_models import AssessmentScanBatch
    c, url = concurrent_database
    batch_id = await new_batch(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    class MatchingProvider:
        provider_id, model_id, calls = "matching-fake", "matching-fake", 0
        async def match(self, request, content):
            self.calls += 1
            entered.set()
            await release.wait()
            return PageMatchingResponse(pages=tuple(PageMatchResult(page_token=page.page_token,
                disposition="unmatched", confidence=Decimal("0.5"), evidence_code="unreadable",
                evidence_summary="No identifier", requires_human_review=True) for page in request.pages)), SimpleNamespace(
                    provider_request_id=None, input_tokens=0, output_tokens=0, cached_tokens=0, cache_write_tokens=0, latency_ms=0)
    provider = MatchingProvider()
    task = None
    try:
        async with factory() as db:
            pipeline = ScanPagePipeline(db, c[3])
            await upload(pipeline, c[2], batch_id)
            await pipeline.extract(batch_id=batch_id, actor_id=c[2]["teacher"])
        async with factory() as first, factory() as second:
            # Retain the second session's stale object across the winning claim.
            stale = await second.get(AssessmentScanBatch, batch_id)
            assert stale.status == "extracting"
            task = asyncio.create_task(ScanMatchingService(first, c[3], provider).run(batch_id, c[2]["teacher"]))
            await asyncio.wait_for(entered.wait(), 20)
            with pytest.raises(ScanMatchingError, match="matching_already_running"):
                await ScanMatchingService(second, c[3], provider).run(batch_id, c[2]["teacher"])
            await second.rollback()
            release.set()
            result = await asyncio.wait_for(task, 20)
            assert result["status"] == "matching_review_required"
            assert provider.calls == 1
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


async def test_manual_review_can_start_and_approve_while_ai_slot_saturated(execution_database):
    c = execution_database
    run = await start(c)
    repo = SQLAlchemyPaperCheckingExecutionRepository(c[1], limits(max_parallel_paper_checks_per_batch=1))
    await repo.prepare(c[2]["paper_a"], run.id, "acceptance-fake", "deterministic-vision")
    review = await manual(c, paper="b")
    approved = await grade(c, review, paper="b")
    assert approved.source_kind == "manual"
    assert await c[0].scalar(text("SELECT count(*) FROM model_runs WHERE paper_submission_id=:paper_b"), c[2]) == 0
    assert (await batch(c)).batch_status == "checking"


@pytest.mark.parametrize("global_cap", [1, 2])
async def test_independent_batches_share_global_capacity(execution_database, global_cap):
    c = execution_database
    run = await start(c)
    paper, other = await seed_independent_paper(c)
    repo = SQLAlchemyPaperCheckingExecutionRepository(c[1], limits(
        max_parallel_paper_checks_per_batch=1, max_parallel_paper_checks_global=global_cap))
    first = await repo.prepare(c[2]["paper_a"], run.id, "acceptance-fake", "deterministic-vision")
    if global_cap == 1:
        with pytest.raises(PaperCheckingExecutionError, match="paper_check_capacity_unavailable"):
            await repo.prepare(paper, other.id, "acceptance-fake", "deterministic-vision")
        assert (await c[0].execute(text("SELECT status,retry_count FROM check_runs WHERE id=:id"), {"id": other.id})).one() == ("pending", 0)
        assert await c[0].scalar(text("SELECT count(*) FROM model_runs WHERE check_run_id=:id"), {"id": other.id}) == 0
        await repo.fail(first, "semantic_invalid", None)
    second = await repo.prepare(paper, other.id, "acceptance-fake", "deterministic-vision")
    assert first.model_run_id and second.model_run_id
    assert await c[0].scalar(text("SELECT count(*) FROM model_runs WHERE id IN (:a,:b) AND status='running'"),
        {"a": first.model_run_id, "b": second.model_run_id}) == global_cap


async def seed_independent_paper(c):
    from tests.integration.test_paper_checking_intake_database import freeze

    other_batch, revision, artifact, page, paper, entry = (uuid4() for _ in range(6))
    params = {**c[2], "other_batch": other_batch, "other_revision": revision, "other_artifact": artifact,
        "other_page": page, "other_paper": paper, "other_entry": entry, "key": uuid4().hex}
    statements = (
        "INSERT INTO assessment_scan_batches(id,assignment_id,class_group_id,created_by_user_id,status,instruction_text,matching_revision,request_key,request_hash) VALUES (:other_batch,:assignment,:group,:teacher,'ready_for_checking','Mark exactly',1,:key,:source_hash)",
        "INSERT INTO scan_batch_artifacts(id,batch_id,input_artifact_id,upload_position,original_filename,extraction_status,page_count) VALUES (:other_artifact,:other_batch,:source,0,'paper.pdf','completed',1)",
        "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status) VALUES (:other_page,:other_artifact,0,:render_b,1,1,0,'normalized_upright_v1',:hash_b,'ready')",
        "INSERT INTO scan_grouping_revisions(id,batch_id,revision,based_on_matching_revision,created_by_user_id,status,confirmed_at,confirmed_by_user_id) VALUES (:other_revision,:other_batch,1,1,:teacher,'confirmed',clock_timestamp(),:teacher)",
        "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:other_entry,:other_revision,:participant_b,0)",
        "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:other_entry,:other_revision,:other_page,0)",
        "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:other_paper,:other_batch,:other_revision,:assignment,:participant_b,:student_b,:variant_b,'ready_for_checking',:teacher)",
        "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:other_paper,:other_page,0)",
    )
    for sql in statements:
        await c[0].execute(text(sql), params)
    other_ids = {**c[2], "batch": other_batch, "paper_b": paper}
    await freeze(c[1], other_ids, "b")
    other = await intake(c[1]).create(request(other_ids, "b", "independent"))
    return paper, other


async def test_independent_connections_race_for_last_global_slot(concurrent_database):
    c, url = concurrent_database
    first_run = await start(c)
    other_paper, other_run = await seed_independent_paper(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SQLAlchemyPaperCheckingExecutionRepository(factory, limits(
        max_parallel_paper_checks_per_batch=1, max_parallel_paper_checks_global=1))
    gate = asyncio.Event()
    async def claim(paper, run):
        await gate.wait()
        return await repo.prepare(paper, run, "acceptance-fake", "deterministic-vision")
    try:
        tasks = [asyncio.create_task(claim(c[2]["paper_a"], first_run.id)),
                 asyncio.create_task(claim(other_paper, other_run.id))]
        gate.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 30)
        winners = [result for result in results if not isinstance(result, Exception)]
        denied = [result for result in results if isinstance(result, PaperCheckingExecutionError)]
        assert len(winners) == len(denied) == 1
        assert denied[0].code == "paper_check_capacity_unavailable"
        loser = 0 if isinstance(results[0], Exception) else 1
        loser_paper, loser_run = [(c[2]["paper_a"], first_run.id), (other_paper, other_run.id)][loser]
        async with factory() as db:
            row = (await db.execute(text("SELECT status,retry_count FROM check_runs WHERE id=:id"), {"id": loser_run})).one()
            assert row == ("pending", 0)
            assert await db.scalar(text("SELECT count(*) FROM model_runs WHERE check_run_id=:id"), {"id": loser_run}) == 0
        await repo.fail(winners[0], "semantic_invalid", None)
        recovered = await repo.prepare(loser_paper, loser_run, "acceptance-fake", "deterministic-vision")
        assert recovered.model_run_id
        await repo.fail(recovered, "semantic_invalid", None)
    finally:
        await engine.dispose()
