"""Executable C9A contracts against a disposable PostgreSQL database."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.c10a_postgres import require_disposable_postgres, rolled_back_connection
from tests.integration.c9ab_fixtures import seed_execution_world

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]
BACKEND = Path(__file__).parents[2]


async def _rejected(connection, sql, values):
    """Prove PostgreSQL (not SQLAlchemy metadata) rejects a row."""
    savepoint = await connection.begin_nested()
    with pytest.raises(IntegrityError):
        await connection.execute(text(sql), values)
    await savepoint.rollback()


async def _insert_result(connection, ids, **targets):
    values = {**ids, **targets, "id": uuid4()}
    await connection.execute(text("""
      INSERT INTO check_results(id,check_run_id,assessment_item_id,remediation_plan_item_id,
        task_version_id,checker_type,checker_version,schema_version,result_status,reason_code,
        confidence_policy_version,confidence_details,score_suggested,max_score,confidence,
        summary,needs_human_review,validated_result)
      VALUES (:id,:source_run_0,:assessment_item_id,:remediation_plan_item_id,:version_0,
        'deterministic','v1','v1','correct','exact','v1',
        '{"effective":"1.0000"}',1,1,1,'correct',false,'{}')
    """), values)
    return values["id"]


async def test_execution_targets_enforce_row_xor_and_partial_uniqueness():
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection)
        common = {**ids, "assessment_item_id": ids["assessment_item_0"],
                  "remediation_plan_item_id": ids["plan_item_0_0"]}
        submission_sql = "INSERT INTO student_submissions(id,assignment_participant_id,remediation_plan_id,attempt_no) VALUES (:id,:participant,:plan,:attempt)"
        # Both target kinds accept attempt 1 without colliding.
        assessment_submission, remediation_submission = uuid4(), uuid4()
        await connection.execute(text(submission_sql), {"id": assessment_submission, "participant": ids["participant_0"], "plan": None, "attempt": 2})
        await connection.execute(text(submission_sql), {"id": remediation_submission, "participant": None, "plan": ids["plan_0"], "attempt": 1})
        await _rejected(connection, submission_sql, {"id": uuid4(), "participant": None, "plan": None, "attempt": 1})
        await _rejected(connection, submission_sql, {"id": uuid4(), "participant": ids["participant_0"], "plan": ids["plan_0"], "attempt": 3})
        await _rejected(connection, submission_sql, {"id": uuid4(), "participant": ids["participant_0"], "plan": None, "attempt": 2})
        await _rejected(connection, submission_sql, {"id": uuid4(), "participant": None, "plan": ids["plan_0"], "attempt": 1})

        answer_sql = "INSERT INTO student_answers(id,submission_id,assessment_item_id,remediation_plan_item_id,raw_answer,normalized_answer) VALUES (:id,:submission,:assessment_item_id,:remediation_plan_item_id,'\"x\"','\"x\"')"
        await connection.execute(text(answer_sql), {**common, "id": uuid4(), "submission": assessment_submission, "remediation_plan_item_id": None})
        await connection.execute(text(answer_sql), {**common, "id": uuid4(), "submission": remediation_submission, "assessment_item_id": None})
        await _rejected(connection, answer_sql, {**common, "id": uuid4(), "submission": assessment_submission, "assessment_item_id": None, "remediation_plan_item_id": None})
        await _rejected(connection, answer_sql, {**common, "id": uuid4(), "submission": assessment_submission})
        await _rejected(connection, answer_sql, {**common, "id": uuid4(), "submission": assessment_submission, "remediation_plan_item_id": None})
        await _rejected(connection, answer_sql, {**common, "id": uuid4(), "submission": remediation_submission, "assessment_item_id": None})

        assessment_result = await _insert_result(connection, common, remediation_plan_item_id=None)
        remediation_result = await _insert_result(connection, common, assessment_item_id=None)
        result_sql = """INSERT INTO check_results(id,check_run_id,assessment_item_id,remediation_plan_item_id,task_version_id,checker_type,checker_version,schema_version,result_status,reason_code,confidence_policy_version,confidence_details,score_suggested,max_score,confidence,summary,needs_human_review,validated_result) VALUES (:id,:source_run_0,:assessment_item_id,:remediation_plan_item_id,:version_0,'deterministic','v1','v1','correct','exact','v1','{\"effective\":\"1.0000\"}',1,1,1,'correct',false,'{}')"""
        for targets in ({"assessment_item_id": None, "remediation_plan_item_id": None}, common,
                        {"assessment_item_id": ids["assessment_item_0"], "remediation_plan_item_id": None},
                        {"assessment_item_id": None, "remediation_plan_item_id": ids["plan_item_0_0"]}):
            await _rejected(connection, result_sql, {**common, **targets, "id": uuid4()})

        prompt = uuid4()
        await connection.execute(text("INSERT INTO prompt_versions(id,name,semantic_version,template_hash,output_schema_version,template_text) VALUES (:id,'c9ab','v1','dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd','v1','prompt')"), {"id": prompt})
        model_sql = """INSERT INTO model_runs(id,check_run_id,assessment_item_id,remediation_plan_item_id,prompt_version_id,check_result_id,provider_id,model_id,settings_snapshot,request_fingerprint,attempt_no,timeout_ms) VALUES (:id,:source_run_0,:assessment_item_id,:remediation_plan_item_id,:prompt,:result,'provider','model','{}','eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',:attempt,1000)"""
        await connection.execute(text(model_sql), {**common, "id": uuid4(), "prompt": prompt, "result": assessment_result, "attempt": 1, "remediation_plan_item_id": None})
        await connection.execute(text(model_sql), {**common, "id": uuid4(), "prompt": prompt, "result": remediation_result, "attempt": 1, "assessment_item_id": None})
        for targets in ({"assessment_item_id": None, "remediation_plan_item_id": None}, common,
                        {"assessment_item_id": ids["assessment_item_0"], "remediation_plan_item_id": None},
                        {"assessment_item_id": None, "remediation_plan_item_id": ids["plan_item_0_0"]}):
            await _rejected(connection, model_sql, {**common, **targets, "id": uuid4(), "prompt": prompt, "result": None, "attempt": 1})

        event_sql = "INSERT INTO checker_events(id,check_run_id,assessment_item_id,remediation_plan_item_id,event_type,details) VALUES (:id,:source_run_0,:assessment_item_id,:remediation_plan_item_id,'routing_decision','{}')"
        for targets in ({"assessment_item_id": None, "remediation_plan_item_id": None},
                        {"assessment_item_id": ids["assessment_item_0"], "remediation_plan_item_id": None},
                        {"assessment_item_id": None, "remediation_plan_item_id": ids["plan_item_0_0"]}):
            await connection.execute(text(event_sql), {**ids, **targets, "id": uuid4()})
        await _rejected(connection, event_sql, {**common, "id": uuid4()})


async def test_schema_metadata_remains_explicit_and_at_expected_head():
    async with rolled_back_connection() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260907_04"
        names = set((await connection.execute(text("SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_%execution_%'"))).scalars())
        assert {"ck_student_submissions_execution_target_xor", "ck_student_answers_execution_item_xor",
                "ck_check_results_execution_item_xor", "ck_model_runs_execution_item_xor",
                "ck_checker_events_execution_item_at_most_one"} <= names


def _alembic(*args):
    env = os.environ.copy()
    env["DATABASE_URL"] = require_disposable_postgres()
    result = subprocess.run(["alembic", *args], cwd=BACKEND, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_exact_20260907_03_to_20260907_04_preserves_assessment_execution_rows():
    """Step the exact C9A edge and prove historical rows are altered in place."""
    engine = create_async_engine(require_disposable_postgres())
    try:
        async with engine.begin() as c:
            await c.execute(text("DROP SCHEMA public CASCADE"))
            await c.execute(text("CREATE SCHEMA public"))
        _alembic("upgrade", "20260907_03")
        async with engine.begin() as c:
            ids = await seed_execution_world(c, plans=1)
            ids.update({name: uuid4() for name in ("answer", "result", "prompt", "model", "event")})
            await c.execute(text("INSERT INTO student_answers(id,submission_id,assessment_item_id,raw_answer,normalized_answer) VALUES (:answer,:source_submission_0,:assessment_item_0,'\"old\"','\"old\"')"), ids)
            await c.execute(text("""INSERT INTO check_results(id,check_run_id,assessment_item_id,task_version_id,checker_type,checker_version,schema_version,result_status,reason_code,confidence_policy_version,confidence_details,score_suggested,max_score,confidence,summary,needs_human_review,validated_result) VALUES (:result,:source_run_0,:assessment_item_0,:version_0,'deterministic','v1','v1','correct','exact','v1','{"effective":"1.0000"}',1,1,1,'old',false,'{}')"""), ids)
            await c.execute(text("INSERT INTO prompt_versions(id,name,semantic_version,template_hash,output_schema_version,template_text) VALUES (:prompt,'old','v1','ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff','v1','old')"), ids)
            await c.execute(text("""INSERT INTO model_runs(id,check_run_id,assessment_item_id,prompt_version_id,check_result_id,provider_id,model_id,settings_snapshot,request_fingerprint,attempt_no,timeout_ms) VALUES (:model,:source_run_0,:assessment_item_0,:prompt,:result,'provider','model','{}','eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',1,1000)"""), ids)
            await c.execute(text("INSERT INTO checker_events(id,check_run_id,check_result_id,assessment_item_id,event_type,details) VALUES (:event,:source_run_0,:result,:assessment_item_0,'routing_decision','{}')"), ids)
        _alembic("upgrade", "20260907_04")
        async with engine.connect() as c:
            expectations = (("student_submissions", "source_submission_0", "assignment_participant_id"),
                ("student_answers", "answer", "assessment_item_id"), ("check_runs", "source_run_0", None),
                ("check_results", "result", "assessment_item_id"), ("model_runs", "model", "assessment_item_id"),
                ("checker_events", "event", "assessment_item_id"))
            for table, key, old_column in expectations:
                row = (await c.execute(text(f"SELECT * FROM {table} WHERE id=:id"), {"id": ids[key]})).mappings().one()
                assert row["id"] == ids[key]
                if old_column:
                    assert row[old_column] == ids["participant_0" if old_column == "assignment_participant_id" else "assessment_item_0"]
                if table == "student_submissions":
                    assert row["remediation_plan_id"] is None
                if table in {"student_answers", "check_results", "model_runs", "checker_events"}:
                    assert row["remediation_plan_item_id"] is None
                assert await c.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    finally:
        async with engine.begin() as c:
            await c.execute(text("DROP SCHEMA public CASCADE"))
            await c.execute(text("CREATE SCHEMA public"))
        await engine.dispose()
        _alembic("upgrade", "head")
