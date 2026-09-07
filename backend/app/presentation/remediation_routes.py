from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Query,Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.capabilities import REMEDIATION_MANAGE,STUDENT_REMEDIATIONS_READ
from app.application.principal import Principal
from app.db.session import get_session
from app.infrastructure.remediation_repository import RemediationRepository
from app.presentation.auth_dependencies import require_capability,require_student_identity,require_trusted_origin
from app.presentation.remediation_schemas import *
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
