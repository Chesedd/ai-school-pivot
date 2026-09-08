"""C10D3S: one real Classroom -> Assessment -> remediation product smoke.

Only identity, role, catalogue, and immutable Assessment authoring prerequisites are
fixture SQL.  Every mutable product artifact is created through its application
boundary below.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.assessments import AssessmentService, CreateAssignmentCommand
from app.application.checking_deterministic import execute_deterministic
from app.application.checking_intake import CheckingIntakeRequest, CheckingIntakeService
from app.application.checking_results import ConfidenceGatePolicy
from app.application.checking_routing import CheckerRequest, route_snapshot
from app.application.classroom_administration import ClassroomAdministrationService
from app.application.classroom_notes import ClassroomNotesError, ClassroomNotesService
from app.application.classroom_results import (
    ClassroomAssessmentResultsService,
    ClassroomResultsError,
    ResultActor,
)
from app.application.content_bank import ActorContext
from app.application.remediation import RemediationError
from app.application.remediation_execution import RemediationExecutionService
from app.application.remediation_results import RemediationResultsService
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentUnitOfWork
from app.infrastructure.checking_intake_repository import SQLAlchemyCheckingIntakeUnitOfWorkFactory
from app.infrastructure.checking_repository import CheckingRepository, SQLAlchemyCheckingResultPersistence
from app.infrastructure.classroom_repository import (
    SQLAlchemyClassroomNotesRepository,
    SQLAlchemyClassroomRepository,
)
from app.infrastructure.classroom_results_repository import SQLAlchemyClassroomResultsReadRepository
from app.infrastructure.remediation_repository import RemediationRepository
from app.infrastructure.student_assessment_repository import StudentAssessmentService
from app.presentation.remediation_schemas import CandidateSearch, CreateRemediation, ItemInput
from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class ProviderCounter:
    """A tripwire documenting that the entirely deterministic vertical uses no LLM."""

    def __init__(self):
        self.calls = 0

    async def evaluate(self, _request):
        self.calls += 1
        raise AssertionError("the deterministic smoke must not call an LLM provider")


async def _finish_deterministically(factory, run):
    async with factory() as session, session.begin():
        running = await CheckingRepository(session).transition_run(run.id, run.row_version, "running")
    decisions = route_snapshot(run.input_snapshot)
    drafts = tuple(
        [await execute_deterministic(CheckerRequest(item, decision))
         for item, decision in zip(run.input_snapshot["items"], decisions)]
    )
    gate = ConfidenceGatePolicy(
        "checking_confidence_v1", Decimal("0.5"), Decimal("0.1"),
        Decimal("0.1"), Decimal("0.1"), Decimal("0.1"),
    )
    return await SQLAlchemyCheckingResultPersistence(factory).finalize(
        run.id, running.row_version, gate, drafts
    )


async def test_full_classroom_product_vertical():
    """C10D3S full product smoke; focused tests retain responsibility for matrices."""
    async with rolled_back_connection() as connection:
        ids = {name: uuid4() for name in (
            "admin", "teacher_a", "teacher_b", "student_a_user", "student_b_user",
            "grade", "subject", "topic", "subtopic", "skill", "source_task",
            "source_version", "candidate_task", "candidate_version", "accepted",
            "candidate_accepted",
            "assessment", "variant", "assessment_item",
        )}
        # Fixture SQL: auth has no convenient in-process password/role bootstrap;
        # Content Bank and the immutable published Assessment graph are prerequisites.
        await connection.execute(text("""
          INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES
            (:admin,'vertical-admin','vertical-admin','Admin','hash'),
            (:teacher_a,'vertical-teacher-a','vertical-teacher-a','Teacher A','hash'),
            (:teacher_b,'vertical-teacher-b','vertical-teacher-b','Teacher B','hash'),
            (:student_a_user,'vertical-student-a','vertical-student-a','Student A','hash'),
            (:student_b_user,'vertical-student-b','vertical-student-b','Student B','hash');
          INSERT INTO user_roles(user_id,role) VALUES
            (:admin,'admin'),(:teacher_a,'teacher'),(:teacher_b,'teacher'),
            (:student_a_user,'student'),(:student_b_user,'student');
          INSERT INTO grades(id,number,name,normalized_name)
            VALUES (:grade,7,'Vertical Grade 7','vertical grade 7');
          INSERT INTO subjects(id,code,name,normalized_name)
            VALUES (:subject,'vertical-subject','Vertical Subject','vertical subject');
          INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name)
            VALUES (:topic,:subject,:grade,'vertical-topic','Vertical Topic','vertical topic');
          INSERT INTO subtopics(id,topic_id,code,name,normalized_name)
            VALUES (:subtopic,:topic,'vertical-subtopic','Vertical Subtopic','vertical subtopic');
          INSERT INTO skills(id,subtopic_id,code,name,normalized_name)
            VALUES (:skill,:subtopic,'vertical-skill','Vertical Skill','vertical skill');
          INSERT INTO tasks(id,subject_id,grade_id,topic_id,subtopic_id,created_by) VALUES
            (:source_task,:subject,:grade,:topic,:subtopic,:teacher_a),
            (:candidate_task,:subject,:grade,:topic,:subtopic,:teacher_a);
          INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,
            answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES
            (:source_version,:source_task,1,'Source exact','Type forty-two','problem',
             'short_text',40,'approved',:teacher_a,:teacher_a,clock_timestamp()),
            (:candidate_version,:candidate_task,1,'Candidate exact','Type forty-two again','problem',
             'short_text',40,'approved',:teacher_a,:teacher_a,clock_timestamp());
          INSERT INTO accepted_answers(id,task_version_id,answer_value,value_kind,canonical_text,
            normalization_policy_code,normalization_policy_version)
            VALUES
              (:accepted,:source_version,'forty-two','text','forty-two','exact_text_v1',1),
              (:candidate_accepted,:candidate_version,'forty-two','text','forty-two',
               'exact_text_v1',1);
          INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) VALUES
            (:source_version,:skill,1,true),(:candidate_version,:skill,1,true);
          INSERT INTO assessments(id,title,status,created_by,published_at,published_by)
            VALUES (:assessment,'Vertical Assessment','published',:teacher_a,clock_timestamp(),:teacher_a);
          INSERT INTO assessment_variants(id,assessment_id,name,position)
            VALUES (:variant,:assessment,'A',1);
          INSERT INTO assessment_items(id,variant_id,task_version_id,position,points)
            VALUES (:assessment_item,:variant,:source_version,1,1);
        """), ids)

        factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        # Real Classroom administration creates 7A, memberships, and both students.
        async with factory() as session, session.begin():
            classroom = ClassroomAdministrationService(SQLAlchemyClassroomRepository(session))
            group = await classroom.create_class(
                name="Class 7A", grade_id=ids["grade"], external_ref="vertical-7a", actor=ids["admin"]
            )
            await classroom.assign_teacher(group.id, ids["teacher_a"], ids["admin"])
            await classroom.assign_teacher(group.id, ids["teacher_b"], ids["admin"])
            student_a = await classroom.create_student(
                group.id, "Student A", "vertical-a", ids["admin"]
            )
            student_b = await classroom.create_student(
                group.id, "Student B", "vertical-b", ids["admin"]
            )
        ids.update(group=group.id, student_a=student_a.id, student_b=student_b.id)
        await connection.execute(text("""
          INSERT INTO student_user_links(user_id,student_id) VALUES
            (:student_a_user,:student_a),(:student_b_user,:student_b)
        """), ids)

        # Real notes service proves object ownership is narrower than class membership.
        async with factory() as session, session.begin():
            notes = ClassroomNotesService(SQLAlchemyClassroomNotesRepository(session))
            class_note = await notes.create_class(group.id, ids["teacher_a"], False, "private class note")
            student_note = await notes.create_student(
                group.id, student_a.id, ids["teacher_a"], False, "private student note"
            )
            assert (await notes.get("class", group.id, None, class_note.id,
                                    ids["teacher_a"], False)).body == "private class note"
            assert (await notes.get("student", group.id, student_a.id, student_note.id,
                                    ids["teacher_a"], False)).body == "private student note"
            for kind, student_id, note_id in (
                ("class", None, class_note.id), ("student", student_a.id, student_note.id)
            ):
                with pytest.raises(ClassroomNotesError) as denied_note:
                    await notes.get(kind, group.id, student_id, note_id, ids["teacher_b"], False)
                assert denied_note.value.code == "note_not_found"

        # Real Assessment assignment snapshots the current 7A roster.
        now = datetime.now(timezone.utc)
        assessment_service = AssessmentService(SQLAlchemyAssessmentUnitOfWork(factory))
        assignment = await assessment_service.create_assignment(
            CreateAssignmentCommand(ids["assessment"], group.id, now - timedelta(minutes=1),
                                    now + timedelta(days=1), 1),
            ActorContext(ids["teacher_a"]),
        )
        assert set(assignment.participant_ids) == {student_a.id, student_b.id}
        participant_a = await connection.scalar(text("""
          SELECT id FROM assignment_participants WHERE assignment_id=:assignment AND student_id=:student
        """), {"assignment": assignment.id, "student": student_a.id})
        assert participant_a is not None

        # Real student Assessment lifecycle creates the source submission and answer.
        assessment_execution = StudentAssessmentService(factory)
        attempt, status = await assessment_execution.start(assignment.id, student_a.id, "vertical-start")
        assert status == 201
        answer, status = await assessment_execution.save_answer(
            attempt["id"], ids["assessment_item"], student_a.id, "forty-two", None
        )
        assert status == 201 and answer["normalized_answer"] == "forty-two"
        submitted, status = await assessment_execution.submit(
            attempt["id"], student_a.id, "vertical-submit"
        )
        assert status == 200
        source_row = (await connection.execute(text("""
          SELECT assignment_participant_id,remediation_plan_id FROM student_submissions WHERE id=:id
        """), {"id": submitted["id"]})).one()
        assert source_row == (participant_a, None)

        provider = ProviderCounter()
        intake = CheckingIntakeService(SQLAlchemyCheckingIntakeUnitOfWorkFactory(factory))
        source_run = await intake.create(CheckingIntakeRequest(
            submitted["id"], "vertical-assessment", "routing-v1", "deterministic-v1",
            "confidence-v1", "no-provider-v1",
        ))
        source_final = await _finish_deterministically(factory, source_run)
        assert source_final.status == "completed"
        source_result = (await connection.execute(text("""
          SELECT assessment_item_id,remediation_plan_item_id,result_status::text,score_suggested
          FROM check_results WHERE check_run_id=:run
        """), {"run": source_run.id})).mappings().one()
        assert source_result["assessment_item_id"] == ids["assessment_item"]
        assert source_result["remediation_plan_item_id"] is None
        assert source_result["result_status"] == "correct" and source_result["score_suggested"] == 1

        session = AsyncSession(bind=connection, expire_on_commit=False)
        c7 = ClassroomAssessmentResultsService(SQLAlchemyClassroomResultsReadRepository(session))
        teacher_result = await c7.student_result(
            assignment.id, student_a.id, ResultActor(ids["teacher_a"], False)
        )
        assert teacher_result["check_status"] == "checked"
        assert teacher_result["suggested_score_total"] == 1
        assert teacher_result["items"][0]["check_result"]["result_status"] == "correct"
        assert "diagnostics" in teacher_result
        with pytest.raises(ClassroomResultsError, match="Работа или участник не найдены") as denied:
            await c7.student_result(
                assignment.id, student_a.id, ResultActor(ids["teacher_b"], False)
            )
        assert denied.value.code == "participant_not_found"

        remediation = RemediationRepository(session)
        search = CandidateSearch(
            assignment_id=assignment.id, student_id=student_a.id,
            source_submission_id=submitted["id"], source_check_run_id=source_run.id,
            finding_ids=[], mode="suggested",
        )
        candidates = await remediation.candidates(ids["teacher_a"], False, search)
        returned = [row["task_version_id"] for row in candidates]
        assert ids["candidate_version"] in returned and ids["source_version"] not in returned

        created = await remediation.create(ids["teacher_a"], False, CreateRemediation(
            creation_key="vertical-remediation", student_id=student_a.id, class_group_id=group.id,
            source_assignment_id=assignment.id,
            source_assignment_participant_id=participant_a,
            source_submission_id=submitted["id"], source_check_run_id=source_run.id,
            title="Vertical remediation", finding_ids=[],
            items=[ItemInput(task_version_id=ids["candidate_version"], selection_source="suggested")],
        ))
        assigned = await remediation.assign(created["id"], ids["teacher_a"], False, False)
        assert (assigned["status"], assigned["student_id"], assigned["class_group_id"]) == (
            "assigned", student_a.id, group.id
        )
        plan_item = await connection.scalar(text(
            "SELECT id FROM remediation_plan_items WHERE remediation_plan_id=:id"
        ), {"id": created["id"]})
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await remediation.owned(created["id"], ids["teacher_b"], False)
        assert (await remediation.student_detail(created["id"], student_a.id))["id"] == created["id"]
        await session.close()

        # Real remediation execution, including start and submit replay.
        execution = RemediationExecutionService(factory)
        first, first_status = await execution.start(created["id"], student_a.id)
        replay, replay_status = await execution.start(created["id"], student_a.id)
        assert (first_status, replay_status, first["submission_id"]) == (
            201, 200, replay["submission_id"]
        )
        await execution.save_answer(created["id"], plan_item, student_a.id, "forty-two")

        for operation in (
            lambda: execution.get_execution(created["id"], student_b.id),
            lambda: execution.start(created["id"], student_b.id),
            lambda: execution.save_answer(created["id"], plan_item, student_b.id, "stolen"),
            lambda: execution.delete_answer(created["id"], plan_item, student_b.id),
            lambda: execution.submit(created["id"], student_b.id),
        ):
            with pytest.raises(RemediationError, match="remediation_not_found"):
                await operation()

        remediation_submit = await execution.submit(created["id"], student_a.id)
        remediation_replay = await execution.submit(created["id"], student_a.id)
        assert remediation_replay["submission_id"] == remediation_submit["submission_id"]
        remediation_run = (await connection.execute(text("""
          SELECT * FROM check_runs WHERE submission_id=:submission
        """), {"submission": remediation_submit["submission_id"]})).mappings().one()
        remediation_final = await _finish_deterministically(factory, remediation_run)
        assert remediation_final.status == "completed"

        persisted = (await connection.execute(text("""
          SELECT attempt_no,assignment_participant_id FROM student_submissions
          WHERE remediation_plan_id=:plan
        """), {"plan": created["id"]})).one()
        persisted_answer = (await connection.execute(text("""
          SELECT assessment_item_id,remediation_plan_item_id FROM student_answers
          WHERE submission_id=:submission
        """), {"submission": remediation_submit["submission_id"]})).one()
        remediation_result = (await connection.execute(text("""
          SELECT assessment_item_id,remediation_plan_item_id,result_status::text
          FROM check_results WHERE check_run_id=:run
        """), {"run": remediation_run["id"]})).one()
        assert persisted == (1, None)
        assert persisted_answer == (None, plan_item)
        assert remediation_result == (None, plan_item, "correct")

        session = AsyncSession(bind=connection, expire_on_commit=False)
        results = RemediationResultsService(session)
        teacher = await results.teacher_result(created["id"], ids["teacher_a"], False)
        student = await results.student_execution(created["id"], student_a.id)
        assert teacher["execution_status"] == "checked"
        assert teacher["items"][0]["student_raw_answer"] == "forty-two"
        assert teacher["suggested_score_total"] == 1
        assert "findings" in teacher["items"][0]
        assert student["execution_status"] == "checked"
        assert student["items"][0]["student_raw_answer"] == "forty-two"
        assert student["items"][0]["student_feedback"] is not None
        serialized_student = repr(student)
        for private_key in ("teacher_summary", "findings", "rubric", "model_limitations", "provider"):
            assert private_key not in serialized_student
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await results.student_execution(created["id"], student_b.id)
        await session.close()

        # Scoped end-state invariants and one explicit provider tripwire.
        assert await connection.scalar(text(
            "SELECT count(*) FROM assignments WHERE assessment_id=:id"
        ), {"id": ids["assessment"]}) == 1
        assert await connection.scalar(text("""
          SELECT count(*) FROM assignment_participants WHERE assignment_id=:assignment AND student_id=:student
        """), {"assignment": assignment.id, "student": student_a.id}) == 1
        assert await connection.scalar(text("""
          SELECT count(*) FROM student_submissions WHERE assignment_participant_id=:participant
        """), {"participant": participant_a}) == 1
        assert await connection.scalar(text(
            "SELECT count(*) FROM check_runs WHERE submission_id=:id"
        ), {"id": submitted["id"]}) == 1
        assert await connection.scalar(text("""
          SELECT count(*) FROM remediation_plans WHERE owner_user_id=:owner
            AND student_id=:student AND source_submission_id=:source
        """), {"owner": ids["teacher_a"], "student": student_a.id,
                 "source": submitted["id"]}) == 1
        assert await connection.scalar(text(
            "SELECT count(*) FROM student_submissions WHERE remediation_plan_id=:plan"
        ), {"plan": created["id"]}) == 1
        assert await connection.scalar(text(
            "SELECT count(*) FROM check_runs WHERE submission_id=:id"
        ), {"id": remediation_submit["submission_id"]}) == 1
        assert await connection.scalar(text("""
          SELECT count(*) FROM model_runs WHERE check_run_id IN (:source_run,:remediation_run)
        """), {"source_run": source_run.id, "remediation_run": remediation_run["id"]}) == 0
        assert provider.calls == 0
