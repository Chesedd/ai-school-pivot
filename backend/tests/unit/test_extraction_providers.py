import hashlib
from types import SimpleNamespace as NS

import pytest

from app.application.authoring import FailureCode, ModelRoute, ProviderFailure, Usage
from app.application.image_solving_contracts import InputArtifactV1
from app.infrastructure.extraction_providers import (AnthropicExtractionAdapter,
    OpenAIExtractionAdapter)

PAYLOAD = {"extracted_text":"2 + 2?", "structured_statement":"2 + 2?",
    "detected_task_type":"calculation", "detected_answer_format":"number",
    "choices":None, "extraction_confidence":"0.98", "ocr_issues":[]}

class Storage:
    def __init__(self): self.ids=[]
    async def read_artifact_bytes(self, artifact_id): self.ids.append(artifact_id); return b"raw-image"

class Call:
    def __init__(self, response): self.response=response; self.kwargs=None
    async def create(self, **kwargs): self.kwargs=kwargs; return self.response

def artifact(mime="image/png"):
    return InputArtifactV1(artifact_id="artifact-1",mime_type=mime,
        content_hash=hashlib.sha256(b"raw-image").hexdigest(),user_context="extract")

async def test_openai_multimodal_request_is_strict_and_binary_stays_in_adapter():
    call=Call(NS(id="resp_1",output_text=__import__('json').dumps(PAYLOAD),
        usage=NS(input_tokens=10,output_tokens=2,input_tokens_details=NS(cached_tokens=3))))
    storage=Storage(); adapter=OpenAIExtractionAdapter(NS(responses=call),storage)
    result=await adapter.extract(artifact(),ModelRoute("openai","opaque-model"))
    assert result.extracted_text == "2 + 2?" and storage.ids == ["artifact-1"]
    assert call.kwargs["model"] == "opaque-model"
    assert call.kwargs["text"]["format"]["strict"] is True
    assert call.kwargs["text"]["format"]["schema"]["additionalProperties"] is False
    encoded=call.kwargs["input"][0]["content"][0]["image_url"]
    assert encoded.startswith("data:image/png;base64,") and b"raw-image" not in repr(call.kwargs).encode()
    assert adapter.last_telemetry.usage == Usage(7,2,3,0)

async def test_anthropic_keeps_system_and_user_blocks_separate():
    call=Call(NS(id="msg_1",content=[NS(type="text",text=__import__('json').dumps(PAYLOAD))],
        usage=NS(input_tokens=4,output_tokens=2)))
    adapter=AnthropicExtractionAdapter(NS(messages=call),Storage())
    await adapter.extract(artifact(),ModelRoute("anthropic","opaque-model"))
    assert call.kwargs["system"] and call.kwargs["messages"][0]["role"] == "user"
    assert call.kwargs["messages"][0]["content"][0]["type"] == "image"
    assert call.kwargs["output_config"]["format"]["type"] == "json_schema"

@pytest.mark.parametrize("adapter_kind",["openai","anthropic"])
async def test_malformed_structured_response_is_provider_neutral(adapter_kind):
    response=(NS(id="bad",output_text='{"answer":"4"}',usage=NS(input_tokens=1,output_tokens=1))
        if adapter_kind == "openai" else
        NS(id="bad",content=[NS(type="text",text='{"answer":"4"}')],usage=NS(input_tokens=1,output_tokens=1)))
    call=Call(response)
    adapter=(OpenAIExtractionAdapter(NS(responses=call),Storage()) if adapter_kind == "openai"
        else AnthropicExtractionAdapter(NS(messages=call),Storage()))
    with pytest.raises(ProviderFailure) as error:
        await adapter.extract(artifact(),ModelRoute(adapter_kind,"model"))
    assert error.value.code is FailureCode.MALFORMED_RESPONSE

def test_prompt_has_no_solver_leakage():
    from app.application.extraction_prompts import IMAGE_EXTRACT_V1_SYSTEM
    lowered=IMAGE_EXTRACT_V1_SYSTEM.lower()
    assert "never solve" in lowered and "create hints" in lowered
