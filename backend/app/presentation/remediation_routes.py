from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Query,Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.capabilities import REMEDIATION_MANAGE,STUDENT_REMEDIATIONS_READ,STUDENT_REMEDIATIONS_EXECUTE
from app.application.remediation_execution import RemediationExecutionService
from app.application.principal import Principal
from app.db.session import get_session
from app.infrastructure.remediation_repository import RemediationRepository
from app.presentation.auth_dependencies import require_capability,require_student_identity,require_trusted_origin
from app.presentation.remediation_schemas import *
from app.presentation.assessment_schemas import EmptyRequest
from app.db.session import async_session_factory
router=APIRouter(prefix="/api",dependencies=[Depends(require_trusted_origin)],tags=["remediation"])
def repo(s:AsyncSession=Depends(get_session)):return RemediationRepository(s)
def admin(p):return "admin" in p.roles
@router.post("/remediations/candidates/search",response_model=list[Candidate])
async def candidates(body:CandidateSearch,p:Principal=Depends(require_capability(REMEDIATION_MANAGE)),r:RemediationRepository=Depends(repo)):return await r.candidates(p.user_id,admin(p),body)
@router.post("/remediations",response_model=RemediationPlanResponse,status_code=201)
async def create(body:CreateRemediation,response:Response,p:Principal=Depends(require_capability(REMEDIATION_MANAGE)),r:RemediationRepository=Depends(repo),s:AsyncSession=Depends(get_session)):
 v=await r.create(p.user_id,admin(p),body);await s.commit();response.headers['Location']=f"/api/remediations/{v['id']}";return v
@router.get("/remediations/{id}",response_model=RemediationPlanResponse)
async def get(id:UUID,p:Principal=Depends(require_capability(REMEDIATION_MANAGE)),r:RemediationRepository=Depends(repo)):return await r.view(await r.owned(id,p.user_id,admin(p)))
@router.patch("/remediations/{id}",response_model=RemediationPlanResponse)
async def update(id:UUID,body:UpdateRemediation,p:Principal=Depends(require_capability(REMEDIATION_MANAGE)),r:RemediationRepository=Depends(repo),s:AsyncSession=Depends(get_session)):v=await r.update(id,p.user_id,admin(p),body);await s.commit();return v
@router.post("/remediations/{id}/assign",response_model=RemediationPlanResponse)
async def assign(id:UUID,body:AssignRemediation,p:Principal=Depends(require_capability(REMEDIATION_MANAGE)),r:RemediationRepository=Depends(repo),s:AsyncSession=Depends(get_session)):v=await r.assign(id,p.user_id,admin(p),body.acknowledge_review_required);await s.commit();return v
@router.post("/remediations/{id}/cancel",response_model=RemediationPlanResponse)
async def cancel(id:UUID,p:Principal=Depends(require_capability(REMEDIATION_MANAGE)),r:RemediationRepository=Depends(repo),s:AsyncSession=Depends(get_session)):v=await r.cancel(id,p.user_id,admin(p));await s.commit();return v
@router.get("/classrooms/{class_id}/students/{student_id}/remediations",response_model=RemediationPage)
async def history(class_id:UUID,student_id:UUID,status:Literal['draft','assigned','cancelled','all']='all',offset:int=0,limit:int=50,p:Principal=Depends(require_capability(REMEDIATION_MANAGE)),r:RemediationRepository=Depends(repo)):return await r.history(class_id,student_id,p.user_id,admin(p),status,offset,limit)
@router.get("/student/remediations",response_model=StudentPage)
async def student_list(student_id:UUID=Depends(require_student_identity),_:Principal=Depends(require_capability(STUDENT_REMEDIATIONS_READ)),offset:Annotated[int,Query(ge=0)]=0,limit:Annotated[int,Query(ge=1,le=100)]=20,r:RemediationRepository=Depends(repo)):return await r.student_list(student_id,offset,limit)
@router.get("/student/remediations/{id}",response_model=StudentDetail)
async def student_detail(id:UUID,student_id:UUID=Depends(require_student_identity),_:Principal=Depends(require_capability(STUDENT_REMEDIATIONS_READ)),r:RemediationRepository=Depends(repo)):return await r.student_detail(id,student_id)

def execution_service(): return RemediationExecutionService(async_session_factory)

@router.get("/student/remediations/{id}/execution",response_model=RemediationExecutionResponse)
async def execution(id:UUID,student_id:UUID=Depends(require_student_identity),_:Principal=Depends(require_capability(STUDENT_REMEDIATIONS_EXECUTE))):
 return await execution_service().get_execution(id,student_id)

@router.post("/student/remediations/{id}/start",response_model=RemediationExecutionResponse)
async def start_execution(id:UUID,payload:EmptyRequest,response:Response,student_id:UUID=Depends(require_student_identity),_:Principal=Depends(require_capability(STUDENT_REMEDIATIONS_EXECUTE))):
 result,status=await execution_service().start(id,student_id);response.status_code=status
 if status==201:response.headers["Location"]=f"/api/student/remediations/{id}/execution"
 return result

@router.put("/student/remediations/{id}/answers/{item_id}",response_model=RemediationAnswerResponse|None)
async def save_execution_answer(id:UUID,item_id:UUID,payload:RemediationAnswerPut,response:Response,student_id:UUID=Depends(require_student_identity),_:Principal=Depends(require_capability(STUDENT_REMEDIATIONS_EXECUTE))):
 result,status=await execution_service().save_answer(id,item_id,student_id,payload.raw_answer);response.status_code=status
 if status==204:return Response(status_code=204)
 return result

@router.delete("/student/remediations/{id}/answers/{item_id}",status_code=204)
async def delete_execution_answer(id:UUID,item_id:UUID,student_id:UUID=Depends(require_student_identity),_:Principal=Depends(require_capability(STUDENT_REMEDIATIONS_EXECUTE))):
 await execution_service().delete_answer(id,item_id,student_id);return Response(status_code=204)

@router.post("/student/remediations/{id}/submit",response_model=RemediationExecutionResponse)
async def submit_execution(id:UUID,payload:EmptyRequest,student_id:UUID=Depends(require_student_identity),_:Principal=Depends(require_capability(STUDENT_REMEDIATIONS_EXECUTE))):
 return await execution_service().submit(id,student_id)
