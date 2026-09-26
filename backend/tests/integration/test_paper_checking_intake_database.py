"""Real PostgreSQL acceptance proofs for PR 6 paper-checking intake.

These tests deliberately exercise the database adapter and PostgreSQL constraints;
they do not invoke a checking provider and are not substitutes for the historical
digital intake acceptance module.
"""

from __future__ import annotations

import copy
import json
import os
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.checking import ActiveRunConflict, IdempotencyConflict
from app.application.checking_intake import InvalidCheckingInput
from app.application.paper_checking_intake import (
    PAPER_POLICY_COMPILER_VERSION,
    PAPER_PROMPT_POLICY_VERSION,
    PaperCheckingIntakeRequest,
    PaperCheckingIntakeService,
    PaperGradingPolicyIncomplete,
)
from app.application.scan_grading_policy import (
    AssessmentItemPolicy,
    GradeScale,
    GradeThreshold,
    RubricSnapshot,
    ScanGradingPolicy,
)
from app.infrastructure.checking_repository import CheckingRepository
from app.infrastructure.paper_checking_repository import (
    PaperGradingPolicyRepository,
    SQLAlchemyPaperCheckingIntakeUnitOfWorkFactory,
)
from tests.integration.c10a_postgres import rolled_back_connection

URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("paper checking tests require a database ending in _test")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required"),
]


@pytest_asyncio.fixture
async def paper_database():
    names = (
        "teacher",
        "group",
        "student_a",
        "student_b",
        "subject",
        "topic",
        "task_a",
        "task_b",
        "version_a",
        "version_b",
        "assessment",
        "variant_a",
        "variant_b",
        "item_a",
        "item_b",
        "assignment",
        "participant_a",
        "participant_b",
        "source",
        "batch",
        "batch_artifact",
        "render_a",
        "render_b",
        "render_bad_mime",
        "render_bad_hash",
        "page_a",
        "page_b",
        "revision",
        "entry_a",
        "entry_b",
        "paper_a",
        "paper_b",
        "digital_submission",
    )
    ids = {name: uuid4() for name in names}
    suffix = uuid4().hex
    ids.update(
        hash_a="a" * 64,
        hash_b="b" * 64,
        hash_bad="d" * 64,
        source_hash="c" * 64,
        suffix=suffix,
        subject_code=suffix,
        subject_name=suffix,
        subject_normalized_name=suffix,
        grade_name=suffix,
        grade_normalized_name=suffix,
        topic_code=suffix,
        topic_name=suffix,
        topic_normalized_name=suffix,
    )
    statements = (
        "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:teacher,:suffix,:suffix,'Paper teacher','hash')",
        "INSERT INTO user_roles(user_id,role) VALUES (:teacher,'teacher')",
        "INSERT INTO class_groups(id,name,created_by) VALUES (:group,:suffix,:teacher)",
        "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:teacher,:teacher)",
        "INSERT INTO students(id,class_group_id,display_name) VALUES (:student_a,:group,'Private A'),(:student_b,:group,'Private B')",
        "INSERT INTO subjects(id,code,name,normalized_name) VALUES (:subject,:subject_code,:subject_name,:subject_normalized_name)",
        "INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name) VALUES (:topic,:subject,:grade,:topic_code,:topic_name,:topic_normalized_name)",
        "INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task_a,:subject,:grade,:topic,:teacher),(:task_b,:subject,:grade,:topic,:teacher)",
        "INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version_a,:task_a,1,'Paper A','problem','short_text',50,'approved',:teacher),(:version_b,:task_b,1,'Paper B','problem','short_text',50,'approved',:teacher)",
        "INSERT INTO assessments(id,title,status,created_by,published_at,published_by) VALUES (:assessment,:suffix,'published',:teacher,clock_timestamp(),:teacher)",
        "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant_a,:assessment,'A',1),(:variant_b,:assessment,'B',2)",
        "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item_a,:variant_a,:version_a,1,10.00),(:item_b,:variant_b,:version_b,1,20.00)",
        "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 day',3,:teacher)",
        "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant_a,:assignment,:student_a,:variant_a,clock_timestamp()),(:participant_b,:assignment,:student_b,:variant_b,clock_timestamp())",
        "INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status,submitted_at) VALUES (:digital_submission,:participant_a,1,'submitted',clock_timestamp())",
        "INSERT INTO input_artifacts(id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference) VALUES (:source,:teacher,'application/pdf',:source_hash,100,:suffix||'/original.pdf'),(:render_a,:teacher,'image/png',:hash_a,100,:suffix||'/a.png'),(:render_b,:teacher,'image/png',:hash_b,100,:suffix||'/b.png'),(:render_bad_mime,:teacher,'image/jpeg',:hash_a,100,:suffix||'/bad-mime.jpg'),(:render_bad_hash,:teacher,'image/png',:hash_bad,100,:suffix||'/bad-hash.png')",
        "INSERT INTO assessment_scan_batches(id,assignment_id,class_group_id,created_by_user_id,status,instruction_text,matching_revision,row_version,request_key,request_hash) VALUES (:batch,:assignment,:group,:teacher,'ready_for_checking','Mark exactly',1,1,:suffix,:source_hash)",
        "INSERT INTO scan_batch_artifacts(id,batch_id,input_artifact_id,upload_position,original_filename,extraction_status,page_count) VALUES (:batch_artifact,:batch,:source,0,'private-original.pdf','completed',2)",
        "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status) VALUES (:page_a,:batch_artifact,0,:render_a,1200,1600,0,'normalized_upright_v1',:hash_a,'ready'),(:page_b,:batch_artifact,1,:render_b,1200,1600,0,'normalized_upright_v1',:hash_b,'ready')",
        "INSERT INTO scan_grouping_revisions(id,batch_id,revision,based_on_matching_revision,created_by_user_id,status,confirmed_at,confirmed_by_user_id) VALUES (:revision,:batch,1,1,:teacher,'confirmed',clock_timestamp(),:teacher)",
        "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:entry_a,:revision,:participant_a,0),(:entry_b,:revision,:participant_b,1)",
        "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry_a,:revision,:page_a,0),(:entry_b,:revision,:page_b,0)",
        "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:paper_a,:batch,:revision,:assignment,:participant_a,:student_a,:variant_a,'ready_for_checking',:teacher),(:paper_b,:batch,:revision,:assignment,:participant_b,:student_b,:variant_b,'ready_for_checking',:teacher)",
        "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper_a,:page_a,0),(:paper_b,:page_b,0)",
    )
    async with rolled_back_connection() as connection:
        grade_id = await connection.scalar(
            text(
                "SELECT id FROM grades "
                "WHERE number=7 AND status IN ('active','provisional') LIMIT 1"
            )
        )
        if grade_id is None:
            grade_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO grades(id,number,name,normalized_name) "
                    "VALUES (:grade,7,:grade_name,:grade_normalized_name)"
                ),
                {**ids, "grade": grade_id},
            )
        ids["grade"] = grade_id
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        for statement in statements:
            await connection.execute(text(statement), ids)
        yield connection, factory, ids


def policy(ids, variant="a", *, rule="Use exact evidence."):
    item_id = ids[f"item_{variant}"]
    maximum = Decimal(10) if variant == "a" else Decimal(20)
    return ScanGradingPolicy(
        original_teacher_instruction="Mark exactly",
        assessment_items=(
            AssessmentItemPolicy(
                assessment_item_id=item_id,
                max_score=maximum,
                rubric_source="authored",
                rubric=RubricSnapshot(text=f"Rubric {variant}"),
                partial_credit_policy="Award proportionally.",
                special_rules=(),
            ),
        ),
        total_max_score=maximum,
        general_rules=(rule,),
        grade_scale=GradeScale(
            basis="points",
            thresholds=(GradeThreshold(minimum=Decimal(0), grade_label="graded"),),
        ),
        compiler_version=PAPER_POLICY_COMPILER_VERSION,
        prompt_policy_version=PAPER_PROMPT_POLICY_VERSION,
    )


async def freeze(factory, ids, variant="a", **changes):
    value = policy(ids, variant, **changes)
    async with factory() as session, session.begin():
        row = await PaperGradingPolicyRepository(session).freeze(
            ids["batch"], ids[f"variant_{variant}"], value, ids["teacher"]
        )
        result = (
            row.id,
            row.revision,
            row.policy_fingerprint,
            copy.deepcopy(row.policy_json),
        )
    return result


def request(ids, paper="a", key="paper-key", version="prompt", supersedes=None):
    return PaperCheckingIntakeRequest(
        ids[f"paper_{paper}"],
        key,
        "routing",
        "checkers",
        "threshold",
        version,
        supersedes,
    )


def intake(factory):
    return PaperCheckingIntakeService(
        SQLAlchemyPaperCheckingIntakeUnitOfWorkFactory(factory)
    )


async def terminalize(factory, run_id):
    async with factory() as session, session.begin():
        await CheckingRepository(session).transition_run(run_id, 1, "running")
    async with factory() as session, session.begin():
        await CheckingRepository(session).transition_run(run_id, 2, "completed")


def constraint(exc):
    return getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)


async def test_policy_freeze_replay_revision_validation_and_immutability(
    paper_database,
):
    connection, factory, ids = paper_database
    first = await freeze(factory, ids)
    replay = await freeze(factory, ids)
    assert replay == first and first[1] == 1 and first[2] == policy(ids).fingerprint
    second = await freeze(factory, ids, rule="Changed legitimate rule.")
    assert second[1] == 2 and second[0] != first[0]
    rows = (
        await connection.execute(
            text(
                "SELECT id,revision,supersedes_policy_id,compiler_version,prompt_policy_version,policy_json FROM scan_grading_policy_revisions WHERE batch_id=:batch AND assessment_variant_id=:variant ORDER BY revision"
            ),
            {"batch": ids["batch"], "variant": ids["variant_a"]},
        )
    ).all()
    assert len(rows) == 2 and rows[1].supersedes_policy_id == rows[0].id
    assert rows[0].compiler_version == PAPER_POLICY_COMPILER_VERSION
    assert rows[0].prompt_policy_version == PAPER_PROMPT_POLICY_VERSION
    async with factory() as session:
        for sql in (
            "UPDATE scan_grading_policy_revisions SET revision=9 WHERE id=:id",
            "DELETE FROM scan_grading_policy_revisions WHERE id=:id",
        ):
            with pytest.raises(DBAPIError, match="immutable"):
                async with session.begin_nested():
                    await session.execute(text(sql), {"id": first[0]})
            await session.rollback()
    assert (
        await connection.scalar(
            text(
                "SELECT count(*) FROM scan_grading_policy_revisions WHERE batch_id=:batch AND assessment_variant_id=:variant"
            ),
            {"batch": ids["batch"], "variant": ids["variant_a"]},
        )
        == 2
    )


async def test_policy_coverage_variant_and_version_authority_fail_closed(
    paper_database,
):
    connection, factory, ids = paper_database
    bad = policy(ids).model_copy(update={"compiler_version": "caller-owned"})
    async with factory() as session:
        with pytest.raises(InvalidCheckingInput, match="version_mismatch"):
            await PaperGradingPolicyRepository(session).freeze(
                ids["batch"], ids["variant_a"], bad, ids["teacher"]
            )
        await session.rollback()
        wrong = policy(ids).model_copy(
            update={"original_teacher_instruction": "Different"}
        )
        with pytest.raises(InvalidCheckingInput, match="instruction_mismatch"):
            await PaperGradingPolicyRepository(session).freeze(
                ids["batch"], ids["variant_a"], wrong, ids["teacher"]
            )
        await session.rollback()
        missing = policy(ids).model_copy(
            update={"assessment_items": (), "total_max_score": Decimal(10)}
        )
        with pytest.raises(InvalidCheckingInput, match="item_coverage"):
            await PaperGradingPolicyRepository(session).freeze(
                ids["batch"], ids["variant_a"], missing, ids["teacher"]
            )
        await session.rollback()
    assert (
        await connection.scalar(
            text(
                "SELECT count(*) FROM scan_grading_policy_revisions WHERE batch_id=:batch"
            ),
            {"batch": ids["batch"]},
        )
        == 0
    )


async def test_multivariant_completeness_snapshot_privacy_and_atomic_transition(
    paper_database,
):
    connection, factory, ids = paper_database
    await freeze(factory, ids, "a")
    with pytest.raises(
        PaperGradingPolicyIncomplete, match="paper_grading_policy_incomplete"
    ):
        await intake(factory).create(request(ids))
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "ready_for_checking"
    )
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM check_runs WHERE paper_submission_id=:paper"),
            {"paper": ids["paper_a"]},
        )
        == 0
    )
    assert (
        await connection.scalar(
            text(
                "SELECT count(*) FROM checker_events ce JOIN check_runs cr ON cr.id=ce.check_run_id WHERE cr.paper_submission_id=:paper"
            ),
            {"paper": ids["paper_a"]},
        )
        == 0
    )
    await freeze(factory, ids, "b")
    run = await intake(factory).create(request(ids))
    row = (
        await connection.execute(
            text(
                "SELECT submission_id,paper_submission_id,input_snapshot,input_fingerprint FROM check_runs WHERE id=:id"
            ),
            {"id": run.id},
        )
    ).one()
    status = await connection.scalar(
        text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
        {"id": ids["batch"]},
    )
    events = await connection.scalar(
        text(
            "SELECT count(*) FROM checker_events WHERE check_run_id=:id AND event_type='run_created'"
        ),
        {"id": run.id},
    )
    snapshot = row.input_snapshot
    assert row.submission_id is None and row.paper_submission_id == ids["paper_a"]
    assert status == "checking" and events == 1
    assert snapshot["snapshot_schema_version"] == "checking_input_paper_v1"
    assert snapshot["pages"] == [
        {
            "scan_page_id": str(ids["page_a"]),
            "page_order": 0,
            "width": 1200,
            "height": 1600,
            "coordinate_space": "normalized_upright_v1",
            "content_fingerprint": ids["hash_a"],
            "derived_render_artifact_id": str(ids["render_a"]),
        }
    ]
    assert [x["assessment_item_id"] for x in snapshot["items"]] == [str(ids["item_a"])]
    assert snapshot["grading_policy"]["policy"] == policy(ids).model_dump(mode="json")
    forbidden = {
        "student_id",
        "assignment_participant_id",
        "user_id",
        "display_name",
        "login",
        "email",
        "student_note",
        "teacher_note",
        "storage_reference",
        "original_filename",
        "raw_answer",
        "normalized_answer",
        "raw_output",
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value)) if value else set()
        return set()

    assert not (keys(snapshot) & forbidden)
    encoded = json.dumps(snapshot, sort_keys=True)
    for secret in (ids["student_a"], ids["participant_a"], ids["teacher"]):
        assert str(secret) not in encoded
    assert f"{ids['suffix']}/original.pdf" not in encoded


async def test_paper_idempotency_active_conflict_attempts_and_policy_lock(
    paper_database,
):
    connection, factory, ids = paper_database
    await freeze(factory, ids, "a")
    await freeze(factory, ids, "b")
    service = intake(factory)
    first = await service.create(request(ids))
    replay = await service.create(request(ids))
    assert replay.id == first.id and replay.input_fingerprint == first.input_fingerprint
    with pytest.raises(IdempotencyConflict):
        await service.create(request(ids, version="changed"))
    with pytest.raises(ActiveRunConflict):
        await service.create(request(ids, key="different"))
    await terminalize(factory, first.id)
    second = await service.create(request(ids, key="rerun-2", supersedes=first.id))
    await terminalize(factory, second.id)
    third = await service.create(request(ids, key="rerun-3", supersedes=second.id))
    assert [first.attempt_no, second.attempt_no, third.attempt_no] == [1, 2, 3]
    async with factory() as session:
        with pytest.raises(InvalidCheckingInput, match="paper_grading_policy_locked"):
            await PaperGradingPolicyRepository(session).freeze(
                ids["batch"],
                ids["variant_a"],
                policy(ids, rule="Too late"),
                ids["teacher"],
            )
        await session.rollback()
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM check_runs WHERE paper_submission_id=:p"),
            {"p": ids["paper_a"]},
        )
        == 3
    )
    assert (
        await connection.scalar(
            text(
                "SELECT count(*) FROM checker_events ce JOIN check_runs cr ON cr.id=ce.check_run_id WHERE cr.paper_submission_id=:paper AND ce.event_type='run_created'"
            ),
            {"paper": ids["paper_a"]},
        )
        == 3
    )


async def test_xor_request_attempt_and_active_constraints_are_postgresql_enforced(
    paper_database,
):
    _, factory, ids = paper_database
    await freeze(factory, ids, "a")
    await freeze(factory, ids, "b")
    run = await intake(factory).create(request(ids))
    async with factory() as session:
        templates = {
            "ck_check_runs_execution_source_xor": "INSERT INTO check_runs(id,submission_id,paper_submission_id,request_key,request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,attempt_no) SELECT :id,NULL,NULL,'xor-null',request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,91 FROM check_runs WHERE id=:run",
            "uq_check_runs_paper_request": "INSERT INTO check_runs(id,submission_id,paper_submission_id,request_key,request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,status,attempt_no,started_at,finished_at) SELECT :id,NULL,paper_submission_id,request_key,request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,'completed',92,clock_timestamp(),clock_timestamp() FROM check_runs WHERE id=:run",
            "uq_check_runs_paper_attempt": "INSERT INTO check_runs(id,submission_id,paper_submission_id,request_key,request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,status,attempt_no,started_at,finished_at) SELECT :id,NULL,paper_submission_id,'attempt-conflict',request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,'completed',attempt_no,clock_timestamp(),clock_timestamp() FROM check_runs WHERE id=:run",
            "uq_check_runs_one_active_paper": "INSERT INTO check_runs(id,submission_id,paper_submission_id,request_key,request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,attempt_no) SELECT :id,NULL,paper_submission_id,'active-race',request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,93 FROM check_runs WHERE id=:run",
        }
        for expected, sql in templates.items():
            with pytest.raises(IntegrityError) as caught:
                async with session.begin_nested():
                    await session.execute(text(sql), {"id": uuid4(), "run": run.id})
            assert constraint(caught.value) == expected
            await session.rollback()
        with pytest.raises(IntegrityError) as caught:
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO check_runs(id,submission_id,paper_submission_id,request_key,request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,attempt_no) SELECT :id,:digital,paper_submission_id,'xor-both',request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,94 FROM check_runs WHERE id=:run"
                    ),
                    {
                        "id": uuid4(),
                        "run": run.id,
                        "digital": ids["digital_submission"],
                    },
                )
        assert constraint(caught.value) == "ck_check_runs_execution_source_xor"


@pytest.mark.parametrize("corruption", ["status", "mime", "hash", "page_order"])
async def test_invalid_page_state_rolls_back_run_event_and_batch(
    paper_database, corruption
):
    connection, factory, ids = paper_database
    await freeze(factory, ids, "a")
    await freeze(factory, ids, "b")
    updates = {
        "status": (
            "UPDATE scan_pages SET status='pending',width_px=NULL,height_px=NULL,coordinate_space_version=NULL,content_fingerprint=NULL WHERE id=:page_a",
            "invalid",
        ),
        "mime": (
            "UPDATE scan_pages SET derived_render_artifact_id=:render_bad_mime WHERE id=:page_a",
            "invalid derived",
        ),
        "hash": (
            "UPDATE scan_pages SET derived_render_artifact_id=:render_bad_hash WHERE id=:page_a",
            "invalid derived",
        ),
        "page_order": (
            "UPDATE paper_submission_pages SET page_order=2 WHERE paper_submission_id=:paper_a",
            "contiguous",
        ),
    }
    await connection.execute(text(updates[corruption][0]), ids)
    with pytest.raises(InvalidCheckingInput, match=updates[corruption][1]):
        await intake(factory).create(request(ids))
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM check_runs WHERE paper_submission_id=:paper"),
            {"paper": ids["paper_a"]},
        )
        == 0
    )
    assert (
        await connection.scalar(
            text(
                "SELECT count(*) FROM checker_events ce JOIN check_runs cr ON cr.id=ce.check_run_id WHERE cr.paper_submission_id=:paper AND ce.event_type='run_created'"
            ),
            {"paper": ids["paper_a"]},
        )
        == 0
    )
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:batch"), ids
        )
        == "ready_for_checking"
    )


async def test_paper_runs_do_not_create_or_increment_digital_attempts(paper_database):
    connection, factory, ids = paper_database
    await freeze(factory, ids, "a")
    await freeze(factory, ids, "b")
    async def digital_state():
        submission_ids = tuple(
            (
                await connection.execute(
                    text(
                        "SELECT id FROM student_submissions WHERE assignment_participant_id IN (:participant_a,:participant_b) ORDER BY id"
                    ),
                    ids,
                )
            ).scalars()
        )
        answer_count = await connection.scalar(
            text(
                "SELECT count(*) FROM student_answers sa JOIN student_submissions ss ON ss.id=sa.submission_id WHERE ss.assignment_participant_id IN (:participant_a,:participant_b)"
            ),
            ids,
        )
        max_attempts = await connection.scalar(
            text("SELECT max_attempts FROM assignments WHERE id=:assignment"), ids
        )
        return submission_ids, answer_count, max_attempts

    before = await digital_state()
    await intake(factory).create(request(ids))
    after = await digital_state()
    assert before == after
    assert before == ((ids["digital_submission"],), 0, 3)


async def test_check_run_identity_is_immutable_but_cas_lifecycle_still_works(
    paper_database,
):
    _, factory, ids = paper_database
    await freeze(factory, ids, "a")
    await freeze(factory, ids, "b")
    run = await intake(factory).create(request(ids))
    changes = {
        "paper_submission_id": "NULL",
        "submission_id": f"'{uuid4()}'::uuid",
        "request_key": "'changed'",
        "request_hash": f"'{'d' * 64}'",
        "input_snapshot": "'{}'::jsonb",
        "input_fingerprint": f"'{'e' * 64}'",
        "attempt_no": "attempt_no+1",
        "supersedes_run_id": f"'{uuid4()}'::uuid",
    }
    async with factory() as session:
        for column, expression in changes.items():
            with pytest.raises(DBAPIError, match="immutable"):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            f"UPDATE check_runs SET {column}={expression} WHERE id=:id"
                        ),
                        {"id": run.id},
                    )
            await session.rollback()
    await terminalize(factory, run.id)


async def test_second_paper_in_checking_batch_is_allowed(paper_database):
    connection, factory, ids = paper_database
    await freeze(factory, ids, "a")
    await freeze(factory, ids, "b")
    first = await intake(factory).create(request(ids, "a", "first"))
    second = await intake(factory).create(request(ids, "b", "second"))
    assert (
        first.paper_submission_id == ids["paper_a"]
        and second.paper_submission_id == ids["paper_b"]
    )
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:batch"), ids
        )
        == "checking"
    )
