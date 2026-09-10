"""Executable C9D projections over a persisted remediation Checking graph."""
import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.remediation import RemediationError
from app.application.remediation_results import RemediationResultsService
from tests.integration.c10a_postgres import rolled_back_connection
from tests.integration.c9ab_fixtures import seed_execution_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _world(connection, *, status="completed", scores=("2.00", "3.00")):
    ids = await seed_execution_world(connection)
    ids.update({name: uuid4() for name in ("submission", "run", "result_0", "result_1",
                                            "finding", "teacher_b")})
    fixture_sql = """
      INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by)
        VALUES (:group_a,:owner,:owner);
      INSERT INTO users(id,login,normalized_login,display_name,password_hash)
        VALUES (:teacher_b,'c9d-b','c9d-b','Teacher B','hash');
      INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by)
        VALUES (:group_a,:teacher_b,:owner);
      INSERT INTO student_submissions(id,remediation_plan_id,attempt_no,status,started_at,submitted_at)
        VALUES (:submission,:plan_0,1,'submitted',clock_timestamp(),clock_timestamp());
      INSERT INTO student_answers(submission_id,remediation_plan_item_id,raw_answer,normalized_answer)
        VALUES (:submission,:plan_item_0_0,'\"student-secret-answer\"',CAST(:answer0 AS jsonb)),
               (:submission,:plan_item_0_1,'\"second answer\"',CAST(:answer1 AS jsonb));
      INSERT INTO check_runs(id,submission_id,request_key,request_hash,handoff_version,input_snapshot,
        input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,
        threshold_policy_version,prompt_model_policy_version,status,attempt_no,started_at,finished_at)
        VALUES (:run,:submission,'result-run','dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
        1,'{}','eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
        'checking_input_remediation_v1','v1','v1','v1','v1',CAST(:status AS checking_run_status),1,
        clock_timestamp(),clock_timestamp());
      INSERT INTO check_results(id,check_run_id,remediation_plan_item_id,task_version_id,checker_type,
        checker_version,schema_version,result_status,reason_code,confidence_policy_version,
        confidence_details,score_suggested,max_score,confidence,summary,student_feedback_draft,
        teacher_summary,needs_human_review,review_reason,model_limitations,validated_result)
      VALUES (:result_0,:run,:plan_item_0_0,:version_0_0,'exact','v1','v1',
        CASE WHEN CAST(:score0 AS numeric) IS NULL THEN 'manual_required' ELSE 'correct' END::checking_result_status,
        'historical_result','v1',CAST(:confidence AS jsonb),CAST(:score0 AS numeric),2,1,
        'bounded summary','safe student feedback','private teacher summary',:review,
        CASE WHEN :review THEN 'teacher_confirmation' ELSE NULL END,'private model limitation',CAST(:validated AS jsonb)),
       (:result_1,:run,:plan_item_0_1,:version_0_1,'exact','v1','v1',
        CASE WHEN CAST(:score1 AS numeric) IS NULL THEN 'manual_required' ELSE 'correct' END::checking_result_status,
        'historical_result','v1',CAST(:confidence AS jsonb),CAST(:score1 AS numeric),3,1,
        'second summary','second safe feedback','second teacher summary',false,NULL,NULL,CAST(:validated AS jsonb));
      INSERT INTO check_findings(id,check_result_id,finding_type,snapshot_code,snapshot_title,
        snapshot_criterion,severity,confidence,evidence)
        VALUES (:finding,:result_0,'general','historical-code','Historical title','Historical criterion',
        'minor',.75,CAST(:evidence AS jsonb))
    """
    values = {**ids, "status": status, "score0": scores[0], "score1": scores[1],
              "review": status == "completed_with_review_required",
              "answer0": json.dumps({"text": "student-secret-answer"}),
              "answer1": json.dumps({"text": "second answer"}),
              "confidence": json.dumps({"effective": "1.0000"}),
              "validated": json.dumps({"safe": True}),
              "evidence": json.dumps({"private_provider_evidence": "must-not-project"})}
    for statement in fixture_sql.split(";"):
        if statement.strip():
            await connection.execute(text(statement), values)
    return ids, AsyncSession(bind=connection, expire_on_commit=False)


def _json(value):
    return json.dumps(value, default=str, sort_keys=True)


async def test_completed_teacher_and_student_serialized_projections_are_bounded_and_read_only():
    async with rolled_back_connection() as connection:
        ids, session = await _world(connection)
        service = RemediationResultsService(session)
        before = tuple((await connection.execute(text("""
          SELECT (SELECT count(*) FROM remediation_plans),(SELECT count(*) FROM check_runs),
                 (SELECT count(*) FROM check_results),(SELECT count(*) FROM model_runs)
        """))).one())
        teacher = await service.teacher_result(ids["plan_0"], ids["owner"])
        student = await service.student_execution(ids["plan_0"], ids["student_0"])
        after = tuple((await connection.execute(text("""
          SELECT (SELECT count(*) FROM remediation_plans),(SELECT count(*) FROM check_runs),
                 (SELECT count(*) FROM check_results),(SELECT count(*) FROM model_runs)
        """))).one())
        assert before == after
        assert (teacher["execution_status"], teacher["check_run_id"]) == ("checked", ids["run"])
        assert (teacher["suggested_score_total"], teacher["max_score_total"],
                teacher["suggested_percent"]) == (Decimal("5.00"), Decimal("5.00"), Decimal("100.00"))
        assert teacher["items"][0]["student_raw_answer"] == "student-secret-answer"
        assert teacher["items"][0]["check_result"]["teacher_summary"] == "private teacher summary"
        assert teacher["items"][0]["findings"][0]["snapshot_criterion"] == "Historical criterion"
        teacher_json = _json(teacher)
        for forbidden in ("raw_output", "prompt", "validated_output", "confidence_details",
                          "private_provider_evidence"):
            assert forbidden not in teacher_json
        assert student["execution_status"] == "checked"
        assert student["items"][0]["student_raw_answer"] == "student-secret-answer"
        assert student["items"][0]["student_feedback"] == "safe student feedback"
        student_json = _json(student)
        for forbidden in ("teacher_summary", "findings", "skill_id", "typical_error_id",
            "rubric_item_id", "snapshot_", "review_reason", "model_limitations",
            "expected_solution", "accepted_answers", '"rubric"', "provider", "raw_output"):
            assert forbidden not in student_json
        await session.close()


async def test_review_required_suppresses_uncertain_student_feedback():
    async with rolled_back_connection() as connection:
        ids, session = await _world(connection, status="completed_with_review_required",
                                    scores=(None, "3.00"))
        service = RemediationResultsService(session)
        teacher = await service.teacher_result(ids["plan_0"], ids["owner"])
        student = await service.student_execution(ids["plan_0"], ids["student_0"])
        assert teacher["execution_status"] == student["execution_status"] == "review_required"
        assert teacher["review_required_count"] == 1
        assert student["items"][0]["student_feedback"] == "Требуется проверка учителя."
        assert "safe student feedback" not in _json(student)
        await session.close()


@pytest.mark.parametrize("failure_status", ["failed_terminal", "failed_retryable"])
async def test_latest_failed_run_never_falls_back_or_leaks_failure_detail(failure_status):
    async with rolled_back_connection() as connection:
        ids, session = await _world(connection)
        failed = uuid4()
        await connection.execute(text("""
          INSERT INTO check_runs(id,submission_id,request_key,request_hash,handoff_version,input_snapshot,
            input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,
            threshold_policy_version,prompt_model_policy_version,status,attempt_no,started_at,finished_at,
            failure_code,failure_detail,supersedes_run_id)
          VALUES (:failed,:submission,'retry','ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
            1,'{}','9999999999999999999999999999999999999999999999999999999999999999',
            'checking_input_remediation_v1','v1','v1','v1','v1',CAST(:status AS checking_run_status),2,
            clock_timestamp(),clock_timestamp(),'provider_5xx','PRIVATE STACK TRACE',:run)
        """), {**ids, "failed": failed, "status": failure_status})
        service = RemediationResultsService(session)
        teacher = await service.teacher_result(ids["plan_0"], ids["owner"])
        student = await service.student_execution(ids["plan_0"], ids["student_0"])
        assert teacher["execution_status"] == student["execution_status"] == "check_failed"
        assert teacher["check_run_id"] == student["check_run_id"] == failed
        assert teacher["failure_code"] == "provider_5xx" and teacher["suggested_score_total"] is None
        assert all(item["check_result"] is None for item in teacher["items"])
        assert "PRIVATE STACK TRACE" not in _json(teacher) + _json(student)
        await session.close()


async def test_null_score_nullifies_aggregate_instead_of_showing_partial_total():
    async with rolled_back_connection() as connection:
        ids, session = await _world(connection, status="completed_with_review_required",
                                    scores=(None, "3.00"))
        result = await RemediationResultsService(session).teacher_result(ids["plan_0"], ids["owner"])
        assert result["suggested_score_total"] is result["suggested_percent"] is None
        assert result["max_score_total"] == Decimal("5.00")
        await session.close()


async def test_teacher_membership_and_student_idor_are_fail_closed_and_reversible():
    async with rolled_back_connection() as connection:
        ids, session = await _world(connection)
        service = RemediationResultsService(session)
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await service.teacher_result(ids["plan_0"], ids["teacher_b"])
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await service.student_execution(ids["plan_0"], ids["student_1"])
        await connection.execute(text("DELETE FROM class_group_teachers WHERE class_group_id=:group_a AND teacher_user_id=:owner"), ids)
        session.expire_all()
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await service.teacher_result(ids["plan_0"], ids["owner"])
        await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group_a,:owner,:owner)"), ids)
        assert (await service.teacher_result(ids["plan_0"], ids["owner"]))["check_run_id"] == ids["run"]
        await session.close()


async def test_cancelled_plan_keeps_historical_result_without_rechecking_or_follow_up():
    async with rolled_back_connection() as connection:
        ids, session = await _world(connection)
        await connection.execute(text("UPDATE remediation_plans SET status='cancelled',cancelled_at=clock_timestamp() WHERE id=:plan_0"), ids)
        before = await connection.scalar(text("SELECT count(*) FROM remediation_plans"))
        result = await RemediationResultsService(session).student_execution(ids["plan_0"], ids["student_0"])
        assert (result["plan_status"], result["execution_status"], result["check_run_id"]) == (
            "cancelled", "checked", ids["run"])
        assert await connection.scalar(text("SELECT count(*) FROM remediation_plans")) == before
        await session.close()
