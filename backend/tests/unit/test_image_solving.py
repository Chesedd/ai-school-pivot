from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
import logging
import pytest
from app.application.image_solving import ImageSolvingError, ImageSolvingService
from app.application.image_solving_contracts import ExtractionResultV1, ImageSolvingSession, ImageSolvingStatus, SolutionResultV1
from app.application.input_artifacts import ArtifactOwnershipService, InputArtifactRecord

EXTRACTION = ExtractionResultV1(extracted_text="2 + 2 = ?", structured_statement="2 + 2 = ?", detected_task_type="calculation", detected_answer_format="number", choices=None, extraction_confidence=Decimal(".98"), ocr_issues=(), metadata={"title":"Сложение чисел","subject":"Математика","grade":1,"topic":"Сложение","subtopic":"Натуральные числа","skills":("Складывать числа",),"task_type":"calculation","answer_format":"number","difficulty":1,"tags":()})
SOLUTION = SolutionResultV1(status="solved", reasoning_summary="Add the values.", final_answer="4", confidence=Decimal(".99"))

class Artifacts:
    def __init__(self, item): self.item=item
    async def get(self, artifact_id): return self.item if artifact_id == self.item.id else None
class Integrity:
    def __init__(self, digest): self.digest=digest
    async def sha256(self, artifact): return self.digest
class Extractor:
    def __init__(self): self.inputs=[]
    async def extract(self, value): self.inputs.append(value); return EXTRACTION
class Solver:
    def __init__(self): self.inputs=[]
    async def solve(self, value): self.inputs.append(value); return SOLUTION
class Repo:
    def __init__(self): self.state=None; self.stages=[]
    async def create(self, owner_id, artifact_id, solution_instruction=None):
        now=datetime.now(UTC); self.state=ImageSolvingSession(session_id=uuid4(), owner_id=owner_id, input_artifact_id=artifact_id, solution_instruction=solution_instruction, lifecycle_status=ImageSolvingStatus.CREATED, created_at=now, updated_at=now); return self.state
    async def get(self, sid): return self.state if self.state and self.state.session_id == sid else None
    async def claim(self, sid, expected, running):
        if self.state.lifecycle_status != expected: return False
        self.state=self.state.model_copy(update={"lifecycle_status":running}); return True
    async def save_checkpoint(self, sid, stage, payload, status):
        names={"extraction":"extraction_checkpoint","solver":"solver_checkpoint","validation":"validation_checkpoint"}; self.stages.append(stage)
        self.state=self.state.model_copy(update={names[stage]:payload,"lifecycle_status":status}); return self.state
    async def fail(self, sid, code): self.state=self.state.model_copy(update={"lifecycle_status":ImageSolvingStatus.FAILED})
    async def retry_solver(self, sid):
        if self.state.lifecycle_status is not ImageSolvingStatus.FAILED: return False
        self.state=self.state.model_copy(update={"lifecycle_status":ImageSolvingStatus.EXTRACTED})
        return True

def item(owner=None):
    return InputArtifactRecord(id=uuid4(), owner_id=owner or uuid4(), mime_type="image/png", content_hash_sha256="a"*64, size_bytes=4, storage_reference="private/object", created_at=datetime.now(UTC))
def setup(record=None):
    record=record or item(); repo=Repo(); extractor=Extractor(); solver=Solver()
    service=ImageSolvingService(repo, ArtifactOwnershipService(Artifacts(record)), Integrity(record.content_hash_sha256), extractor, solver)
    return record,repo,extractor,solver,service

async def test_ownership_is_checked_on_create_and_run():
    record,_,_,_,service=setup()
    with pytest.raises(Exception) as error: await service.create_session(owner_id=uuid4(), input_artifact_id=record.id)
    assert getattr(error.value,"code",None) == "artifact_access_denied"

async def test_integrity_failure_fails_closed():
    record,repo,extractor,_,service=setup(); session=await service.create_session(owner_id=record.owner_id,input_artifact_id=record.id); service.integrity=Integrity("b"*64)
    with pytest.raises(ImageSolvingError, match="artifact_integrity_failed"): await service.run(session_id=session.session_id,owner_id=record.owner_id)
    assert repo.state.lifecycle_status == ImageSolvingStatus.FAILED and extractor.inputs == []

async def test_extraction_solver_validation_flow_and_isolated_inputs():
    record,repo,extractor,solver,service=setup(); session=await service.create_session(owner_id=record.owner_id,input_artifact_id=record.id)
    result=await service.run(session_id=session.session_id,owner_id=record.owner_id)
    assert repo.stages == ["extraction","solver","validation"] and result.lifecycle_status == ImageSolvingStatus.VALIDATED
    assert set(type(solver.inputs[0]).model_fields) == {"schema_version","extracted_text","structured_statement","detected_task_type","detected_answer_format","choices","ocr_issues","solution_instruction"}
    assert "storage_reference" not in solver.inputs[0].model_dump_json() and not hasattr(solver.inputs[0],"provider_keys")
    assert not ({"answer","solution","hints"} & set(type(EXTRACTION).model_fields))
    assert not hasattr(repo,"create_task_version")

async def test_resume_after_extraction_skips_extractor():
    record,repo,extractor,_,service=setup(); session=await service.create_session(owner_id=record.owner_id,input_artifact_id=record.id)
    repo.state=repo.state.model_copy(update={"extraction_checkpoint":EXTRACTION,"lifecycle_status":ImageSolvingStatus.EXTRACTED})
    await service.resume(session_id=session.session_id,owner_id=record.owner_id)
    assert extractor.inputs == [] and repo.stages == ["solver","validation"]

async def test_resume_after_solver_only_validates_and_terminal_is_idempotent():
    record,repo,extractor,solver,service=setup(); session=await service.create_session(owner_id=record.owner_id,input_artifact_id=record.id)
    repo.state=repo.state.model_copy(update={"extraction_checkpoint":EXTRACTION,"solver_checkpoint":SOLUTION,"lifecycle_status":ImageSolvingStatus.SOLVED})
    first=await service.resume(session_id=session.session_id,owner_id=record.owner_id); second=await service.resume(session_id=session.session_id,owner_id=record.owner_id)
    assert extractor.inputs == solver.inputs == [] and repo.stages == ["validation"] and second == first

async def test_invalid_checkpoint_fails_closed_before_calls():
    record,repo,extractor,solver,service=setup(); session=await service.create_session(owner_id=record.owner_id,input_artifact_id=record.id)
    repo.state=repo.state.model_copy(update={"solver_checkpoint":SOLUTION,"lifecycle_status":ImageSolvingStatus.SOLVED})
    with pytest.raises(ImageSolvingError, match="invalid_checkpoint"): await service.resume(session_id=session.session_id,owner_id=record.owner_id)
    assert extractor.inputs == solver.inputs == []


async def test_extraction_failure_logs_safe_structured_diagnostics(caplog):
    class FailedExtractor:
        provider_id = "anthropic"
        model_id = "opaque-model"
        async def extract(self, value):
            from app.application.authoring import FailureCode, ProviderFailure
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE,
                "ValidationError: metadata: dict_type")

    record, repo, _, solver, service = setup()
    service.extractor = FailedExtractor()
    session = await service.create_session(owner_id=record.owner_id,
        input_artifact_id=record.id)
    with caplog.at_level(logging.ERROR), pytest.raises(Exception):
        await service.run(session_id=session.session_id, owner_id=record.owner_id)
    entry = next(item for item in caplog.records
        if item.message.startswith("image solving stage failed"))
    assert "stage=extraction" in entry.message and "failure_code=malformed_response" in entry.message
    assert entry.session_id == str(session.session_id)
    assert (entry.stage, entry.provider, entry.model) == (
        "extraction", "anthropic", "opaque-model")
    assert entry.failure_code == "malformed_response"
    assert entry.exception_category == "ProviderFailure"
    assert entry.validation_reason == "ValidationError: metadata: dict_type"
    assert repo.state.lifecycle_status == ImageSolvingStatus.FAILED


@pytest.mark.parametrize(("code", "detail"), [
    ("timeout", ""),
    ("malformed_response", "ValidationError: confidence: decimal_parsing"),
])
async def test_solver_provider_failure_logs_stage_and_safe_diagnostics(caplog, code, detail):
    from app.application.authoring import FailureCode, ProviderFailure
    class FailedSolver:
        provider_id = "anthropic"
        model_id = "opaque-model"
        def __init__(self): self.calls = 0
        async def solve(self, value):
            self.calls += 1
            raise ProviderFailure(FailureCode(code), detail)

    record, repo, _, _, service = setup()
    service.solver = FailedSolver()
    session = await service.create_session(owner_id=record.owner_id,
        input_artifact_id=record.id)
    with caplog.at_level(logging.ERROR), pytest.raises(ProviderFailure):
        await service.run(session_id=session.session_id, owner_id=record.owner_id)
    entry = next(item for item in caplog.records
        if item.message.startswith("image solving stage failed"))
    assert f"failure_code={code}" in entry.message and "provider=anthropic" in entry.message
    assert (entry.stage, entry.provider, entry.model) == (
        "solver", "anthropic", "opaque-model")
    assert entry.failure_code == code
    assert entry.validation_reason == (detail or None)
    assert service.solver.calls == (2 if code == "timeout" else 1)


async def test_explicit_retry_reuses_extraction_and_completes_solver():
    from app.application.authoring import FailureCode, ProviderFailure
    class OnceFailedSolver(Solver):
        async def solve(self, value):
            self.inputs.append(value)
            if len(self.inputs) == 1:
                raise ProviderFailure(FailureCode.MALFORMED_RESPONSE)
            return SOLUTION
    record, repo, extractor, _, service = setup()
    service.solver = OnceFailedSolver()
    session = await service.create_session(owner_id=record.owner_id,
        input_artifact_id=record.id)
    with pytest.raises(ProviderFailure):
        await service.run(session_id=session.session_id, owner_id=record.owner_id)
    result = await service.run(session_id=session.session_id, owner_id=record.owner_id)
    assert len(extractor.inputs) == 1 and len(service.solver.inputs) == 2
    assert result.lifecycle_status is ImageSolvingStatus.VALIDATED

async def test_persisted_solution_instruction_is_solver_only_and_survives_retry():
    record,repo,extractor,solver,service=setup()
    instruction="Реши через дискриминант"
    session=await service.create_session(owner_id=record.owner_id,input_artifact_id=record.id,solution_instruction=instruction)
    await service.run(session_id=session.session_id,owner_id=record.owner_id)
    assert extractor.inputs[0].user_context == "Solve the task shown in this artifact"
    assert solver.inputs[0].solution_instruction == instruction
    assert repo.state.solution_instruction == instruction


def test_solver_instruction_changes_fingerprint():
    from app.application.extraction_pipeline import SolverInputV1
    plain=SolverInputV1.from_extraction(EXTRACTION)
    guided=SolverInputV1.from_extraction(EXTRACTION,solution_instruction="Только ответ")
    assert plain.solution_instruction is None
    assert guided.solution_instruction == "Только ответ"
    assert plain.fingerprint != guided.fingerprint
