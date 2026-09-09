"""C8 remediation provenance, privacy, lifecycle, and ranking DB contracts."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.remediation import RemediationError
from app.infrastructure.auth_models import User
from app.infrastructure.remediation_repository import RemediationRepository
from app.presentation.remediation_schemas import CandidateSearch, CreateRemediation, ItemInput, UpdateRemediation
from tests.integration.c10a_postgres import assert_constraints, assert_tables, rolled_back_connection
from tests.integration.c9ab_fixtures import seed_execution_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_provenance_chain_and_selected_finding_task_constraints_exist():
    async with rolled_back_connection() as connection:
        await assert_tables(connection, "remediation_plans", "remediation_plan_signals", "remediation_plan_items", "remediation_events")
        foreign_keys = set((await connection.execute(text("""
          SELECT a.attname FROM pg_constraint c
          JOIN unnest(c.conkey) k(attnum) ON true
          JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum
          WHERE c.contype='f' AND c.conrelid='remediation_plans'::regclass
        """))).scalars())
        assert {"source_assignment_id","source_assignment_participant_id","source_submission_id","source_check_run_id","student_id"} <= foreign_keys
        await assert_constraints(connection, "uq_remediation_plans_owner_creation_key")


async def test_lifecycle_cas_privacy_and_candidate_inputs_are_persisted():
    async with rolled_back_connection() as connection:
        metadata = User.__table__.metadata
        for table_name, column_name in (
            ("remediation_plans", "owner_user_id"),
            ("remediation_events", "actor_user_id"),
        ):
            foreign_key = next(iter(metadata.tables[table_name].c[column_name].foreign_keys))
            assert foreign_key.column is User.__table__.c.id
        columns = set((await connection.execute(text("""
          SELECT column_name FROM information_schema.columns WHERE table_name='remediation_plans'
        """))).scalars())
        assert {"owner_user_id","status","updated_at","assigned_at","cancelled_at","review_acknowledged_at","class_group_id"} <= columns
        links = set((await connection.execute(text("""
          SELECT table_name FROM information_schema.tables WHERE table_schema='public'
          AND table_name=ANY(ARRAY['task_skill_links','task_error_links','task_versions','check_findings'])
        """))).scalars())
        assert links == {"task_skill_links","task_error_links","task_versions","check_findings"}


async def test_source_substitution_is_rejected_as_one_coherent_chain():
    """Individually valid foreign IDs must not compose a valid remediation source."""
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection)
        await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group_a,:owner,:owner)"), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)
        valid = (ids["assignment_0"], ids["student_0"], ids["source_submission_0"],
                 ids["source_run_0"], ids["participant_0"])
        row = await repository.source(ids["owner"], False, *valid)
        assignment, participant, submission, run, student, _assessment = row
        assert assignment.id == ids["assignment_0"]
        assert participant.id == ids["participant_0"]
        assert submission.id == ids["source_submission_0"]
        assert run.id == ids["source_run_0"]
        assert student.id == ids["student_0"]
        substitutions = (
            (ids["assignment_0"], ids["student_0"], ids["source_submission_0"], ids["source_run_0"], ids["participant_1"]),
            (ids["assignment_0"], ids["student_0"], ids["source_submission_1"], ids["source_run_0"], ids["participant_0"]),
            (ids["assignment_0"], ids["student_0"], ids["source_submission_0"], ids["source_run_1"], ids["participant_0"]),
            (ids["assignment_0"], ids["student_1"], ids["source_submission_0"], ids["source_run_0"], ids["participant_0"]),
        )
        for candidate in substitutions:
            with pytest.raises(RemediationError, match="remediation_source_not_found") as error:
                await repository.source(ids["owner"], False, *candidate)
            assert error.value.status == 404
        await session.close()


async def test_cancel_and_owner_privacy_are_idempotent_and_preserve_source():
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection)
        await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group_a,:owner,:owner)"), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)
        before = await connection.scalar(text("SELECT count(*) FROM assignment_participants WHERE id=:participant_0"), ids)
        cancelled = await repository.cancel(ids["plan_0"], ids["owner"], False)
        replay = await repository.cancel(ids["plan_0"], ids["owner"], False)
        assert cancelled["status"] == replay["status"] == "cancelled"
        assert cancelled["cancelled_at"] == replay["cancelled_at"]
        assert await connection.scalar(text("SELECT count(*) FROM remediation_events WHERE remediation_plan_id=:plan_0 AND event_type='plan_cancelled'"), ids) == 1
        assert await connection.scalar(text("SELECT count(*) FROM assignment_participants WHERE id=:participant_0"), ids) == before
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await repository.owned(ids["plan_0"], ids["user_1"], False)
        await session.close()


def _draft(ids, key, *, title="Focused remediation"):
    return CreateRemediation(
        creation_key=key,
        student_id=ids["student_0"],
        class_group_id=ids["group_a"],
        source_assignment_id=ids["assignment_0"],
        source_assignment_participant_id=ids["participant_0"],
        source_submission_id=ids["source_submission_0"],
        source_check_run_id=ids["source_run_0"],
        title=title,
        items=[ItemInput(task_version_id=ids["version_0_0"], selection_source="suggested")],
    )


@pytest.mark.parametrize("availability", ["eligible", "unapproved", "archived"])
async def test_assign_locks_eligible_rows_and_rejects_unavailable_content(availability):
    """Send locks concrete TaskVersion and Task rows rather than an aggregate."""
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection, plans=1)
        await connection.execute(text(
            "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) "
            "VALUES (:group_a,:owner,:owner)"), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)
        created = await repository.create(
            ids["owner"], False, _draft(ids, f"availability-{availability}"))

        if availability == "unapproved":
            await connection.execute(text(
                "UPDATE task_versions SET status='draft' WHERE id=:version_0_0"), ids)
        elif availability == "archived":
            await connection.execute(text(
                "UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:task_0_0"), ids)

        if availability == "eligible":
            assigned = await repository.assign(created["id"], ids["owner"], False, False)
            assert assigned["status"] == "assigned"
            assert assigned["assigned_at"] is not None
            assert await connection.scalar(text(
                "SELECT count(*) FROM remediation_events WHERE remediation_plan_id=:id "
                "AND event_type='plan_assigned'"), {"id": created["id"]}) == 1
        else:
            with pytest.raises(RemediationError, match="remediation_task_unavailable"):
                await repository.assign(created["id"], ids["owner"], False, False)
            assert (await repository.owned(created["id"], ids["owner"], False)).status == "draft"
            assert await connection.scalar(text(
                "SELECT count(*) FROM remediation_events WHERE remediation_plan_id=:id "
                "AND event_type='plan_assigned'"), {"id": created["id"]}) == 0
        await session.close()


async def test_real_draft_idempotency_cas_send_and_move_after_send():
    """C8 lifecycle acceptance through the existing application repository boundary."""
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection, plans=1)
        await connection.execute(text(
            "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group_a,:owner,:owner)"), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)

        created = await repository.create(ids["owner"], False, _draft(ids, "lifecycle-K"))
        replay = await repository.create(ids["owner"], False, _draft(ids, "lifecycle-K"))
        assert replay["id"] == created["id"]
        assert await connection.scalar(text(
            "SELECT count(*) FROM remediation_plans WHERE creation_key='lifecycle-K'")) == 1
        with pytest.raises(RemediationError, match="remediation_idempotency_conflict") as conflict:
            await repository.create(ids["owner"], False, _draft(ids, "lifecycle-K", title="Changed intent"))
        assert conflict.value.status == 409

        token = created["updated_at"]
        await repository.update(created["id"], ids["owner"], False,
            UpdateRemediation(expected_updated_at=token, title="Update A"))
        with pytest.raises(RemediationError, match="remediation_concurrent_conflict"):
            await repository.update(created["id"], ids["owner"], False,
                UpdateRemediation(expected_updated_at=token, title="Update B"))
        assert (await repository.owned(created["id"], ids["owner"], False)).title == "Update A"

        assignment_count = await connection.scalar(text("SELECT count(*) FROM assignments"))
        assigned = await repository.assign(created["id"], ids["owner"], False, False)
        assigned_replay = await repository.assign(created["id"], ids["owner"], False, False)
        assert assigned["status"] == "assigned"
        assert assigned["assigned_at"] is not None
        assert assigned["student_id"] == ids["student_0"]
        assert assigned["class_group_id"] == ids["group_a"]
        assert assigned_replay["id"] == assigned["id"]
        assert await connection.scalar(text(
            "SELECT count(*) FROM remediation_events WHERE remediation_plan_id=:id "
            "AND event_type='plan_assigned'"), {"id": created["id"]}) == 1
        assert await connection.scalar(text("SELECT count(*) FROM assignments")) == assignment_count

        await connection.execute(text(
            "UPDATE students SET class_group_id=:group_b WHERE id=:student_0"), ids)
        historical = await repository.owned(created["id"], ids["owner"], False)
        assert historical.status == "assigned"
        assert historical.student_id == ids["student_0"]
        assert historical.class_group_id == ids["group_a"]
        await session.close()


async def test_real_send_requires_review_acknowledgement_and_rejects_pre_send_move():
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection, plans=1)
        await connection.execute(text(
            "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group_a,:owner,:owner)"), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)

        await connection.execute(text(
            "UPDATE check_runs SET status='completed_with_review_required', "
            "row_version=row_version + 1 WHERE id=:source_run_0"), ids)
        review = await repository.create(ids["owner"], False, _draft(ids, "review-K"))
        with pytest.raises(RemediationError, match="remediation_review_ack_required"):
            await repository.assign(review["id"], ids["owner"], False, False)
        acknowledged = await repository.assign(review["id"], ids["owner"], False, True)
        assert acknowledged["status"] == "assigned"
        assert acknowledged["review_acknowledged_at"] is not None

        await connection.execute(text(
            "UPDATE check_runs SET status='completed', row_version=row_version + 1 "
            "WHERE id=:source_run_0"), ids)
        moved = await repository.create(ids["owner"], False, _draft(ids, "move-K"))
        event_count = await connection.scalar(text("SELECT count(*) FROM remediation_events"))
        await connection.execute(text(
            "UPDATE students SET class_group_id=:group_b WHERE id=:student_0"), ids)
        with pytest.raises(RemediationError, match="remediation_student_moved"):
            await repository.assign(moved["id"], ids["owner"], False, False)
        assert (await repository.owned(moved["id"], ids["owner"], False)).status == "draft"
        assert await connection.scalar(text("SELECT count(*) FROM remediation_events")) == event_count
        await session.close()


async def test_real_candidate_search_eligibility_ranking_and_manual_mode():
    """Candidate selection is local SQL: it has no provider port or provider calls."""
    from uuid import uuid4

    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection, plans=1)
        ids.update({name: uuid4() for name in (
            "subtopic", "skill", "error", "result", "finding", "wrong_subject", "wrong_grade",
            "wrong_topic", "wrong_grade_topic", "te_task", "te_version", "primary_task",
            "primary_version", "secondary_task", "secondary_version", "irrelevant_task",
            "irrelevant_version", "wrong_subject_task", "wrong_subject_version", "wrong_grade_task",
            "wrong_grade_version", "unapproved_task", "unapproved_version", "inactive_task",
            "inactive_version")})
        fixture_statements = (
          """INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by)
            VALUES (:group_a,:owner,:owner)""",
          """INSERT INTO subtopics(id,topic_id,code,name,normalized_name)
            VALUES (:subtopic,:topic,'candidate-sub','Candidate Sub','candidate sub');
          """,
          """INSERT INTO skills(id,subtopic_id,code,name,normalized_name)
            VALUES (:skill,:subtopic,'candidate-skill','Candidate Skill','candidate skill');
          """,
          """INSERT INTO typical_errors(id,skill_id,code,title,description,severity)
            VALUES (:error,:skill,'candidate-error','Exact error','Exact error','high');
          """,
          """INSERT INTO check_results(id,check_run_id,assessment_item_id,task_version_id,checker_type,
            checker_version,schema_version,result_status,reason_code,confidence_policy_version,
            confidence_details,score_suggested,max_score,confidence,summary,needs_human_review,
            validated_result)
          VALUES (:result,:source_run_0,:assessment_item_0,:version_0,'exact','v1','v1','incorrect',
            'incorrect','v1','{"effective":"1.0000"}',0,1,1,'Incorrect',false,'{}');
          """,
          """INSERT INTO check_findings(id,check_result_id,finding_type,typical_error_id,skill_id,
            snapshot_code,snapshot_title,severity,confidence,evidence)
          VALUES (:finding,:result,'typical_error',:error,:skill,'candidate-error','Exact error',
            'major',1,'[]');
          """,
          """INSERT INTO subjects(id,code,name,normalized_name)
            VALUES (:wrong_subject,'wrong-subject','Wrong Subject','wrong subject');
          """,
          """INSERT INTO grades(id,number,name,normalized_name)
            VALUES (:wrong_grade,8,'Wrong Grade','wrong grade');
          """,
          """INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name) VALUES
            (:wrong_topic,:wrong_subject,:grade,'wrong-topic','Wrong Topic','wrong topic'),
            (:wrong_grade_topic,:subject,:wrong_grade,'wrong-grade-topic','Wrong Grade Topic','wrong grade topic')
          """,
        )
        for statement in fixture_statements:
            await connection.execute(text(statement), ids)
        candidates = (
            ("te", "Candidate TE", ids["subject"], ids["grade"], ids["topic"], "approved", False, 40),
            ("primary", "Candidate Primary Manual Needle", ids["subject"], ids["grade"], ids["topic"], "approved", False, 40),
            ("secondary", "Candidate Secondary", ids["subject"], ids["grade"], ids["topic"], "approved", False, 41),
            ("irrelevant", "Candidate Irrelevant", ids["subject"], ids["grade"], ids["topic"], "approved", False, 40),
            ("wrong_subject", "Manual Needle WrongSubject", ids["wrong_subject"], ids["grade"], ids["wrong_topic"], "approved", False, 40),
            ("wrong_grade", "Manual Needle WrongGrade", ids["subject"], ids["wrong_grade"], ids["wrong_grade_topic"], "approved", False, 40),
            ("unapproved", "Manual Needle Unapproved", ids["subject"], ids["grade"], ids["topic"], "draft", False, 40),
            ("inactive", "Manual Needle Inactive", ids["subject"], ids["grade"], ids["topic"], "approved", True, 40),
        )
        for key, title, subject, grade, topic, status, inactive, difficulty in candidates:
            values = {**ids, "task_id": ids[f"{key}_task"], "version_id": ids[f"{key}_version"],
                      "title": title, "candidate_subject": subject, "candidate_grade": grade,
                      "candidate_topic": topic, "status": status, "difficulty": difficulty,
                      "inactive": inactive}
            task_statements = (
              """
              INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by,archived_at)
                VALUES (:task_id,:candidate_subject,:candidate_grade,:candidate_topic,:owner,
                  CASE WHEN :inactive THEN clock_timestamp() END)
              """,
              """INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,
                difficulty,status,created_by,approved_by,approved_at)
                VALUES (:version_id,:task_id,1,:title,:title,'problem','short_text',:difficulty,:status,
                  :owner,CASE WHEN :status='approved' THEN :owner END,
                  CASE WHEN :status='approved' THEN clock_timestamp() END)
              """,
            )
            for statement in task_statements:
                await connection.execute(text(statement), values)
        link_statements = (
          """INSERT INTO task_error_links(task_version_id,typical_error_id)
            VALUES (:te_version,:error)""",
          """INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) VALUES
            (:primary_version,:skill,1,true),(:secondary_version,:skill,.5,false),
            (:wrong_subject_version,:skill,1,true),(:wrong_grade_version,:skill,1,true),
            (:unapproved_version,:skill,1,true),(:inactive_version,:skill,1,true)
          """,
        )
        for statement in link_statements:
            await connection.execute(text(statement), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)
        base = dict(assignment_id=ids["assignment_0"], student_id=ids["student_0"],
                    source_submission_id=ids["source_submission_0"],
                    source_check_run_id=ids["source_run_0"], finding_ids=[ids["finding"]])
        suggested = await repository.candidates(ids["owner"], False, CandidateSearch(**base))
        returned = [row["task_version_id"] for row in suggested]
        assert returned == [ids["te_version"], ids["primary_version"], ids["secondary_version"]]
        assert [row["reasons"][0]["match_type"] for row in suggested] == [
            "typical_error", "primary_skill", "secondary_skill"]
        excluded = {ids["version_0"], ids["wrong_subject_version"], ids["wrong_grade_version"],
                    ids["unapproved_version"], ids["inactive_version"]}
        assert excluded.isdisjoint(returned)

        manual = await repository.candidates(ids["owner"], False,
            CandidateSearch(**base, mode="manual", q="Manual Needle"))
        assert [row["task_version_id"] for row in manual] == [ids["primary_version"]]
        await session.close()
