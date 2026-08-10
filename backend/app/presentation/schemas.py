"""HTTP-only Pydantic schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


Difficulty = Annotated[int, Field(strict=True, ge=1, le=100)]


def plain_decimal(value: Decimal | None) -> str | None:
    """Serialize exact Decimal values without exponent notation or signed zero."""
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value, "f")


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TagCreateRequest(StrictRequest):
    category_code: str
    subject_id: UUID | None = None
    name: str


class TagPatchRequest(StrictRequest):
    name: str | None = None
    category_code: str | None = None
    subject_id: UUID | None = None
    replacement_tag_id: UUID | None = None
    expected_updated_at: datetime


class TagDeprecateRequest(StrictRequest):
    replacement_tag_id: UUID | None = None
    expected_updated_at: datetime


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID; category: dict; subject: dict | None; name: str; normalized_name: str
    status: Literal["active","deprecated"]; replacement: dict | None
    created_at: datetime; created_by: UUID; updated_at: datetime; updated_by: UUID


class SkillLinkCreate(StrictRequest):
    skill_id: UUID
    weight: Decimal
    is_primary: bool


class InitialVersionCreate(StrictRequest):
    title: str | None = None
    statement: str
    task_type: Literal["test", "calculation", "problem", "open_question", "essay"]
    answer_format: Literal["single_choice", "multiple_choice", "short_text", "number", "expression", "long_text"]
    difficulty: Difficulty
    source: str | None = None
    skills: list[SkillLinkCreate] = Field(min_length=1)


class TaskCreateRequest(StrictRequest):
    subject_id: UUID
    grade_id: UUID
    topic_id: UUID
    subtopic_id: UUID | None = None
    folder_id: UUID | None = None
    initial_version: InitialVersionCreate
    tag_ids: list[UUID] = Field(default_factory=list)

class TagRefResponse(BaseModel):
    id: UUID; name: str; category_code: str; subject_id: UUID | None; status: str
    replacement: dict | None = None

class VersionTagsPutRequest(StrictRequest):
    tag_ids: list[UUID]
    expected_updated_at: datetime

class VersionTagsResponse(BaseModel):
    task_id: UUID; task_version_id: UUID; version_no: int; updated_at: datetime
    tags: list[TagRefResponse]


class SkillLinkResponse(BaseModel):
    id: UUID
    skill_id: UUID
    name: str
    weight: Decimal
    is_primary: bool


class TaskVersionResponse(BaseModel):
    id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: int
    source: str | None
    status: str
    created_by: UUID
    created_at: datetime
    skills: list[SkillLinkResponse]
    tags: list[TagRefResponse] = Field(default_factory=list)


class TaskResponse(BaseModel):
    id: UUID
    subject_id: UUID
    grade_id: UUID
    topic_id: UUID
    subtopic_id: UUID | None
    created_by: UUID
    created_at: datetime
    initial_version: TaskVersionResponse
    folder_id: UUID | None = None
    duplicate_warnings: list["DuplicateCandidateResponse"] = Field(default_factory=list)


DuplicateReason = Literal["exact_statement", "high_statement_similarity", "same_primary_skill", "same_final_answer"]
class DuplicateCheckRequest(StrictRequest):
    statement: str
    primary_skill_id: UUID
    final_answer: str | None = None
    exclude_task_id: UUID | None = None
    limit: int = Field(default=5, ge=1, le=20)
class DuplicateCandidateResponse(BaseModel):
    task_id: UUID; task_version_id: UUID; version_no: int; title: str | None
    status: Literal["draft","review","approved"]; statement: str
    statement_similarity: float; same_primary_skill: bool; same_final_answer: bool
    reasons: list[DuplicateReason]
class DuplicateCheckResponse(BaseModel):
    has_likely_duplicates: bool
    items: list[DuplicateCandidateResponse]


class TaskListItemResponse(BaseModel):
    task_id: UUID
    subject_id: UUID
    subject_name: str
    grade_id: UUID
    grade_name: str
    topic_id: UUID
    topic_name: str
    subtopic_id: UUID | None
    subtopic_name: str | None
    latest_version_id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: int
    status: str
    primary_skill_id: UUID | None
    primary_skill_name: str | None
    created_at: datetime
    archived_at: datetime | None
    updated_at: datetime
    folder_id: UUID | None = None
    folder_name: str | None = None
    tags: list[TagRefResponse] = Field(default_factory=list)


class TaskListPageResponse(BaseModel):
    items: list[TaskListItemResponse]
    total: int
    offset: int
    limit: int


class CatalogRefResponse(BaseModel):
    id: UUID
    name: str


class TaskVersionSummaryResponse(BaseModel):
    id: UUID
    version_no: int
    status: str
    created_at: datetime
    approved_at: datetime | None
    tags: list[TagRefResponse] = Field(default_factory=list)


class TaskCardVersionResponse(BaseModel):
    id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: int
    source: str | None
    status: str
    skills: list[SkillLinkResponse]
    created_by: UUID
    created_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    methodology: "MethodologyResponse"
    updated_at: datetime
    tags: list[TagRefResponse] = Field(default_factory=list)


class ExpectedSolutionRequest(StrictRequest):
    solution_text: str
    final_answer: str | None = None
    solution_steps: list[str]

class RubricItemRequest(StrictRequest):
    criterion: str
    max_points: Decimal
    required: bool
    common_failure: str | None = None

class RubricRequest(StrictRequest):
    grading_mode: Literal["points"]
    notes: str | None = None
    items: list[RubricItemRequest]

class AcceptedAnswerRequest(StrictRequest):
    answer_value: str
    tolerance: Decimal | None = None
    unit: str | None = None
    normalization_rule: str | None = None
    value_kind: Literal["legacy_untyped","text","decimal","expression","choice_set"] = "legacy_untyped"
    canonical_text: str | None = None
    canonical_decimal: Decimal | None = None
    option_keys: list[str] = []
    absolute_tolerance: Decimal | None = None
    relative_tolerance: Decimal | None = None
    unit_code: str | None = None
    normalization_policy_code: str | None = None
    normalization_policy_version: int | None = None

class ChoiceOptionRequest(StrictRequest):
    option_key: str
    content: str
    order_index: int
class ChoiceOptionRuleRequest(StrictRequest):
    option_key: str
    role: Literal["correct", "distractor"]
    weight: Decimal
class ChoiceScoringPolicyRequest(StrictRequest):
    mode: Literal["all_or_nothing","per_option"]
    policy_version: int = 1
    option_rules: list[ChoiceOptionRuleRequest] = []

class TypicalErrorRequest(StrictRequest):
    skill_id: UUID
    code: str
    title: str
    description: str
    severity: Literal["low", "medium", "high"]
    remediation_hint: str | None = None
    detection_hint: str | None = None

class HintRequest(StrictRequest):
    level: int
    hint_text: str

class MethodologyPutRequest(StrictRequest):
    expected_solution: ExpectedSolutionRequest | None
    rubric: RubricRequest | None
    accepted_answers: list[AcceptedAnswerRequest]
    typical_errors: list[TypicalErrorRequest]
    hints: list[HintRequest]
    choice_options: list[ChoiceOptionRequest] = []
    choice_scoring_policy: ChoiceScoringPolicyRequest | None = None

class ExpectedSolutionResponse(BaseModel):
    id: UUID
    solution_text: str
    final_answer: str | None
    solution_steps: list[str]

class RubricItemResponse(BaseModel):
    id: UUID
    criterion: str
    max_points: Decimal
    required: bool
    common_failure: str | None
    order_index: int

    @field_serializer("max_points", when_used="json")
    def serialize_max_points(self, value: Decimal) -> str:
        return plain_decimal(value)

class RubricResponse(BaseModel):
    id: UUID
    grading_mode: str
    max_score: Decimal
    notes: str | None
    items: list[RubricItemResponse]

    @field_serializer("max_score", when_used="json")
    def serialize_max_score(self, value: Decimal) -> str:
        return plain_decimal(value)

class AcceptedAnswerResponse(BaseModel):
    id: UUID
    answer_value: str
    tolerance: Decimal | None
    unit: str | None
    normalization_rule: str | None
    value_kind: str
    canonical_text: str | None
    canonical_decimal: Decimal | None
    option_keys: list[str]
    option_ids: list[UUID]
    absolute_tolerance: Decimal | None
    relative_tolerance: Decimal | None
    unit_code: str | None
    normalization_policy_code: str | None
    normalization_policy_version: int | None

    @field_serializer(
        "tolerance", "canonical_decimal", "absolute_tolerance", "relative_tolerance",
        when_used="json",
    )
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        return plain_decimal(value)

class ChoiceOptionResponse(BaseModel):
    id: UUID
    option_key: str
    content: str
    order_index: int
class ChoiceOptionRuleResponse(BaseModel):
    option_key: str
    role: str
    weight: Decimal

    @field_serializer("weight", when_used="json")
    def serialize_weight(self, value: Decimal) -> str:
        return plain_decimal(value)
class ChoiceScoringPolicyResponse(BaseModel):
    mode: str
    policy_version: int
    option_rules: list[ChoiceOptionRuleResponse]

class ReadinessIssueResponse(BaseModel):
    field: str
    code: str
    message: str

class AutomationReadinessResponse(BaseModel):
    ready: bool
    checker_candidate: str
    contract_version: str
    reason_codes: list[str]
    issues: list[ReadinessIssueResponse]

class TypicalErrorResponse(BaseModel):
    id: UUID
    skill_id: UUID
    code: str
    title: str
    description: str
    severity: str
    remediation_hint: str | None
    detection_hint: str | None

class HintResponse(BaseModel):
    id: UUID
    level: int
    hint_text: str

class MethodologyResponse(BaseModel):
    expected_solution: ExpectedSolutionResponse | None
    rubric: RubricResponse | None
    accepted_answers: list[AcceptedAnswerResponse]
    typical_errors: list[TypicalErrorResponse]
    hints: list[HintResponse]
    choice_options: list[ChoiceOptionResponse] = []
    choice_scoring_policy: ChoiceScoringPolicyResponse | None = None
    automation_readiness: AutomationReadinessResponse | None = None


class TaskCardResponse(BaseModel):
    id: UUID
    subject: CatalogRefResponse
    grade: CatalogRefResponse
    topic: CatalogRefResponse
    subtopic: CatalogRefResponse | None
    created_by: UUID
    created_at: datetime
    archived_at: datetime | None
    updated_at: datetime
    latest_version: TaskCardVersionResponse
    approved_version: TaskVersionSummaryResponse | None
    versions: list[TaskVersionSummaryResponse]


class CatalogItemResponse(BaseModel):
    id: UUID
    name: str
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None
    code: str | None = None
    number: int | None = None


class CatalogResponse(BaseModel):
    catalog: str
    items: list[CatalogItemResponse]


class EmptyRequest(StrictRequest):
    pass


class ReturnToDraftRequest(StrictRequest):
    reason: str = Field(min_length=1, max_length=1000)


class CreateVersionRequest(StrictRequest):
    source_version_no: int = Field(gt=0)


class ArchiveRequest(StrictRequest):
    reason: str | None = Field(default=None, max_length=1000)


class ValidationIssueResponse(BaseModel):
    field: str
    code: str
    message: str


class ValidationReportResponse(BaseModel):
    valid_for_approval: bool
    issues: list[ValidationIssueResponse]


class StatusCommandResponse(BaseModel):
    task_id: UUID
    task_version_id: UUID
    version_no: int
    previous_status: str
    status: str
    created_at: datetime
    created_by: UUID
    approved_at: datetime | None
    approved_by: UUID | None
    validation: ValidationReportResponse | None = None


class CreatedVersionResponse(BaseModel):
    task_id: UUID
    task_version_id: UUID
    version_no: int
    status: str
    created_at: datetime
    created_by: UUID
    approved_at: datetime | None
    approved_by: UUID | None


class ArchiveResponse(BaseModel):
    task_id: UUID
    archived_at: datetime
    latest_status: str


class AuditEventResponse(BaseModel):
    id: UUID
    task_id: UUID
    task_version_id: UUID | None
    version_no: int | None
    action: str
    actor_id: UUID
    reason: str | None
    details: dict[str, object]
    occurred_at: datetime


class AuditPageResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int
    offset: int
    limit: int

class ImportRowRequest(TaskCreateRequest):
    row_number: int = Field(gt=0)
    tags: list[str] = Field(default_factory=list, max_length=100)

class ImportPreviewRequest(StrictRequest):
    format: Literal["csv", "xlsx"]
    rows: list[ImportRowRequest] = Field(min_length=1, max_length=500)

class ImportIssueResponse(BaseModel):
    code: str; field: str; message: str; severity: str
    duplicate_candidates: list[DuplicateCandidateResponse] = Field(default_factory=list)
    duplicate_row_number: int | None = None
    value: str | None = None
class ImportResolvedTagResponse(BaseModel):
    input: str; tag_id: UUID; name: str; category_code: str; subject_id: UUID | None
    status: str; replacement: dict | None = None
class ImportPreviewRowResponse(BaseModel):
    row_number: int; status: str; issues: list[ImportIssueResponse]
    raw_tag_names: list[str] = Field(default_factory=list)
    resolved_tags: list[ImportResolvedTagResponse] = Field(default_factory=list)
class ImportSummaryResponse(BaseModel):
    rows_total: int; rows_valid: int; rows_invalid: int
class ImportPreviewResponse(BaseModel):
    import_token: UUID; format: str; expires_at: datetime; can_commit: bool
    summary: ImportSummaryResponse; rows: list[ImportPreviewRowResponse]
class ImportCommitRequest(StrictRequest):
    import_token: UUID
    row_numbers: list[int] = Field(max_length=500)
class ImportCommitItemResponse(BaseModel):
    row_number: int; task_id: UUID; task_version_id: UUID; version_no: int; status: str
class ImportCommitResponse(BaseModel):
    imported_count: int; items: list[ImportCommitItemResponse]

class FolderCreateRequest(StrictRequest):
    name: str
    parent_id: UUID | None = None
class FolderRenameRequest(StrictRequest):
    name: str
    expected_updated_at: datetime
class FolderMoveRequest(StrictRequest):
    parent_id: UUID | None = None
    expected_updated_at: datetime
class TaskLocationRequest(StrictRequest):
    folder_id: UUID | None = None
    expected_folder_id: UUID | None = None
class FolderSummaryResponse(BaseModel):
    id: UUID; subject_id: UUID; parent_id: UUID | None; name: str; depth: int; created_at: datetime; updated_at: datetime
class FolderTreeNodeResponse(BaseModel):
    id: UUID; subject_id: UUID; parent_id: UUID | None; name: str; depth: int; children: list["FolderTreeNodeResponse"]
class FolderTreeResponse(BaseModel):
    subject: CatalogRefResponse; folders: list[FolderTreeNodeResponse]
class TaskLocationResponse(BaseModel):
    task_id: UUID; subject_id: UUID; folder_id: UUID | None; previous_folder_id: UUID | None; updated_at: datetime
