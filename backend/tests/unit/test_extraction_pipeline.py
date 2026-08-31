from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.authoring import (Cost, ModelRoute, ProviderCapabilities,
    ProviderRegistry, ProviderResult, Usage, canonical_json_bytes)
from app.application.extraction_pipeline import (ExtractionPipelineService,
    ExtractionResumeState, SolverInputV1)
from app.application.input_artifacts import InputArtifactRecord


EXTRACTION = {"extracted_text":"2 + 2 = ?", "structured_statement":"2 + 2 = ?",
    "detected_task_type":"calculation", "detected_answer_format":"number",
    "choices":None, "extraction_confidence":"0.98", "ocr_issues":[],
    "metadata":{"title":"Сложение чисел","subject":"Математика","grade":1,
        "topic":"Сложение","subtopic":"Натуральные числа",
        "skills":["Складывать числа"],"task_type":"calculation",
        "answer_format":"number","difficulty":1,"tags":[]}}
SOLUTION = {"status":"solved", "reasoning_summary":"Adding gives four.",
    "final_answer":"4", "confidence":"0.99"}


class Provider:
    capabilities = ProviderCapabilities()
    def __init__(self, payload): self.payload, self.requests = payload, []
    async def execute(self, request):
        self.requests.append(request)
        return ProviderResult(self.payload, "provider-request", Usage(1,1),
            Cost(Decimal("0"),"USD","test-v1","test"),1)


class Repo:
    def __init__(self): self.saved=(None,None,None,None); self.identity=None; self.attempts=[]
    async def commit(self): pass
    async def configure_extraction_pipeline(self, sid, identity, extractor_route, solver_route):
        self.identity=identity
        return ExtractionResumeState.from_persisted(*self.saved)
    async def create_attempt(self, sid, execution):
        attempt=SimpleNamespace(id=uuid4(),status="pending",execution=execution)
        self.attempts.append(attempt); return attempt,True
    async def claim(self, attempt_id): return True
    async def recover_stale(self, attempt_id): return False
    async def finalize_success(self, *args, **kwargs): return True
    async def finalize_failure(self, *args, **kwargs): return True
    async def checkpoint_extraction_success(self,sid,identity,attempt_id,result,value):
        self.saved=(value.model_dump(mode="json"),attempt_id,self.saved[2],self.saved[3])
    async def checkpoint_solution_success(self,sid,identity,attempt_id,result,value):
        self.saved=(self.saved[0],self.saved[1],value.model_dump(mode="json"),attempt_id)


def artifact():
    return InputArtifactRecord(id=uuid4(),owner_id=uuid4(),mime_type="image/png",
        content_hash_sha256="a"*64,size_bytes=100,storage_reference="objects/task.png",
        created_at=datetime.now(UTC))


async def run(repo, extractor, solver, item=None):
    registry=ProviderRegistry(); registry.register("extractor",extractor); registry.register("solver",solver)
    return await ExtractionPipelineService(repo,registry).run(uuid4(),item or artifact(),
        ModelRoute("extractor","model"),ModelRoute("solver","model"),user_context="Solve this task",
        policy_version="authoring-v1",correlation_id="correlation",idempotency_key="pipeline-key")


async def test_extraction_checkpoint_and_solver_receives_only_extracted_data():
    repo=Repo(); extractor=Provider(EXTRACTION); solver=Provider(SOLUTION)
    result=await run(repo,extractor,solver)
    assert result.final_answer == "4"
    assert repo.saved[0] == EXTRACTION and repo.saved[1] is not None
    message=canonical_json_bytes(solver.requests[0].messages).decode()
    assert "2 + 2 = ?" in message
    assert "artifact_id" not in message and "storage_reference" not in message
    assert "expected_answer" not in message and "solution" not in SolverInputV1.model_fields
    assert all("generated_task_draft" not in request.prompt.output_schema_version for request in extractor.requests+solver.requests)


async def test_extraction_resume_does_not_call_extractor_again():
    item=artifact(); repo=Repo(); extractor=Provider(EXTRACTION); solver=Provider(SOLUTION)
    await run(repo,extractor,solver,item)
    repo.saved=(repo.saved[0],repo.saved[1],None,None)
    second_extractor=Provider({"must":"not run"}); second_solver=Provider(SOLUTION)
    await run(repo,second_extractor,second_solver,item)
    assert second_extractor.requests == []


async def test_extraction_path_has_no_task_version_creation_capability():
    repo=Repo(); await run(repo,Provider(EXTRACTION),Provider(SOLUTION))
    assert not hasattr(repo,"create_task_version")
    assert set(repo.saved[0]) == {"extracted_text","structured_statement","detected_task_type",
        "detected_answer_format","choices","extraction_confidence","ocr_issues","metadata"}
