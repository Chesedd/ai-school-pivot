from datetime import datetime, timezone
from uuid import uuid4
import pytest
from app.application.classroom_notes import ClassroomNotesError, ClassroomNotesService, NoteRecord

class Repo:
    def __init__(self): self.archived=False; self.student_archived=False; self.allowed=True; self.notes=[]
    async def class_state(self,*a): return self.archived if self.allowed else None
    async def student_state(self,*a,**kw): return self.student_archived if self.allowed else None
    async def create_note(self,kind,c,s,a,b):
        n=NoteRecord(uuid4(),b,a,"Teacher",c,datetime.now(timezone.utc),datetime.now(timezone.utc),s);self.notes.append(n);return n
    async def list_notes(self,kind,c,s,a,u,o,l):
        x=self.notes if u else [n for n in self.notes if n.teacher_user_id==a]
        return {"items":x[o:o+l],"total":len(x),"offset":o,"limit":l}
    async def get_note(self,kind,c,s,n,a,u,delete=False): return next((x for x in self.notes if x.id==n and (u or x.teacher_user_id==a)),None)
    async def update_note(self,kind,n,a,b,e):
        old=next((x for x in self.notes if x.id==n and x.teacher_user_id==a and x.updated_at==e),None)
        if not old:return None
        new=NoteRecord(old.id,b,a,old.teacher_display_name,old.class_group_id,datetime.now(timezone.utc),datetime.now(timezone.utc),old.student_id);self.notes[self.notes.index(old)]=new;return new
    async def delete_note(self,kind,n): self.notes=[x for x in self.notes if x.id!=n]

async def test_create_class_and_student_notes_and_normalize_body():
    r=Repo();s=ClassroomNotesService(r);a,c,student=uuid4(),uuid4(),uuid4()
    assert (await s.create_class(c,a,False,"  line 1\nline 2  ")).body=="line 1\nline 2"
    assert (await s.create_student(c,student,a,False,"student")).student_id==student
@pytest.mark.parametrize("body",["   ","x"*12001])
async def test_invalid_body(body):
    with pytest.raises(ClassroomNotesError) as x: await ClassroomNotesService(Repo()).create_class(uuid4(),uuid4(),False,body)
    assert (x.value.code,x.value.status)==("note_body_invalid",422)
async def test_membership_and_lifecycle_required():
    r=Repo();s=ClassroomNotesService(r);r.allowed=False
    with pytest.raises(ClassroomNotesError) as x: await s.create_class(uuid4(),uuid4(),False,"x")
    assert x.value.code=="classroom_not_found"
    r.allowed=True;r.archived=True
    with pytest.raises(ClassroomNotesError) as x: await s.create_class(uuid4(),uuid4(),False,"x")
    assert x.value.code=="classroom_archived"
    r.archived=False;r.student_archived=True
    with pytest.raises(ClassroomNotesError) as x: await s.create_student(uuid4(),uuid4(),uuid4(),False,"x")
    assert x.value.code=="student_archived"
async def test_privacy_admin_rules_and_cas():
    r=Repo();s=ClassroomNotesService(r);c,a,b=uuid4(),uuid4(),uuid4();n=await s.create_class(c,a,False,"one")
    assert (await s.list_class(c,b,False,0,50))["items"]==[]
    assert (await s.list_class(c,b,True,0,50))["items"]==[n]
    with pytest.raises(ClassroomNotesError): await s.update("class",c,None,n.id,b,True,"bad",n.updated_at)
    changed=await s.update("class",c,None,n.id,a,False,"two",n.updated_at)
    with pytest.raises(ClassroomNotesError) as x: await s.update("class",c,None,n.id,a,False,"stale",n.updated_at)
    assert x.value.code=="note_concurrent_conflict" and changed.body=="two"
    await s.delete("class",c,None,n.id,b,True);assert not r.notes
async def test_archived_existing_note_edit_delete_allowed():
    r=Repo();s=ClassroomNotesService(r);c,a=uuid4(),uuid4();n=await s.create_class(c,a,False,"one");r.archived=True
    changed=await s.update("class",c,None,n.id,a,False,"two",n.updated_at);assert changed.teacher_user_id==a
    await s.delete("class",c,None,n.id,a,False);assert not r.notes
