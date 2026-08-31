"""Small, image-only attachment transport shared by existing bounded contexts."""
from pathlib import Path
from uuid import UUID, uuid4
import re
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.application.auth_errors import AuthenticationError
from app.application.principal import PrincipalResolver
from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.infrastructure.models import Attachment, TaskVersion, TaskVersionAttachment
from app.infrastructure.assessment_models import (AssignmentParticipant,
    StudentAnswer, StudentAnswerAttachment, StudentSubmission)
from app.presentation.auth_dependencies import get_principal_resolver, require_student_identity

router=APIRouter(prefix="/api")
MIMES={"image/png":b"\x89PNG\r\n\x1a\n","image/jpeg":b"\xff\xd8\xff","image/webp":b"RIFF"}
ROLES={"statement","description","additional_material","solution_explanation","methodological_material"}

def serialize(a:Attachment,role:str|None=None):
    return {"id":a.id,"filename":a.filename,"mime_type":a.mime_type,"size_bytes":a.size_bytes,"created_at":a.created_at,"role":role,"url":f"/api/attachments/{a.id}/content"}

async def payload(request:Request,filename:str,mime:str,settings:Settings):
    data=await request.body()
    if not data or len(data)>settings.attachment_max_bytes: raise HTTPException(413,"Image is empty or exceeds configured size limit")
    if mime not in MIMES: raise HTTPException(415,"Only image/png, image/jpeg and image/webp are allowed")
    valid=data.startswith(MIMES[mime]) and (mime!="image/webp" or len(data)>=12 and data[8:12]==b"WEBP")
    if not valid: raise HTTPException(415,"File signature does not match image MIME type")
    clean=re.sub(r"[^A-Za-z0-9._ -]","_",Path(filename).name).strip()[:255] or "image"
    ref=str(uuid4()); root=Path(settings.attachment_storage_path);root.mkdir(parents=True,exist_ok=True)
    (root/ref).write_bytes(data)
    return Attachment(filename=clean,mime_type=mime,storage_reference=ref,size_bytes=len(data))

@router.post("/content-bank/task-versions/{version_id}/attachments",status_code=201)
async def upload_task(version_id:UUID,request:Request,role:str,x_filename:str=Header(...),content_type:str=Header(...),settings:Settings=Depends(get_settings)):
    if role not in ROLES: raise HTTPException(422,"Unsupported attachment role")
    async with async_session_factory() as s:
        version=await s.get(TaskVersion,version_id)
        if not version: raise HTTPException(404,"Task version not found")
        if version.status!="draft": raise HTTPException(409,"Attachments can only be changed on draft versions")
        a=await payload(request,x_filename,content_type,settings);s.add(a);await s.flush();s.add(TaskVersionAttachment(task_version_id=version_id,attachment_id=a.id,role=role));await s.commit();await s.refresh(a);return serialize(a,role)

@router.get("/content-bank/task-versions/{version_id}/attachments")
async def task_files(version_id:UUID):
    async with async_session_factory() as s:
        rows=(await s.execute(select(Attachment,TaskVersionAttachment.role).join(TaskVersionAttachment).where(TaskVersionAttachment.task_version_id==version_id).order_by(Attachment.created_at,Attachment.id))).all()
        return {"items":[serialize(a,r) for a,r in rows]}

async def owned_answer(s,submission_id,item_id,student_id):
    return (await s.execute(select(StudentAnswer).join(StudentSubmission,StudentAnswer.submission_id==StudentSubmission.id).join(AssignmentParticipant,StudentSubmission.assignment_participant_id==AssignmentParticipant.id).where(StudentSubmission.id==submission_id,StudentAnswer.assessment_item_id==item_id,AssignmentParticipant.student_id==student_id))).scalar_one_or_none()

@router.post("/assessment-core/student/attempts/{submission_id}/answers/{item_id}/attachments",status_code=201)
async def upload_answer(submission_id:UUID,item_id:UUID,request:Request,x_filename:str=Header(...),content_type:str=Header(...),settings:Settings=Depends(get_settings),student_id:UUID=Depends(require_student_identity)):
    async with async_session_factory() as s:
        answer=await owned_answer(s,submission_id,item_id,student_id)
        submission=await s.get(StudentSubmission,submission_id)
        if not answer: raise HTTPException(404,"Saved answer not found")
        if submission.status!="draft": raise HTTPException(409,"Submitted answer is read-only")
        a=await payload(request,x_filename,content_type,settings);s.add(a);await s.flush();s.add(StudentAnswerAttachment(student_answer_id=answer.id,attachment_id=a.id));await s.commit();await s.refresh(a);return serialize(a)

@router.get("/assessment-core/student/attempts/{submission_id}/answers/{item_id}/attachments")
async def answer_files(submission_id:UUID,item_id:UUID,student_id:UUID=Depends(require_student_identity)):
    async with async_session_factory() as s:
        answer=await owned_answer(s,submission_id,item_id,student_id)
        if not answer:return {"items":[]}
        rows=(await s.scalars(select(Attachment).join(StudentAnswerAttachment).where(StudentAnswerAttachment.student_answer_id==answer.id).order_by(Attachment.created_at,Attachment.id))).all()
        return {"items":[serialize(a) for a in rows]}

@router.get("/attachments/{attachment_id}/content")
async def content(attachment_id:UUID,request:Request,settings:Settings=Depends(get_settings),resolver:PrincipalResolver=Depends(get_principal_resolver)):
    async with async_session_factory() as s:
        a=await s.get(Attachment,attachment_id)
        student_owner = await s.scalar(
            select(AssignmentParticipant.student_id)
            .join(StudentSubmission, StudentSubmission.assignment_participant_id == AssignmentParticipant.id)
            .join(StudentAnswer, StudentAnswer.submission_id == StudentSubmission.id)
            .join(StudentAnswerAttachment, StudentAnswerAttachment.student_answer_id == StudentAnswer.id)
            .where(StudentAnswerAttachment.attachment_id == attachment_id)
        )
    if not a:
        raise HTTPException(404, "Attachment not found")
    if student_owner is not None:
        secret = request.cookies.get(settings.auth_session_cookie_name)
        if not secret:
            raise HTTPException(401, "authentication_required")
        try:
            principal = await resolver.resolve(secret)
        except AuthenticationError as exc:
            raise HTTPException(401, "authentication_required") from exc
        if principal.student_id is None:
            raise HTTPException(403, "student_identity_required")
        if principal.student_id != student_owner:
            raise HTTPException(404, "Attachment not found")
    path=Path(settings.attachment_storage_path)/a.storage_reference
    if not path.is_file():raise HTTPException(404,"Attachment content not found")
    return FileResponse(path,media_type=a.mime_type,filename=a.filename,headers={"Content-Disposition":f'inline; filename="{a.filename}"',"X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'; sandbox"})
