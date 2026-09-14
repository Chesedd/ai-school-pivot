from dataclasses import replace
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from app.application.classroom_administration import ClassView,ClassroomAdministrationService,ClassroomError,StudentAccount,StudentView

class Repo:
 def __init__(self):
  self.grade=uuid4();self.actor=uuid4();self.audit_events=[];self.groups={};self.students={};self.members=set();self.eligibility='eligible';self.accounts={}
 async def grade_exists(self,id):return id==self.grade
 async def create_class(self,**v):
  x=ClassView(uuid4(),v['name'],v['grade_id'],9,'9',v['external_ref'],datetime.now(timezone.utc),None);self.groups[x.id]=x;return x
 async def get_class(self,id,lock=False):return self.groups.get(id)
 async def update_class(self,id,values):self.groups[id]=replace(self.groups[id],**values);return self.groups[id]
 async def audit(self,*args):self.audit_events.append(args)
 async def teacher_eligibility(self,id):return self.eligibility
 async def membership_exists(self,c,t,lock=False):return (c,t) in self.members
 async def assign_teacher(self,c,t,a):self.members.add((c,t))
 async def unassign_teacher(self,c,t):
  if (c,t) not in self.members:return False
  self.members.remove((c,t));return True
 async def get_student(self,id,lock=False):return self.students.get(id)
 async def update_student(self,id,values):self.students[id]=replace(self.students[id],**values);return self.students[id]
 async def create_student(self,**v):
  x=StudentView(uuid4(),v['display_name'],v['external_ref'],v['class_group_id'],None);self.students[x.id]=x;return x
 async def student_account(self,id,lock=False):return self.accounts.get(id)
 async def provision_student(self,**v):
  x=await self.create_student(**{k:v[k] for k in ('class_group_id','display_name','external_ref')});self.accounts[v['user_id']]=replace(self.accounts[v['user_id']],linked_student=x);return x

def configured(r):
 g=ClassView(uuid4(),'9A',r.grade,9,'9',None,datetime.now(timezone.utc),None);r.groups[g.id]=g;return g
async def test_create_requires_canonical_grade_and_audits_success_only():
 r=Repo();s=ClassroomAdministrationService(r)
 with pytest.raises(ClassroomError,match='grade required'):await s.create_class(name='A',grade_id=None,external_ref=None,actor=r.actor)
 with pytest.raises(ClassroomError,match='grade not found'):await s.create_class(name='A',grade_id=uuid4(),external_ref=None,actor=r.actor)
 assert r.audit_events==[]
 result=await s.create_class(name='A',grade_id=r.grade,external_ref='a',actor=r.actor)
 assert result.grade_id==r.grade and [x[2] for x in r.audit_events]==['class.created']
async def test_membership_is_idempotent_and_eligibility_enforced():
 r=Repo();g=configured(r);s=ClassroomAdministrationService(r);teacher=uuid4()
 await s.assign_teacher(g.id,teacher,r.actor);await s.assign_teacher(g.id,teacher,r.actor)
 assert len(r.members)==1 and [x[2] for x in r.audit_events]==['teacher.assigned']
 await s.unassign_teacher(g.id,teacher,r.actor);await s.unassign_teacher(g.id,teacher,r.actor)
 assert [x[2] for x in r.audit_events]==['teacher.assigned','teacher.unassigned']
 r.eligibility='inactive'
 with pytest.raises(ClassroomError,match='teacher inactive'):await s.assign_teacher(g.id,uuid4(),r.actor)
async def test_move_stale_archived_target_and_same_class_semantics():
 r=Repo();source=configured(r);target=configured(r);s=ClassroomAdministrationService(r);student=StudentView(uuid4(),'S',None,source.id,None);r.students[student.id]=student
 with pytest.raises(ClassroomError,match='student class conflict'):await s.move_student(student.id,target.id,uuid4(),r.actor)
 assert await s.move_student(student.id,source.id,source.id,r.actor)==student and not r.audit_events
 moved=await s.move_student(student.id,target.id,source.id,r.actor)
 assert moved.class_group_id==target.id and [x[2] for x in r.audit_events]==['student.moved']
async def test_legacy_group_can_be_mapped_but_not_receive_students_or_teachers():
 r=Repo();g=configured(r);r.groups[g.id]=replace(g,grade_id=None);s=ClassroomAdministrationService(r)
 with pytest.raises(ClassroomError,match='class grade required'):await s.create_student(g.id,'S',None,r.actor)
 with pytest.raises(ClassroomError,match='class grade required'):await s.assign_teacher(g.id,uuid4(),r.actor)
 mapped=await s.update_class(g.id,{'grade_id':r.grade},r.actor);assert mapped.is_configuration_complete
async def test_archive_student_checks_nested_class_id_and_is_idempotent():
 r=Repo();g=configured(r);s=ClassroomAdministrationService(r);user=uuid4();r.accounts[user]=StudentAccount(user,'S',True,True,None);x=await s.create_student(g.id,user,None,r.actor);r.audit_events.clear()
 with pytest.raises(ClassroomError,match='student not found'):await s.archive_student(uuid4(),x.id,r.actor)
 await s.archive_student(g.id,x.id,r.actor);await s.archive_student(g.id,x.id,r.actor)
 assert [x[2] for x in r.audit_events]==['student.archived']

async def test_provisioning_validates_account_and_existing_link_states():
 r=Repo();group=configured(r);other=configured(r);s=ClassroomAdministrationService(r)
 missing=uuid4()
 with pytest.raises(ClassroomError) as error:await s.create_student(group.id,missing,None,r.actor)
 assert error.value.code=='user_not_found'
 for account,code in (
  (StudentAccount(uuid4(),'Inactive',False,True,None),'student_user_inactive'),
  (StudentAccount(uuid4(),'Teacher',True,False,None),'student_role_required'),
  (StudentAccount(uuid4(),'Same',True,True,StudentView(uuid4(),'Same',None,group.id,None)),'student_already_in_class'),
  (StudentAccount(uuid4(),'Other',True,True,StudentView(uuid4(),'Other',None,other.id,None)),'student_in_another_class'),
  (StudentAccount(uuid4(),'Old',True,True,StudentView(uuid4(),'Old',None,group.id,datetime.now(timezone.utc))),'student_profile_archived'),
 ):
  r.accounts[account.user_id]=account
  with pytest.raises(ClassroomError) as error:await s.create_student(group.id,account.user_id,None,r.actor)
  assert error.value.code==code

async def test_provisioning_uses_canonical_name_and_audits_both_ids():
 r=Repo();group=configured(r);s=ClassroomAdministrationService(r);user=uuid4()
 r.accounts[user]=StudentAccount(user,'Canonical Name',True,True,None)
 student=await s.create_student(group.id,user,'school-42',r.actor)
 assert student.display_name=='Canonical Name' and student.external_ref=='school-42'
 details=r.audit_events[-1][4]
 assert details['user_id']==str(user) and details['student_id']==str(student.id)
