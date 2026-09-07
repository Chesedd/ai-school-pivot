from datetime import datetime
from typing import Literal, Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
class Strict(BaseModel): model_config=ConfigDict(extra="forbid",from_attributes=True)
class ItemInput(Strict): task_version_id:UUID; selection_source:Literal["suggested","manual"]
class CreateRemediation(Strict):
 creation_key:Annotated[str,Field(min_length=1,max_length=128)]; student_id:UUID; class_group_id:UUID; source_assignment_id:UUID; source_assignment_participant_id:UUID; source_submission_id:UUID; source_check_run_id:UUID; finding_ids:list[UUID]=[]; title:Annotated[str,Field(min_length=1,max_length=200)]; instructions:Annotated[str,Field(max_length=4000)]|None=None; due_at:datetime|None=None; items:Annotated[list[ItemInput],Field(max_length=20)]=[]
 @field_validator("title")
 @classmethod
 def title_trimmed(cls,v):
  if v!=v.strip(): raise ValueError("title must be trimmed")
  return v
class UpdateRemediation(Strict):
 expected_updated_at:datetime; title:Annotated[str,Field(min_length=1,max_length=200)]|None=None; instructions:Annotated[str,Field(max_length=4000)]|None=None; due_at:datetime|None=None; finding_ids:list[UUID]|None=None; items:Annotated[list[ItemInput],Field(max_length=20)]|None=None
class AssignRemediation(Strict): acknowledge_review_required:bool=False
class RemediationItemResponse(Strict): position:int; task_version_id:UUID; selection_source:str; title:str|None=None; statement:str|None=None; task_type:str|None=None; answer_format:str|None=None; difficulty:int|None=None
class RemediationPlanResponse(Strict):
 id:UUID; student_id:UUID; class_group_id:UUID; source_assignment_id:UUID; source_assignment_participant_id:UUID; source_submission_id:UUID; source_check_run_id:UUID; status:Literal["draft","assigned","cancelled"]; title:str; instructions:str|None; due_at:datetime|None; review_acknowledged_at:datetime|None; created_at:datetime; updated_at:datetime; assigned_at:datetime|None; cancelled_at:datetime|None; finding_ids:list[UUID]; items:list[RemediationItemResponse]
class RemediationPage(Strict): items:list[RemediationPlanResponse]; total:int; offset:int; limit:int
class StudentRemediationSummary(Strict): id:UUID; title:str; instructions:str|None; status:Literal["assigned","cancelled"]; assigned_at:datetime; due_at:datetime|None; cancelled_at:datetime|None; item_count:int
class StudentPage(Strict): items:list[StudentRemediationSummary]; total:int; offset:int; limit:int
class StudentDetail(StudentRemediationSummary): items:list[RemediationItemResponse]
class CandidateSearch(Strict): assignment_id:UUID; student_id:UUID; source_submission_id:UUID; source_check_run_id:UUID; finding_ids:list[UUID]=[]; mode:Literal["suggested","manual"]="suggested"; q:str|None=None; difficulty_min:int|None=Field(None,ge=1,le=100); difficulty_max:int|None=Field(None,ge=1,le=100); task_type:str|None=None; offset:int=Field(0,ge=0); limit:int=Field(20,ge=1,le=50)
class CandidateReason(Strict): match_type:str; matched_skill_id:UUID|None=None; matched_typical_error_id:UUID|None=None; matched_title:str|None=None
class Candidate(Strict): task_id:UUID; task_version_id:UUID; title:str|None; statement_preview:str; task_type:str; answer_format:str; difficulty:int; subject_id:UUID; grade_id:UUID; topic_id:UUID; subtopic_id:UUID|None; primary_skill_id:UUID|None; primary_skill_name:str|None; reasons:list[CandidateReason]
