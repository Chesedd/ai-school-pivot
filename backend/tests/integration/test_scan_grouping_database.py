"""Real PostgreSQL acceptance coverage for scan grouping and paper submissions.

The fixture is intentionally persistence-heavy: it creates the complete, FK-valid
matching evidence consumed by the production repository.  The service is never
mocked, and the outer transaction survives its commits by using a savepoint-backed
``AsyncSession``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.scan_grouping import ScanGroupingError, ScanGroupingService
from app.infrastructure.classroom_repository import SQLAlchemyClassroomAccessRepository
from app.infrastructure.scan_grouping_repository import SqlAlchemyScanGroupingRepository
from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def grouping_database():
    async with rolled_back_connection() as connection:
        names = (
            "teacher co_teacher unrelated_teacher group other_group assessment other_assessment "
            "variant_a variant_b other_variant assignment other_assignment student_a student_b "
            "other_student participant_a participant_b other_participant source_artifact "
            "batch_artifact batch foreign_batch foreign_batch_artifact matching_run"
        ).split()
        ids = {name: uuid4() for name in names}
        ids["pages"] = [uuid4() for _ in range(5)]
        ids["foreign_page"] = uuid4()
        ids["renders"] = [uuid4() for _ in range(6)]
        suffix = uuid4().hex
        now = datetime.now(timezone.utc)
        values = {
            **ids,
            "teacher_login": f"grouping-teacher-{suffix}",
            "co_login": f"grouping-co-{suffix}",
            "unrelated_login": f"grouping-unrelated-{suffix}",
            "group_name": f"Grouping class {suffix}",
            "other_group_name": f"Other grouping class {suffix}",
            "start": now,
            "due": now + timedelta(days=1),
            "request_key": f"grouping-{suffix}",
            "foreign_request_key": f"foreign-grouping-{suffix}",
        }
        statements = (
            """INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES
               (:teacher,:teacher_login,:teacher_login,'Grouping teacher','hash'),
               (:co_teacher,:co_login,:co_login,'Grouping co-teacher','hash'),
               (:unrelated_teacher,:unrelated_login,:unrelated_login,'Unrelated teacher','hash')""",
            """INSERT INTO user_roles(user_id,role) VALUES
               (:teacher,'teacher'),(:co_teacher,'teacher'),(:unrelated_teacher,'teacher')""",
            """INSERT INTO class_groups(id,name,created_by) VALUES
               (:group,:group_name,:teacher),(:other_group,:other_group_name,:unrelated_teacher)""",
            """INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES
               (:group,:teacher,:teacher),(:group,:co_teacher,:teacher),
               (:other_group,:unrelated_teacher,:unrelated_teacher)""",
            """INSERT INTO students(id,class_group_id,display_name) VALUES
               (:student_a,:group,'Student Alpha'),(:student_b,:group,'Student Beta'),
               (:other_student,:other_group,'Student Outside')""",
            """INSERT INTO assessments(id,title,status,created_by,published_at,published_by) VALUES
               (:assessment,'Grouping assessment','published',:teacher,clock_timestamp(),:teacher),
               (:other_assessment,'Other assessment','published',:unrelated_teacher,clock_timestamp(),:unrelated_teacher)""",
            """INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES
               (:variant_a,:assessment,'A',1),(:variant_b,:assessment,'B',2),
               (:other_variant,:other_assessment,'Outside',1)""",
            """INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES
               (:assignment,:assessment,:group,:start,:due,3,:teacher),
               (:other_assignment,:other_assessment,:other_group,:start,:due,7,:unrelated_teacher)""",
            """INSERT INTO assignment_participants
                 (id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES
               (:participant_a,:assignment,:student_a,:variant_a,clock_timestamp()),
               (:participant_b,:assignment,:student_b,:variant_b,clock_timestamp()),
               (:other_participant,:other_assignment,:other_student,:other_variant,clock_timestamp())""",
            """INSERT INTO input_artifacts
                 (id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference)
               VALUES (:source_artifact,:teacher,'application/pdf',:source_hash,600,:source_storage)""",
            """INSERT INTO assessment_scan_batches
                 (id,assignment_id,class_group_id,created_by_user_id,status,instruction_text,
                  matching_revision,row_version,request_key,request_hash) VALUES
               (:batch,:assignment,:group,:teacher,'matching_review_required','Group papers',1,4,
                :request_key,:request_hash),
               (:foreign_batch,:other_assignment,:other_group,:unrelated_teacher,
                'matching_review_required','Other papers',1,1,:foreign_request_key,:foreign_hash)""",
            """INSERT INTO scan_batch_artifacts
                 (id,batch_id,input_artifact_id,upload_position,original_filename,extraction_status,page_count)
               VALUES (:batch_artifact,:batch,:source_artifact,0,'papers.pdf','completed',5)""",
        )
        values.update(
            source_hash="0" * 64,
            source_storage=f"test://{suffix}/original.pdf",
            request_hash="1" * 64,
            foreign_hash="2" * 64,
        )
        for statement in statements:
            await connection.execute(text(statement), values)

        for index, render_id in enumerate(ids["renders"]):
            await connection.execute(
                text("""INSERT INTO input_artifacts
                     (id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference)
                     VALUES (:id,:owner,'image/png',:hash,100,:storage)"""),
                {
                    "id": render_id,
                    "owner": ids["teacher"],
                    "hash": f"{index + 3:x}" * 64,
                    "storage": f"test://{suffix}/normalized-{index}.png",
                },
            )
        for index, page_id in enumerate(ids["pages"]):
            await connection.execute(
                text("""INSERT INTO scan_pages
                     (id,batch_artifact_id,source_page_index,derived_render_artifact_id,
                      width_px,height_px,rotation_degrees,coordinate_space_version,
                      content_fingerprint,status)
                     VALUES (:id,:artifact,:position,:render,1200,1600,0,
                             'normalized_upright_v1',:fingerprint,'ready')"""),
                {
                    "id": page_id,
                    "artifact": ids["batch_artifact"],
                    "position": index,
                    "render": ids["renders"][index],
                    "fingerprint": f"{index + 9:x}" * 64,
                },
            )
        # A second batch and page exist solely to prove ownership boundaries.
        await connection.execute(
            text("""INSERT INTO scan_batch_artifacts
                 (id,batch_id,input_artifact_id,upload_position,original_filename,extraction_status,page_count)
                 VALUES (:id,:batch,:artifact,0,'outside.pdf','completed',1)"""),
            {
                "id": ids["foreign_batch_artifact"],
                "batch": ids["foreign_batch"],
                "artifact": ids["renders"][5],
            },
        )
        await connection.execute(
            text("""INSERT INTO scan_pages
                 (id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,
                  rotation_degrees,coordinate_space_version,content_fingerprint,status)
                 VALUES (:id,:artifact,0,:render,100,200,0,'normalized_upright_v1',:hash,'ready')"""),
            {
                "id": ids["foreign_page"],
                "artifact": ids["foreign_batch_artifact"],
                "render": ids["renders"][5],
                "hash": "f" * 64,
            },
        )
        await connection.execute(
            text("""INSERT INTO scan_matching_runs
                 (id,batch_id,revision,requested_by_user_id,status,prompt_name,prompt_version,
                  prompt_template_hash,output_schema_version,provider_route,
                  request_context_fingerprint,assessment_title_snapshot,completed_at)
                 VALUES (:matching_run,:batch,1,:teacher,'succeeded','scan-match','1','a'||repeat('a',63),
                         'v1','test/no-network','b'||repeat('b',63),'Grouping assessment',clock_timestamp())"""),
            ids,
        )
        for position, participant in enumerate(
            (ids["participant_a"], ids["participant_b"])
        ):
            await connection.execute(
                text("""INSERT INTO scan_matching_roster_entries
                     (matching_run_id,roster_token,assignment_participant_id,display_name_snapshot,position)
                     VALUES (:run,:token,:participant,:name,:position)"""),
                {
                    "run": ids["matching_run"],
                    "token": f"roster-{position}",
                    "participant": participant,
                    "name": f"Student {position}",
                    "position": position,
                },
            )
        dispositions = (
            ("matched", ids["participant_a"], 1),
            ("matched", ids["participant_a"], 0),
            ("matched", ids["participant_b"], 0),
            ("ambiguous", None, None),
            ("unmatched", None, None),
        )
        for position, (disposition, participant, proposed_order) in enumerate(
            dispositions
        ):
            page_id = ids["pages"][position]
            await connection.execute(
                text("""INSERT INTO scan_matching_page_entries
                     (matching_run_id,page_token,scan_page_id,source_order)
                     VALUES (:run,:token,:page,:position)"""),
                {
                    "run": ids["matching_run"],
                    "token": f"page-{position}",
                    "page": page_id,
                    "position": position,
                },
            )
            await connection.execute(
                text("""INSERT INTO scan_page_match_proposals
                     (matching_run_id,scan_page_id,disposition,proposed_assignment_participant_id,
                      confidence,evidence_code,evidence_summary,proposed_group_token,
                      proposed_page_order,provider_requires_human_review)
                     VALUES (:run,:page,:disposition,:participant,.8,'visual_identifier',
                             'bounded evidence',:group_token,:page_order,true)"""),
                {
                    "run": ids["matching_run"],
                    "page": page_id,
                    "disposition": disposition,
                    "participant": participant,
                    "group_token": "ai-group" if participant else None,
                    "page_order": proposed_order,
                },
            )
        await connection.execute(
            text("""INSERT INTO scan_page_match_candidates
                 (matching_run_id,scan_page_id,assignment_participant_id,rank)
                 VALUES (:run,:page,:participant_a,1),(:run,:page,:participant_b,2)"""),
            {"run": ids["matching_run"], "page": ids["pages"][3], **ids},
        )

        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        service = ScanGroupingService(SqlAlchemyScanGroupingRepository(session))
        try:
            yield connection, session, service, ids
        finally:
            await session.close()


async def scalar(connection, sql, ids, **values):
    return await connection.scalar(text(sql), {**ids, **values})


async def rows(connection, sql, ids, **values):
    return (await connection.execute(text(sql), {**ids, **values})).all()


async def initialize(service, ids):
    return await service.initialize(ids["batch"], ids["teacher"])


async def resolve_all(service, ids, view):
    for page_id, participant in zip(
        ids["pages"][3:], (ids["participant_b"], ids["participant_a"])
    ):
        view = await service.assign(
            ids["batch"],
            page_id,
            participant,
            view["grouping_revision"],
            view["row_version"],
            ids["teacher"],
        )
    return view


async def assert_constraint(connection, name, statement, parameters):
    savepoint = await connection.begin_nested()
    with pytest.raises(IntegrityError) as raised:
        await connection.execute(text(statement), parameters)
    assert name in str(raised.value.orig)
    await savepoint.rollback()


async def test_scan_grouping_initialization_seeds_matching_evidence_idempotently(
    grouping_database,
):
    connection, _, service, ids = grouping_database
    view = await initialize(service, ids)
    revision = (
        await connection.execute(
            text("""SELECT id,revision,based_on_matching_revision,status,
        row_version,created_by_user_id FROM scan_grouping_revisions WHERE batch_id=:batch"""),
            ids,
        )
    ).one()
    assert tuple(revision)[1:] == (1, 1, "draft", 1, ids["teacher"])
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM scan_grouping_revisions WHERE batch_id=:batch",
            ids,
        )
        == 1
    )
    groups = {
        group["assignment_participant_id"]: [p["page_id"] for p in group["pages"]]
        for group in view["groups"]
    }
    assert groups == {
        ids["participant_a"]: [ids["pages"][1], ids["pages"][0]],
        ids["participant_b"]: [ids["pages"][2]],
    }
    assert [(p["page_id"], p["reason_code"]) for p in view["unresolved_pages"]] == [
        (ids["pages"][3], "ambiguous"),
        (ids["pages"][4], "unmatched"),
    ]
    repeated = await initialize(service, ids)
    assert repeated["grouping_revision"] == view["grouping_revision"]
    assert repeated["row_version"] == view["row_version"]
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM scan_grouping_revisions WHERE batch_id=:batch",
            ids,
        )
        == 1
    )


async def test_scan_grouping_postgresql_structural_constraints(grouping_database):
    connection, session, service, ids = grouping_database
    await initialize(service, ids)
    await session.rollback()
    revision_id = await scalar(
        connection, "SELECT id FROM scan_grouping_revisions WHERE batch_id=:batch", ids
    )
    entry_id = await scalar(
        connection,
        "SELECT id FROM scan_grouping_entries WHERE grouping_revision_id=:revision AND assignment_participant_id=:participant_a",
        ids,
        revision=revision_id,
    )
    checks = (
        (
            "uq_scan_grouping_revisions_revision",
            "INSERT INTO scan_grouping_revisions(batch_id,revision,based_on_matching_revision,created_by_user_id,status) VALUES (:batch,1,1,:teacher,'superseded')",
            ids,
        ),
        (
            "uq_scan_grouping_revisions_one_draft",
            "INSERT INTO scan_grouping_revisions(batch_id,revision,based_on_matching_revision,created_by_user_id,status) VALUES (:batch,2,1,:teacher,'draft')",
            ids,
        ),
        (
            "uq_scan_grouping_entries_participant",
            "INSERT INTO scan_grouping_entries(grouping_revision_id,assignment_participant_id,position) VALUES (:revision,:participant_a,9)",
            {**ids, "revision": revision_id},
        ),
        (
            "uq_scan_grouping_pages_revision_page",
            "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry,:revision,:page,9)",
            {"entry": entry_id, "revision": revision_id, "page": ids["pages"][0]},
        ),
        (
            "uq_scan_grouping_pages_order",
            "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry,:revision,:page,0)",
            {"entry": entry_id, "revision": revision_id, "page": ids["pages"][3]},
        ),
        (
            "scan_grouping_unmatched_pages_pkey",
            "INSERT INTO scan_grouping_unmatched_pages(grouping_revision_id,scan_page_id,reason_code) VALUES (:revision,:page,'again')",
            {"revision": revision_id, "page": ids["pages"][3]},
        ),
    )
    for name, statement, parameters in checks:
        await assert_constraint(connection, name, statement, parameters)


async def test_scan_grouping_assign_move_unassign_boundaries_and_concurrency(
    grouping_database,
):
    connection, session, service, ids = grouping_database
    view = await initialize(service, ids)
    old_version = view["row_version"]
    view = await service.assign(
        ids["batch"],
        ids["pages"][3],
        ids["participant_a"],
        1,
        old_version,
        ids["co_teacher"],
    )
    assert view["row_version"] == old_version + 1
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM scan_grouping_unmatched_pages u JOIN scan_grouping_revisions r ON r.id=u.grouping_revision_id WHERE r.batch_id=:batch AND u.scan_page_id=:page",
            ids,
            page=ids["pages"][3],
        )
        == 0
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM scan_grouping_pages p JOIN scan_grouping_revisions r ON r.id=p.grouping_revision_id WHERE r.batch_id=:batch AND p.scan_page_id=:page",
            ids,
            page=ids["pages"][3],
        )
        == 1
    )
    with pytest.raises(ScanGroupingError, match="grouping_revision_conflict"):
        await service.assign(
            ids["batch"],
            ids["pages"][4],
            ids["participant_b"],
            1,
            old_version,
            ids["teacher"],
        )
    view = await service.assign(
        ids["batch"],
        ids["pages"][3],
        ids["participant_b"],
        1,
        view["row_version"],
        ids["teacher"],
    )
    assert (
        sum(p["page_id"] == ids["pages"][3] for g in view["groups"] for p in g["pages"])
        == 1
    )
    view = await service.assign(
        ids["batch"], ids["pages"][3], None, 1, view["row_version"], ids["teacher"]
    )
    assert ids["pages"][3] in {p["page_id"] for p in view["unresolved_pages"]}
    snapshot = (
        view["row_version"],
        [
            (g["assignment_participant_id"], [p["page_id"] for p in g["pages"]])
            for g in view["groups"]
        ],
    )
    for page, participant, code in (
        (
            ids["pages"][4],
            ids["other_participant"],
            "grouping_participant_not_in_assignment",
        ),
        (ids["foreign_page"], ids["participant_a"], "grouping_page_not_in_batch"),
    ):
        with pytest.raises(ScanGroupingError, match=code):
            await service.assign(
                ids["batch"], page, participant, 1, view["row_version"], ids["teacher"]
            )
        current = await service.read(ids["batch"])
        assert (
            current["row_version"],
            [
                (g["assignment_participant_id"], [p["page_id"] for p in g["pages"]])
                for g in current["groups"]
            ],
        ) == snapshot
    # A revision identity, not merely its row version, is part of concurrency.
    await session.rollback()
    await connection.execute(
        text(
            "UPDATE scan_grouping_revisions SET status='superseded' WHERE batch_id=:batch"
        ),
        ids,
    )
    await connection.execute(
        text("""INSERT INTO scan_grouping_revisions
             (batch_id,revision,based_on_matching_revision,created_by_user_id,status)
             VALUES (:batch,2,1,:co_teacher,'draft')"""),
        ids,
    )
    session.expire_all()
    with pytest.raises(ScanGroupingError, match="grouping_revision_conflict"):
        await service.assign(
            ids["batch"],
            ids["pages"][4],
            ids["participant_a"],
            1,
            snapshot[0],
            ids["teacher"],
        )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM scan_grouping_pages WHERE grouping_revision_id="
            "(SELECT id FROM scan_grouping_revisions WHERE batch_id=:batch AND revision=2)",
            ids,
        )
        == 0
    )


async def test_scan_grouping_reorder_is_exact_contiguous_and_failure_safe(
    grouping_database,
):
    connection, _, service, ids = grouping_database
    view = await initialize(service, ids)
    requested = [ids["pages"][0], ids["pages"][1]]
    view = await service.reorder(
        ids["batch"],
        ids["participant_a"],
        requested,
        1,
        view["row_version"],
        ids["teacher"],
    )
    persisted = await rows(
        connection,
        """SELECT p.scan_page_id,p.page_order FROM scan_grouping_pages p
        JOIN scan_grouping_entries e ON e.id=p.grouping_entry_id
        WHERE e.assignment_participant_id=:participant_a ORDER BY p.page_order""",
        ids,
    )
    assert persisted == [(requested[0], 0), (requested[1], 1)]
    for bad in (
        [requested[0], requested[0]],
        [requested[0]],
        [requested[0], ids["pages"][2]],
        [requested[0], ids["foreign_page"]],
    ):
        with pytest.raises(
            ScanGroupingError,
            match="grouping_duplicate_page|grouping_page_order_mismatch",
        ):
            await service.reorder(
                ids["batch"],
                ids["participant_a"],
                bad,
                1,
                view["row_version"],
                ids["teacher"],
            )
        assert (
            await rows(
                connection,
                """SELECT p.scan_page_id,p.page_order FROM scan_grouping_pages p JOIN scan_grouping_entries e ON e.id=p.grouping_entry_id WHERE e.assignment_participant_id=:participant_a ORDER BY p.page_order""",
                ids,
            )
            == persisted
        )


async def test_scan_grouping_stale_matching_revision_and_unresolved_confirmation_reject(
    grouping_database,
):
    connection, session, service, ids = grouping_database
    view = await initialize(service, ids)
    with pytest.raises(ScanGroupingError, match="unmatched_pages_block_confirmation"):
        await service.confirm(ids["batch"], 1, view["row_version"], ids["teacher"])
    assert (
        await scalar(
            connection,
            "SELECT status FROM scan_grouping_revisions WHERE batch_id=:batch",
            ids,
        )
        == "draft"
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM paper_submissions WHERE batch_id=:batch",
            ids,
        )
        == 0
    )
    assert (
        await scalar(
            connection,
            "SELECT status FROM assessment_scan_batches WHERE id=:batch",
            ids,
        )
        == "matching_review_required"
    )
    await connection.execute(
        text("UPDATE assessment_scan_batches SET matching_revision=2 WHERE id=:batch"),
        ids,
    )
    session.expire_all()
    with pytest.raises(ScanGroupingError, match="grouping_based_on_stale_matching"):
        await service.confirm(ids["batch"], 1, view["row_version"], ids["teacher"])
    assert (
        await scalar(
            connection,
            "SELECT status FROM scan_grouping_revisions WHERE batch_id=:batch",
            ids,
        )
        == "draft"
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM paper_submissions WHERE batch_id=:batch",
            ids,
        )
        == 0
    )


async def test_paper_submission_confirmation_is_atomic_canonical_idempotent_and_isolated(
    grouping_database,
):
    connection, _, service, ids = grouping_database
    view = await resolve_all(service, ids, await initialize(service, ids))
    before = (
        await rows(
            connection, "SELECT max_attempts FROM assignments WHERE id=:assignment", ids
        ),
        await scalar(connection, "SELECT count(*) FROM student_submissions", ids),
        await scalar(connection, "SELECT count(*) FROM student_answers", ids),
        await scalar(connection, "SELECT count(*) FROM check_runs", ids),
    )
    confirmed = await service.confirm(
        ids["batch"], 1, view["row_version"], ids["co_teacher"]
    )
    revision_id = await scalar(
        connection, "SELECT id FROM scan_grouping_revisions WHERE batch_id=:batch", ids
    )
    revision = (
        await connection.execute(
            text(
                "SELECT status,confirmed_at,confirmed_by_user_id FROM scan_grouping_revisions WHERE id=:id"
            ),
            {"id": revision_id},
        )
    ).one()
    assert revision.status == "confirmed" and revision.confirmed_at is not None
    assert revision.confirmed_by_user_id == ids["co_teacher"]
    assert confirmed["batch_status"] == "ready_for_checking"
    papers = await rows(
        connection,
        """SELECT assignment_participant_id,student_id,assigned_variant_id,status,
        batch_id,grouping_revision_id,assignment_id,created_by_user_id FROM paper_submissions
        WHERE batch_id=:batch ORDER BY assignment_participant_id""",
        ids,
    )
    expected = sorted(
        (
            (ids["participant_a"], ids["student_a"], ids["variant_a"]),
            (ids["participant_b"], ids["student_b"], ids["variant_b"]),
        ),
        key=lambda item: item[0],
    )
    assert [
        (p.assignment_participant_id, p.student_id, p.assigned_variant_id)
        for p in papers
    ] == expected
    assert all(
        (
            p.status,
            p.batch_id,
            p.grouping_revision_id,
            p.assignment_id,
            p.created_by_user_id,
        )
        == (
            "ready_for_checking",
            ids["batch"],
            revision_id,
            ids["assignment"],
            ids["co_teacher"],
        )
        for p in papers
    )
    grouping_pages = await rows(
        connection,
        """SELECT e.assignment_participant_id,gp.scan_page_id,gp.page_order FROM scan_grouping_pages gp JOIN scan_grouping_entries e ON e.id=gp.grouping_entry_id WHERE gp.grouping_revision_id=:revision ORDER BY e.assignment_participant_id,gp.page_order""",
        ids,
        revision=revision_id,
    )
    paper_pages = await rows(
        connection,
        """SELECT ps.assignment_participant_id,pp.scan_page_id,pp.page_order FROM paper_submission_pages pp JOIN paper_submissions ps ON ps.id=pp.paper_submission_id WHERE ps.batch_id=:batch ORDER BY ps.assignment_participant_id,pp.page_order""",
        ids,
    )
    assert (
        paper_pages == grouping_pages
        and len({p.scan_page_id for p in paper_pages}) == 5
    )
    after = (
        await rows(
            connection, "SELECT max_attempts FROM assignments WHERE id=:assignment", ids
        ),
        await scalar(connection, "SELECT count(*) FROM student_submissions", ids),
        await scalar(connection, "SELECT count(*) FROM student_answers", ids),
        await scalar(connection, "SELECT count(*) FROM check_runs", ids),
    )
    assert (
        after == before
    )  # paper confirmation never enters the digital attempt aggregate
    event_rows = await rows(
        connection,
        "SELECT event_type,details::text FROM scan_checking_events WHERE batch_id=:batch ORDER BY occurred_at,id",
        ids,
    )
    assert sum(event.event_type == "grouping.confirmed" for event in event_rows) == 1
    assert (
        sum(event.event_type == "paper_submission.created" for event in event_rows) == 2
    )
    forbidden = (
        "display_name",
        "student alpha",
        "login",
        "email",
        "storage_reference",
        "original.pdf",
        "provider raw",
    )
    assert not any(
        term in event.details.lower() for event in event_rows for term in forbidden
    )
    again = await service.confirm(ids["batch"], 1, view["row_version"], ids["teacher"])
    assert again["grouping_revision"] == 1 and again["grouping_status"] == "confirmed"
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM paper_submissions WHERE batch_id=:batch",
            ids,
        )
        == 2
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM paper_submission_pages pp JOIN paper_submissions ps ON ps.id=pp.paper_submission_id WHERE ps.batch_id=:batch",
            ids,
        )
        == 5
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type='grouping.confirmed'",
            ids,
        )
        == 1
    )
    for operation in (
        lambda: service.assign(
            ids["batch"],
            ids["pages"][0],
            None,
            1,
            confirmed["row_version"],
            ids["teacher"],
        ),
        lambda: service.assign(
            ids["batch"],
            ids["pages"][0],
            ids["participant_b"],
            1,
            confirmed["row_version"],
            ids["teacher"],
        ),
        lambda: service.reorder(
            ids["batch"],
            ids["participant_a"],
            [ids["pages"][0]],
            1,
            confirmed["row_version"],
            ids["teacher"],
        ),
    ):
        with pytest.raises(ScanGroupingError, match="grouping_immutable"):
            await operation()
    assert await rows(
        connection,
        "SELECT scan_page_id,page_order FROM paper_submission_pages ORDER BY scan_page_id",
        ids,
    ) == sorted([(p.scan_page_id, p.page_order) for p in paper_pages])


async def test_paper_submission_database_membership_constraints(grouping_database):
    connection, session, service, ids = grouping_database
    view = await resolve_all(service, ids, await initialize(service, ids))
    await service.confirm(ids["batch"], 1, view["row_version"], ids["teacher"])
    await session.rollback()
    revision = await scalar(
        connection, "SELECT id FROM scan_grouping_revisions WHERE batch_id=:batch", ids
    )
    await assert_constraint(
        connection,
        "uq_paper_submissions_batch_participant",
        """INSERT INTO paper_submissions(batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:batch,:revision,:assignment,:participant_a,:student_a,:variant_a,'ready_for_checking',:teacher)""",
        {**ids, "revision": revision},
    )
    papers = await rows(
        connection,
        "SELECT id,assignment_participant_id FROM paper_submissions WHERE batch_id=:batch ORDER BY assignment_participant_id",
        ids,
    )
    first, second = papers
    first_page = await scalar(
        connection,
        "SELECT scan_page_id FROM paper_submission_pages WHERE paper_submission_id=:paper LIMIT 1",
        ids,
        paper=first.id,
    )
    await assert_constraint(
        connection,
        "paper_submission_pages_scan_page_id_key",
        "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper,:page,99)",
        {"paper": second.id, "page": first_page},
    )


async def test_scan_grouping_confirmation_rolls_back_every_side_effect(
    grouping_database,
):
    connection, _, service, ids = grouping_database
    view = await resolve_all(service, ids, await initialize(service, ids))
    await connection.execute(
        text(
            "ALTER TABLE paper_submission_pages ADD CONSTRAINT ck_test_reject_paper_pages CHECK (false)"
        )
    )
    with pytest.raises(IntegrityError):
        await service.confirm(ids["batch"], 1, view["row_version"], ids["teacher"])
    assert (
        await scalar(
            connection,
            "SELECT status FROM scan_grouping_revisions WHERE batch_id=:batch",
            ids,
        )
        == "draft"
    )
    assert await scalar(
        connection,
        "SELECT confirmed_at IS NULL FROM scan_grouping_revisions WHERE batch_id=:batch",
        ids,
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM paper_submissions WHERE batch_id=:batch",
            ids,
        )
        == 0
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM paper_submission_pages pp JOIN paper_submissions ps ON ps.id=pp.paper_submission_id WHERE ps.batch_id=:batch",
            ids,
        )
        == 0
    )
    assert (
        await scalar(
            connection,
            "SELECT count(*) FROM scan_checking_events WHERE batch_id=:batch AND event_type IN ('grouping.confirmed','paper_submission.created')",
            ids,
        )
        == 0
    )
    assert (
        await scalar(
            connection,
            "SELECT status FROM assessment_scan_batches WHERE id=:batch",
            ids,
        )
        == "matching_review_required"
    )


async def test_scan_grouping_access_uses_current_class_membership_not_creator(
    grouping_database,
):
    connection, session, _, ids = grouping_database
    access = SQLAlchemyClassroomAccessRepository(session)
    assert await access.get_accessible_class(ids["group"], ids["teacher"], False)
    assert await access.get_accessible_class(ids["group"], ids["co_teacher"], False)
    assert not await access.get_accessible_class(
        ids["group"], ids["unrelated_teacher"], False
    )
    await connection.execute(
        text(
            "DELETE FROM class_group_teachers WHERE class_group_id=:group AND teacher_user_id=:co_teacher"
        ),
        ids,
    )
    session.expire_all()
    assert not await access.get_accessible_class(ids["group"], ids["co_teacher"], False)
