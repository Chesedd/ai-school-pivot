from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictStr

class AuthoringCreateRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    subject_id: UUID; grade_id: UUID; topic_id: UUID; subtopic_id: UUID|None=None; skill_ids: tuple[UUID,...]=Field(min_length=1,max_length=32)
    task_goal: StrictStr=Field(min_length=1,max_length=2000)
    task_type: Literal["test","calculation","problem","open_question","essay"]
    answer_format: Literal["single_choice","multiple_choice","short_text","number","expression","long_text"]
    difficulty: int=Field(ge=1,le=100); pedagogical_constraints: tuple[StrictStr,...]=Field(default=(),max_length=32)
    source_text: StrictStr|None=Field(default=None,max_length=30000); language: StrictStr|None=None

class RouteRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    provider_id: StrictStr=Field(min_length=1,max_length=128); model_id: StrictStr=Field(min_length=1,max_length=128)
class RunRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    generator_route: RouteRequest; solver_route: RouteRequest

class CostTotal(BaseModel): currency:str; amount:Decimal
class SessionResponse(BaseModel):
    id:UUID; schema_version:str; created_at:datetime; request:dict; request_fingerprint:str; execution_status:str; semantic_status:str|None
    generator_route:dict|None; solver_route:dict|None; preview_available:bool; attempt_count:int; cost_totals:list[CostTotal]
