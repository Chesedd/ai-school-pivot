import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from app.application.authoring import FailureCode, ModelRoute, ProviderFailure, Usage
from app.application.extraction_pipeline import SolverInputV1
from app.application.image_solving_contracts import (ExtractionResultV1, InputArtifactV1,
    SolutionResultV1)
from app.infrastructure.extraction_providers import (AnthropicExtractionAdapter,
    AnthropicSolverAdapter, OpenAIExtractionAdapter)

PAYLOAD = {"extracted_text":"2 + 2?", "structured_statement":"2 + 2?",
    "detected_task_type":"calculation", "detected_answer_format":"number",
    "choices":None, "extraction_confidence":"0.98", "ocr_issues":[],
    "metadata":{"title":"Сложение чисел","subject":"Математика","grade":1,
    "topic":"Сложение","subtopic":"Сложение натуральных чисел",
    "skills":["Складывать натуральные числа"],"task_type":"calculation",
    "answer_format":"number","difficulty":1,"tags":[]}}

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

async def test_anthropic_extraction_forces_tool_and_uses_its_input():
    call=Call(NS(id="msg_1",content=[NS(type="text",text="metadata"),
        NS(type="tool_use",name="record_extraction",input=PAYLOAD)],
        usage=NS(input_tokens=4,output_tokens=2)))
    adapter=AnthropicExtractionAdapter(NS(messages=call),Storage())
    result=await adapter.extract(artifact(),ModelRoute("anthropic","opaque-model"))
    assert isinstance(result, ExtractionResultV1)
    assert call.kwargs["system"] and call.kwargs["messages"][0]["role"] == "user"
    assert call.kwargs["messages"][0]["content"][0]["type"] == "image"
    assert "output_config" not in call.kwargs and "extra_body" not in call.kwargs
    assert call.kwargs["tool_choice"] == {"type":"tool","name":"record_extraction"}
    tool=call.kwargs["tools"][0]
    assert tool["name"] == "record_extraction"
    assert tool["input_schema"] == ExtractionResultV1.model_json_schema()


@pytest.mark.parametrize("metadata", [
    PAYLOAD["metadata"],
    json.dumps(PAYLOAD["metadata"], ensure_ascii=False),
])
async def test_anthropic_accepts_metadata_object_or_once_encoded_object(metadata):
    payload = dict(PAYLOAD, metadata=metadata)
    call = Call(NS(id="metadata", content=[NS(type="tool_use",
        name="record_extraction", input=payload)], usage=NS(input_tokens=4, output_tokens=2)))
    result = await AnthropicExtractionAdapter(NS(messages=call), Storage()).extract(
        artifact(), ModelRoute("anthropic", "opaque-model"))
    assert result.metadata.task_type == "calculation"
    assert result.metadata.answer_format == "number"


@pytest.mark.parametrize("metadata", [
    "hello", "[1]", "null", "123", "{bad json",
    json.dumps(json.dumps(PAYLOAD["metadata"])),
])
async def test_anthropic_rejects_non_object_stringified_metadata(metadata):
    payload = dict(PAYLOAD, metadata=metadata)
    call = Call(NS(id="bad-metadata", content=[NS(type="tool_use",
        name="record_extraction", input=payload)], usage=NS(input_tokens=1, output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicExtractionAdapter(NS(messages=call), Storage()).extract(
            artifact(), ModelRoute("anthropic", "opaque-model"))
    assert error.value.code is FailureCode.MALFORMED_RESPONSE


@pytest.mark.parametrize("metadata", [
    dict(PAYLOAD["metadata"], task_type="решение линейного уравнения"),
    dict(PAYLOAD["metadata"], answer_format="значение переменной X"),
    dict(PAYLOAD["metadata"], unknown_field="value"),
])
async def test_anthropic_metadata_object_remains_strict(metadata):
    payload = dict(PAYLOAD, metadata=metadata)
    call = Call(NS(id="strict-metadata", content=[NS(type="tool_use",
        name="record_extraction", input=payload)], usage=NS(input_tokens=1, output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicExtractionAdapter(NS(messages=call), Storage()).extract(
            artifact(), ModelRoute("anthropic", "opaque-model"))
    assert error.value.code is FailureCode.MALFORMED_RESPONSE


def test_extraction_schema_exposes_content_bank_machine_values():
    schema = ExtractionResultV1.model_json_schema()
    metadata = schema["$defs"]["ExtractionMetadataV1"]["properties"]
    assert set(metadata["task_type"]["enum"]) == {
        "test", "calculation", "problem", "open_question", "essay"}
    assert set(metadata["answer_format"]["enum"]) == {
        "single_choice", "multiple_choice", "short_text", "number", "expression", "long_text"}


async def test_anthropic_extraction_normalizes_only_supported_text_edges():
    payload = dict(PAYLOAD, extracted_text="  first line\nsecond line\n",
        structured_statement="\n2 + 2?  ", detected_task_type=" calculation ",
        detected_answer_format="\nnumber\n", choices=[" A ", "\nB\n"],
        ocr_issues=[" glare ", "\nblur\n"])
    call=Call(NS(id="normalized",content=[NS(type="tool_use",
        name="record_extraction",input=payload)],usage=NS(input_tokens=4,output_tokens=2)))
    result=await AnthropicExtractionAdapter(NS(messages=call),Storage()).extract(
        artifact(),ModelRoute("anthropic","opaque-model"))
    assert result.extracted_text == "first line\nsecond line"
    assert result.structured_statement == "2 + 2?"
    assert result.detected_task_type == "calculation"
    assert result.detected_answer_format == "number"
    assert result.choices == ("A", "B")
    assert result.ocr_issues == ("glare", "blur")
    assert payload["extracted_text"] == "  first line\nsecond line\n"


@pytest.mark.parametrize("overrides", [
    {"choices":[" A ", "A"]},
    {"choices":["   "]},
    {"choices":["A", 2]},
    {"ocr_issues":["ok", 2]},
    {"extracted_text":123},
])
async def test_anthropic_extraction_normalization_still_fails_closed(overrides):
    payload = dict(PAYLOAD, **overrides)
    call=Call(NS(id="bad",content=[NS(type="tool_use",name="record_extraction",
        input=payload)],usage=NS(input_tokens=1,output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicExtractionAdapter(NS(messages=call),Storage()).extract(
            artifact(),ModelRoute("anthropic","opaque-model"))
    assert error.value.code is FailureCode.MALFORMED_RESPONSE


@pytest.mark.parametrize("prefix", [[], [NS(type="thinking",thinking="...")],
    [NS(type="text",text="ordinary prose")]])
async def test_anthropic_extraction_ignores_non_tool_blocks(prefix):
    call=Call(NS(id="msg",content=prefix + [NS(type="tool_use",
        name="record_extraction",input=PAYLOAD)],usage=NS(input_tokens=4,output_tokens=2)))
    result=await AnthropicExtractionAdapter(NS(messages=call),Storage()).extract(
        artifact(),ModelRoute("anthropic","claude-sonnet-4-6"))
    assert result.extracted_text == PAYLOAD["extracted_text"]


@pytest.mark.parametrize("content", [
    [],
    [NS(type="text",text=__import__('json').dumps(PAYLOAD))],
    [NS(type="tool_use",name="wrong",input=PAYLOAD)],
    [NS(type="tool_use",name="record_extraction",input=PAYLOAD),
     NS(type="tool_use",name="record_extraction",input=PAYLOAD)],
    [NS(type="tool_use",name="record_extraction",input="not an object")],
    [NS(type="tool_use",name="record_extraction",input={"extracted_text":"incomplete"})],
])
async def test_anthropic_extraction_fails_closed_on_invalid_tool_response(content):
    call=Call(NS(id="bad",content=content,usage=NS(input_tokens=1,output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicExtractionAdapter(NS(messages=call),Storage()).extract(
            artifact(),ModelRoute("anthropic","claude-sonnet-4-6"))
    assert error.value.code is FailureCode.MALFORMED_RESPONSE


SOLUTION={"status":"solved", "reasoning_summary":"Add the values.",
    "final_answer":"4", "confidence":"0.99"}

def solver_input():
    return SolverInputV1.from_extraction(
        ExtractionResultV1.model_validate_json(__import__('json').dumps(PAYLOAD)))

async def test_anthropic_solver_forces_tool_and_parses_strict_contract():
    call=Call(NS(id="msg_2",content=[NS(type="tool_use",name="record_solution",
        input=SOLUTION)],usage=NS(input_tokens=5,output_tokens=3)))
    result=await AnthropicSolverAdapter(NS(messages=call),
        ModelRoute("anthropic","opaque-model")).solve(solver_input())
    assert isinstance(result, SolutionResultV1) and result.final_answer == "4"
    assert "output_config" not in call.kwargs and "extra_body" not in call.kwargs
    assert call.kwargs["tool_choice"] == {"type":"tool","name":"record_solution"}
    tool=call.kwargs["tools"][0]
    assert tool["name"] == "record_solution"
    assert tool["input_schema"] == SolutionResultV1.model_json_schema()


async def test_anthropic_solver_normalizes_real_runtime_payload():
    payload = {
        "status": "solved",
        "reasoning_summary": (
            "\nSolve: 7 · (X – 3) = 21\n"
            "...\n"
            "Verification: ... ✓\n"
        ),
        "final_answer": "X = 6",
        "confidence": "1.0",
    }
    call=Call(NS(id="runtime",content=[NS(type="tool_use",name="record_solution",
        input=payload)],usage=NS(input_tokens=5,output_tokens=3)))
    result=await AnthropicSolverAdapter(NS(messages=call),
        ModelRoute("anthropic","opaque-model")).solve(solver_input())
    assert isinstance(result, SolutionResultV1)
    assert result.reasoning_summary == (
        "Solve: 7 · (X – 3) = 21\n...\nVerification: ... ✓")
    assert result.reasoning_summary == result.reasoning_summary.strip()
    assert result.final_answer == "X = 6"
    assert result.status == "solved"
    assert payload["reasoning_summary"].startswith("\n")


async def test_anthropic_solver_canonicalizes_observed_aiprime_success_payload():
    payload = {"confidence": "1", "final_answer": "6",
        "reasoning_summary": "Решение уравнения...", "status": "success"}
    call = Call(NS(id="runtime-success", content=[NS(type="tool_use",
        name="record_solution", input=payload)], usage=NS(input_tokens=5, output_tokens=3)))
    result = await AnthropicSolverAdapter(NS(messages=call),
        ModelRoute("anthropic", "opaque-model")).solve(solver_input())

    assert result.status == "solved"
    from app.application.image_solving import DeterministicImageValidator
    assert DeterministicImageValidator().validate(
        ExtractionResultV1.model_validate_json(json.dumps(PAYLOAD)), result
    ).solver_status_check is True


@pytest.mark.parametrize(("overrides", "detail"), [
    ({"confidence": "not-a-decimal"}, "confidence: decimal_parsing"),
    ({"final_answer": None}, "final_answer: string_type"),
])
async def test_anthropic_solver_preserves_safe_contract_validation_detail(overrides, detail):
    payload = dict(SOLUTION, **overrides)
    call = Call(NS(id="invalid", content=[NS(type="tool_use",
        name="record_solution", input=payload)], usage=NS(input_tokens=1, output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicSolverAdapter(NS(messages=call),
            ModelRoute("anthropic", "opaque-model")).solve(solver_input())
    assert error.value.code is FailureCode.MALFORMED_RESPONSE
    assert detail in error.value.adapter_detail


async def test_anthropic_solver_rejects_unknown_status():
    payload = dict(SOLUTION, status="unknown")
    call = Call(NS(id="unknown", content=[NS(type="tool_use",
        name="record_solution", input=payload)], usage=NS(input_tokens=1, output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicSolverAdapter(NS(messages=call),
            ModelRoute("anthropic", "opaque-model")).solve(solver_input())
    assert error.value.code is FailureCode.MALFORMED_RESPONSE
    assert "status: literal_error" in error.value.adapter_detail


async def test_anthropic_solver_reports_missing_final_answer_safely():
    payload = dict(SOLUTION)
    payload.pop("final_answer")
    call = Call(NS(id="missing", content=[NS(type="tool_use",
        name="record_solution", input=payload)], usage=NS(input_tokens=1, output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicSolverAdapter(NS(messages=call),
            ModelRoute("anthropic", "opaque-model")).solve(solver_input())
    assert error.value.code is FailureCode.MALFORMED_RESPONSE
    assert "final_answer: missing" in error.value.adapter_detail


@pytest.mark.parametrize(("reasoning", "expected"), [
    ("  Add the values.  ", "Add the values."),
    ("\nParagraph one.\n\nParagraph two.\n", "Paragraph one.\n\nParagraph two."),
])
async def test_anthropic_solver_trims_edges_and_preserves_internal_newlines(
    reasoning, expected,
):
    payload = dict(SOLUTION, status=" solved ", reasoning_summary=reasoning,
        final_answer=" 4 ")
    call=Call(NS(id="normalized",content=[NS(type="tool_use",name="record_solution",
        input=payload)],usage=NS(input_tokens=5,output_tokens=3)))
    result=await AnthropicSolverAdapter(NS(messages=call),
        ModelRoute("anthropic","opaque-model")).solve(solver_input())
    assert result.reasoning_summary == expected
    assert result.status == "solved" and result.final_answer == "4"


@pytest.mark.parametrize("overrides", [
    {"reasoning_summary":"   "},
    {"reasoning_summary":123},
    {"status":123},
    {"confidence":"abc"},
    {"extra_field":"x"},
])
async def test_anthropic_solver_normalization_still_fails_closed(overrides):
    payload = dict(SOLUTION, **overrides)
    call=Call(NS(id="bad",content=[NS(type="tool_use",name="record_solution",
        input=payload)],usage=NS(input_tokens=1,output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicSolverAdapter(NS(messages=call),
            ModelRoute("anthropic","opaque-model")).solve(solver_input())
    assert error.value.code is FailureCode.MALFORMED_RESPONSE


@pytest.mark.parametrize("prefix", [[NS(type="text",text="prose")],
    [NS(type="thinking",thinking="...")],
    [NS(type="thinking",thinking="..."),NS(type="text",text="prose")]])
async def test_anthropic_solver_ignores_non_tool_blocks(prefix):
    call=Call(NS(id="solver",content=prefix + [NS(type="tool_use",
        name="record_solution",input=SOLUTION)],usage=NS(input_tokens=5,output_tokens=3)))
    result=await AnthropicSolverAdapter(NS(messages=call),
        ModelRoute("anthropic","opaque-model")).solve(solver_input())
    assert result.final_answer == "4"


@pytest.mark.parametrize("content", [
    [], [NS(type="text",text=__import__('json').dumps(SOLUTION))],
    [NS(type="tool_use",name="wrong",input=SOLUTION)],
    [NS(type="tool_use",name="record_solution",input=SOLUTION),
     NS(type="tool_use",name="record_solution",input=SOLUTION)],
    [NS(type="tool_use",name="record_solution",input={"status":"solved"})],
])
async def test_anthropic_solver_fails_closed_on_invalid_tool_response(content):
    call=Call(NS(id="bad",content=content,usage=NS(input_tokens=1,output_tokens=1)))
    with pytest.raises(ProviderFailure) as error:
        await AnthropicSolverAdapter(NS(messages=call),
            ModelRoute("anthropic","opaque-model")).solve(solver_input())
    assert error.value.code is FailureCode.MALFORMED_RESPONSE

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
    assert "choices = null" in lowered and "solution steps" in lowered
    assert "machine values" in lowered and "never translate" in lowered
