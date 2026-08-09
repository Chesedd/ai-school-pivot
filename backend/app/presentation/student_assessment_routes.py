"""Student-only Assessment Core HTTP boundary."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Response

from app.application.student_assessments import PilotStudentContext, validate_idempotency_key
from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.infrastructure.student_assessment_repository import StudentAssessmentService
from app.presentation.assessment_schemas import EmptyRequest
from app.presentation.student_assessment_schemas import (AnswerPutRequest, StudentAnswerResponse,
    StudentAssignmentDetail, StudentAssignmentPage, SubmissionResponse)

router=APIRouter(prefix="/api/assessment-core/student")

def student_context(settings: Settings=Depends(get_settings)) -> PilotStudentContext:
    return PilotStudentContext(settings.assessment_dev_student_id)

def service(): return StudentAssessmentService(async_session_factory)

@router.get("/assignments",response_model=StudentAssignmentPage)
async def assignments(context:Annotated[PilotStudentContext,Depends(student_context)],offset:Annotated[int,Query(ge=0)]=0,
                      limit:Annotated[int,Query(ge=1,le=100)]=20):
    return await service().list_assignments(context.student_id,offset,limit)

@router.get("/assignments/{assignment_id}",response_model=StudentAssignmentDetail)
async def assignment(assignment_id:UUID,context:Annotated[PilotStudentContext,Depends(student_context)]):
    return await service().assignment_detail(assignment_id,context.student_id)

@router.post("/assignments/{assignment_id}/attempts/start",response_model=SubmissionResponse)
async def start(assignment_id:UUID,payload:EmptyRequest,response:Response,
                context:Annotated[PilotStudentContext,Depends(student_context)],
                idempotency_key:Annotated[str|None,Header(alias="Idempotency-Key")]=None):
    result,status=await service().start(assignment_id,context.student_id,validate_idempotency_key(idempotency_key))
    response.status_code=status
    if status==201: response.headers["Location"]=f"/api/assessment-core/student/attempts/{result['id']}"
    return result

@router.get("/attempts/{submission_id}",response_model=SubmissionResponse)
async def attempt(submission_id:UUID,context:Annotated[PilotStudentContext,Depends(student_context)]):
    return await service().get_attempt(submission_id,context.student_id)

@router.put("/attempts/{submission_id}/answers/{item_id}",response_model=StudentAnswerResponse|None)
async def put_answer(submission_id:UUID,item_id:UUID,payload:AnswerPutRequest,response:Response,
                     context:Annotated[PilotStudentContext,Depends(student_context)]):
    result,status=await service().save_answer(submission_id,item_id,context.student_id,payload.raw_answer,payload.expected_updated_at)
    response.status_code=status
    if status==201: response.headers["Location"]=f"/api/assessment-core/student/attempts/{submission_id}/answers/{item_id}"
    if status==204: return Response(status_code=204)
    return result

@router.delete("/attempts/{submission_id}/answers/{item_id}",status_code=204)
async def delete_answer(submission_id:UUID,item_id:UUID,context:Annotated[PilotStudentContext,Depends(student_context)]):
    await service().delete_answer(submission_id,item_id,context.student_id)
    return Response(status_code=204)

@router.post("/attempts/{submission_id}/submit",response_model=SubmissionResponse)
async def submit(submission_id:UUID,payload:EmptyRequest,response:Response,
                 context:Annotated[PilotStudentContext,Depends(student_context)],
                 idempotency_key:Annotated[str|None,Header(alias="Idempotency-Key")]=None):
    result,status=await service().submit(submission_id,context.student_id,validate_idempotency_key(idempotency_key))
    response.status_code=status
    return result
