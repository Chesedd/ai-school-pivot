from sqlalchemy import select,func,delete,or_,case
from sqlalchemy.orm import aliased
from app.application.remediation import RemediationError,fingerprint,validate_item_ids,utcnow
from app.infrastructure.remediation_models import *
from app.infrastructure.assessment_models import Assignment,AssignmentParticipant,StudentSubmission,Student,Assessment,AssessmentItem,AssessmentVariant
from app.infrastructure.classroom_models import ClassGroupTeacher
from app.infrastructure.checking_models import CheckRun,CheckResult,CheckFinding
from app.infrastructure.models import Task,TaskVersion,TaskSkillLink,TaskErrorLink,TypicalError,Skill
class RemediationRepository:
 def __init__(self,s): self.s=s
 async def source(self,actor,admin,assignment_id,student_id,submission_id,run_id,participant_id=None,finding_ids=()):
  q=select(Assignment,AssignmentParticipant,StudentSubmission,CheckRun,Student,Assessment).join(AssignmentParticipant,AssignmentParticipant.assignment_id==Assignment.id).join(StudentSubmission,StudentSubmission.assignment_participant_id==AssignmentParticipant.id).join(CheckRun,CheckRun.submission_id==StudentSubmission.id).join(Student,Student.id==AssignmentParticipant.student_id).join(Assessment,Assessment.id==Assignment.assessment_id).where(Assignment.id==assignment_id,AssignmentParticipant.student_id==student_id,StudentSubmission.id==submission_id,CheckRun.id==run_id)
  if participant_id:q=q.where(AssignmentParticipant.id==participant_id)
  if not admin:q=q.join(ClassGroupTeacher,(ClassGroupTeacher.class_group_id==Assignment.class_group_id)&(ClassGroupTeacher.teacher_user_id==actor)).where(Assessment.created_by==actor)
  row=(await self.s.execute(q)).first()
  if not row: raise RemediationError("remediation_source_not_found",404)
  a,p,sub,run,student,assessment=row
  if run.status not in ("completed","completed_with_review_required"): raise RemediationError("remediation_source_not_ready")
  if student.archived_at: raise RemediationError("remediation_student_archived")
  if student.class_group_id!=a.class_group_id: raise RemediationError("remediation_student_moved")
  if finding_ids:
   n=await self.s.scalar(select(func.count()).select_from(CheckFinding).join(CheckResult).where(CheckResult.check_run_id==run.id,CheckFinding.id.in_(finding_ids)))
   if n!=len(set(finding_ids)): raise RemediationError("remediation_source_not_found",404)
  return row
 async def create(self,actor,admin,p):
  validate_item_ids(p.items); intent=p.model_dump(mode="json",exclude={"creation_key"}); fp=fingerprint(intent)
  old=await self.s.scalar(select(RemediationPlan).where(RemediationPlan.owner_user_id==actor,RemediationPlan.creation_key==p.creation_key))
  if old:
   if old.creation_fingerprint!=fp: raise RemediationError("remediation_idempotency_conflict")
   return await self.view(old)
  await self.source(actor,admin,p.source_assignment_id,p.student_id,p.source_submission_id,p.source_check_run_id,p.source_assignment_participant_id,p.finding_ids)
  plan=RemediationPlan(owner_user_id=actor,student_id=p.student_id,class_group_id=p.class_group_id,source_assignment_id=p.source_assignment_id,source_assignment_participant_id=p.source_assignment_participant_id,source_submission_id=p.source_submission_id,source_check_run_id=p.source_check_run_id,title=p.title,instructions=p.instructions,due_at=p.due_at,creation_key=p.creation_key,creation_fingerprint=fp)
  self.s.add(plan); await self.s.flush()
  self.s.add_all([RemediationPlanSignal(remediation_plan_id=plan.id,check_finding_id=x) for x in dict.fromkeys(p.finding_ids)]+[RemediationPlanItem(remediation_plan_id=plan.id,position=i,task_version_id=x.task_version_id,selection_source=x.selection_source) for i,x in enumerate(p.items)]+[RemediationEvent(remediation_plan_id=plan.id,event_type="plan_created",actor_user_id=actor,details={"item_count":len(p.items),"signal_count":len(set(p.finding_ids))})]); await self.s.flush(); return await self.view(plan)
 async def owned(self,id,actor,admin,lock=False):
  q=select(RemediationPlan).where(RemediationPlan.id==id)
  if lock:q=q.with_for_update()
  plan=await self.s.scalar(q)
  if not plan or (not admin and (plan.owner_user_id!=actor or not await self.s.scalar(select(ClassGroupTeacher).where(ClassGroupTeacher.class_group_id==plan.class_group_id,ClassGroupTeacher.teacher_user_id==actor)))): raise RemediationError("remediation_not_found",404)
  return plan
 async def view(self,plan):
  signals=list((await self.s.scalars(select(RemediationPlanSignal.check_finding_id).where(RemediationPlanSignal.remediation_plan_id==plan.id))).all())
  rows=(await self.s.execute(select(RemediationPlanItem,TaskVersion).join(TaskVersion,TaskVersion.id==RemediationPlanItem.task_version_id).where(RemediationPlanItem.remediation_plan_id==plan.id).order_by(RemediationPlanItem.position))).all()
  return {**{k:getattr(plan,k) for k in ("id","student_id","class_group_id","source_assignment_id","source_assignment_participant_id","source_submission_id","source_check_run_id","status","title","instructions","due_at","review_acknowledged_at","created_at","updated_at","assigned_at","cancelled_at")},"finding_ids":signals,"items":[{"position":i.position,"task_version_id":i.task_version_id,"selection_source":i.selection_source,"title":v.title,"statement":v.statement,"task_type":v.task_type,"answer_format":v.answer_format,"difficulty":v.difficulty} for i,v in rows]}
 async def update(self,id,actor,admin,p):
  plan=await self.owned(id,actor,admin,True)
  if admin or plan.status!="draft": raise RemediationError("remediation_immutable")
  if plan.updated_at!=p.expected_updated_at: raise RemediationError("remediation_concurrent_conflict")
  data=p.model_dump(exclude_unset=True,exclude={"expected_updated_at","finding_ids","items"})
  for k,v in data.items():setattr(plan,k,v)
  if p.finding_ids is not None:
   await self.source(actor,False,plan.source_assignment_id,plan.student_id,plan.source_submission_id,plan.source_check_run_id,plan.source_assignment_participant_id,p.finding_ids); await self.s.execute(delete(RemediationPlanSignal).where(RemediationPlanSignal.remediation_plan_id==id)); self.s.add_all([RemediationPlanSignal(remediation_plan_id=id,check_finding_id=x) for x in dict.fromkeys(p.finding_ids)])
  if p.items is not None:
   validate_item_ids(p.items); await self.s.execute(delete(RemediationPlanItem).where(RemediationPlanItem.remediation_plan_id==id)); self.s.add_all([RemediationPlanItem(remediation_plan_id=id,position=i,task_version_id=x.task_version_id,selection_source=x.selection_source) for i,x in enumerate(p.items)])
  plan.updated_at=utcnow(); self.s.add(RemediationEvent(remediation_plan_id=id,event_type="plan_updated",actor_user_id=actor,details={})); await self.s.flush(); return await self.view(plan)
 async def assign(self,id,actor,admin,ack):
  plan=await self.owned(id,actor,admin,True)
  if plan.status=="assigned":return await self.view(plan)
  if plan.status!="draft" or admin:raise RemediationError("remediation_immutable")
  row=await self.source(actor,False,plan.source_assignment_id,plan.student_id,plan.source_submission_id,plan.source_check_run_id,plan.source_assignment_participant_id)
  run=row[3]; items=(await self.s.scalars(select(RemediationPlanItem).where(RemediationPlanItem.remediation_plan_id==id))).all()
  if not items:raise RemediationError("remediation_empty")
  eligible=await self.s.scalar(select(func.count()).select_from(TaskVersion).join(Task).where(TaskVersion.id.in_([x.task_version_id for x in items]),TaskVersion.status=="approved",Task.archived_at.is_(None)).with_for_update())
  if eligible!=len(items):raise RemediationError("remediation_task_unavailable")
  if run.status=="completed_with_review_required" and not ack:raise RemediationError("remediation_review_ack_required")
  now=utcnow(); plan.status="assigned";plan.assigned_at=now;plan.updated_at=now;plan.review_acknowledged_at=now if run.status=="completed_with_review_required" else None;self.s.add(RemediationEvent(remediation_plan_id=id,event_type="plan_assigned",actor_user_id=actor,details={"item_count":len(items)}));await self.s.flush();return await self.view(plan)
 async def cancel(self,id,actor,admin):
  plan=await self.owned(id,actor,admin,True)
  if plan.status=="cancelled":return await self.view(plan)
  now=utcnow();plan.status="cancelled";plan.cancelled_at=now;plan.updated_at=now;self.s.add(RemediationEvent(remediation_plan_id=id,event_type="plan_cancelled",actor_user_id=actor,details={}));await self.s.flush();return await self.view(plan)
 async def candidates(self,actor,admin,p):
  a,part,sub,run,student,assessment=await self.source(actor,admin,p.assignment_id,p.student_id,p.source_submission_id,p.source_check_run_id,None,p.finding_ids)
  excluded=select(AssessmentItem.task_version_id).join(AssessmentVariant).where(AssessmentVariant.assessment_id==a.assessment_id)
  source_tv=aliased(TaskVersion); source_task=aliased(Task)
  context=(await self.s.execute(select(source_task.subject_id,source_task.grade_id,source_tv.difficulty).select_from(CheckResult).join(source_tv,source_tv.id==CheckResult.task_version_id).join(source_task,source_task.id==source_tv.task_id).where(CheckResult.check_run_id==run.id).limit(1))).first()
  if not context:return []
  subject,grade,difficulty=context
  findings=(await self.s.execute(select(CheckFinding.typical_error_id,CheckFinding.skill_id,CheckFinding.snapshot_title).join(CheckResult).where(CheckResult.check_run_id==run.id,CheckFinding.id.in_(p.finding_ids)))).all() if p.finding_ids else []
  errors={x[0] for x in findings if x[0]}; skills={x[1] for x in findings if x[1]}
  if errors:
   skills.update((await self.s.scalars(select(TypicalError.skill_id).where(TypicalError.id.in_(errors)))).all())
  if not skills and not errors and p.mode=="suggested": skills.update((await self.s.scalars(select(TaskSkillLink.skill_id).join(CheckResult,CheckResult.task_version_id==TaskSkillLink.task_version_id).where(CheckResult.check_run_id==run.id))).all())
  primary=aliased(TaskSkillLink); skill=aliased(Skill)
  q=select(TaskVersion,Task,primary,skill).join(Task).outerjoin(primary,(primary.task_version_id==TaskVersion.id)&primary.is_primary.is_(True)).outerjoin(skill,skill.id==primary.skill_id).where(TaskVersion.status=="approved",Task.archived_at.is_(None),Task.subject_id==subject,Task.grade_id==grade,TaskVersion.id.not_in(excluded))
  if p.q:q=q.where(or_(TaskVersion.title.ilike(f"%{p.q}%"),TaskVersion.statement.ilike(f"%{p.q}%")))
  if p.difficulty_min:q=q.where(TaskVersion.difficulty>=p.difficulty_min)
  if p.difficulty_max:q=q.where(TaskVersion.difficulty<=p.difficulty_max)
  if p.task_type:q=q.where(TaskVersion.task_type==p.task_type)
  if p.mode=="suggested":
   matches=[]
   if errors:matches.append(TaskErrorLink.typical_error_id.in_(errors))
   if skills:matches.append(TaskSkillLink.skill_id.in_(skills))
   if not matches:return []
   q=q.outerjoin(TaskErrorLink,TaskErrorLink.task_version_id==TaskVersion.id).outerjoin(TaskSkillLink,TaskSkillLink.task_version_id==TaskVersion.id).where(or_(*matches)).distinct(TaskVersion.id,TaskVersion.title).order_by(TaskVersion.id,TaskVersion.title)
  else:q=q.order_by(TaskVersion.title.asc().nulls_last(),TaskVersion.id)
  rows=(await self.s.execute(q.offset(p.offset).limit(p.limit))).all(); out=[]
  for v,t,pl,sk in rows:
   exact=(await self.s.scalar(select(TaskErrorLink.typical_error_id).where(TaskErrorLink.task_version_id==v.id,TaskErrorLink.typical_error_id.in_(errors)).limit(1))) if errors else None
   sm=(await self.s.execute(select(TaskSkillLink,Skill).join(Skill).where(TaskSkillLink.task_version_id==v.id,TaskSkillLink.skill_id.in_(skills)).order_by(TaskSkillLink.is_primary.desc(),TaskSkillLink.weight.desc()).limit(1))).first() if skills else None
   reason=[]
   if exact: reason=[{"match_type":"typical_error","matched_typical_error_id":exact,"matched_skill_id":None,"matched_title":next((x[2] for x in findings if x[0]==exact),None)}]
   elif sm: reason=[{"match_type":"primary_skill" if sm[0].is_primary else ("secondary_skill" if p.finding_ids else "source_skill_fallback"),"matched_skill_id":sm[0].skill_id,"matched_typical_error_id":None,"matched_title":sm[1].name}]
   out.append({"task_id":t.id,"task_version_id":v.id,"title":v.title,"statement_preview":v.statement[:240],"task_type":v.task_type,"answer_format":v.answer_format,"difficulty":v.difficulty,"subject_id":t.subject_id,"grade_id":t.grade_id,"topic_id":t.topic_id,"subtopic_id":t.subtopic_id,"primary_skill_id":pl.skill_id if pl else None,"primary_skill_name":sk.name if sk else None,"reasons":reason})
  return sorted(out,key=lambda x:(0 if x['reasons'] and x['reasons'][0]['match_type']=='typical_error' else 1,abs(x['difficulty']-difficulty),x['title'] or '',str(x['task_version_id'])))
 async def history(self,class_id,student_id,actor,admin,status,offset,limit):
  student=await self.s.scalar(select(Student).where(Student.id==student_id,Student.class_group_id==class_id,Student.archived_at.is_(None)))
  member=admin or await self.s.scalar(select(ClassGroupTeacher).where(ClassGroupTeacher.class_group_id==class_id,ClassGroupTeacher.teacher_user_id==actor))
  if not student or not member:raise RemediationError("remediation_not_found",404)
  q=select(RemediationPlan).where(RemediationPlan.class_group_id==class_id,RemediationPlan.student_id==student_id)
  if not admin:q=q.where(RemediationPlan.owner_user_id==actor)
  if status!="all":q=q.where(RemediationPlan.status==status)
  plans=(await self.s.scalars(q.order_by(RemediationPlan.created_at.desc(),RemediationPlan.id).offset(offset).limit(limit))).all();return {"items":[await self.view(x) for x in plans],"total":len(plans),"offset":offset,"limit":limit}
 async def student_list(self,student_id,offset,limit):
  q=select(RemediationPlan,func.count(RemediationPlanItem.id)).outerjoin(RemediationPlanItem).where(RemediationPlan.student_id==student_id,RemediationPlan.status.in_(("assigned","cancelled"))).group_by(RemediationPlan.id).order_by(RemediationPlan.assigned_at.desc(),RemediationPlan.id).offset(offset).limit(limit)
  rows=(await self.s.execute(q)).all();return {"items":[{"id":p.id,"title":p.title,"instructions":p.instructions,"status":p.status,"assigned_at":p.assigned_at,"due_at":p.due_at,"cancelled_at":p.cancelled_at,"item_count":n} for p,n in rows],"total":len(rows),"offset":offset,"limit":limit}
 async def student_detail(self,id,student_id):
  p=await self.s.scalar(select(RemediationPlan).where(RemediationPlan.id==id,RemediationPlan.student_id==student_id,RemediationPlan.status.in_(("assigned","cancelled"))))
  if not p:raise RemediationError("remediation_not_found",404)
  v=await self.view(p);return {"id":p.id,"title":p.title,"instructions":p.instructions,"status":p.status,"assigned_at":p.assigned_at,"due_at":p.due_at,"cancelled_at":p.cancelled_at,"item_count":len(v['items']),"items":v['items']}
