"""Database-free presentation security contracts for remediation routes."""
import os
from uuid import uuid4
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
from fastapi.testclient import TestClient
from app.application.principal import Principal
from app.main import app
from app.presentation.auth_dependencies import require_principal
from app.presentation.remediation_schemas import StudentResultItem,TeacherRemediationResultItem

def principal(*capabilities):
    return Principal(uuid4(),"account","User",frozenset({"student"}),frozenset(capabilities),uuid4())

def test_student_result_schema_excludes_teacher_checking_fields():
    student=set(StudentResultItem.model_fields)
    assert not {"teacher_summary","findings","skill_id","typical_error_id","snapshot_title","review_reason","model_limitations","rubric","expected_solution","accepted_answers","provider"}&student
    assert {"student_raw_answer","result_status","score_suggested","student_feedback"}<=student
    assert {"check_result","findings"}<=set(TeacherRemediationResultItem.model_fields)

def test_teacher_result_requires_remediation_manage_before_database_access():
    app.dependency_overrides[require_principal]=lambda:principal("classroom.use")
    try: response=TestClient(app).get(f"/api/remediations/{uuid4()}/result")
    finally: app.dependency_overrides.pop(require_principal,None)
    assert response.status_code==403

def test_student_execution_and_mutations_require_execute_capability():
    app.dependency_overrides[require_principal]=lambda:principal("student.remediations.read")
    client=TestClient(app); rid,iid=uuid4(),uuid4()
    try:
        responses=[client.get(f"/api/student/remediations/{rid}/execution"),client.post(f"/api/student/remediations/{rid}/start",json={}),client.put(f"/api/student/remediations/{rid}/answers/{iid}",json={"raw_answer":"x"}),client.delete(f"/api/student/remediations/{rid}/answers/{iid}"),client.post(f"/api/student/remediations/{rid}/submit",json={})]
    finally: app.dependency_overrides.pop(require_principal,None)
    assert [r.status_code for r in responses]==[403]*5
