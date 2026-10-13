"""PR 7 PostgreSQL acceptance. All fixture writes roll back per test.

Migration cycles are process-level gates; see docs/pr7-postgres-acceptance.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from app.application.paper_checking_execution import (
    PaperCheckingExecutionError,
    PaperCheckingExecutionService,
    PaperCheckingTelemetry,
)
from app.application.scan_checking_contracts import PaperCheckResponse
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.paper_checking_execution_repository import (
    SQLAlchemyPaperCheckingExecutionRepository,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.c10a_postgres import rolled_back_connection
from tests.integration.test_paper_checking_intake_database import (
    constraint,
    freeze,
    intake,
    request,
)

URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("paper execution tests require a database ending in _test")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required"),
]

# Deterministic 1x1 PNG, with genuine IHDR/IDAT/IEND chunks and CRCs.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def source_pdf(*, three_pages=False):
    """Build a deterministic source PDF with a real cross-reference table."""
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1 1] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1 1] /Contents 4 0 R >>",
    )
    if three_pages:
        objects = (
            objects[0],
            b"<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>",
            *objects[2:],
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1 1] /Contents 8 0 R >>",
            b"<< /Length 0 >>\nstream\n\nendstream",
        )
    body = b"%PDF-1.4\n"
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + value + b"\nendobj\n"
    start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return body


@pytest_asyncio.fixture
async def execution_database(tmp_path, *, confirmed=True, three_papers=False, source_content=None):
    source_content = source_content or source_pdf(three_pages=three_papers)
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
        hash_a=hashlib.sha256(PNG).hexdigest(),
        hash_b=hashlib.sha256(PNG).hexdigest(),
        source_hash=hashlib.sha256(source_content).hexdigest(),
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
    if three_papers:
        ids.update({name[:-1] + "c": uuid4() for name in names if name.endswith("_b")})
        ids["hash_c"] = ids["hash_b"]
    storage = FilesystemArtifactStorage(str(tmp_path))
    ids["storage_a"] = await storage.store(PNG, "image/png")
    ids["storage_b"] = await storage.store(PNG, "image/png")
    if three_papers:
        ids["storage_c"] = await storage.store(PNG, "image/png")
    ids["storage_source"] = await storage.store(source_content, "application/pdf")
    ids["source_size"] = len(source_content)
    ids["png_size"] = len(PNG)
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
        "INSERT INTO input_artifacts(id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference) VALUES (:source,:teacher,'application/pdf',:source_hash,:source_size,:storage_source),(:render_a,:teacher,'image/png',:hash_a,:png_size,:storage_a),(:render_b,:teacher,'image/png',:hash_b,:png_size,:storage_b)",
        "INSERT INTO assessment_scan_batches(id,assignment_id,class_group_id,created_by_user_id,status,instruction_text,matching_revision,row_version,request_key,request_hash) VALUES (:batch,:assignment,:group,:teacher,'ready_for_checking','Mark exactly',1,1,:suffix,:source_hash)",
        "INSERT INTO scan_batch_artifacts(id,batch_id,input_artifact_id,upload_position,original_filename,extraction_status,page_count) VALUES (:batch_artifact,:batch,:source,0,'private-original.pdf','completed',2)",
        "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status) VALUES (:page_a,:batch_artifact,0,:render_a,1,1,0,'normalized_upright_v1',:hash_a,'ready'),(:page_b,:batch_artifact,1,:render_b,1,1,0,'normalized_upright_v1',:hash_b,'ready')",
        "INSERT INTO scan_grouping_revisions(id,batch_id,revision,based_on_matching_revision,created_by_user_id,status,confirmed_at,confirmed_by_user_id) VALUES (:revision,:batch,1,1,:teacher,'confirmed',clock_timestamp(),:teacher)",
        "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:entry_a,:revision,:participant_a,0),(:entry_b,:revision,:participant_b,1)",
        "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry_a,:revision,:page_a,0),(:entry_b,:revision,:page_b,0)",
        "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:paper_a,:batch,:revision,:assignment,:participant_a,:student_a,:variant_a,'ready_for_checking',:teacher),(:paper_b,:batch,:revision,:assignment,:participant_b,:student_b,:variant_b,'ready_for_checking',:teacher)",
        "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper_a,:page_a,0),(:paper_b,:page_b,0)",
    )
    if three_papers:
        # Add a third target to the same confirmed batch before identity is frozen.
        import re

        expanded = []
        for statement in statements:
            expanded.append(statement.replace("'completed',2)", "'completed',3)"))
            if ":student_b" not in statement and not any(
                f":{name}_b" in statement
                for name in ("task", "version", "variant", "item", "render", "page", "entry", "paper")
            ):
                continue
            if "),(" not in statement:
                continue
            row = statement[statement.rfind("),(") + 2:]
            row = re.sub(r":(\w+)_b\b", r":\1_c", row)
            row = row.replace("'Private B'", "'Private C'").replace("'Paper B'", "'Paper C'")
            row = row.replace("'B',2)", "'C',3)").replace(":batch_artifact,1,", ":batch_artifact,2,")
            row = row.replace(":participant_c,1)", ":participant_c,2)")
            expanded.append(statement.split(" VALUES ")[0] + " VALUES " + row)
        statements = tuple(expanded)
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
            if not confirmed:
                if statement.startswith(("INSERT INTO paper_submissions", "INSERT INTO paper_submission_pages")):
                    continue
                if statement.startswith("INSERT INTO assessment_scan_batches"):
                    statement = statement.replace("'ready_for_checking'", "'matching_review_required'")
                if statement.startswith("INSERT INTO scan_grouping_revisions"):
                    statement = statement.replace("'confirmed',clock_timestamp(),:teacher", "'draft',NULL,NULL")
            await connection.execute(text(statement), ids)
        from app.config import get_settings

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(get_settings(), "artifact_storage_path", str(storage.root))
            yield connection, factory, ids, storage


class FakePaperProvider:
    provider_id = "acceptance-fake"
    model_id = "deterministic-vision"

    def __init__(self, mode="success", status="correct", severity="info"):
        self.mode, self.status, self.severity = mode, status, severity
        self.calls = []

    async def check(self, value, content):
        self.calls.append((value, dict(content)))
        if self.mode == "transport":
            raise OSError("synthetic retryable transport failure")
        maximum = value.assessment_items[0].max_score
        score = {
            "correct": maximum,
            "incorrect": Decimal(0),
            "partially_correct": maximum / 2,
        }.get(self.status)
        payload = {
            "schema_version": "paper_check_output.v1",
            "items": (
                {
                    "item_token": "item-0001",
                    "status": self.status,
                    "rubric_score": score,
                    "rubric_max_score": maximum,
                    "confidence": Decimal("0.9"),
                    "requires_human_review": True,
                    "findings": (
                        {
                            "finding_token": "finding-1",
                            "category": "work",
                            "short_explanation": "Visible evidence",
                            "severity": self.severity,
                            "page_token": "page-0001",
                            "region": {
                                "kind": "point",
                                "coordinate_space": "normalized_upright_v1",
                                "x": Decimal("0.25"),
                                "y": Decimal("0.5"),
                            },
                            "confidence": Decimal("0.8"),
                            "requires_human_review": True,
                        },
                    ),
                },
            ),
            "summary_draft": "Review this draft",
            "overall_confidence": Decimal("0.9"),
            "requires_human_review": True,
        }
        response = PaperCheckResponse.model_validate(payload)
        item = response.items[0]
        if self.mode == "unknown_item":
            response = response.model_copy(
                update={"items": (item.model_copy(update={"item_token": "item-9999"}),)}
            )
        elif self.mode == "unknown_page":
            finding = item.findings[0].model_copy(update={"page_token": "page-9999"})
            response = response.model_copy(
                update={"items": (item.model_copy(update={"findings": (finding,)}),)}
            )
        elif self.mode == "invalid_region":
            payload["items"][0]["findings"][0]["region"]["x"] = Decimal("1.5")
            response = PaperCheckResponse.model_validate(payload)
        elif self.mode == "score_mismatch":
            response = response.model_copy(
                update={
                    "items": (
                        item.model_copy(update={"rubric_max_score": maximum + 1}),
                    )
                }
            )
        return response, PaperCheckingTelemetry("fake-request", 10, 5, 0, 7)


async def start(context, paper="a"):
    connection, factory, ids, _ = context
    if (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "ready_for_checking"
    ):
        await freeze(factory, ids, "a")
        await freeze(factory, ids, "b")
        if "variant_c" in ids:
            await freeze(factory, ids, "c")
    return await intake(factory).create(request(ids, paper, f"execution-{paper}"))


async def test_both_papers_complete_without_batch_publication(execution_database):
    connection, _, ids, _ = execution_database
    first = await start(execution_database)
    second = await start(execution_database, "b")
    provider = FakePaperProvider()
    execution = service(execution_database, provider)
    a = await execution.execute(ids["paper_a"], first.id)
    b = await execution.execute(ids["paper_b"], second.id)
    assert a["status"] == b["status"] == "completed_with_review_required"
    a_ids = await durable_ids(connection, first.id)
    b_ids = await durable_ids(connection, second.id)
    assert all(
        a_set.isdisjoint(b_set) for a_set, b_set in zip(a_ids, b_ids, strict=True)
    )
    assert len(provider.calls) == 2
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "checking_completed"
    )


async def test_migrated_postgres_schema_contract(execution_database):
    from tests.integration.test_migrations import repository_head
    connection, _, _, _ = execution_database
    assert (
        await connection.scalar(text("SELECT version_num FROM alembic_version"))
        == repository_head()
    )
    labels = await rows(
        connection,
        """SELECT t.typname,e.enumlabel FROM pg_type t
        JOIN pg_enum e ON e.enumtypid=t.oid WHERE t.typname IN
        ('checking_result_status','checking_checker_type')""",
    )
    assert {
        ("checking_result_status", status)
        for status in ("not_attempted", "unreadable", "insufficient_evidence")
    } <= {(row["typname"], row["enumlabel"]) for row in labels}
    assert ("checking_checker_type", "paper_vision") in {
        (row["typname"], row["enumlabel"]) for row in labels
    }
    index = await connection.scalar(
        text("""SELECT indexdef FROM pg_indexes
        WHERE schemaname='public' AND indexname='uq_model_runs_paper_attempt'""")
    )
    assert "UNIQUE" in index and "paper_submission_id IS NOT NULL" in index


def service(context, provider):
    _, factory, _, storage = context
    return PaperCheckingExecutionService(
        SQLAlchemyPaperCheckingExecutionRepository(factory), storage, provider
    )


async def rows(connection, sql, **params):
    return (await connection.execute(text(sql), params)).mappings().all()


async def durable_ids(connection, run_id):
    queries = (
        "SELECT id FROM paper_ai_revisions WHERE check_run_id=:run",
        "SELECT id FROM check_results WHERE check_run_id=:run",
        "SELECT f.id FROM check_findings f JOIN check_results r ON r.id=f.check_result_id WHERE r.check_run_id=:run",
        "SELECT f.id FROM paper_check_finding_regions f JOIN paper_ai_revisions r ON r.id=f.paper_ai_revision_id WHERE r.check_run_id=:run",
        "SELECT id FROM model_runs WHERE check_run_id=:run AND status='succeeded'",
        "SELECT c.id FROM cost_events c JOIN model_runs m ON m.id=c.model_run_id WHERE m.check_run_id=:run",
    )
    result = []
    for sql in queries:
        rows = await connection.execute(text(sql), {"run": run_id})
        result.append(frozenset(rows.scalars()))
    return tuple(result)


async def clone(connection, table, row_id, **changes):
    # Only static table names from this module are used. Clone immutable rows at
    # INSERT time rather than updating evidence or disabling any database guard.
    changes = {"id": str(uuid4()), **changes}
    return await connection.execute(
        text(
            f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table}, "
            f"to_jsonb(t) || CAST(:changes AS jsonb))).* FROM {table} t WHERE id=:id RETURNING id"
        ),
        {"id": row_id, "changes": json.dumps(changes)},
    )


async def rejected(connection, sql, params, *, name=None, match=None):
    with pytest.raises(DBAPIError, match=match) as caught:
        async with connection.begin_nested():
            await connection.execute(text(sql), params)
    if name:
        assert constraint(caught.value) == name


@pytest.mark.parametrize(
    "status,score",
    [
        ("correct", Decimal(10)),
        ("partially_correct", Decimal(5)),
        ("incorrect", Decimal(0)),
        ("not_attempted", None),
        ("unreadable", None),
        ("insufficient_evidence", None),
    ],
)
async def test_success_status_lifecycle_and_replay(execution_database, status, score):
    connection, _, ids, _ = execution_database
    run = await start(execution_database)
    assert run.status == "pending"
    provider = FakePaperProvider(status=status)
    execution = service(execution_database, provider)
    result = await execution.execute(ids["paper_a"], run.id)
    assert result["status"] == "completed_with_review_required"
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == {"content-0001": PNG}
    item_rows = await rows(
        connection, "SELECT * FROM check_results WHERE check_run_id=:run", run=run.id
    )
    assert len(item_rows) == 1
    item = item_rows[0]
    assert item["assessment_item_id"] == ids["item_a"]
    assert (item["result_status"], item["score_suggested"], item["max_score"]) == (
        status,
        score,
        Decimal(10),
    )
    assert item["checker_type"] == "paper_vision"
    assert (
        item["needs_human_review"]
        and item["review_reason"] == "paper_ai_review_required"
    )
    attempts = await rows(
        connection, "SELECT * FROM model_runs WHERE check_run_id=:run", run=run.id
    )
    assert len(attempts) == 1 and attempts[0]["status"] == "succeeded"
    assert attempts[0]["paper_submission_id"] == ids["paper_a"]
    assert (
        attempts[0]["assessment_item_id"] is None
        and attempts[0]["remediation_plan_item_id"] is None
    )
    assert (
        attempts[0]["input_tokens"],
        attempts[0]["output_tokens"],
        attempts[0]["latency_ms"],
    ) == (10, 5, 7)
    transitions = await rows(
        connection,
        "SELECT from_status,to_status FROM checker_events WHERE check_run_id=:run AND event_type='run_transition' ORDER BY occurred_at,id",
        run=run.id,
    )
    assert [(r["from_status"], r["to_status"]) for r in transitions] == [
        ("pending", "running"),
        ("running", "completed_with_review_required"),
    ]
    before = await durable_ids(connection, run.id)
    assert [len(x) for x in before[:5]] == [1, 1, 1, 1, 1]
    revision = (
        await rows(
            connection,
            """SELECT a.*, cr.input_snapshot FROM paper_ai_revisions a
        JOIN check_runs cr ON cr.id=a.check_run_id WHERE a.check_run_id=:run""",
            run=run.id,
        )
    )[0]
    policy_snapshot = revision["input_snapshot"]["grading_policy"]
    assert revision["paper_submission_id"] == ids["paper_a"]
    assert revision["model_run_id"] == attempts[0]["id"]
    assert revision["grading_policy_revision"] == policy_snapshot["revision"]
    assert revision["grading_policy_fingerprint"] == policy_snapshot["fingerprint"]
    assert revision["validated_response"] == attempts[0]["validated_output"]
    assert await execution.execute(ids["paper_a"], run.id) == result
    assert await durable_ids(connection, run.id) == before
    assert len(provider.calls) == 1
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "checking"
    )


@pytest.mark.parametrize(
    "severity,canonical", [("info", "info"), ("warning", "minor"), ("error", "major")]
)
async def test_finding_region_provenance(execution_database, severity, canonical):
    connection, _, ids, _ = execution_database
    run = await start(execution_database)
    result = await service(
        execution_database, FakePaperProvider(severity=severity)
    ).execute(ids["paper_a"], run.id)
    evidence = await rows(
        connection,
        """SELECT f.severity,r.assessment_item_id,p.scan_page_id,p.region,
        p.coordinate_space_version,p.finding_token,p.paper_ai_revision_id,sp.paper_submission_id
        FROM check_findings f JOIN check_results r ON r.id=f.check_result_id
        JOIN paper_check_finding_regions p ON p.check_finding_id=f.id
        JOIN paper_submission_pages sp ON sp.scan_page_id=p.scan_page_id
        WHERE r.check_run_id=:run""",
        run=run.id,
    )
    assert len(evidence) == 1
    row = evidence[0]
    assert row["severity"] == canonical and row["assessment_item_id"] == ids["item_a"]
    assert (
        row["scan_page_id"] == ids["page_a"]
        and row["paper_submission_id"] == ids["paper_a"]
    )
    assert row["paper_ai_revision_id"] == result["ai_revision_id"]
    assert row["coordinate_space_version"] == "normalized_upright_v1"
    assert row["finding_token"] == "finding-1"
    assert row["region"] == {
        "kind": "point",
        "coordinate_space": "normalized_upright_v1",
        "x": "0.25",
        "y": "0.5",
    }


@pytest.mark.parametrize(
    "mode", ["unknown_item", "unknown_page", "invalid_region", "score_mismatch"]
)
async def test_invalid_response_is_atomic_and_paper_failure_isolated(
    execution_database, mode
):
    connection, _, ids, _ = execution_database
    run_a = await start(execution_database)
    run_b = await start(execution_database, "b")
    provider = FakePaperProvider(mode)
    with pytest.raises(PaperCheckingExecutionError, match="paper_response_invalid"):
        await service(execution_database, provider).execute(ids["paper_a"], run_a.id)
    assert len(provider.calls) == 1
    assert all(not values for values in await durable_ids(connection, run_a.id))
    attempt = (
        await rows(
            connection,
            "SELECT status,error_code,attempt_no FROM model_runs WHERE check_run_id=:run",
            run=run_a.id,
        )
    )[0]
    assert tuple(attempt.values()) == ("invalid", "semantic_invalid", 1)
    assert (
        await connection.scalar(
            text("SELECT status FROM check_runs WHERE id=:id"), {"id": run_a.id}
        )
        == "failed_terminal"
    )
    assert (
        await connection.scalar(
            text("SELECT status FROM check_runs WHERE id=:id"), {"id": run_b.id}
        )
        == "pending"
    )
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "checking"
    )
    result = await service(execution_database, FakePaperProvider()).execute(
        ids["paper_b"], run_b.id
    )
    assert result["status"] == "completed_with_review_required"
    assert result["items"][0]["assessment_item_id"] == ids["item_b"]
    assert result["findings"][0]["page_id"] == ids["page_b"]
    assert all(not values for values in await durable_ids(connection, run_a.id))
    assert (
        await connection.scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:id"),
            {"id": ids["batch"]},
        )
        == "checking_completed"
    )


async def test_transport_budget_durable_contiguous_attempts(execution_database):
    connection, _, ids, _ = execution_database
    run = await start(execution_database)
    provider = FakePaperProvider("transport")
    execution = service(execution_database, provider)
    with pytest.raises(PaperCheckingExecutionError, match="paper_provider_failed"):
        await execution.execute(ids["paper_a"], run.id)
    attempts = await rows(
        connection,
        "SELECT attempt_no,status,error_code,request_fingerprint FROM model_runs WHERE check_run_id=:run ORDER BY attempt_no",
        run=run.id,
    )
    assert [a["attempt_no"] for a in attempts] == [1, 2, 3]
    assert all(
        a["status"] == "failed" and a["error_code"] == "transport" for a in attempts
    )
    assert len({a["request_fingerprint"] for a in attempts}) == 1
    assert len(provider.calls) == 3
    assert (
        await connection.scalar(
            text("SELECT status FROM check_runs WHERE id=:id"), {"id": run.id}
        )
        == "failed_retryable"
    )
    with pytest.raises(
        PaperCheckingExecutionError, match="paper_check_run_not_executable"
    ):
        await execution.execute(ids["paper_a"], run.id)
    assert len(provider.calls) == 3
    assert all(not values for values in await durable_ids(connection, run.id))


@pytest.mark.parametrize(
    "table,column",
    [
        ("paper_ai_revisions", "summary_draft"),
        ("paper_check_finding_regions", "short_explanation"),
        ("check_results", "summary"),
        ("check_findings", "snapshot_title"),
    ],
)
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_immutable_evidence_triggers(
    execution_database, table, column, operation
):
    connection, _, ids, _ = execution_database
    run = await start(execution_database)
    await service(execution_database, FakePaperProvider()).execute(
        ids["paper_a"], run.id
    )
    sets = await durable_ids(connection, run.id)
    index = {
        "paper_ai_revisions": 0,
        "check_results": 1,
        "check_findings": 2,
        "paper_check_finding_regions": 3,
    }[table]
    row_id = next(iter(sets[index]))
    sql = (
        f"UPDATE {table} SET {column}='changed' WHERE id=:id"
        if operation == "update"
        else f"DELETE FROM {table} WHERE id=:id"
    )
    await rejected(connection, sql, {"id": row_id}, match="immutable")
    assert await durable_ids(connection, run.id) == sets


async def test_revision_and_attempt_database_uniqueness(execution_database):
    connection, _, ids, _ = execution_database
    run = await start(execution_database)
    await service(execution_database, FakePaperProvider()).execute(
        ids["paper_a"], run.id
    )
    sets = await durable_ids(connection, run.id)
    for table, row_id, expected in (
        ("paper_ai_revisions", next(iter(sets[0])), "uq_paper_ai_revisions_run"),
        ("model_runs", next(iter(sets[4])), "uq_model_runs_paper_attempt"),
    ):
        with pytest.raises(IntegrityError) as caught:
            async with connection.begin_nested():
                await clone(connection, table, row_id)
        assert constraint(caught.value) == expected
    assert await durable_ids(connection, run.id) == sets


@pytest.mark.parametrize(
    "change",
    [
        "paper_submission_id",
        "check_run_id",
        "assessment_item_id",
        "remediation_plan_item_id",
    ],
)
async def test_model_run_identity_guard_and_finalization(execution_database, change):
    connection, factory, ids, _ = execution_database
    run = await start(execution_database)
    repository = SQLAlchemyPaperCheckingExecutionRepository(factory)
    prepared = await repository.prepare(
        ids["paper_a"], run.id, "acceptance-fake", "deterministic-vision"
    )
    # A terminal lifecycle update alongside identity mutation must still fail;
    # nullable sources specifically exercise IS DISTINCT FROM.
    await rejected(
        connection,
        f"UPDATE model_runs SET {change}=:changed,status='failed',error_code='transport',finished_at=clock_timestamp() WHERE id=:id",
        {
            "changed": ids["paper_b"] if change == "paper_submission_id" else uuid4(),
            "id": prepared.model_run_id,
        },
        match="identity is immutable",
    )
    assert await repository.fail(prepared, "transport", None)
    next_attempt = await repository.prepare(
        ids["paper_a"], run.id, "acceptance-fake", "deterministic-vision"
    )
    provider = FakePaperProvider()
    response, telemetry = await provider.check(
        next_attempt.compiled.request, {"content-0001": PNG}
    )
    result = await repository.succeed(next_attempt, response, telemetry)
    assert result["status"] == "completed_with_review_required"
    attempts = await rows(
        connection,
        "SELECT attempt_no,status FROM model_runs WHERE check_run_id=:run ORDER BY attempt_no",
        run=run.id,
    )
    assert [tuple(r.values()) for r in attempts] == [(1, "failed"), (2, "succeeded")]


@pytest.mark.parametrize(
    "sources", ["assessment_paper", "remediation_paper", "all", "none"]
)
async def test_model_run_source_xor_rejects_invalid_combinations(
    execution_database, sources
):
    connection, factory, ids, _ = execution_database
    run = await start(execution_database)
    prepared = await SQLAlchemyPaperCheckingExecutionRepository(factory).prepare(
        ids["paper_a"], run.id, "acceptance-fake", "deterministic-vision"
    )
    changes = {"attempt_no": 2}
    if sources in {"assessment_paper", "all"}:
        changes["assessment_item_id"] = str(ids["item_a"])
    if sources in {"remediation_paper", "all"}:
        changes["remediation_plan_item_id"] = str(uuid4())
    if sources == "none":
        changes["paper_submission_id"] = None
    with pytest.raises(IntegrityError) as caught:
        async with connection.begin_nested():
            await clone(connection, "model_runs", prepared.model_run_id, **changes)
    assert constraint(caught.value) == "ck_model_runs_execution_item_xor"


async def test_status_score_constraint_rejects_invalid_insert(execution_database):
    connection, _, ids, _ = execution_database
    run = await start(execution_database)
    result = await service(
        execution_database, FakePaperProvider(status="unreadable")
    ).execute(ids["paper_a"], run.id)
    with pytest.raises(IntegrityError) as caught:
        async with connection.begin_nested():
            await clone(
                connection,
                "check_results",
                result["items"][0]["check_result_id"],
                result_status="unreadable",
                score_suggested="1",
            )
    assert constraint(caught.value) == "ck_check_results_status_score"


def recursive_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from recursive_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from recursive_values(child)
    elif isinstance(value, str) and value.startswith(("{", "[")):
        yield value
        try:
            decoded = json.loads(value)
        except ValueError:
            return
        yield from recursive_values(decoded)
    else:
        yield value


async def test_private_identity_and_raw_bytes_absent_from_evidence(execution_database):
    connection, _, ids, storage = execution_database
    run = await start(execution_database)
    provider = FakePaperProvider()
    await service(execution_database, provider).execute(ids["paper_a"], run.id)
    blobs = [provider.calls[0][0].model_dump(mode="json")]
    for sql in (
        "SELECT input_snapshot FROM check_runs WHERE id=:run",
        "SELECT settings_snapshot,validated_output,validation_errors,raw_output FROM model_runs WHERE check_run_id=:run",
        "SELECT validated_response FROM paper_ai_revisions WHERE check_run_id=:run",
        "SELECT details FROM checker_events WHERE check_run_id=:run",
    ):
        for row in await rows(connection, sql, run=run.id):
            blobs.extend(row.values())
    forbidden = {
        "student_id",
        "user_id",
        "login",
        "email",
        "student_name",
        "display_name",
        "storage_reference",
        "filesystem_path",
        "original_filename",
        "image_base64",
        "image_bytes",
    }
    private_values = {
        str(ids[k])
        for k in ("student_a", "student_b", "teacher", "participant_a", "participant_b")
    }
    private_values.update(
        {
            ids["storage_a"],
            ids["storage_b"],
            ids["storage_source"],
            f"{ids['suffix']}/original.pdf",
            "Private A",
            "Private B",
        }
    )
    private_values.add(str(storage.root))
    for blob in blobs:
        leaves = list(recursive_values(blob))
        assert not forbidden.intersection(x for x in leaves if isinstance(x, str))
        assert not private_values.intersection(x for x in leaves if isinstance(x, str))
        assert not any(isinstance(x, bytes) for x in leaves)
        encoded = json.dumps(blob)
        assert base64.b64encode(PNG).decode() not in encoded
        assert "data:image/" not in encoded


@pytest.mark.parametrize(
    "mode", ["bad_mime", "bad_hash", "invalid_png", "metadata_mismatch"]
)
async def test_render_provenance_fails_closed(execution_database, mode):
    connection, _, ids, storage = execution_database
    # Separate immutable artifact: the original InputArtifact is never changed.
    raw = b"not a PNG" if mode == "invalid_png" else PNG
    digest = hashlib.sha256(raw).hexdigest()
    reference = await storage.store(raw, "image/png")
    artifact_id = uuid4()
    await connection.execute(
        text("""INSERT INTO input_artifacts(id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference)
        VALUES (:id,:owner,:mime,:hash,:size,:reference)"""),
        {
            "id": artifact_id,
            "owner": ids["teacher"],
            "mime": "image/jpeg" if mode == "bad_mime" else "image/png",
            "hash": "d" * 64 if mode == "metadata_mismatch" else digest,
            "size": len(raw),
            "reference": reference,
        },
    )
    await connection.execute(
        text(
            "UPDATE scan_pages SET derived_render_artifact_id=:artifact,content_fingerprint=:hash WHERE id=:page"
        ),
        {"artifact": artifact_id, "hash": digest, "page": ids["page_a"]},
    )
    if mode in {"bad_mime", "metadata_mismatch"}:
        # PR6 already prevents this handoff from becoming executable.
        from app.application.checking_intake import InvalidCheckingInput

        with pytest.raises(InvalidCheckingInput):
            await start(execution_database)
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM check_runs WHERE paper_submission_id=:paper"
                ),
                {"paper": ids["paper_a"]},
            )
            == 0
        )
        return
    run = await start(execution_database)
    if mode == "bad_hash":
        # Simulate storage corruption after a valid immutable handoff, not metadata mutation.
        storage._path(reference).write_bytes(PNG + b"corrupted")
    provider = FakePaperProvider()
    with pytest.raises(
        PaperCheckingExecutionError, match="paper_render_(hash_mismatch|not_png)"
    ):
        await service(execution_database, provider).execute(ids["paper_a"], run.id)
    assert not provider.calls
    assert all(not values for values in await durable_ids(connection, run.id))
    assert (
        await connection.scalar(
            text("SELECT status FROM check_runs WHERE id=:id"), {"id": run.id}
        )
        == "failed_terminal"
    )


@pytest.mark.parametrize("kind", ["assessment", "remediation"])
async def test_existing_digital_and_remediation_attempt_repository(
    execution_database, kind
):
    from app.application.checking import CreateRunCommand
    from app.application.checking_items import CheckingItemKey, CheckingItemKind
    from app.application.checking_provider import (
        ContractProbe,
        PromptSpec,
        ProviderMessage,
        build_request,
    )
    from app.infrastructure.checking_repository import CheckingRepository

    connection, factory, ids, _ = execution_database
    source_snapshot = {
        "submission_id": str(ids["digital_submission"]),
        "items": [
            {
                "assessment_item_id": str(ids["item_a"]),
                "task_version_id": str(ids["version_a"]),
                "points": "10",
            }
        ],
    }
    async with factory() as session, session.begin():
        repository = CheckingRepository(session)
        source = await repository.create_run(
            CreateRunCommand(
                ids["digital_submission"],
                "regression-source",
                "a" * 64,
                1,
                source_snapshot,
                "b" * 64,
                "v1",
                "v1",
                "v1",
                "v1",
                "v1",
            )
        )
    key = CheckingItemKey(CheckingItemKind.ASSESSMENT, ids["item_a"])
    run_id = source.id
    if kind == "remediation":
        from tests.integration.test_paper_checking_intake_database import terminalize

        await terminalize(factory, source.id)
        plan, plan_item, submission = uuid4(), uuid4(), uuid4()
        values = {
            **ids,
            "plan": plan,
            "plan_item": plan_item,
            "source_run": source.id,
            "submission": submission,
        }
        await connection.execute(
            text("""INSERT INTO remediation_plans(id,owner_user_id,student_id,class_group_id,
            source_assignment_id,source_assignment_participant_id,source_submission_id,source_check_run_id,
            status,title,due_at,creation_key,creation_fingerprint,assigned_at)
            VALUES (:plan,:teacher,:student_a,:group,:assignment,:participant_a,:digital_submission,
            :source_run,'assigned',:suffix,clock_timestamp()+interval '1 day',:suffix,:hash_a,clock_timestamp())"""),
            values,
        )
        await connection.execute(
            text("""INSERT INTO remediation_plan_items(id,remediation_plan_id,position,task_version_id,selection_source)
            VALUES (:plan_item,:plan,0,:version_a,'manual')"""),
            values,
        )
        # Existing shared checking repository accepts source-aware frozen items.
        await connection.execute(
            text("""INSERT INTO student_submissions(id,remediation_plan_id,attempt_no,status,submitted_at)
            VALUES (:submission,:plan,1,'submitted',clock_timestamp())"""),
            values,
        )
        snapshot = {
            "submission_id": str(submission),
            "items": [
                {
                    "remediation_plan_item_id": str(plan_item),
                    "task_version_id": str(ids["version_a"]),
                    "points": "10",
                }
            ],
        }
        async with factory() as session, session.begin():
            run_id = (
                await CheckingRepository(session).create_run(
                    CreateRunCommand(
                        submission,
                        "regression-remediation",
                        "c" * 64,
                        1,
                        snapshot,
                        "d" * 64,
                        "v1",
                        "v1",
                        "v1",
                        "v1",
                        "v1",
                    )
                )
            ).id
        key = CheckingItemKey(CheckingItemKind.REMEDIATION, plan_item)
    contract = ContractProbe()
    prompt = PromptSpec(
        f"regression-{ids['suffix']}",
        "1.0.0",
        "Synthetic template",
        contract.schema_version,
    )
    provider_request = build_request(
        provider_id="fake",
        model_id="probe",
        prompt=prompt,
        contract=contract,
        messages=(ProviderMessage("user", "Synthetic"),),
        settings={},
    )
    async with factory() as session, session.begin():
        repository = CheckingRepository(session)
        prompt_row = await repository.register_prompt(prompt)
        attempt = await repository.claim_model_attempt(
            run_id, key, prompt_row, provider_request
        )
        await repository.finalize_model_run(
            attempt.id, "failed", {"error_code": "transport"}
        )
    persisted = (
        await rows(connection, "SELECT * FROM model_runs WHERE id=:id", id=attempt.id)
    )[0]
    assert persisted["status"] == "failed" and persisted["paper_submission_id"] is None
    assert (
        persisted[f"{kind if kind == 'assessment' else 'remediation_plan'}_item_id"]
        == key.item_id
    )
    other = "remediation_plan_item_id" if kind == "assessment" else "assessment_item_id"
    assert persisted[other] is None
