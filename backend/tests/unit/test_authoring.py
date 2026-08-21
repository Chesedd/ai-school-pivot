from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.application.authoring import *


def request(**changes):
    data=dict(schema_version="authoring-request.v1",task_goal="Create one bounded task",subject="math",grade="g7",topic="fractions",subtopic=None,task_type="test",answer_format="single_choice",difficulty=50,skills=("reasoning","fractions"),pedagogical_constraints=("No calculator",),source_text="Reference text",language="en",policy_version="authoring-v1")
    data.update(changes); return AuthoringRequestV1.model_validate(data)


def test_request_supported_values_and_boundaries():
    for value in TaskType: assert request(task_type=value.value).task_type == value.value
    for value in AnswerFormat: assert request(answer_format=value.value).answer_format == value.value
    assert request(difficulty=1).difficulty == 1 and request(difficulty=100).difficulty == 100
    for invalid in (0,101,1.0,"1"):
        with pytest.raises(ValidationError): request(difficulty=invalid)


def test_request_is_strict_bounded_and_unknown_fields_rejected():
    with pytest.raises(ValidationError): request(task_goal=" goal ")
    with pytest.raises(ValidationError): request(source_text="x"*(MAX_SOURCE+1))
    with pytest.raises(ValidationError): request(extra=True)
    with pytest.raises(ValidationError): request(skills=["reasoning"])
    with pytest.raises(AuthoringError) as error: validate_authoring_request({"schema_version":"wrong"})
    assert error.value.code in {"invalid_request","unsupported_value"}


def test_canonical_fingerprint_golden_and_semantic_change():
    value=request(source_text=None,pedagogical_constraints=())
    expected=b'{"answer_format":"single_choice","difficulty":50,"grade":"g7","language":"en","pedagogical_constraints":[],"policy_version":"authoring-v1","schema_version":"authoring-request.v1","skills":["reasoning","fractions"],"source_text":null,"subject":"math","subtopic":null,"task_goal":"Create one bounded task","task_type":"test","topic":"fractions"}'
    assert value.canonical_bytes()==expected
    assert value.fingerprint=="6f9b8923abc331b7938bc282bb52214f35e129f01a267055cc72e2ad7cd210a4"
    assert value.fingerprint != request(source_text=None,pedagogical_constraints=(),difficulty=51).fingerprint
    assert canonical_json_bytes({"b":1,"a":2})==canonical_json_bytes({"a":2,"b":1})
    with pytest.raises(AuthoringError): canonical_json_bytes({"x":1.0})


def test_frozen_catalog_enforces_allowlist():
    catalog=FrozenCatalogContext("math","g7","fractions",None,("reasoning","fractions")); catalog.validate_request(request())
    with pytest.raises(AuthoringError,match="catalog_reference_not_allowed"): catalog.validate_request(request(topic="geometry"))


def prompt(role=AuthoringRole.GENERATOR):
    return PromptSpecification("contract-probe",role,"1.0.0","probe-v1","a"*64,"authoring-provider-probe.v1","authoring-v1")


def test_provider_boundary_snapshots_roles_timeout_and_privacy():
    for role in AuthoringRole: assert prompt(role).role is role
    spec=prompt()
    with pytest.raises(FrozenInstanceError): spec.stable_name="changed"
    settings={"temperature":"0"}; execution=ExecutionRequest(AuthoringRole.GENERATOR,"fake","probe",settings,spec,"a"*64,1000,"corr","key",RetryPolicy(2)); settings["secret"]="no"
    assert "secret" not in execution.settings
    for timeout in (0,MAX_TIMEOUT_MS+1,1.5):
        with pytest.raises(AuthoringError): ExecutionRequest(AuthoringRole.GENERATOR,"fake","probe",{},spec,"a"*64,timeout,"corr","key",RetryPolicy())
    failure=ProviderFailure(FailureCode.AUTHENTICATION,"api key and raw prose")
    assert str(failure)=="authentication" and "api key" not in str(failure)
    assert FailureCode.TIMEOUT in RETRYABLE and FailureCode.AUTHENTICATION not in RETRYABLE


def test_usage_cost_and_response_hash():
    usage=Usage(3,4,1); cost=decimal_cost("1.23000000","USD","price-v1","catalog")
    assert cost.amount==Decimal("1.23000000")
    result=ProviderResult({"acknowledged":True},"request-1",usage,cost,12)
    assert len(result.response_hash)==64
    for bad in (1.2,"NaN","Infinity","-1","1e-2","1.000000000"):
        with pytest.raises(AuthoringError): decimal_cost(bad,"USD","price-v1","catalog")
    with pytest.raises(AuthoringError): Usage(-1,0)


async def test_fake_provider_contract_probe_and_failure_mapping():
    execution=ExecutionRequest(AuthoringRole.SOLVER,"fake","probe",{},prompt(AuthoringRole.SOLVER),"a"*64,1000,"corr","key",RetryPolicy())
    result=await invoke_provider(FakeAuthoringProvider(),execution)
    assert result.technical_response["schema_version"]=="authoring-provider-probe.v1"
    with pytest.raises(ProviderFailure) as error: await invoke_provider(FakeAuthoringProvider(failures=(FailureCode.PROVIDER_5XX,)),execution)
    assert error.value.code is FailureCode.PROVIDER_5XX
