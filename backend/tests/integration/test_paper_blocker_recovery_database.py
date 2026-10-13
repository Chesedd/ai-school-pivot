"""PostgreSQL crash boundaries, exact storage ownership and safe rollback."""
import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.scan_checking_contracts import (
    PageMatchingResponse,
    PageMatchResult,
)
from app.application.scan_matching import MatchingTelemetry
from app.application.scan_matching_service import ScanMatchingError, ScanMatchingService
from app.infrastructure.scan_orphan_cleanup import (
    compensate_scan_objects,
    sweep_scan_orphans,
)
from app.infrastructure.scan_page_pipeline import ScanPagePipeline
from tests.integration.test_paper_checking_execution_database import (
    PNG,
)
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_operational_limits_database import upload
from tests.integration.test_paper_production_audit_database import matching_service
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")]


class Crash(BaseException):
    pass


class Provider:
    provider_id = model_id = "recovery-test"
    calls = 0

    async def match(self, request, content):
        self.calls += 1
        assert len(content) == len(request.pages)
        return PageMatchingResponse(pages=tuple(PageMatchResult(
            page_token=p.page_token, disposition="unmatched", confidence=Decimal("0.5"),
            evidence_code="insufficient_evidence", evidence_summary="Review required",
            requires_human_review=True) for p in request.pages)), MatchingTelemetry("attempt-" + str(self.calls))


async def expire(db, batch_id):
    await db.execute(text("UPDATE scan_matching_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE batch_id=:batch"), {"batch": batch_id})
    await db.commit()


@pytest.mark.parametrize("boundary", ["run_created", "claimed", "provider_success", "chunk_committed", "finalized"])
async def test_matching_crash_recovery_and_post_finalize_replay(execution_database, monkeypatch, boundary):
    c = execution_database
    provider = Provider()
    db, batch, service = await matching_service(c, provider)
    batch_id, actor = batch.id, c[2]["teacher"]
    try:
        if boundary == "run_created":
            await service._begin_or_resume(batch_id, actor)
        elif boundary == "claimed":
            run = await service._begin_or_resume(batch_id, actor)
            await service.repo.claim_chunk(run.id, 0, service.claim_token)
        elif boundary == "provider_success":
            original = provider.match
            async def interrupted(*args):
                await original(*args)
                raise Crash()
            monkeypatch.setattr(provider, "match", interrupted)
            with pytest.raises(Crash):
                await service.run(batch_id, actor)
            monkeypatch.setattr(provider, "match", original)
        elif boundary == "chunk_committed":
            original = service._finalize
            monkeypatch.setattr(service, "_finalize", AsyncMock(side_effect=Crash()))
            with pytest.raises(Crash):
                await service.run(batch_id, actor)
            monkeypatch.setattr(service, "_finalize", original)
        else:
            await service.run(batch_id, actor)
        if boundary != "finalized":
            await expire(db, batch_id)
        result = await service.run(batch_id, actor)
        assert result["status"] == "matching_review_required"
        assert provider.calls == (2 if boundary == "provider_success" else 1)
        assert await db.scalar(text("SELECT count(*) FROM scan_matching_runs WHERE batch_id=:batch"), {"batch": batch_id}) == 1
        assert await db.scalar(text("SELECT count(*) FROM scan_page_match_proposals p JOIN scan_matching_runs r ON r.id=p.matching_run_id WHERE r.batch_id=:batch"), {"batch": batch_id}) == 1
        attempts = (await db.execute(text("SELECT a.status FROM scan_matching_attempts a JOIN scan_matching_chunks c ON c.id=a.chunk_id JOIN scan_matching_runs r ON r.id=c.matching_run_id WHERE r.batch_id=:batch ORDER BY a.started_at"), {"batch": batch_id})).scalars().all()
        assert attempts == (["interrupted", "succeeded"] if boundary == "provider_success" else ["succeeded"])
        # Teacher grouping remains the required next action; no academic run exists.
        assert await db.scalar(text("SELECT count(*) FROM paper_submissions WHERE batch_id=:batch"), {"batch": batch_id}) == 0
        before = (await db.execute(text("SELECT p.id,p.evidence_summary FROM scan_page_match_proposals p JOIN scan_matching_runs r ON r.id=p.matching_run_id WHERE r.batch_id=:batch"), {"batch": batch_id})).all()
        await service.run(batch_id, actor)
        after = (await db.execute(text("SELECT p.id,p.evidence_summary FROM scan_page_match_proposals p JOIN scan_matching_runs r ON r.id=p.matching_run_id WHERE r.batch_id=:batch"), {"batch": batch_id})).all()
        assert before == after
        if boundary == "provider_success":
            from app.application.scan_grouping import ScanGroupingService
            from app.infrastructure.scan_grouping_repository import (
                SqlAlchemyScanGroupingRepository,
            )
            grouping = ScanGroupingService(SqlAlchemyScanGroupingRepository(db))
            view = await grouping.initialize(batch_id, actor)
            assert not view["groups"]  # Unmatched evidence cannot seed confirmed grouping.
            view = await grouping.assign(batch_id, result["proposals"][0]["page_id"],
                c[2]["participant_a"], view["grouping_revision"], view["row_version"], actor)
            view = await grouping.confirm(batch_id, view["grouping_revision"], view["row_version"], actor)
            assert view["batch_status"] == "ready_for_checking"
    finally:
        await db.close()


async def test_concurrent_recovery_fences_old_provider_and_one_finalizer(concurrent_database):
    c, url = concurrent_database
    entered, release = asyncio.Event(), asyncio.Event()
    class Paused(Provider):
        async def match(self, request, content):
            result = await super().match(request, content)
            entered.set()
            await release.wait()
            return result
    old_provider = Paused()
    db, batch, _ = await matching_service(c, old_provider)
    batch_id, actor = batch.id, c[2]["teacher"]
    await db.close()
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old_task = recovery_task = None
    try:
        async with factory() as old, factory() as winner, factory() as loser:
            old_task = asyncio.create_task(ScanMatchingService(old, c[3], old_provider).run(batch_id, actor))
            await asyncio.wait_for(entered.wait(), 20)
            await expire(loser, batch_id)
            winner_entered, winner_release = asyncio.Event(), asyncio.Event()
            class Recovery(Provider):
                async def match(self, request, content):
                    result = await super().match(request, content)
                    winner_entered.set()
                    await winner_release.wait()
                    return result
            recovery_task = asyncio.create_task(ScanMatchingService(winner, c[3], Recovery()).run(batch_id, actor))
            await asyncio.wait_for(winner_entered.wait(), 20)
            with pytest.raises(ScanMatchingError, match="matching_already_running"):
                await ScanMatchingService(loser, c[3], Provider()).run(batch_id, actor)
            release.set()
            with pytest.raises(ScanMatchingError, match="matching_claim_lost"):
                await asyncio.wait_for(old_task, 20)
            winner_release.set()
            assert (await asyncio.wait_for(recovery_task, 20))["status"] == "matching_review_required"
            assert await loser.scalar(text("SELECT count(*) FROM scan_page_match_proposals p JOIN scan_matching_runs r ON r.id=p.matching_run_id WHERE r.batch_id=:batch"), {"batch": batch_id}) == 1
            assert (await loser.execute(text("SELECT a.status FROM scan_matching_attempts a JOIN scan_matching_chunks c ON c.id=a.chunk_id JOIN scan_matching_runs r ON r.id=c.matching_run_id WHERE r.batch_id=:batch ORDER BY a.started_at"), {"batch": batch_id})).scalars().all() == ["interrupted", "succeeded"]
    finally:
        release.set()
        if 'winner_release' in locals():
            winner_release.set()
        for task in (old_task, recovery_task):
            if task:
                await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.parametrize("stage", ["source", "render"])
@pytest.mark.parametrize("boundary", ["write", "insert", "commit"])
@pytest.mark.parametrize("cleanup", ["success", "missing", "transient"])
async def test_scan_storage_failure_compensation_and_retry(execution_database, monkeypatch, stage, boundary, cleanup):
    c = execution_database
    db, batch, _ = await matching_service(c, Provider())
    batch_id = batch.id
    # New owned operation on this batch; existing committed source/render are protected.
    await db.execute(text("UPDATE assessment_scan_batches SET status='uploading' WHERE id=:id"), {"id": batch_id})
    await db.commit()
    pipeline = ScanPagePipeline(db, c[3])
    created = []
    original_store = c[3].store_scan
    async def store(*args):
        ref = await original_store(*args)
        created.append(ref)
        if boundary == "write":
            raise OSError("write interrupted after allocation")
        return ref
    # For render, first create a normal additional source.
    if stage == "render":
        await upload(pipeline, c[2], batch_id, 1)
    original_delete = c[3].delete
    async def delete(ref):
        if cleanup == "transient":
            raise OSError("temporary backend failure")
        if cleanup == "missing":
            await original_delete(ref)
        return await original_delete(ref)
    monkeypatch.setattr(c[3], "delete", delete)
    monkeypatch.setattr(c[3], "store_scan", store)
    if boundary in {"insert", "commit"}:
        method = "flush" if boundary == "insert" else "commit"
        original = getattr(db, method)
        failed = False
        async def fault(*args, **kwargs):
            nonlocal failed
            if created and not failed:
                failed = True
                raise RuntimeError("DB failed after storage")
            return await original(*args, **kwargs)
        monkeypatch.setattr(db, method, fault)
    try:
        if stage == "source":
            with pytest.raises((OSError, RuntimeError)):
                await upload(pipeline, c[2], batch_id, 1)
        else:
            # Extraction handles retryable local rendering faults; commit faults propagate.
            if boundary == "commit":
                with pytest.raises(RuntimeError):
                    await pipeline.extract(batch_id=batch_id, actor_id=c[2]["teacher"])
            else:
                await pipeline.extract(batch_id=batch_id, actor_id=c[2]["teacher"])
        assert created
        for ref in created:
            assert await db.scalar(text("SELECT count(*) FROM input_artifacts WHERE storage_reference=:ref"), {"ref": ref}) == 0
            if c[3]._path(ref).exists():
                assert c[3].scan_marker(batch_id, ref).exists()
        monkeypatch.setattr(c[3], "delete", original_delete)
        await db.rollback()
        outcome = await sweep_scan_orphans(c[1], c[3], dry_run=False)
        assert outcome.get("retryable_failed", 0) == 0
        assert all(not c[3]._path(ref).exists() for ref in created)
        assert not c[3].pending_scan_objects()
        refs = (await db.execute(text("SELECT i.storage_reference FROM input_artifacts i JOIN scan_batch_artifacts a ON a.input_artifact_id=i.id WHERE a.batch_id=:batch"), {"batch": batch_id})).scalars().all()
        assert all([await c[3].read(ref) for ref in refs])
    finally:
        await db.close()


async def test_committed_and_unrelated_blobs_are_never_orphan_deleted(execution_database):
    c = execution_database
    db, batch, _ = await matching_service(c, Provider())
    batch_id = batch.id
    try:
        refs = (await db.execute(text("SELECT i.storage_reference FROM input_artifacts i JOIN scan_batch_artifacts a ON a.input_artifact_id=i.id WHERE a.batch_id=:batch"), {"batch": batch.id})).scalars().all()
        unrelated = await c[3].store(PNG, "image/png")
        for ref in refs:
            # Replay compensation for a committed allocation is safe even if its
            # journal acknowledgement was lost. Normal shared writes have no marker.
            marker = c[3].scan_marker(batch_id, ref)
            marker.touch()
        await db.rollback()
        await compensate_scan_objects(db, c[3], batch_id, refs)
        assert all([await c[3].read(ref) for ref in [*refs, unrelated]])
        assert not c[3].pending_scan_objects()
    finally:
        await db.close()


async def test_provider_success_database_finalize_failure_is_retryable(execution_database, monkeypatch):
    c = execution_database
    provider = Provider()
    db, batch, service = await matching_service(c, provider)
    batch_id = batch.id
    original = db.commit
    interrupted = False
    async def fail_finalization():
        nonlocal interrupted
        if getattr(service, "provider_succeeded", False) and not interrupted:
            interrupted = True
            raise RuntimeError("database unavailable before result commit")
        await original()
    monkeypatch.setattr(db, "commit", fail_finalization)
    try:
        with pytest.raises(ScanMatchingError, match="matching_persistence_failed"):
            await service.run(batch_id, c[2]["teacher"])
        assert (await service.run(batch_id, c[2]["teacher"]))["status"] == "matching_review_required"
        assert provider.calls == 2
        assert (await db.execute(text("SELECT a.status FROM scan_matching_attempts a JOIN scan_matching_chunks c ON c.id=a.chunk_id JOIN scan_matching_runs r ON r.id=c.matching_run_id WHERE r.batch_id=:batch ORDER BY a.started_at"), {"batch": batch_id})).scalars().all() == ["failed", "succeeded"]
    finally:
        await db.close()



async def test_matching_success_evidence_is_database_immutable(execution_database):
    from sqlalchemy.exc import DBAPIError
    c = execution_database
    db, batch, service = await matching_service(c, Provider())
    batch_id = batch.id
    try:
        await service.run(batch_id, c[2]["teacher"])
        for table, mutation in (("scan_matching_attempts", "evidence='{}'::jsonb"),
                                ("scan_page_match_proposals", "evidence_summary='replacement'")):
            with pytest.raises(DBAPIError, match="immutable"):
                async with db.begin_nested():
                    if table == "scan_matching_attempts":
                        await db.execute(text("UPDATE scan_matching_attempts a SET " + mutation + " FROM scan_matching_chunks c,scan_matching_runs r WHERE a.chunk_id=c.id AND c.matching_run_id=r.id AND r.batch_id=:batch"), {"batch": batch_id})
                    else:
                        await db.execute(text("UPDATE scan_page_match_proposals p SET " + mutation + " FROM scan_matching_runs r WHERE p.matching_run_id=r.id AND r.batch_id=:batch"), {"batch": batch_id})
        assert (await service.run(batch_id, c[2]["teacher"]))["status"] == "matching_review_required"
    finally:
        await db.close()


@pytest.mark.parametrize("delete_fails", [False, True])
async def test_scan_partial_write_before_registration_is_discoverable(execution_database, monkeypatch, delete_fails):
    c = execution_database
    db, batch, _ = await matching_service(c, Provider())
    batch_id = batch.id
    await db.execute(text("UPDATE assessment_scan_batches SET status='uploading' WHERE id=:batch"), {"batch": batch_id})
    await db.commit()
    original_write, original_delete = c[3]._write_new, c[3].delete
    async def partial(path, content):
        path.write_bytes(content[:3])
        raise OSError("partial allocation")
    monkeypatch.setattr(c[3], "_write_new", partial)
    if delete_fails:
        monkeypatch.setattr(c[3], "delete", AsyncMock(side_effect=OSError("backend unavailable")))
    try:
        with pytest.raises(OSError, match="partial allocation"):
            await upload(ScanPagePipeline(db, c[3]), c[2], batch_id, 1)
        assert bool(c[3].pending_scan_objects()) == delete_fails
        monkeypatch.setattr(c[3], "_write_new", original_write)
        monkeypatch.setattr(c[3], "delete", original_delete)
        await sweep_scan_orphans(c[1], c[3], dry_run=False)
        assert not c[3].pending_scan_objects()
        assert await db.scalar(text("SELECT count(*) FROM scan_batch_artifacts WHERE batch_id=:batch"), {"batch": batch_id}) == 1
    finally:
        await db.close()


async def test_absent_scan_bytes_are_success_even_if_directory_cleanup_fails(tmp_path, monkeypatch):
    from pathlib import Path
    from uuid import uuid4

    from app.infrastructure.artifact_storage import FilesystemArtifactStorage

    storage = FilesystemArtifactStorage(str(tmp_path))
    batch_id = uuid4()
    ref = await storage.store_scan(PNG, "image/png", batch_id)
    def unavailable_directory(path):
        raise OSError("directory unavailable")
    monkeypatch.setattr(Path, "rmdir", unavailable_directory)
    assert await storage.delete(ref) is True
    assert await storage.delete(ref) is False
    storage.acknowledge_scan(batch_id, ref)
    assert not storage.pending_scan_objects()
    assert not storage._path(ref).exists()
