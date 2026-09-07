from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
class ClassResponse(BaseModel):
    id: UUID; name: str; grade_id: UUID|None; grade_number:int|None; grade_name:str|None; external_ref:str|None; created_at:datetime; archived_at:datetime|None; is_configuration_complete:bool; active_student_count:int; assigned_teacher_count:int
class CreateClassRequest(BaseModel):
    model_config=ConfigDict(extra='forbid'); name:Annotated[str,Field(min_length=1,max_length=120)]; grade_id:UUID; external_ref:Annotated[str,Field(max_length=120)]|None=None
class UpdateClassRequest(BaseModel):
    model_config=ConfigDict(extra='forbid'); name:Annotated[str,Field(min_length=1,max_length=120)]|None=None; grade_id:UUID|None=None; external_ref:Annotated[str,Field(max_length=120)]|None=None
    @model_validator(mode='after')
    def nonempty(self):
        if not self.model_fields_set:raise ValueError('at least one field is required')
        return self
class TeacherResponse(BaseModel): user_id:UUID;display_name:str;is_active:bool
class StudentResponse(BaseModel): id:UUID;display_name:str;external_ref:str|None;class_group_id:UUID;archived_at:datetime|None
class CreateStudentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid');display_name:Annotated[str,Field(min_length=1,max_length=120)];external_ref:Annotated[str,Field(max_length=120)]|None=None
class MoveStudentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid');target_class_group_id:UUID;expected_current_class_group_id:UUID
