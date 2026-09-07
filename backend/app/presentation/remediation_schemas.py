from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Annotated
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

class RemediationAnswerPut(Strict): raw_answer:Any
class RemediationChoiceOption(Strict): option_id:str; content:str
class RemediationExecutionItem(Strict):
 remediation_plan_item_id:UUID; position:int; task_version_id:UUID; title:str|None; statement:str; task_type:str; answer_format:str; difficulty:int; choice_options:list[RemediationChoiceOption]; current_raw_answer:Any|None
class RemediationExecutionResponse(Strict):
 remediation_id:UUID; title:str; instructions:str|None; plan_status:Literal["assigned","cancelled"]; due_at:datetime|None; execution_status:Literal["not_started","in_progress","submitted","cancelled","expired"]; submission_id:UUID|None; submission_status:Literal["draft","submitted"]|None; attempt_no:int|None; items:list[RemediationExecutionItem]
class RemediationAnswerResponse(Strict):
 remediation_plan_item_id:UUID; raw_answer:Any; normalized_answer:Any; created_at:datetime; updated_at:datetime

ExecutionStatus=Literal["not_started","in_progress","submitted","checking","checked","review_required","check_failed","cancelled","expired"]
class StudentResultItem(Strict):
 remediation_plan_item_id:UUID; position:int; task_version_id:UUID; task_title:str|None; task_statement:str
 student_raw_answer:Any|None; task_type:str; answer_format:str; difficulty:int
 choice_options:list[RemediationChoiceOption]; result_status:str|None; score_suggested:Decimal|None
 max_score:Decimal|None; student_feedback:str|None
class StudentResultExecution(Strict):
 remediation_id:UUID; title:str; instructions:str|None; plan_status:Literal["assigned","cancelled"]
 due_at:datetime|None; execution_status:ExecutionStatus; submission_id:UUID|None
 submission_status:Literal["draft","submitted"]|None; attempt_no:int|None; started_at:datetime|None
 submitted_at:datetime|None; check_run_id:UUID|None; check_run_status:str|None; items:list[StudentResultItem]
class RemediationFindingResult(Strict):
 finding_id:UUID; finding_type:str; rubric_item_id:UUID|None; typical_error_id:UUID|None; skill_id:UUID|None
 snapshot_code:str|None; snapshot_title:str|None; snapshot_criterion:str|None; severity:str; confidence:Decimal
class RemediationCheckResult(Strict):
 check_result_id:UUID; result_status:str; checker_type:str; score_suggested:Decimal|None; max_score:Decimal
 confidence:Decimal; summary:str; teacher_summary:str|None; needs_human_review:bool
 review_reason:str|None; model_limitations:str|None
class TeacherRemediationResultItem(Strict):
 remediation_plan_item_id:UUID; position:int; task_version_id:UUID; task_title:str|None; task_statement:str
 student_raw_answer:Any|None; check_result:RemediationCheckResult|None; findings:list[RemediationFindingResult]
class TeacherRemediationResult(Strict):
 remediation_id:UUID; plan_status:Literal["draft","assigned","cancelled"]; execution_status:ExecutionStatus
 student_id:UUID; student_display_name:str; submission_id:UUID|None; submission_status:str|None
 started_at:datetime|None; submitted_at:datetime|None; check_run_id:UUID|None; check_run_status:str|None
 failure_code:str|None; suggested_score_total:Decimal|None; max_score_total:Decimal|None
 suggested_percent:Decimal|None; review_required_count:int; items:list[TeacherRemediationResultItem]
