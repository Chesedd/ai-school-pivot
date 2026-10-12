"""PR14 real PostgreSQL, actual files, protected publications and worker races."""

import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from app.application.capabilities import capabilities_for_roles
from app.application.paper_checking_review import PaperReviewApproval
from app.application.paper_result_publication import PaperPublicationError
from app.application.principal import Principal
from app.application.scan_storage_retention import (
    PaperRetentionSettings,
    ScanStorageRetentionService,
)
from app.config import get_settings
from app.db.session import get_session
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from app.infrastructure.scan_storage_content import read_scan_content
from app.infrastructure.scan_storage_retention_repository import (
    ScanStorageRetentionRepository,
)
from app.presentation.auth_dependencies import require_principal
from app.presentation.paper_result_publication_routes import (
    router as publication_router,
)
from app.presentation.scan_intake_routes import router as scan_router
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_paper_batch_orchestration_database import action
from tests.integration.test_paper_checking_execution_database import PNG
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_review_database import call, edited, evidence
from tests.integration.test_paper_remediation_database import (
    create,
    eligibility,
    invoke,
    published,
)
from tests.integration.test_paper_result_publication_database import (
    approval,
    publish,
    repo_call,
)
from tests.integration.test_paper_result_publication_database import (
    concurrent_database as concurrent_database,  # noqa: PLC0414
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


async def historical_completion(c, days=31, state="completed"):
    await c[0].execute(
        text("UPDATE assessment_scan_batches SET status=:state WHERE id=:batch"),
        {**c[2], "state": state},
    )
    # Seed historical immutable event time, never derive eligibility from updated_at.
    await c[0].execute(
        text("""INSERT INTO scan_checking_events
        (id,batch_id,aggregate_type,aggregate_id,event_type,actor_user_id,details,occurred_at)
        VALUES (:id,:batch,'batch',:batch,'batch.completed',:teacher,'{}',
                clock_timestamp()-make_interval(days => :days))"""),
        {**c[2], "id": uuid4(), "days": days},
    )


def sweeper(c, settings=None, storage=None):
    return ScanStorageRetentionService(
        ScanStorageRetentionRepository(c[1]), storage or c[3], settings
    )


async def targets(c, settings=None):
    return await ScanStorageRetentionRepository(c[1]).discover(
        settings or PaperRetentionSettings()
    )


async def ledger(c):
    return (
        (
            await c[0].execute(
                text(
                    "SELECT * FROM scan_storage_retention_entries WHERE scan_batch_id=:batch ORDER BY target_kind,id"
                ),
                c[2],
            )
        )
        .mappings()
        .all()
    )


@pytest.mark.parametrize("days,count", [(29, 0), (30, 3), (31, 3)])
async def test_retention_window_and_exact_boundary(execution_database, days, count):
    c = execution_database
    await historical_completion(c, days)
    assert len(await targets(c)) == count


@pytest.mark.parametrize(
    "state",
    [
        "draft",
        "uploading",
        "extracting",
        "matching",
        "matching_review_required",
        "grouping_confirmed",
        "ready_for_checking",
        "checking",
        "checking_completed",
        "processing_failed",
    ],
)
async def test_incomplete_batches_never_eligible(execution_database, state):
    c = execution_database
    await historical_completion(c, state=state)
    assert await targets(c) == []


async def test_completed_without_completion_event_fails_closed(execution_database):
    c = execution_database
    await c[0].execute(
        text(
            "UPDATE assessment_scan_batches SET status='completed',updated_at=clock_timestamp()-interval '90 days' WHERE id=:batch"
        ),
        c[2],
    )
    assert await targets(c) == []


async def test_updated_at_does_not_reset_clock_and_completion_event_immutable(
    execution_database,
):
    c = execution_database
    await historical_completion(c)
    await c[0].execute(
        text(
            "UPDATE assessment_scan_batches SET updated_at=clock_timestamp() WHERE id=:batch"
        ),
        c[2],
    )
    assert len(await targets(c)) == 3
    for sql in (
        "UPDATE scan_checking_events SET occurred_at=clock_timestamp() WHERE batch_id=:batch AND event_type='batch.completed'",
        "DELETE FROM scan_checking_events WHERE batch_id=:batch AND event_type='batch.completed'",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            async with c[0].begin_nested():
                await c[0].execute(text(sql), c[2])


async def test_dry_run_is_zero_mutation_and_bounded_oldest_first(execution_database):
    c = execution_database
    await historical_completion(c)
    settings = PaperRetentionSettings(
        source_retention_days=20, retention_max_targets_per_run=2
    )
    selected = await targets(c, settings)
    assert len(selected) == 2 and selected[0].target_kind == "source_artifact"
    assert selected == await targets(c, settings)
    report = await sweeper(c, settings).run(dry_run=True)
    assert report["selected"] == 2 and not await ledger(c)
    for ref in ("storage_source", "storage_a", "storage_b"):
        assert (c[3].root / c[2][ref]).exists()


async def test_source_and_page_purge_preserve_all_metadata_and_replay(
    execution_database,
):
    c = execution_database
    await historical_completion(c)
    before = (
        await c[0].execute(
            text("SELECT to_jsonb(b) FROM assessment_scan_batches b WHERE id=:batch"),
            c[2],
        )
    ).scalar_one()
    report = await sweeper(c).run(dry_run=False)
    assert report["purged"] == 3
    for ref in ("storage_source", "storage_a", "storage_b"):
        assert not (c[3].root / c[2][ref]).exists()
    assert (await sweeper(c).run(dry_run=False))["selected"] == 0
    assert len(await ledger(c)) == 3
    assert all(
        r["state"] == "purged"
        and r["attempt_count"] == 1
        and r["policy_version"] == "paper_storage_retention.v2"
        for r in await ledger(c)
    )
    assert (
        await c[0].execute(
            text("SELECT to_jsonb(b) FROM assessment_scan_batches b WHERE id=:batch"),
            c[2],
        )
    ).scalar_one() == before
    for table, where in (
        ("input_artifacts", "id IN (:source,:render_a,:render_b)"),
        ("scan_pages", "id IN (:page_a,:page_b)"),
        ("paper_submissions", "batch_id=:batch"),
        ("scan_batch_artifacts", "batch_id=:batch"),
    ):
        assert (
            await c[0].scalar(text(f"SELECT count(*) FROM {table} WHERE {where}"), c[2])
            > 0
        )


@pytest.mark.parametrize("missing", ["before_claim", "after_claim"])
async def test_missing_storage_is_successful_idempotent_purge(
    execution_database, missing
):
    c = execution_database
    await historical_completion(c)
    repo = ScanStorageRetentionRepository(c[1])
    target = next(t for t in await targets(c) if t.target_kind == "source_artifact")
    if missing == "after_claim":
        claim = await repo.claim(target, PaperRetentionSettings())
        await c[3].delete(target.storage_reference)
        # Simulate crash after delete and before finalize, then lease expiration.
        await c[0].execute(
            text(
                "UPDATE scan_storage_retention_entries SET claimed_at=clock_timestamp()-interval '10 minutes',claim_expires_at=clock_timestamp()-interval '5 minutes' WHERE id=:id"
            ),
            {"id": claim[0]},
        )
    else:
        await c[3].delete(target.storage_reference)
    assert (await sweeper(c).run(dry_run=False))["purged"] == 3
    assert (
        next(r for r in await ledger(c) if r["target_kind"] == "source_artifact")[
            "state"
        ]
        == "purged"
    )


async def test_retryable_delete_failure_preserves_academic_state_and_retries(
    execution_database,
):
    c = execution_database
    await historical_completion(c)

    class Broken:
        async def delete(self, reference):
            raise OSError("private path and provider details")

    assert (await sweeper(c, storage=Broken()).run(dry_run=False))[
        "retryable_failed"
    ] == 3
    assert all(
        r["state"] == "retryable_failed"
        and r["last_error_code"] == "storage_delete_failed"
        for r in await ledger(c)
    )
    assert (c[3].root / c[2]["storage_source"]).exists()
    assert (
        await c[0].scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:batch"), c[2]
        )
        == "completed"
    )
    assert (await sweeper(c).run(dry_run=False))["purged"] == 3
    assert all(r["attempt_count"] == 2 for r in await ledger(c))


async def test_claim_reclaim_and_fencing(execution_database):
    c = execution_database
    await historical_completion(c)
    repo, settings = ScanStorageRetentionRepository(c[1]), PaperRetentionSettings()
    target = (await targets(c))[0]
    old = await repo.claim(target, settings)
    assert old and await repo.claim(target, settings) is None
    assert target not in await targets(c)
    await c[0].execute(
        text(
            "UPDATE scan_storage_retention_entries SET claimed_at=clock_timestamp()-interval '10 minutes',claim_expires_at=clock_timestamp()-interval '5 minutes' WHERE id=:id"
        ),
        {"id": old[0]},
    )
    new = await repo.claim(target, settings)
    assert new[0] == old[0] and new[1] != old[1]
    assert not await repo.finalize(old, None)
    assert await repo.finalize(new, None)
    assert await repo.claim(target, settings) is None
    assert (await ledger(c))[0]["attempt_count"] == 2


async def test_unrelated_and_shared_inputs_are_protected(execution_database):
    c = execution_database
    await historical_completion(c)
    ref = await c[3].store(PNG, "image/png")
    generic = uuid4()
    await c[0].execute(
        text(
            "INSERT INTO input_artifacts(id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference) VALUES (:id,:teacher,'image/png',:hash_a,:png_size,:ref)"
        ),
        {**c[2], "id": generic, "ref": ref},
    )
    await c[0].execute(
        text(
            "INSERT INTO image_solving_sessions(id,owner_id,input_artifact_id) VALUES (:id,:teacher,:source)"
        ),
        {**c[2], "id": uuid4()},
    )
    assert all(t.target_kind != "source_artifact" for t in await targets(c))
    await sweeper(c).run(dry_run=False)
    assert (c[3].root / ref).exists() and (c[3].root / c[2]["storage_source"]).exists()


async def test_shared_source_and_render_memberships_excluded(execution_database):
    from tests.integration.test_paper_operational_limits_database import (
        seed_independent_paper,
    )

    c = execution_database
    await seed_independent_paper(c)
    await historical_completion(c)
    selected = await targets(c)
    assert [t.target_id for t in selected] == [c[2]["page_a"]]


async def test_authoritative_page_protection_and_final_recheck(execution_database):
    c = execution_database
    _, a = await approval(c)
    await historical_completion(c)
    repo = ScanStorageRetentionRepository(c[1])
    target = next(t for t in await targets(c) if t.target_id == c[2]["page_a"])
    await publish(c, a.latest_approved_revision_id)
    assert await repo.claim(target, PaperRetentionSettings()) is None
    assert c[2]["page_a"] not in {t.target_id for t in await targets(c)}
    await sweeper(c).run(dry_run=False)
    assert (c[3].root / c[2]["storage_a"]).exists()
    assert all(r["scan_page_id"] != c[2]["page_a"] for r in await ledger(c))


async def test_claim_prevents_later_publication(execution_database):
    c = execution_database
    _, a = await approval(c)
    await historical_completion(c)
    repo = ScanStorageRetentionRepository(c[1])
    target = next(t for t in await targets(c) if t.target_id == c[2]["page_a"])
    assert await repo.claim(target, PaperRetentionSettings())
    with pytest.raises(
        PaperPublicationError, match="paper_publication_pages_unavailable"
    ):
        await publish(c, a.latest_approved_revision_id)


async def test_historical_publication_same_pages_still_protected(execution_database):
    c = execution_database
    run, a = await approval(c)
    first = await publish(c, a.latest_approved_revision_id)
    correction = await call(c, run, "edit", edited(a, summary="new"), a.version)
    b = await call(
        c, run, "approve", PaperReviewApproval(expected_version=correction.version)
    )
    second = await publish(c, b.latest_approved_revision_id)
    assert first["id"] != second["id"]
    # Seed an immutable historical row without an active pointer, as PR9 supports
    # in persistence. This does not introduce product withdrawal semantics.
    from app.infrastructure.paper_result_publication_models import (
        PaperResultPublication,
    )
    from app.infrastructure.scan_checking_models import PaperSubmission

    _, other = await approval(c, "b")
    async with c[1]() as db, db.begin():
        paper = await db.get(PaperSubmission, c[2]["paper_b"])
        snapshot = await PaperResultPublicationRepository(db)._snapshot(
            paper, other.latest_approved_revision_id
        )
        db.add(
            PaperResultPublication(
                paper_submission_id=paper.id,
                review_id=other.latest_approved_revision.review_id,
                approved_review_revision_id=other.latest_approved_revision_id,
                published_by_user_id=c[2]["teacher"],
                publication_number=1,
                publication_snapshot=snapshot.model_dump(mode="json"),
            )
        )
    await historical_completion(c)
    assert c[2]["page_a"] not in {t.target_id for t in await targets(c)}
    assert c[2]["page_b"] in {t.target_id for t in await targets(c)}


def http_app(c):
    identity = {"role": "teacher", "user_id": c[2]["teacher"], "student_id": None}
    app = FastAPI()
    app.include_router(scan_router)
    app.include_router(publication_router)

    async def session():
        async with c[1]() as db:
            yield db

    def principal():
        roles = frozenset({identity["role"]})
        return Principal(
            identity["user_id"],
            "teacher",
            "Teacher",
            roles,
            capabilities_for_roles(roles),
            identity["student_id"],
        )

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[require_principal] = principal
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"artifact_storage_path": str(c[3].root)}
    )
    return app, identity


async def test_published_source_orphan_http_corrections_and_remediation(
    execution_database,
):
    c, ids = execution_database, execution_database[2]
    run, a, pub, _ = await published(c)
    _, b = await approval(c, "b")
    await publish(c, b.latest_approved_revision_id, "b")
    assert (await action(c, "finalize")).batch_status == "completed"
    assert (await sweeper(c).run())["selected"] == 0  # No immediate completion purge.
    before = await evidence(c, run)
    student_before = await repo_call(
        c, "student_result", ids["paper_a"], ids["student_a"]
    )
    history_before = (await call(c, run, "history"))["revisions"]
    # Extra normalized page has no final paper membership.
    orphan, render = uuid4(), uuid4()
    ref = await c[3].store(PNG, "image/png")
    await c[0].execute(
        text(
            "INSERT INTO input_artifacts(id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference) VALUES (:render,:teacher,'image/png',:hash_a,:png_size,:ref)"
        ),
        {**ids, "render": render, "ref": ref},
    )
    await c[0].execute(
        text(
            "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,content_fingerprint,coordinate_space_version,status) VALUES (:page,:batch_artifact,2,:render,1,1,:hash_a,'normalized_upright_v1','ready')"
        ),
        {**ids, "page": orphan, "render": render},
    )
    await historical_completion(c)
    app, identity = http_app(c)
    source_url = f"/api/assessment-core/scan-batches/{ids['batch']}/artifacts/{ids['batch_artifact']}/content"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        assert (await client.get(source_url)).status_code == 200
        report = await sweeper(c).run(dry_run=False)
        assert report["purged"] == 2
        gone = await client.get(source_url)
        assert gone.status_code == 410 and gone.json() == {
            "detail": "scan_artifact_content_purged"
        }
        gone = await client.get(f"/api/assessment-core/scan-pages/{orphan}/content")
        assert gone.status_code == 410 and gone.json() == {
            "detail": "scan_page_content_purged"
        }
        assert (
            await client.get(
                f"/api/assessment-core/scan-batches/{ids['batch']}/artifacts"
            )
        ).status_code == 200
        assert (
            not (c[3].root / ids["storage_source"]).exists()
            and not (c[3].root / ref).exists()
        )
        assert (c[3].root / ids["storage_a"]).exists() and (
            c[3].root / ids["storage_b"]
        ).exists()
        identity.update(role="student", user_id=uuid4(), student_id=ids["student_a"])
        own = f"/api/assessment-core/student/paper-submissions/{ids['paper_a']}"
        assert (await client.get(source_url)).status_code == 403
        assert (await client.get(own + "/result")).status_code == 200
        assert (await client.get(own + f"/pages/{ids['page_a']}")).content == PNG
    assert (
        await repo_call(c, "student_result", ids["paper_a"], ids["student_a"])
        == student_before
    )
    assert await evidence(c, run) == before
    assert (await call(c, run, "history"))["revisions"] == history_before
    assert (await eligibility(c))["eligible"]
    from app.presentation.remediation_schemas import ItemInput

    plan = await create(
        c,
        pub,
        items=[ItemInput(task_version_id=ids["version_b"], selection_source="manual")],
    )
    await invoke(c, "assign", plan["id"], ids["teacher"], False, False)
    from app.application.remediation_execution import RemediationExecutionService

    execution, status = await RemediationExecutionService(c[1]).start(
        plan["id"], ids["student_a"]
    )
    assert status == 201 and execution["execution_status"] == "in_progress"
    correction = await call(
        c, run, "edit", edited(a, summary="after source purge"), a.version
    )
    approved = await call(
        c, run, "approve", PaperReviewApproval(expected_version=correction.version)
    )
    assert (await repo_call(c, "student_result", ids["paper_a"], ids["student_a"]))[
        "id"
    ] == pub["id"]
    new = await publish(c, approved.latest_approved_revision_id)
    assert new["publication_number"] == 2
    assert (
        await repo_call(
            c, "student_page", ids["paper_a"], ids["page_a"], ids["student_a"], c[3]
        )
        == PNG
    )
    assert await evidence(c, run) == before


async def test_read_between_delete_and_finalize_returns_410(execution_database):
    c = execution_database
    await historical_completion(c)
    repo = ScanStorageRetentionRepository(c[1])
    target = next(t for t in await targets(c) if t.target_kind == "source_artifact")
    claim = await repo.claim(target, PaperRetentionSettings())
    async with c[1]() as db:
        assert await read_scan_content(
            db, c[3], target.storage_reference, batch_artifact_id=target.target_id
        )
        await c[3].delete(target.storage_reference)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as error:
            await read_scan_content(
                db, c[3], target.storage_reference, batch_artifact_id=target.target_id
            )
        assert (
            error.value.status_code == 410
            and error.value.detail == "scan_artifact_content_purged"
        )
    assert await repo.finalize(claim, None)


async def test_duplicate_ledger_and_invalid_target_integrity(execution_database):
    c = execution_database
    await historical_completion(c)
    repo = ScanStorageRetentionRepository(c[1])
    target = next(t for t in await targets(c) if t.target_kind == "source_artifact")
    claim = await repo.claim(target, PaperRetentionSettings())
    with pytest.raises(IntegrityError):
        async with c[0].begin_nested():
            await c[0].execute(
                text(
                    "INSERT INTO scan_storage_retention_entries(id,scan_batch_id,target_kind,batch_artifact_id,policy_version,eligible_at) VALUES (:id,:batch,'source_artifact',:batch_artifact,'paper_storage_retention.v1',clock_timestamp())"
                ),
                {**c[2], "id": uuid4()},
            )
    for sql in (
        "UPDATE scan_storage_retention_entries SET scan_page_id=:page_a WHERE id=:id",
        "UPDATE scan_storage_retention_entries SET policy_version='different' WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with c[0].begin_nested():
                await c[0].execute(text(sql), {**c[2], "id": claim[0]})


async def test_two_workers_claim_once_on_independent_connections(concurrent_database):
    c, url = concurrent_database
    await historical_completion(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = ScanStorageRetentionRepository(factory)
    settings = PaperRetentionSettings()
    try:
        target = (await repo.discover(settings))[0]
        claims = await asyncio.gather(
            repo.claim(target, settings), repo.claim(target, settings)
        )
        assert sum(x is not None for x in claims) == 1
        claim = next(x for x in claims if x)
        await c[3].delete(target.storage_reference)
        assert await repo.finalize(claim, None)
        async with factory() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM scan_storage_retention_entries WHERE scan_batch_id=:batch"
                    ),
                    c[2],
                )
                == 1
            )

        class CountDeletes:
            def __init__(self):
                self.calls = []

            async def delete(self, reference):
                self.calls.append(reference)
                return await c[3].delete(reference)

        storage = CountDeletes()
        workers = [ScanStorageRetentionService(repo, storage) for _ in range(2)]
        reports = await asyncio.gather(*(w.run(dry_run=False) for w in workers))
        assert sum(r["purged"] for r in reports) == 2
        assert len(storage.calls) == len(set(storage.calls)) == 2
    finally:
        await engine.dispose()


async def test_manual_corrections_after_source_purge(execution_database):
    from tests.integration.test_manual_paper_review_database import (
        grade,
        manual,
        policies,
    )

    c = execution_database
    await policies(c)
    a = await grade(c, await manual(c))
    b = await grade(c, await manual(c, paper="b"), paper="b")
    await publish(c, a.latest_approved_revision_id)
    await publish(c, b.latest_approved_revision_id, "b")
    await action(c, "finalize")
    await historical_completion(c)
    assert (await sweeper(c).run(dry_run=False))["purged"] == 1
    new = await grade(c, a)
    await publish(c, new.latest_approved_revision_id)
    assert new.source_kind == "manual"
    assert (
        await repo_call(
            c, "student_page", c[2]["paper_a"], c[2]["page_a"], c[2]["student_a"], c[3]
        )
        == PNG
    )
    assert (
        await manual(c, paper="b")
    ).review_id == b.review_id  # Existing start remains idempotent.


async def test_completed_batch_still_rejects_brand_new_manual_review(
    execution_database,
):
    from app.application.paper_checking_review import PaperReviewError
    from tests.integration.test_manual_paper_review_database import manual, policies

    c = execution_database
    await policies(c)
    await historical_completion(c)
    await sweeper(c).run(dry_run=False)
    with pytest.raises(PaperReviewError, match="completed"):
        await manual(c)


async def test_remediation_candidates_assignment_and_full_execution_after_purge(
    execution_database, monkeypatch
):
    from app.presentation.remediation_schemas import PaperCandidateSearch
    from tests.integration import test_paper_remediation_database as remediation_tests

    c = execution_database
    original = remediation_tests.published

    async def after_purge(context, *args, **kwargs):
        result = await original(context, *args, **kwargs)
        _, b = await approval(context, "b")
        await publish(context, b.latest_approved_revision_id, "b")
        await action(context, "finalize")
        await historical_completion(context)
        assert (await sweeper(context).run(dry_run=False))["purged"] == 1
        assert (await eligibility(context))["eligible"]
        candidates = await invoke(
            context,
            "paper_candidates",
            context[2]["teacher"],
            False,
            context[2]["paper_a"],
            PaperCandidateSearch(
                source_paper_publication_id=result[2]["id"],
                source_assessment_item_ids=[context[2]["item_a"]],
                mode="manual",
            ),
        )
        assert candidates == []  # Both existing tasks are source assessment tasks.
        return result

    monkeypatch.setattr(remediation_tests, "published", after_purge)
    # Reuse PR11's real assign/start/answer/submit/check/result acceptance flow.
    await remediation_tests.test_assigned_plan_survives_republication_and_executes(c)


async def test_actual_create_upload_extract_match_group_publish_then_purge(
    execution_database,
):
    from app.application.scan_checking_contracts import (
        PageMatchingResponse,
        PageMatchResult,
    )
    from app.application.scan_intake import ScanIntakeService
    from app.application.scan_matching import MatchingTelemetry
    from app.application.scan_matching_service import ScanMatchingService
    from app.infrastructure.scan_grouping_repository import (
        SqlAlchemyScanGroupingRepository,
    )
    from app.infrastructure.scan_intake_repository import SqlAlchemyScanIntakeRepository
    from app.infrastructure.scan_page_pipeline import ScanPagePipeline
    from app.presentation.scan_intake_routes import Access
    from tests.integration.test_paper_checking_execution_database import source_pdf

    original = execution_database
    ids = dict(original[2])
    c = (original[0], original[1], ids, original[3])
    async with c[1]() as db:
        batch = await ScanIntakeService(
            SqlAlchemyScanIntakeRepository(db), Access(db, False)
        ).create_batch(
            assignment_id=ids["assignment"],
            instruction_text="Mark exactly",
            request_key=uuid4().hex,
            actor_user_id=ids["teacher"],
        )
        ids["batch"] = batch.id
        source, artifact = await ScanPagePipeline(db, c[3]).upload(
            batch_id=batch.id,
            actor_id=ids["teacher"],
            content=source_pdf(),
            claimed_mime_type="application/pdf",
            upload_position=0,
            filename="multi-student.pdf",
        )
        ids.update(
            batch_artifact=source.id,
            source=artifact.id,
            storage_source=artifact.storage_reference,
        )
        extracted = await ScanPagePipeline(db, c[3]).extract(
            batch_id=batch.id, actor_id=ids["teacher"]
        )
        states = (
            await db.execute(
                text(
                    "SELECT extraction_status,failure_code FROM scan_batch_artifacts WHERE batch_id=:batch"
                ),
                ids,
            )
        ).all()
        assert states == [("completed", None)], (states, extracted)

        class Matcher:
            provider_id = "acceptance-fake"
            model_id = "deterministic-matching"

            async def match(self, request, content):
                roster = sorted(request.roster, key=lambda r: r.display_name)
                return PageMatchingResponse(
                    pages=tuple(
                        PageMatchResult(
                            page_token=p.page_token,
                            disposition="matched",
                            proposed_roster_token=roster[p.source_order].roster_token,
                            confidence=Decimal("0.9"),
                            evidence_code="visual_identifier",
                            evidence_summary="Student name",
                            proposed_page_order=0,
                            requires_human_review=True,
                        )
                        for p in request.pages
                    )
                ), MatchingTelemetry(None)

        await ScanMatchingService(db, c[3], Matcher()).run(batch.id, ids["teacher"])
        grouping = SqlAlchemyScanGroupingRepository(db)
        draft = await grouping.initialize(batch.id, ids["teacher"])
        await grouping.confirm(
            batch.id, draft["grouping_revision"], draft["row_version"], ids["teacher"]
        )
    for paper in ("a", "b"):
        ids[f"paper_{paper}"] = await c[0].scalar(
            text(
                "SELECT id FROM paper_submissions WHERE batch_id=:batch AND assignment_participant_id=:participant"
            ),
            {**ids, "participant": ids[f"participant_{paper}"]},
        )
        ids[f"page_{paper}"] = await c[0].scalar(
            text(
                "SELECT scan_page_id FROM paper_submission_pages WHERE paper_submission_id=:paper"
            ),
            {"paper": ids[f"paper_{paper}"]},
        )
    run, _a, pub, _ = await published(c)
    _, b = await approval(c, "b")
    await publish(c, b.latest_approved_revision_id, "b")
    await action(c, "finalize")
    before = await evidence(c, run)
    student = await repo_call(c, "student_result", ids["paper_a"], ids["student_a"])
    page = await repo_call(
        c, "student_page", ids["paper_a"], ids["page_a"], ids["student_a"], c[3]
    )
    await historical_completion(c)
    assert (await sweeper(c).run(dry_run=False))["purged"] == 1
    assert not (c[3].root / ids["storage_source"]).exists()
    assert await evidence(c, run) == before
    assert (
        await repo_call(c, "student_result", ids["paper_a"], ids["student_a"])
        == student
    )
    assert (
        await repo_call(
            c, "student_page", ids["paper_a"], ids["page_a"], ids["student_a"], c[3]
        )
        == page
    )
    assert (await eligibility(c))["eligible"]
    assert (await create(c, pub))["source_paper_publication_id"] == pub["id"]


async def test_concurrent_content_read_delete_has_only_content_or_410(
    concurrent_database,
):
    c, url = concurrent_database
    await historical_completion(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    shared = (c[0], factory, c[2], c[3])
    app, _ = http_app(shared)
    path = f"/api/assessment-core/scan-batches/{c[2]['batch']}/artifacts/{c[2]['batch_artifact']}/content"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:

            async def read():
                response = await client.get(path)
                assert response.status_code in {200, 410}
                return response.status_code

            results = await asyncio.gather(
                *(read() for _ in range(8)), sweeper(shared).run(dry_run=False)
            )
            assert results[-1]["purged"] == 3
            assert (await client.get(path)).status_code == 410
    finally:
        await engine.dispose()


async def test_publication_page_lock_prevents_concurrent_retention_claim(
    concurrent_database,
):
    c, url = concurrent_database
    _, review = await approval(c)
    await historical_completion(c)
    await c[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = ScanStorageRetentionRepository(factory)
    settings = PaperRetentionSettings()
    try:
        page = next(
            t for t in await repo.discover(settings) if t.target_id == c[2]["page_a"]
        )
        async with factory() as publisher:
            await PaperResultPublicationRepository(publisher).publish(
                c[2]["paper_a"],
                review.latest_approved_revision_id,
                c[2]["teacher"],
                request_key=str(uuid4()),
            )
            assert await repo.claim(page, settings) is None
            await publisher.commit()
        assert await repo.claim(page, settings) is None
        async with factory() as db:
            assert not await db.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM scan_storage_retention_entries WHERE scan_page_id=:page_a)"
                ),
                c[2],
            )
        assert (c[3].root / c[2]["storage_a"]).exists()
    finally:
        await engine.dispose()
