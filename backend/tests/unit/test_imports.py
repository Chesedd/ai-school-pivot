from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from app.application.content_bank import *
pytestmark=pytest.mark.asyncio

class Repo:
 def __init__(self):
  self.subject,self.grade,self.topic,self.subtopic,self.skill=[uuid4() for _ in range(5)]; self.other=uuid4(); self.preview=None; self.created=[]; self.audit=[]; self.bulk_calls=0; self.fail_create_at=0; self.fail_audit=False
 def context(self,commands):
  return ImportCatalogContext({self.subject:CatalogRecord(self.subject,'s')},{self.grade:CatalogRecord(self.grade,'g')},{self.topic:CatalogRecord(self.topic,'t',subject_id=self.subject,grade_id=self.grade)},{self.subtopic:CatalogRecord(self.subtopic,'st',topic_id=self.topic)},{self.skill:CatalogRecord(self.skill,'sk',topic_id=self.topic,subtopic_id=self.subtopic)})
 async def get_import_catalog_context(self,commands): self.bulk_calls+=1; return self.context(commands)
 async def find_duplicate_candidates(self,query): return ()
 async def get_subject(self,x): return self.context(()).subjects.get(x)
 async def get_grade(self,x): return self.context(()).grades.get(x)
 async def get_topic(self,x): return self.context(()).topics.get(x)
 async def get_subtopic(self,x): return self.context(()).subtopics.get(x)
 async def get_skills(self,xs): return {x:self.context(()).skills[x] for x in xs if x in self.context(()).skills}
 async def save_import_preview(self,p): self.preview=p
 async def get_import_preview_for_update(self,t): return self.preview if self.preview and self.preview.import_token==t else None
 async def mark_import_preview_committed(self,t,at): self.preview=replace(self.preview,committed_at=at)
 async def append_audit(self,e):
  if self.fail_audit: raise RuntimeError('audit')
  self.audit.append(e)
 async def create_task_with_initial_version(self,c,a):
  if self.fail_create_at and len(self.created)+1==self.fail_create_at: raise RuntimeError('insert')
  now=datetime.now(UTC); s=c.initial_version.skills[0]; dto=TaskDTO(uuid4(),c.subject_id,c.grade_id,c.topic_id,c.subtopic_id,a.actor_id,now,TaskVersionDTO(uuid4(),1,c.initial_version.title,c.initial_version.statement,c.initial_version.task_type,c.initial_version.answer_format,c.initial_version.difficulty,c.initial_version.source,'draft',a.actor_id,now,(SkillLinkDTO(uuid4(),s.skill_id,'sk',s.weight,s.is_primary),))); self.created.append(dto); return dto
class Uow:
 def __init__(self,r): self.repository=r; self.commits=0; self.rollbacks=0
 async def __aenter__(self): self.snapshot=(list(self.repository.created),list(self.repository.audit),self.repository.preview); return self
 async def __aexit__(self,k,e,t):
  if k: self.rollbacks+=1; self.repository.created,self.repository.audit,self.repository.preview=self.snapshot
 async def commit(self): self.commits+=1

def command(r,**kw):
 skills=kw.get('skills',(SkillLinkInput(kw.get('skill',r.skill),kw.get('weight',Decimal('1.0000')),kw.get('primary',True)),))
 return CreateTaskCommand(kw.get('subject',r.subject),kw.get('grade',r.grade),kw.get('topic',r.topic),kw.get('subtopic',r.subtopic),VersionContentInput(kw.get('title'),kw.get('statement','text'),kw.get('task_type','calculation'),kw.get('answer_format','number'),kw.get('difficulty',25),kw.get('source'),skills))
async def preview(r,rows=None,actor=None,fmt='csv',ttl=30): return await ImportPreviewService(Uow(r),ttl).preview(fmt,tuple(rows or [ImportRow(2,command(r))]),actor or ActorContext(uuid4()))

@pytest.mark.parametrize('fmt',['csv','xlsx'])
async def test_preview_formats_valid(fmt):
 r=Repo(); p=await preview(r,fmt=fmt); assert p.format==fmt and p.rows[0].status=='valid'
async def test_preview_mixed_valid_invalid():
 r=Repo(); p=await preview(r,[ImportRow(2,command(r)),ImportRow(3,command(r,subject=uuid4()))]); assert [x.status for x in p.rows]==['valid','invalid']
@pytest.mark.parametrize('count,ok',[ (0,False),(500,True),(501,False)])
async def test_preview_row_limits(count,ok):
 r=Repo(); rows=[ImportRow(i+1,command(r)) for i in range(count)]
 if ok: assert len((await preview(r,rows)).rows)==count
 else:
  with pytest.raises(IssuesError): await ImportPreviewService(Uow(r)).preview('csv',tuple(rows),ActorContext(uuid4()))
async def test_duplicate_row_number_rejected():
 r=Repo()
 with pytest.raises(IssuesError): await preview(r,[ImportRow(2,command(r)),ImportRow(2,command(r))])
@pytest.mark.parametrize('fmt',['csv','xlsx'])
async def test_in_file_duplicate_uses_original_nonsequential_row_number_and_commits(fmt):
 r=Repo(); actor=ActorContext(uuid4())
 p=await preview(r,[ImportRow(17,command(r,statement='Same statement')),ImportRow(42,command(r,statement='  SAME   statement  '))],actor,fmt)
 first_warnings=[x for x in p.rows[0].issues if x.code=='possible_duplicate' and x.duplicate_row_number is not None]
 second_warnings=[x for x in p.rows[1].issues if x.code=='possible_duplicate' and x.duplicate_row_number is not None]
 assert [x.status for x in p.rows]==['valid','valid']
 assert not first_warnings
 assert len(second_warnings)==1 and second_warnings[0].duplicate_row_number==17
 can_commit=any(x.status=='valid' for x in p.rows)
 assert can_commit is True
 committed=await ImportCommitService(Uow(r)).commit(p.import_token,(17,42),actor)
 assert [row_number for row_number,_ in committed]==[17,42]
@pytest.mark.parametrize('fmt',['csv','xlsx'])
async def test_in_file_duplicate_with_header_data_row_numbers(fmt):
 r=Repo(); p=await preview(r,[ImportRow(2,command(r)),ImportRow(3,command(r))],fmt=fmt)
 warnings=[x for x in p.rows[1].issues if x.code=='possible_duplicate' and x.duplicate_row_number is not None]
 assert len(warnings)==1 and warnings[0].duplicate_row_number==2
async def test_nullable_fields():
 r=Repo(); assert (await preview(r,[ImportRow(1,command(r,subtopic=None,title=None,source=None))])).rows[0].status=='valid'
@pytest.mark.parametrize('change',[{'subject':uuid4()},{'grade':uuid4()},{'topic':uuid4()},{'subtopic':uuid4()},{'skill':uuid4()}])
async def test_catalog_errors(change):
 r=Repo(); assert (await preview(r,[ImportRow(1,command(r,**change))])).rows[0].status=='invalid'
@pytest.mark.parametrize('skills,code',[((), 'primary_count'),((SkillLinkInput(uuid4(),Decimal('.5'),True),SkillLinkInput(uuid4(),Decimal('.5'),True)),'primary_count')])
async def test_primary_skill_rules(skills,code):
 r=Repo(); p=await preview(r,[ImportRow(1,command(r,skills=skills))]); assert any(x.code==code for x in p.rows[0].issues)
async def test_duplicate_skills_and_weight_and_format():
 r=Repo(); s=SkillLinkInput(r.skill,Decimal('.5'),True); p=await preview(r,[ImportRow(1,command(r,skills=(s,s),task_type='essay',answer_format='number'))]); assert {'duplicate','incompatible'}<={x.code for x in p.rows[0].issues}
async def test_preview_token_actor_ttl_save_commit_and_no_entities():
 r=Repo(); a=ActorContext(uuid4()); u=Uow(r); p=await ImportPreviewService(u,7).preview('csv',(ImportRow(1,command(r)),),a); assert isinstance(p.import_token,UUID) and p.actor_id==a.actor_id and p.expires_at-p.created_at==timedelta(minutes=7) and r.preview is p and u.commits==1 and not r.created and not r.audit
async def test_bulk_catalog_calls_constant():
 for n in (1,50):
  r=Repo(); await preview(r,[ImportRow(i+1,command(r)) for i in range(n)]); assert r.bulk_calls==1

async def prepared(rows=2,mixed=False):
 r=Repo(); a=ActorContext(uuid4()); data=[ImportRow(i+2,command(r,subject=uuid4() if mixed and i==1 else r.subject)) for i in range(rows)]; await preview(r,data,a); return r,a
async def test_commit_valid_and_subset_mixed():
 r,a=await prepared(mixed=True); u=Uow(r); out=await ImportCommitService(u).commit(r.preview.import_token,(2,),a); assert len(out)==1 and len(r.created)==len(r.audit)==1 and u.commits==1
@pytest.mark.parametrize('numbers',[(),(2,2),(99,)])
async def test_commit_invalid_selection(numbers):
 r,a=await prepared()
 with pytest.raises(IssuesError): await ImportCommitService(Uow(r)).commit(r.preview.import_token,numbers,a)
async def test_selected_invalid_row():
 r,a=await prepared(mixed=True)
 with pytest.raises(IssuesError): await ImportCommitService(Uow(r)).commit(r.preview.import_token,(3,),a)
async def test_token_not_found_and_actor_masked():
 r,a=await prepared()
 for token,actor in ((uuid4(),a),(r.preview.import_token,ActorContext(uuid4()))):
  with pytest.raises(NotFoundError) as e: await ImportCommitService(Uow(r)).commit(token,(2,),actor)
  assert e.value.code=='import_token_not_found'
async def test_expired_and_consumed_token():
 r,a=await prepared(); r.preview=replace(r.preview,expires_at=datetime.now(UTC)-timedelta(seconds=1))
 with pytest.raises(GoneError): await ImportCommitService(Uow(r)).commit(r.preview.import_token,(2,),a)
 r.preview=replace(r.preview,expires_at=datetime.now(UTC)+timedelta(minutes=1),committed_at=datetime.now(UTC))
 with pytest.raises(ConflictError): await ImportCommitService(Uow(r)).commit(r.preview.import_token,(2,),a)
async def test_multiple_rows_one_commit_and_audit_each():
 r,a=await prepared(3); u=Uow(r); await ImportCommitService(u).commit(r.preview.import_token,(2,3,4),a); assert u.commits==1 and len(r.created)==len(r.audit)==3 and r.bulk_calls==2
async def test_second_insert_rolls_back_and_token_reusable():
 r,a=await prepared(); r.fail_create_at=2; u=Uow(r)
 with pytest.raises(RuntimeError): await ImportCommitService(u).commit(r.preview.import_token,(2,3),a)
 assert not r.created and not r.audit and r.preview.committed_at is None and u.rollbacks==1
 r.fail_create_at=0; assert len(await ImportCommitService(Uow(r)).commit(r.preview.import_token,(2,),a))==1
async def test_audit_failure_rolls_back_and_token_reusable():
 r,a=await prepared(); r.fail_audit=True
 with pytest.raises(RuntimeError): await ImportCommitService(Uow(r)).commit(r.preview.import_token,(2,),a)
 assert not r.created and r.preview.committed_at is None
 r.fail_audit=False; await ImportCommitService(Uow(r)).commit(r.preview.import_token,(2,),a)
async def test_repeat_success_creates_nothing_more():
 r,a=await prepared(); await ImportCommitService(Uow(r)).commit(r.preview.import_token,(2,),a); counts=(len(r.created),len(r.audit))
 with pytest.raises(ConflictError): await ImportCommitService(Uow(r)).commit(r.preview.import_token,(2,),a)
 assert counts==(len(r.created),len(r.audit))
async def test_regular_create_still_commits_once():
 r=Repo(); u=Uow(r); await CreateTaskService(u).create_task(command(r),ActorContext(uuid4())); assert u.commits==1
