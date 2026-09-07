from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Response,status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.capabilities import CLASSROOM_ADMIN
from app.application.classroom_administration import ClassroomAdministrationService
from app.application.principal import Principal
from app.db.session import get_session
from app.infrastructure.classroom_repository import SQLAlchemyClassroomRepository
from app.presentation.auth_dependencies import require_capability,require_trusted_origin
from app.presentation.admin_classroom_schemas import *
router=APIRouter(prefix='/api/admin/class-groups',tags=['admin-classrooms'])
admin=require_capability(CLASSROOM_ADMIN); unsafe=[Depends(require_trusted_origin)]
def service(session:AsyncSession=Depends(get_session)):return ClassroomAdministrationService(SQLAlchemyClassroomRepository(session))
def cr(x):return ClassResponse(**x.__dict__,is_configuration_complete=x.is_configuration_complete)
def sr(x):return StudentResponse(**x.__dict__)
@router.get('',response_model=list[ClassResponse])
async def classes(status:Literal['active','archived','all']='active',_:Principal=Depends(admin),svc=Depends(service)):return [cr(x) for x in await svc.list_classes(status)]
@router.post('',response_model=ClassResponse,status_code=201,dependencies=unsafe)
async def create(body:CreateClassRequest,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 x=await svc.create_class(**body.model_dump(),actor=p.user_id);await session.commit();return cr(x)
@router.get('/{class_group_id}',response_model=ClassResponse)
async def get(class_group_id:UUID,_:Principal=Depends(admin),svc=Depends(service)):return cr(await svc.get_class(class_group_id))
@router.patch('/{class_group_id}',response_model=ClassResponse,dependencies=unsafe)
async def update(class_group_id:UUID,body:UpdateClassRequest,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 x=await svc.update_class(class_group_id,body.model_dump(exclude_unset=True),p.user_id);await session.commit();return cr(x)
@router.post('/{class_group_id}/archive',response_model=ClassResponse,dependencies=unsafe)
async def archive(class_group_id:UUID,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 x=await svc.archive_class(class_group_id,p.user_id);await session.commit();return cr(x)
@router.get('/{class_group_id}/teachers',response_model=list[TeacherResponse])
async def teachers(class_group_id:UUID,_:Principal=Depends(admin),svc=Depends(service)):return await svc.list_teachers(class_group_id)
@router.put('/{class_group_id}/teachers/{teacher_user_id}',status_code=204,dependencies=unsafe)
async def assign(class_group_id:UUID,teacher_user_id:UUID,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 await svc.assign_teacher(class_group_id,teacher_user_id,p.user_id);await session.commit();return Response(status_code=204)
@router.delete('/{class_group_id}/teachers/{teacher_user_id}',status_code=204,dependencies=unsafe)
async def unassign(class_group_id:UUID,teacher_user_id:UUID,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 await svc.unassign_teacher(class_group_id,teacher_user_id,p.user_id);await session.commit();return Response(status_code=204)
@router.get('/{class_group_id}/students',response_model=list[StudentResponse])
async def students(class_group_id:UUID,status:Literal['active','archived','all']='active',_=Depends(admin),svc=Depends(service)):return [sr(x) for x in await svc.list_students(class_group_id,status)]
@router.post('/{class_group_id}/students',response_model=StudentResponse,status_code=201,dependencies=unsafe)
async def create_student(class_group_id:UUID,body:CreateStudentRequest,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 x=await svc.create_student(class_group_id,**body.model_dump(),actor=p.user_id);await session.commit();return sr(x)
@router.post('/{class_group_id}/students/{student_id}/move',response_model=StudentResponse,dependencies=unsafe)
async def move(class_group_id:UUID,student_id:UUID,body:MoveStudentRequest,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 if class_group_id!=body.expected_current_class_group_id: from fastapi import HTTPException;raise HTTPException(409,'student_class_conflict')
 x=await svc.move_student(student_id,body.target_class_group_id,body.expected_current_class_group_id,p.user_id);await session.commit();return sr(x)
@router.post('/{class_group_id}/students/{student_id}/archive',response_model=StudentResponse,dependencies=unsafe)
async def archive_student(class_group_id:UUID,student_id:UUID,p:Principal=Depends(admin),svc=Depends(service),session:AsyncSession=Depends(get_session)):
 x=await svc.archive_student(class_group_id,student_id,p.user_id);await session.commit();return sr(x)
