"""Real-PostgreSQL acceptance proofs for the scanned-paper intake foundation.

The tests deliberately use the application service and SQLAlchemy adapter.  The
outer transaction supplied by ``rolled_back_connection`` makes every generated
identity disposable even though the service exercises its real ``commit`` path.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.scan_checking_contracts import (
    AssessmentScanBatchState,
    NORMALIZED_UPRIGHT_V1,
)
from app.application.scan_intake import (
    ArtifactExtractionStatus,
    ScanIntakeError,
    ScanIntakeService,
    ScanPageRecord,
    ScanPageStatus,
    canonical_request_hash,
)
from app.infrastructure.scan_intake_repository import SqlAlchemyScanIntakeRepository
from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class DatabaseClassAccess:
    """Small production-shaped policy which proves access against PostgreSQL."""

    def __init__(self, session):
        self.session = session

    async def can_access_class(self, class_group_id, actor_id):
        return bool(
            await self.session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM class_group_teachers "
                    "WHERE class_group_id=:class_id AND teacher_user_id=:actor_id)"
                ),
                {"class_id": class_group_id, "actor_id": actor_id},
            )
        )


@pytest_asyncio.fixture
async def intake():
    async with rolled_back_connection() as connection:
        ids = {
            name: uuid4()
            for name in (
                "teacher",
                "other_teacher",
                "group",
                "other_group",
                "assessment",
                "variant",
                "assignment",
                "artifact",
                "artifact_two",
                "foreign_artifact",
                "render_artifact",
            )
        }
        suffix = uuid4().hex
        now = datetime.now(timezone.utc)
        statements = (
            """INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES
               (:teacher,:teacher_login,:teacher_login,'Scan teacher','hash'),
               (:other_teacher,:other_login,:other_login,'Other teacher','hash')""",
            """INSERT INTO user_roles(user_id,role) VALUES
               (:teacher,'teacher'),(:other_teacher,'teacher')""",
            """INSERT INTO class_groups(id,name,created_by) VALUES
               (:group,:group_name,:teacher),
               (:other_group,:other_group_name,:other_teacher)""",
            """INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by)
               VALUES (:group,:teacher,:teacher),
                      (:other_group,:other_teacher,:other_teacher)""",
            """INSERT INTO assessments(id,title,status,created_by,published_at,published_by)
               VALUES (:assessment,'Scan acceptance','published',:teacher,
                       clock_timestamp(),:teacher)""",
            """INSERT INTO assessment_variants(id,assessment_id,name,position)
               VALUES (:variant,:assessment,'A',1)""",
            """INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by)
               VALUES (:assignment,:assessment,:group,:start,:due,:teacher)""",
            """INSERT INTO input_artifacts
                 (id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference)
               VALUES
                 (:artifact,:teacher,'application/pdf',:hash_a,100,:storage_a),
                 (:artifact_two,:teacher,'image/png',:hash_b,100,:storage_b),
                 (:foreign_artifact,:other_teacher,'application/pdf',:hash_c,100,:storage_c),
                 (:render_artifact,:teacher,'image/png',:hash_d,100,:storage_d)""",
        )
        values = {
            **ids,
            "teacher_login": f"scan-teacher-{suffix}",
            "other_login": f"scan-other-{suffix}",
            "group_name": f"Scan group {suffix}",
            "other_group_name": f"Other group {suffix}",
            "start": now,
            "due": now + timedelta(days=1),
            "hash_a": "a" * 64,
            "hash_b": "b" * 64,
            "hash_c": "c" * 64,
            "hash_d": "d" * 64,
            "storage_a": f"test://{suffix}/a",
            "storage_b": f"test://{suffix}/b",
            "storage_c": f"test://{suffix}/c",
            "storage_d": f"test://{suffix}/d",
        }
        for statement in statements:
            await connection.execute(text(statement), values)

        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        repository = SqlAlchemyScanIntakeRepository(session)
        service = ScanIntakeService(repository, DatabaseClassAccess(session))
        try:
            yield connection, session, repository, service, ids
        finally:
            await session.close()


async def create_batch(service, ids, *, actor="teacher", key=None, instruction="Mark all"):
    return await service.create_batch(
        assignment_id=ids["assignment"],
        instruction_text=instruction,
        request_key=key or f"scan-{uuid4()}",
        actor_user_id=ids[actor],
    )


async def attach(service, ids, batch_id, *, artifact="artifact", position=0, filename="paper.pdf"):
    return await service.attach_artifact(
        batch_id=batch_id,
        input_artifact_id=ids[artifact],
        upload_position=position,
        original_filename=filename,
        actor_user_id=ids["teacher"],
    )


async def event_count(connection, batch_id, event_type, aggregate_id=None):
    clause = " AND aggregate_id=:aggregate_id" if aggregate_id else ""
    return await connection.scalar(
        text(
            "SELECT count(*) FROM scan_checking_events "
            "WHERE batch_id=:batch_id AND event_type=:event_type" + clause
        ),
        {"batch_id": batch_id, "event_type": event_type, "aggregate_id": aggregate_id},
    )


async def test_batch_creation_derives_class_and_commits_created_event_atomically(intake):
    connection, _, _, service, ids = intake
    key = f"create-{uuid4()}"
    batch = await create_batch(service, ids, key=key)

    row = (
        await connection.execute(
            text(
                "SELECT assignment_id,class_group_id,created_by_user_id,status,row_version,"
                "request_key,request_hash FROM assessment_scan_batches WHERE id=:id"
            ),
            {"id": batch.id},
        )
    ).one()
    assert row.assignment_id == ids["assignment"]
    assert row.class_group_id == ids["group"]  # no class id exists in the public command
    assert row.created_by_user_id == ids["teacher"]
    assert row.status == "draft"
    assert row.row_version == 1
    assert row.request_key == key
    assert row.request_hash == canonical_request_hash(ids["assignment"], "Mark all")
    assert await event_count(connection, batch.id, "batch.created", batch.id) == 1


async def test_idempotency_is_actor_scoped_and_enforced_by_postgresql(intake):
    connection, session, repository, service, ids = intake
    key = f"same-{uuid4()}"
    first = await create_batch(service, ids, key=key)
    assert (await create_batch(service, ids, key=key)).id == first.id
    with pytest.raises(ScanIntakeError, match="idempotency_conflict"):
        await create_batch(service, ids, key=key, instruction="Different request")

    # Give the second actor access to this assignment's class. The database unique
    # constraint, rather than a mock, then proves the actor namespace is independent.
    await connection.execute(
        text(
            "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) "
            "VALUES (:group,:actor,:actor)"
        ),
        {"group": ids["group"], "actor": ids["other_teacher"]},
    )
    second = await create_batch(service, ids, actor="other_teacher", key=key)
    assert second.id != first.id

    # Bypass the pre-read to hit PostgreSQL's named unique constraint itself.
    with pytest.raises(ScanIntakeError, match="idempotency_conflict"):
        await repository.create_batch(
            assignment_id=ids["assignment"],
            class_group_id=ids["group"],
            created_by_user_id=ids["teacher"],
            instruction_text="Mark all",
            request_key=key,
            request_hash="e" * 64,
        )
    await session.rollback()


async def test_artifact_attachment_sanitization_uniqueness_and_ownership(intake):
    connection, session, _, service, ids = intake
    batch = await create_batch(service, ids)
    artifact = await attach(service, ids, batch.id, filename="  paper.pdf  ")
    row = (
        await connection.execute(
            text(
                "SELECT input_artifact_id,upload_position,original_filename,extraction_status "
                "FROM scan_batch_artifacts WHERE id=:id"
            ),
            {"id": artifact.id},
        )
    ).one()
    assert tuple(row) == (ids["artifact"], 0, "paper.pdf", "pending")
    assert await event_count(connection, batch.id, "artifact.attached", artifact.id) == 1

    with pytest.raises(ScanIntakeError, match="duplicate_artifact"):
        await attach(service, ids, batch.id, position=1)
    await session.rollback()
    with pytest.raises(ScanIntakeError, match="duplicate_upload_position"):
        await attach(service, ids, batch.id, artifact="artifact_two")
    await session.rollback()

    before = await event_count(connection, batch.id, "artifact.attached")
    with pytest.raises(ScanIntakeError, match="artifact_not_owned"):
        await attach(service, ids, batch.id, artifact="foreign_artifact", position=2)
    assert await connection.scalar(
        text("SELECT count(*) FROM scan_batch_artifacts WHERE input_artifact_id=:id"),
        {"id": ids["foreign_artifact"]},
    ) == 0
    assert await event_count(connection, batch.id, "artifact.attached") == before


async def test_pages_persist_metadata_unique_index_and_safe_record_shape(intake):
    connection, session, _, service, ids = intake
    batch = await create_batch(service, ids)
    artifact = await attach(service, ids, batch.id)
    pending = await service.create_page(
        batch_artifact_id=artifact.id,
        source_page_index=0,
        actor_user_id=ids["teacher"],
    )
    assert pending.source_page_index == 0
    ready = await service.create_page(
        batch_artifact_id=artifact.id,
        source_page_index=1,
        derived_render_artifact_id=ids["render_artifact"],
        width_px=1200,
        height_px=1600,
        rotation_degrees=90,
        coordinate_space_version=NORMALIZED_UPRIGHT_V1,
        content_fingerprint="f" * 64,
        status=ScanPageStatus.READY,
        actor_user_id=ids["teacher"],
    )
    assert ready.status is ScanPageStatus.READY
    assert ready.derived_render_artifact_id == ids["render_artifact"]
    assert "storage_reference" not in {field.name for field in fields(ScanPageRecord)}
    assert await event_count(connection, batch.id, "page.created") == 2

    with pytest.raises(ScanIntakeError, match="duplicate_page"):
        await service.create_page(
            batch_artifact_id=artifact.id,
            source_page_index=0,
            actor_user_id=ids["teacher"],
        )
    await session.rollback()


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        ("source_page_index", "-1"),
        ("rotation_degrees", "45"),
        ("content_fingerprint", "'not-a-sha'"),
        ("width_px", "0"),
        ("height_px", "-1"),
        ("status", "'ready'"),
    ],
)
async def test_scan_page_database_constraints_reject_invalid_values(intake, columns, values):
    connection, _, _, service, ids = intake
    batch = await create_batch(service, ids)
    artifact = await attach(service, ids, batch.id)
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(
                text(
                    "INSERT INTO scan_pages(batch_artifact_id,source_page_index"
                    + (f",{columns}" if columns != "source_page_index" else "")
                    + ") VALUES (:artifact,"
                    + (values if columns == "source_page_index" else f"2,{values}")
                    + ")"
                ),
                {"artifact": artifact.id},
            )


async def test_derived_render_foreign_key_is_enforced_by_postgresql(intake):
    connection, _, _, service, ids = intake
    batch = await create_batch(service, ids)
    artifact = await attach(service, ids, batch.id)
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(
                text(
                    "INSERT INTO scan_pages(batch_artifact_id,source_page_index,"
                    "derived_render_artifact_id) VALUES (:artifact,0,:missing)"
                ),
                {"artifact": artifact.id, "missing": uuid4()},
            )


async def test_status_transitions_persist_audit_and_invalid_transition_is_atomic(intake):
    connection, _, repository, service, ids = intake
    batch = await create_batch(service, ids)
    artifact = await attach(service, ids, batch.id)
    page = await service.create_page(
        batch_artifact_id=artifact.id,
        source_page_index=0,
        actor_user_id=ids["teacher"],
    )

    running = await service.transition_artifact(
        artifact_id=artifact.id,
        current=ArtifactExtractionStatus.PENDING,
        target=ArtifactExtractionStatus.RUNNING,
        actor_user_id=ids["teacher"],
    )
    completed = await service.transition_artifact(
        artifact_id=artifact.id,
        current=ArtifactExtractionStatus.RUNNING,
        target=ArtifactExtractionStatus.COMPLETED,
        page_count=1,
        actor_user_id=ids["teacher"],
    )
    assert running.extraction_status is ArtifactExtractionStatus.RUNNING
    assert completed.extraction_status is ArtifactExtractionStatus.COMPLETED
    assert await event_count(
        connection, batch.id, "artifact.extraction_status_changed", artifact.id
    ) == 2

    ready = await service.transition_page(
        page_id=page.id,
        current=ScanPageStatus.PENDING,
        target=ScanPageStatus.READY,
        width_px=100,
        height_px=200,
        rotation_degrees=270,
        coordinate_space_version=NORMALIZED_UPRIGHT_V1,
        content_fingerprint="1" * 64,
        actor_user_id=ids["teacher"],
    )
    assert ready.status is ScanPageStatus.READY
    assert await event_count(connection, batch.id, "page.status_changed", page.id) == 1

    uploading = await service.transition_batch(
        batch_id=batch.id,
        target=AssessmentScanBatchState.UPLOADING,
        actor_user_id=ids["teacher"],
    )
    assert uploading.status is AssessmentScanBatchState.UPLOADING
    assert uploading.row_version == 2

    with pytest.raises(ScanIntakeError, match="concurrent_scan_batch_update"):
        await repository.transition_batch(
            batch_id=batch.id,
            target=AssessmentScanBatchState.EXTRACTING,
            actor_user_id=ids["teacher"],
            expected_version=1,
        )

    event_total = await connection.scalar(
        text("SELECT count(*) FROM scan_checking_events WHERE batch_id=:id"),
        {"id": batch.id},
    )
    with pytest.raises(ScanIntakeError, match="invalid_batch_transition"):
        await service.transition_batch(
            batch_id=batch.id,
            target=AssessmentScanBatchState.COMPLETED,
            actor_user_id=ids["teacher"],
        )
    assert await connection.scalar(
        text("SELECT row_version FROM assessment_scan_batches WHERE id=:id"),
        {"id": batch.id},
    ) == 2
    assert await connection.scalar(
        text("SELECT count(*) FROM scan_checking_events WHERE batch_id=:id"),
        {"id": batch.id},
    ) == event_total


async def test_cancellation_is_idempotent_audited_and_blocks_attachment(intake):
    connection, _, _, service, ids = intake
    batch = await create_batch(service, ids)
    cancelled = await service.cancel_batch(
        batch_id=batch.id, actor_user_id=ids["teacher"], reason="  duplicate scans  "
    )
    again = await service.cancel_batch(
        batch_id=batch.id, actor_user_id=ids["teacher"], reason="ignored on retry"
    )
    assert cancelled.status is AssessmentScanBatchState.CANCELLED
    assert cancelled.cancelled_at is not None
    assert cancelled.cancelled_by_user_id == ids["teacher"]
    assert cancelled.cancellation_reason == "duplicate scans"
    assert again.row_version == cancelled.row_version == 2
    assert await event_count(connection, batch.id, "batch.cancelled", batch.id) == 1
    with pytest.raises(ScanIntakeError, match="batch_cancelled"):
        await attach(service, ids, batch.id)
    assert await connection.scalar(
        text("SELECT count(*) FROM scan_batch_artifacts WHERE batch_id=:id"),
        {"id": batch.id},
    ) == 0


@pytest.mark.parametrize(
    ("event_type", "operation", "row_table"),
    [
        ("batch.created", "batch", "assessment_scan_batches"),
        ("artifact.attached", "artifact", "scan_batch_artifacts"),
        ("page.created", "page", "scan_pages"),
    ],
)
async def test_event_failure_rolls_back_aggregate_and_propagates_integrity_error(
    intake, event_type, operation, row_table
):
    connection, session, _, service, ids = intake
    batch = artifact = None
    if operation != "batch":
        batch = await create_batch(service, ids)
    if operation == "page":
        artifact = await attach(service, ids, batch.id)
    await connection.execute(
        text(
            "ALTER TABLE scan_checking_events ADD CONSTRAINT "
            f"ck_test_reject_target_event CHECK (event_type <> '{event_type}')"
        )
    )
    before = await connection.scalar(text(f"SELECT count(*) FROM {row_table}"))
    with pytest.raises(IntegrityError):
        if operation == "batch":
            await create_batch(service, ids)
        elif operation == "artifact":
            await attach(service, ids, batch.id)
        else:
            await service.create_page(
                batch_artifact_id=artifact.id,
                source_page_index=0,
                actor_user_id=ids["teacher"],
            )
    await session.rollback()
    assert await connection.scalar(text(f"SELECT count(*) FROM {row_table}")) == before
    await connection.execute(
        text("ALTER TABLE scan_checking_events DROP CONSTRAINT ck_test_reject_target_event")
    )
