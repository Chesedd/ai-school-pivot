"""Batch SQLAlchemy read adapter for C7; contains no mutation methods."""
from collections import defaultdict
from sqlalchemy import func, select
from app.application.classroom_results import ResultActor, check_status, score_totals
from app.infrastructure.assessment_models import (Assessment, AssessmentItem, AssessmentVariant, Assignment,
    AssignmentParticipant, ClassGroup, Student, StudentAnswer, StudentSubmission)
from app.infrastructure.checking_models import CheckFinding, CheckResult, CheckRun
from app.infrastructure.classroom_models import ClassGroupTeacher
from app.infrastructure.models import TaskVersion

class SQLAlchemyClassroomResultsReadRepository:
 def __init__(self, session): self.s=session
 async def _assignment(self, aid, actor):
  q=select(Assignment,Assessment,ClassGroup).join(Assessment,Assessment.id==Assignment.assessment_id).join(ClassGroup,ClassGroup.id==Assignment.class_group_id).where(Assignment.id==aid)
  if not actor.unrestricted:q=q.join(ClassGroupTeacher,(ClassGroupTeacher.class_group_id==Assignment.class_group_id)&(ClassGroupTeacher.teacher_user_id==actor.actor_id)).where(Assessment.created_by==actor.actor_id)
  return (await self.s.execute(q)).first()
 async def _data(self, participant_ids):
  subs=(await self.s.scalars(select(StudentSubmission).where(StudentSubmission.assignment_participant_id.in_(participant_ids)).order_by(StudentSubmission.attempt_no.desc()))).all() if participant_ids else []
  byp=defaultdict(list)
  for x in subs:byp[x.assignment_participant_id].append(x)
  submitted=[x for x in subs if x.status=="submitted"]
  runs=(await self.s.scalars(select(CheckRun).where(CheckRun.submission_id.in_([x.id for x in submitted])).order_by(CheckRun.attempt_no.desc()))).all() if submitted else []
  bys=defaultdict(list)
  for x in runs:bys[x.submission_id].append(x)
  latest=[v[0] for v in bys.values()]
  results=(await self.s.scalars(select(CheckResult).where(CheckResult.check_run_id.in_([x.id for x in latest])))).all() if latest else []
  byr=defaultdict(list)
  for x in results:byr[x.check_run_id].append(x)
  findings=(await self.s.scalars(select(CheckFinding).where(CheckFinding.check_result_id.in_([x.id for x in results])))).all() if results else []
  byresult=defaultdict(list)
  for x in findings:byresult[x.check_result_id].append(x)
  return byp,bys,byr,byresult
 def _summary(self,p,student,variant,byp,bys,byr,byfinding):
  ss=byp[p.id]; current=ss[0] if ss else None; submitted=[x for x in ss if x.status=="submitted"]; latest=submitted[0] if submitted else None
  run=bys[latest.id][0] if latest and bys[latest.id] else None; results=byr[run.id] if run and run.status in {"completed","completed_with_review_required"} else []
  score,maximum,percent=score_totals(results); findings=[f for r in results for f in byfinding[r.id]]
  return {"assignment_participant_id":p.id,"student_id":student.id,"student_display_name":student.display_name,"is_current_class_member":student.class_group_id==p._assignment_class_id,"student_archived":student.archived_at is not None,"assigned_variant_id":p.assigned_variant_id,"assigned_variant_name":variant.name if variant else None,"activity_status":"not_started" if current is None else "in_progress" if current.status=="draft" else "submitted","current_attempt_no":current.attempt_no if current else None,"current_attempt_status":current.status if current else None,"submitted_attempt_count":len(submitted),"latest_submitted_attempt_no":latest.attempt_no if latest else None,"latest_submitted_at":latest.submitted_at if latest else None,"latest_submission_id":latest.id if latest else None,"latest_check_run_id":run.id if run else None,"latest_check_run_status":run.status if run else None,"check_status":check_status(latest is not None,run.status if run else None),"suggested_score_total":score,"max_score_total":maximum,"suggested_percent":percent,"review_required_count":sum(1 for r in results if r.needs_human_review),"skill_finding_count":sum(1 for f in findings if f.finding_type=="skill"),"typical_error_finding_count":sum(1 for f in findings if f.finding_type=="typical_error")}
 async def assignment_results(self,aid,actor,offset,limit):
  base=await self._assignment(aid,actor)
  if not base:return None
  assignment,assessment,group=base
  total=await self.s.scalar(select(func.count()).select_from(AssignmentParticipant).where(AssignmentParticipant.assignment_id==aid))
  rows=(await self.s.execute(select(AssignmentParticipant,Student,AssessmentVariant).join(Student,Student.id==AssignmentParticipant.student_id).outerjoin(AssessmentVariant,AssessmentVariant.id==AssignmentParticipant.assigned_variant_id).where(AssignmentParticipant.assignment_id==aid).order_by(Student.display_name,Student.id).offset(offset).limit(limit))).all()
  participants=[r[0] for r in rows]; byp,bys,byr,byf=await self._data([p.id for p in participants]); out=[]
  for p,s,v in rows:p._assignment_class_id=assignment.class_group_id;out.append(self._summary(p,s,v,byp,bys,byr,byf))
  # Counters use the complete historical participant set, independently of pagination.
  allrows=(await self.s.execute(select(AssignmentParticipant,Student,AssessmentVariant).join(Student).outerjoin(AssessmentVariant,AssessmentVariant.id==AssignmentParticipant.assigned_variant_id).where(AssignmentParticipant.assignment_id==aid))).all(); ap=[r[0] for r in allrows]; abp,abs_,abr,abf=await self._data([p.id for p in ap]); allsum=[]
  for p,s,v in allrows:p._assignment_class_id=assignment.class_group_id;allsum.append(self._summary(p,s,v,abp,abs_,abr,abf))
  counts={k:sum(1 for x in allsum if x["activity_status"]==k) for k in ("not_started","in_progress","submitted")};counts.update({k:sum(1 for x in allsum if x["check_status"]==k) for k in ("checking","checked","review_required","check_failed")})
  return {"assignment_id":assignment.id,"assessment_id":assessment.id,"assessment_title":assessment.title,"class_group_id":group.id,"class_group_name":group.name,"assignment_status":assignment.status,"start_at":assignment.start_at,"due_at":assignment.due_at,"participant_count":total,"not_started_count":counts["not_started"],"in_progress_count":counts["in_progress"],"submitted_count":counts["submitted"],"checking_count":counts["checking"],"checked_count":counts["checked"],"review_required_count":counts["review_required"],"check_failed_count":counts["check_failed"],"items":out,"offset":offset,"limit":limit}
 async def student_result(self,aid,sid,actor,attempt_no):
  base=await self._assignment(aid,actor)
  if not base:return None
  assignment,assessment,group=base; row=(await self.s.execute(select(AssignmentParticipant,Student,AssessmentVariant).join(Student).outerjoin(AssessmentVariant,AssessmentVariant.id==AssignmentParticipant.assigned_variant_id).where(AssignmentParticipant.assignment_id==aid,AssignmentParticipant.student_id==sid))).first()
  if not row:return None
  p,student,variant=row; byp,bys,byr,byf=await self._data([p.id]); submissions=byp[p.id]; submitted=[x for x in submissions if x.status=="submitted"]
  selected=next((x for x in submitted if x.attempt_no==attempt_no),None) if attempt_no is not None else (submitted[0] if submitted else None)
  if attempt_no is not None and selected is None:return None
  run=bys[selected.id][0] if selected and bys[selected.id] else None; results=byr[run.id] if run and run.status in {"completed","completed_with_review_required"} else []; resultmap={x.assessment_item_id:x for x in results}
  items=[]
  if variant:
   itemrows=(await self.s.execute(select(AssessmentItem,TaskVersion,StudentAnswer).join(TaskVersion,TaskVersion.id==AssessmentItem.task_version_id).outerjoin(StudentAnswer,(StudentAnswer.assessment_item_id==AssessmentItem.id)&(StudentAnswer.submission_id==selected.id if selected else False)).where(AssessmentItem.variant_id==variant.id).order_by(AssessmentItem.position))).all()
   for item,tv,answer in itemrows:
    cr=resultmap.get(item.id); fs=byf[cr.id] if cr else []
    items.append({"assessment_item_id":item.id,"position":item.position,"task_version_id":item.task_version_id,"task_title":tv.title,"task_statement":tv.statement,"student_answer":answer.raw_answer if answer else None,"attachment_count":0,"check_result":None if not cr else {"check_result_id":cr.id,"result_status":cr.result_status,"checker_type":cr.checker_type,"score_suggested":cr.score_suggested,"max_score":cr.max_score,"confidence":cr.confidence,"summary":cr.summary,"teacher_summary":cr.teacher_summary,"needs_human_review":cr.needs_human_review,"review_reason":cr.review_reason,"model_limitations":cr.model_limitations,"findings":[{"finding_id":f.id,"check_result_id":cr.id,"finding_type":f.finding_type,"rubric_item_id":f.rubric_item_id,"typical_error_id":f.typical_error_id,"skill_id":f.skill_id,"snapshot_code":f.snapshot_code,"snapshot_title":f.snapshot_title,"snapshot_criterion":f.snapshot_criterion,"severity":f.severity,"confidence":f.confidence} for f in fs]}})
  current=submissions[0] if submissions else None; score,maximum,percent=score_totals(results); groups={"skills":[],"typical_errors":[]}
  for kind,key,idfield in (("skill","skills","skill_id"),("typical_error","typical_errors","typical_error_id")):
   bucket={}
   for cr in results:
    for f in byf[cr.id]:
     if f.finding_type!=kind:continue
     ident=getattr(f,idfield); g=bucket.setdefault((ident,f.snapshot_code,f.snapshot_title),{"id":ident,"snapshot_title":f.snapshot_title,"snapshot_code":f.snapshot_code,"finding_count":0,"max_severity":f.severity,"max_confidence":f.confidence,"requires_human_review":False});g["finding_count"]+=1;g["max_confidence"]=max(g["max_confidence"],f.confidence);g["requires_human_review"]|=cr.needs_human_review
   groups[key]=list(bucket.values())
  return {"assignment_id":aid,"assignment_participant_id":p.id,"student_id":sid,"student_display_name":student.display_name,"student_archived":student.archived_at is not None,"is_current_class_member":student.class_group_id==group.id,"assessment_id":assessment.id,"assessment_title":assessment.title,"class_group_id":group.id,"class_group_name":group.name,"current_attempt_no":current.attempt_no if current else None,"current_attempt_status":current.status if current else None,"attempts":[{"submission_id":x.id,"attempt_no":x.attempt_no,"status":x.status,"started_at":x.started_at,"submitted_at":x.submitted_at} for x in submissions],"selected_submission_id":selected.id if selected else None,"selected_attempt_no":selected.attempt_no if selected else None,"latest_check_run_id":run.id if run else None,"latest_check_run_status":run.status if run else None,"failure_code":run.failure_code if run else None,"check_status":check_status(selected is not None,run.status if run else None),"suggested_score_total":score,"max_score_total":maximum,"suggested_percent":percent,"items":items,"diagnostics":groups}
 async def student_history(self,cid,sid,actor,offset,limit):
  access=select(Student).where(Student.id==sid,Student.class_group_id==cid)
  if not actor.unrestricted:access=access.where(select(ClassGroupTeacher.teacher_user_id).where(ClassGroupTeacher.class_group_id==cid,ClassGroupTeacher.teacher_user_id==actor.actor_id).exists())
  if await self.s.scalar(access) is None:return None
  q=select(AssignmentParticipant,Student,AssessmentVariant,Assignment,Assessment).join(Assignment,Assignment.id==AssignmentParticipant.assignment_id).join(Assessment,Assessment.id==Assignment.assessment_id).join(Student,Student.id==AssignmentParticipant.student_id).outerjoin(AssessmentVariant,AssessmentVariant.id==AssignmentParticipant.assigned_variant_id).where(Assignment.class_group_id==cid,AssignmentParticipant.student_id==sid)
  if not actor.unrestricted:q=q.where(Assessment.created_by==actor.actor_id)
  total=await self.s.scalar(select(func.count()).select_from(q.subquery()));rows=(await self.s.execute(q.order_by(Assignment.created_at.desc()).offset(offset).limit(limit))).all();byp,bys,byr,byf=await self._data([r[0].id for r in rows]);items=[]
  for p,s,v,a,assessment in rows:p._assignment_class_id=cid;x=self._summary(p,s,v,byp,bys,byr,byf);items.append({"assignment_id":a.id,"assessment_id":assessment.id,"assessment_title":assessment.title,"assignment_status":a.status,"start_at":a.start_at,"due_at":a.due_at,"current_attempt_no":x["current_attempt_no"],"current_attempt_status":x["current_attempt_status"],"latest_submitted_attempt_no":x["latest_submitted_attempt_no"],"check_status":x["check_status"],"suggested_score_total":x["suggested_score_total"],"max_score_total":x["max_score_total"],"review_required":x["check_status"]=="review_required"})
  return {"items":items,"total":total,"offset":offset,"limit":limit}
