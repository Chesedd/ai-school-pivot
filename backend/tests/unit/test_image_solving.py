from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
import pytest
from app.application.image_solving import ImageSolvingError, ImageSolvingService
from app.application.image_solving_contracts import ExtractionResultV1, ImageSolvingSession, ImageSolvingStatus, SolutionResultV1
from app.application.input_artifacts import ArtifactOwnershipService, InputArtifactRecord

EXTRACTION = ExtractionResultV1(extracted_text="2 + 2 = ?", structured_statement="2 + 2 = ?", detected_task_type="calculation", detected_answer_format="number", choices=None, extraction_confidence=Decimal(".98"), ocr_issues=())
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
    async def create(self, owner_id, artifact_id):
        now=datetime.now(UTC); self.state=ImageSolvingSession(session_id=uuid4(), owner_id=owner_id, input_artifact_id=artifact_id, lifecycle_status=ImageSolvingStatus.CREATED, created_at=now, updated_at=now); return self.state
    async def get(self, sid): return self.state if self.state and self.state.session_id == sid else None
    async def claim(self, sid, expected, running):
        if self.state.lifecycle_status != expected: return False
        self.state=self.state.model_copy(update={"lifecycle_status":running}); return True
    async def save_checkpoint(self, sid, stage, payload, status):
        names={"extraction":"extraction_checkpoint","solver":"solver_checkpoint","validation":"validation_checkpoint"}; self.stages.append(stage)
        self.state=self.state.model_copy(update={names[stage]:payload,"lifecycle_status":status}); return self.state
    async def fail(self, sid, code): self.state=self.state.model_copy(update={"lifecycle_status":ImageSolvingStatus.FAILED})

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
    assert set(type(solver.inputs[0]).model_fields) == {"schema_version","extracted_text","structured_statement","detected_task_type","detected_answer_format","choices","ocr_issues"}
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
