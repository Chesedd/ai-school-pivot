"""Real PostgreSQL publication, isolation and unchanged evidence acceptance."""

import asyncio
import json
import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from app.application.capabilities import capabilities_for_roles
from app.application.paper_checking_review import (
    PaperReviewApproval,
    PaperReviewFinding,
    PaperReviewOverride,
)
from app.application.paper_result_publication import (
    PaperPublicationError,
    PublicationEntry,
)
from app.application.principal import Principal
from app.config import get_settings
from app.db.session import get_session
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from app.presentation.auth_dependencies import require_principal
from app.presentation.image_artifact_routes import router as artifact_router
from app.presentation.paper_result_publication_routes import router
from app.presentation.scan_intake_routes import router as scan_router
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration import c10a_postgres
from tests.integration.test_paper_checking_execution_database import PNG
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_review_database import (
    call,
    completed,
    edited,
    evidence,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


@pytest_asyncio.fixture
async def concurrent_database(tmp_path, monkeypatch):
    """Clone only the disposable test DB, commit seed, then drop the clone.

    Ordinary tests remain rollback isolated. Separate real connections need
    committed seed data; a disposable database avoids immutable-history cleanup.
    """
    base = make_url(c10a_postgres.require_disposable_postgres())
    name = f"publication_concurrency_{uuid4().hex}_test"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    seed = None
    try:
        async with admin.connect() as db:
            assert base.database.endswith("_test")
            await db.execute(
                text(
                    f'CREATE DATABASE "{name}" TEMPLATE "{base.database.replace(chr(34), chr(34) * 2)}"'
                )
            )
        target = base.set(database=name)
        monkeypatch.setattr(
            c10a_postgres,
            "TEST_DATABASE_URL",
            target.render_as_string(hide_password=False),
        )
        seed = execution_database.__wrapped__(tmp_path)
        context = await anext(seed)
        yield context, target
    finally:
        if seed is not None:
            await seed.aclose()
        async with admin.connect() as db:
            await db.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await admin.dispose()


@pytest.mark.parametrize("same_revision", [True, False])
async def test_real_concurrent_publications_serialize(
    concurrent_database, same_revision
):
    context, url = concurrent_database
    run, a = await approval(context)
    b = a
    if not same_revision:
        change = await call(
            context, run, "edit", edited(a, summary="Second revision"), a.version
        )
        b = await call(
            context,
            run,
            "approve",
            PaperReviewApproval(expected_version=change.version),
        )
    await context[0].commit()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = context[2]
    task = None
    try:
        async with factory() as first, factory() as second:
            first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            row_a = await PaperResultPublicationRepository(first).publish(
                ids["paper_a"],
                a.latest_approved_revision_id,
                ids["teacher"],
                request_key=str(uuid4()),
            )

            async def contender():
                row = await PaperResultPublicationRepository(second).publish(
                    ids["paper_a"],
                    b.latest_approved_revision_id,
                    ids["teacher"],
                    request_key=str(uuid4()),
                )
                await second.commit()
                return row

            task = asyncio.create_task(contender())
            # Prove the second backend actually waits on the first transaction.
            for _ in range(100):
                blockers = await first.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                )
                if first_pid in blockers:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("second publisher did not wait on the paper lock")
            assert not task.done()
            await first.commit()
            row_b = await asyncio.wait_for(task, 10)
            history = await PaperResultPublicationRepository(second).history(
                ids["paper_a"]
            )
            assert [x["publication_number"] for x in history] == (
                [1] if same_revision else [1, 2]
            )
            assert sum(x["active"] for x in history) == 1
            assert history[-1]["id"] == row_b["id"]
            assert (row_a["id"] == row_b["id"]) == same_revision
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


async def approval(context, paper="a"):
    run = await completed(context, paper=paper)
    review = await call(context, run, "start", paper=paper)
    review = await call(
        context,
        run,
        "approve",
        PaperReviewApproval(expected_version=review.version),
        paper=paper,
    )
    return run, review


async def publish(context, revision, paper="a", *, request_key=None):
    _, factory, ids, _ = context
    async with factory() as db, db.begin():
        return await PaperResultPublicationRepository(db).publish(
            ids[f"paper_{paper}"],
            revision,
            ids["teacher"],
            request_key=request_key or str(uuid4()),
        )


async def repo_call(context, method, *args, request_key=None):
    async with context[1]() as db, db.begin():
        kwargs = (
            {"request_key": request_key or str(uuid4())}
            if method
            in {"publish", "publish_selected", "bulk", "withdraw", "withdraw_selected"}
            else {}
        )
        return await getattr(PaperResultPublicationRepository(db), method)(
            *args, **kwargs
        )


async def test_correction_republication_is_explicit_frozen_and_idempotent(
    execution_database,
):
    context = execution_database
    _, _, ids, _ = context
    run, first = await approval(context)
    before = await evidence(context, run)
    revisions = (await call(context, run, "history"))["revisions"]
    assert await repo_call(context, "history", ids["paper_a"]) == []
    a = await publish(context, first.latest_approved_revision_id)
    replay = await publish(context, first.latest_approved_revision_id)
    assert a["id"] == replay["id"] and replay["publication_number"] == 1
    assert replay["outcome"] == "already_current"
    assert (await call(context, run, "history"))["revisions"] == revisions
    correction = await call(
        context,
        run,
        "edit",
        edited(
            first,
            summary="New teacher correction",
            items=(
                first.draft.items[0].model_copy(
                    update={
                        "status": "partially_correct",
                        "accepted_score": Decimal(5),
                    }
                ),
            ),
        ),
        first.version,
    )
    assert (
        await repo_call(context, "student_result", ids["paper_a"], ids["student_a"])
    )["id"] == a["id"]
    second = await call(
        context,
        run,
        "approve",
        PaperReviewApproval(expected_version=correction.version),
    )
    assert (
        await repo_call(context, "student_result", ids["paper_a"], ids["student_a"])
    )["result"] == a["result"]
    b = await publish(context, second.latest_approved_revision_id)
    assert (
        b["publication_number"] == 2
        and b["result"]["summary"] == "New teacher correction"
    )
    assert Decimal(a["result"]["grade"]["calculated_total_score"]) == 10
    assert Decimal(b["result"]["grade"]["calculated_total_score"]) == 5
    assert Decimal(b["result"]["grade"]["calculated_percentage"]) == 50
    history = await repo_call(context, "history", ids["paper_a"])
    assert [x["active"] for x in history] == [False, True]
    assert history[0]["result"] == a["result"]
    # Re-publishing an older revision is a new temporal action, preserving history.
    c = await publish(context, first.latest_approved_revision_id)
    assert (
        c["publication_number"] == 3
        and c["id"] != a["id"]
        and c["result"] == a["result"]
    )
    assert before == await evidence(context, run)
    assert (await repo_call(context, "student_list", ids["student_a"]))[0]["id"] == c[
        "id"
    ]
    connection = context[0]
    assert (
        await connection.scalar(
            text("SELECT status FROM paper_submissions WHERE id=:id"),
            {"id": ids["paper_a"]},
        )
        == "ready_for_checking"
    )
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "checking"
    )


@pytest.mark.parametrize("kind", ["ai_seed", "human_edit", "missing", "foreign"])
async def test_invalid_revision_rejected_without_writes(execution_database, kind):
    context = execution_database
    run = await completed(context)
    review = await call(context, run, "start")
    if kind == "human_edit":
        await call(
            context, run, "edit", edited(review, summary="edited"), review.version
        )
    history = await call(context, run, "history")
    revision = history["revisions"][-1].id
    if kind == "missing":
        revision = uuid4()
    if kind == "foreign":
        _, other = await approval(context, "b")
        revision = other.latest_approved_revision_id
    with pytest.raises(PaperPublicationError):
        await publish(context, revision)
    assert await repo_call(context, "history", context[2]["paper_a"]) == []


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
async def test_publication_db_immutability(execution_database, operation):
    _, review = await approval(execution_database)
    row = await publish(execution_database, review.latest_approved_revision_id)
    connection = execution_database[0]
    with pytest.raises(DBAPIError, match="checking history is immutable"):
        async with connection.begin_nested():
            await connection.execute(
                text(
                    "UPDATE paper_result_publications SET publication_number=20 WHERE id=:id"
                    if operation == "UPDATE"
                    else "DELETE FROM paper_result_publications WHERE id=:id"
                ),
                {"id": row["id"]},
            )


async def test_teacher_findings_frozen_grade_override_and_ai_unchanged(
    execution_database,
):
    context = execution_database
    run = await completed(context)
    before = await evidence(context, run)
    review = await call(context, run, "start")
    original = review.draft.findings[0]
    added = PaperReviewFinding.model_validate_json(
        json.dumps(
            {
                **original.model_dump(mode="json"),
                "finding_id": str(uuid4()),
                "source_check_finding_id": None,
                "short_explanation": "Teacher addition",
            }
        )
    )
    corrected = original.model_copy(
        update={
            "finding_id": uuid4(),
            "short_explanation": "Teacher correction",
            "region": original.region.model_copy(update={"x": Decimal("0.75")}),
        }
    )
    review = await call(
        context,
        run,
        "edit",
        edited(review, findings=(corrected, added)),
        review.version,
    )
    review = await call(
        context,
        run,
        "approve",
        PaperReviewApproval(
            expected_version=review.version,
            grade_override=PaperReviewOverride(
                override_grade="Excellent", reason="Internal teacher rationale"
            ),
        ),
    )
    revisions = (await call(context, run, "history"))["revisions"]
    row = await publish(context, review.latest_approved_revision_id)
    findings = row["result"]["findings"]
    assert str(original.finding_id) not in {f["finding_id"] for f in findings}
    assert {f["short_explanation"] for f in findings} == {
        "Teacher correction",
        "Teacher addition",
    }
    assert row["result"]["grade"]["final_grade"] == "Excellent"
    assert (
        next(f for f in findings if f["short_explanation"] == "Teacher correction")[
            "region"
        ]["x"]
        == "0.75"
    )
    assert "Internal teacher rationale" not in str(row)
    assert before == await evidence(context, run)
    assert revisions == (await call(context, run, "history"))["revisions"]


@pytest.mark.parametrize("invalid", ["unapproved", "foreign_paper", "foreign_batch"])
async def test_selected_is_all_or_nothing(execution_database, invalid):
    context = execution_database
    _, a = await approval(context)
    run_b = await completed(context, paper="b")
    await call(context, run_b, "start", paper="b")
    revision_b = (await call(context, run_b, "history", paper="b"))["revisions"][0].id
    ids = context[2]
    entries = [
        PublicationEntry(
            paper_submission_id=ids["paper_a"],
            approved_review_revision_id=a.latest_approved_revision_id,
        ),
        PublicationEntry(
            paper_submission_id=ids["paper_b"],
            approved_review_revision_id=revision_b
            if invalid == "unapproved"
            else a.latest_approved_revision_id,
        ),
    ]
    with pytest.raises(PaperPublicationError):
        await repo_call(
            context,
            "publish_selected",
            entries,
            ids["teacher"],
            uuid4() if invalid == "foreign_batch" else ids["batch"],
        )
    assert await repo_call(context, "history", ids["paper_a"]) == []
    assert await repo_call(context, "history", ids["paper_b"]) == []


async def test_bulk_strict_ineligible_and_already_current(execution_database):
    context = execution_database
    ids = context[2]
    _, a = await approval(context)
    with pytest.raises(PaperPublicationError, match="ineligible"):
        await repo_call(context, "bulk", ids["batch"], ids["teacher"])
    assert await repo_call(context, "history", ids["paper_a"]) == []
    await approval(context, "b")
    rows = await repo_call(context, "bulk", ids["batch"], ids["teacher"])
    assert len(rows) == 2 and {x["outcome"] for x in rows} == {"published"}
    rows = await repo_call(context, "bulk", ids["batch"], ids["teacher"])
    assert len(rows) == 2 and {x["outcome"] for x in rows} == {"already_current"}
    assert (await repo_call(context, "history", ids["paper_a"]))[0][
        "approved_review_revision_id"
    ] == a.latest_approved_revision_id


async def test_student_http_owner_isolation_and_source_pdf_denial(execution_database):
    context = execution_database
    _, factory, ids, storage = context
    _, review = await approval(context)
    identity = {"role": "student", "student_id": ids["student_a"], "user_id": uuid4()}
    app = FastAPI()
    app.include_router(router)
    app.include_router(scan_router)
    app.include_router(artifact_router)

    async def session():
        async with factory() as db:
            yield db

    def principal():
        roles = frozenset({identity["role"]})
        return Principal(
            identity["user_id"],
            "Private B",
            "Private B",
            roles,
            capabilities_for_roles(roles),
            identity["student_id"],
        )

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[require_principal] = principal
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"artifact_storage_path": str(storage.root)}
    )
    base = "/api/assessment-core"
    own = f"{base}/student/paper-submissions/{ids['paper_a']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": "single"},
    ) as client:
        assert (await client.get(own + "/result")).status_code == 404
        assert (await client.get(own + f"/pages/{ids['page_a']}")).status_code == 404
        assert (await client.get(base + "/student/paper-results")).json() == {
            "items": []
        }
        await publish(context, review.latest_approved_revision_id)
        response = await client.get(own + "/result")
        assert response.status_code == 200
        body = response.json()
        assert len(body["pages"]) == 1 and body["pages"][0]["scan_page_id"] == str(
            ids["page_a"]
        )
        for forbidden in (
            "storage_reference",
            "original_filename",
            "source_check",
            "raw_output",
            "Private B",
            str(ids["source"]),
            str(ids["student_b"]),
        ):
            assert forbidden not in response.text
        assert (await client.get(body["pages"][0]["content_url"])).content == PNG
        _, other = await approval(context, "b")
        other_pub = await publish(context, other.latest_approved_revision_id, "b")
        paths = [
            own + f"/pages/{ids['page_b']}",
            own + f"/pages/{ids['source']}",
            own + f"/pages/{ids['render_b']}",
            own + f"/pages/{uuid4()}",
            f"{base}/student/paper-submissions/{ids['paper_b']}/result",
            f"{base}/student/paper-submissions/{ids['paper_b']}/pages/{ids['page_b']}",
            f"{base}/student/paper-submissions/{other_pub['id']}/result",
        ]
        for path in paths:
            denied = await client.get(path)
            assert denied.status_code == 404 and denied.json() == {
                "detail": "paper_result_not_found"
            }
        for path in (
            f"{base}/scan-pages/{ids['page_b']}/content",
            f"{base}/scan-batches/{ids['batch']}/artifacts",
        ):
            assert (await client.get(path)).status_code == 403
        assert (
            await client.get(f"/api/image-solving/artifacts/{ids['source']}")
        ).status_code == 403
        assert (
            len((await client.get(base + "/student/paper-results")).json()["items"])
            == 1
        )
        identity["student_id"] = None
        assert (await client.get(own + "/result")).status_code == 403
        identity.update(role="teacher", student_id=ids["student_a"])
        assert (await client.get(own + "/result")).status_code == 403


@pytest.mark.parametrize("role", ["teacher", "student"])
@pytest.mark.parametrize("route", ["single", "history", "selected", "bulk"])
async def test_teacher_routes_authorization(execution_database, role, route):
    context = execution_database
    ids = context[2]
    _, review = await approval(context)
    app = FastAPI()
    app.include_router(router)
    identity = {"id": ids["teacher"], "role": "teacher"}

    async def session():
        async with context[1]() as db:
            yield db

    def principal():
        roles = frozenset({identity["role"]})
        return Principal(
            identity["id"],
            "teacher",
            "Teacher",
            roles,
            capabilities_for_roles(roles),
            None,
        )

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[require_principal] = principal
    single = f"/api/assessment-core/paper-submissions/{ids['paper_a']}/publications"
    batch = f"/api/assessment-core/scan-batches/{ids['batch']}/publications"
    payload = {"approved_review_revision_id": str(review.latest_approved_revision_id)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": "single"},
    ) as client:
        accepted = await client.post(single, json=payload)
        assert accepted.status_code == 200, accepted.text
        if route == "history":
            assert (await client.get(single)).json()["items"][0][
                "id"
            ] == accepted.json()["id"]
        elif route == "selected":
            selected = await client.post(
                batch + "/selected",
                headers={"Idempotency-Key": "selected"},
                json={
                    "entries": [{**payload, "paper_submission_id": str(ids["paper_a"])}]
                },
            )
            assert selected.status_code == 200, selected.text
            assert selected.json()["items"][0]["outcome"] == "already_current"
        elif route == "bulk":
            await approval(context, "b")
            bulk = await client.post(
                batch + "/bulk", headers={"Idempotency-Key": "bulk"}
            )
            assert bulk.status_code == 200, bulk.text
            assert len(bulk.json()["items"]) == 2
            replay = await client.post(
                batch + "/bulk", headers={"Idempotency-Key": "bulk"}
            )
            assert replay.json() == bulk.json()
        identity.update(id=uuid4(), role=role)
        if route == "history":
            response = await client.get(single)
        elif route == "single":
            response = await client.post(single, json=payload)
        elif route == "selected":
            response = await client.post(
                batch + "/selected",
                headers={"Idempotency-Key": "selected"},
                json={
                    "entries": [{**payload, "paper_submission_id": str(ids["paper_a"])}]
                },
            )
        else:
            response = await client.post(
                batch + "/bulk", headers={"Idempotency-Key": "bulk"}
            )
        assert response.status_code == (404 if role == "teacher" else 403)


@pytest.mark.parametrize(
    "tamper", ["foreign_page", "region", "coordinates", "grade", "policy", "provenance"]
)
async def test_corrupted_approved_revision_fails_closed(execution_database, tamper):
    context = execution_database
    _, review = await approval(context)
    ids = context[2]
    snapshot = review.latest_approved_revision.snapshot.model_dump(mode="json")
    values = {"id": review.latest_approved_revision_id}
    if tamper == "foreign_page":
        snapshot["findings"][0]["scan_page_id"] = str(ids["page_b"])
    elif tamper == "region":
        snapshot["findings"][0]["region"] = {"kind": "point", "x": "1.1", "y": "0"}
    elif tamper == "coordinates":
        snapshot["findings"][0]["coordinate_space_version"] = "pixels"
    elif tamper == "provenance":
        snapshot["check_run_id"] = str(uuid4())
    values["snapshot"] = json.dumps(snapshot)
    # Deliberate fault injection only in the rollback-isolated test database.
    connection = context[0]
    await connection.execute(
        text(
            "ALTER TABLE paper_check_review_revisions DISABLE TRIGGER trg_paper_check_review_revisions_immutable"
        )
    )
    await connection.execute(
        text(
            "UPDATE paper_check_review_revisions SET snapshot=CAST(:snapshot AS jsonb) WHERE id=:id"
        ),
        values,
    )
    if tamper == "grade":
        await connection.execute(
            text(
                "UPDATE paper_check_review_revisions SET calculated_percentage=42 WHERE id=:id"
            ),
            values,
        )
    if tamper == "policy":
        await connection.execute(
            text(
                "UPDATE paper_check_review_revisions SET grading_policy_fingerprint=repeat('0',64) WHERE id=:id"
            ),
            values,
        )
    await connection.execute(
        text(
            "ALTER TABLE paper_check_review_revisions ENABLE TRIGGER trg_paper_check_review_revisions_immutable"
        )
    )
    with pytest.raises(PaperPublicationError):
        await publish(context, review.latest_approved_revision_id)
    assert await repo_call(context, "history", ids["paper_a"]) == []


@pytest.mark.parametrize(
    "tamper", ["source", "foreign_render", "missing_file", "bad_hash", "not_ready"]
)
async def test_student_page_artifact_validation(execution_database, tamper):
    context = execution_database
    _, review = await approval(context)
    ids = context[2]
    await publish(context, review.latest_approved_revision_id)
    connection = context[0]
    if tamper == "source":
        await connection.execute(
            text(
                "UPDATE scan_pages SET derived_render_artifact_id=:source WHERE id=:page_a"
            ),
            ids,
        )
    elif tamper == "foreign_render":
        # An arbitrary nonmember artifact identifier is never accepted as page id.
        with pytest.raises(PaperPublicationError):
            await repo_call(
                context,
                "student_page",
                ids["paper_a"],
                ids["render_b"],
                ids["student_a"],
                context[3],
            )
        return
    elif tamper == "missing_file":
        await context[3].delete(ids["storage_a"])
    elif tamper == "bad_hash":
        await connection.execute(
            text(
                "UPDATE scan_pages SET content_fingerprint=repeat('0',64) WHERE id=:page_a"
            ),
            ids,
        )
    else:
        await connection.execute(
            text("UPDATE scan_pages SET status='pending' WHERE id=:page_a"), ids
        )
    with pytest.raises(PaperPublicationError, match="paper_result_not_found"):
        await repo_call(
            context,
            "student_page",
            ids["paper_a"],
            ids["page_a"],
            ids["student_a"],
            context[3],
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "foreign_pointer",
        "version",
        "delete_state",
        "foreign_revision",
        "foreign_supersedes",
    ],
)
async def test_database_publication_constraints(execution_database, tamper):
    context = execution_database
    _, a = await approval(context)
    _, b = await approval(context, "b")
    published_a = await publish(context, a.latest_approved_revision_id)
    published_b = await publish(context, b.latest_approved_revision_id, "b")
    ids = context[2]
    values = {
        "a": ids["paper_a"],
        "foreign": published_b["id"],
        "revision": b.latest_approved_revision_id,
        "review": b.review_id,
        "teacher": ids["teacher"],
        "snapshot": json.dumps(published_a["result"]),
    }
    sql = {
        "foreign_pointer": "UPDATE paper_result_publication_states SET active_publication_id=:foreign,version=version+1 WHERE paper_submission_id=:a",
        "version": "UPDATE paper_result_publication_states SET version=version WHERE paper_submission_id=:a",
        "delete_state": "DELETE FROM paper_result_publication_states WHERE paper_submission_id=:a",
        "foreign_revision": "INSERT INTO paper_result_publications(paper_submission_id,review_id,approved_review_revision_id,published_by_user_id,publication_number,publication_snapshot) VALUES (:a,:review,:revision,:teacher,2,CAST(:snapshot AS jsonb))",
        "foreign_supersedes": "INSERT INTO paper_result_publications(paper_submission_id,review_id,approved_review_revision_id,published_by_user_id,publication_number,supersedes_publication_id,publication_snapshot) VALUES (:a,:review,:revision,:teacher,2,:foreign,CAST(:snapshot AS jsonb))",
    }[tamper]
    if tamper == "foreign_supersedes":
        values.update(review=a.review_id, revision=a.latest_approved_revision_id)
    with pytest.raises(DBAPIError):
        async with context[0].begin_nested():
            await context[0].execute(text(sql), values)
    assert (
        await repo_call(context, "student_result", ids["paper_a"], ids["student_a"])
    )["id"] == published_a["id"]
