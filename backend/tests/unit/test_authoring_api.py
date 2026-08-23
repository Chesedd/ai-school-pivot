from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.authoring import ModelRoute
from app.application.authoring_api import (AuthoringApiError, AuthoringRouteCatalog,
    attempt_dto, execution_status, session_dto)


def row(**changes):
    data=dict(id=uuid4(),created_at=None,frozen_request={"task_goal":"Safe"},request_fingerprint="a"*64,
        validation_result=None,semantic_status=None,generator_route=None,solver_route=None,generated_draft=None,solver_result=None)
    data.update(changes); return SimpleNamespace(**data)


def attempt(**changes):
    data=dict(id=uuid4(),role="generator",attempt_number=1,provider_id="openai",model_id="allowed",status="succeeded",
        failure_code=None,started_at=None,finished_at=None,latency_ms=2,input_tokens=3,cache_read_tokens=1,
        cache_write_tokens=0,output_tokens=4,cost_amount=Decimal("1.25"),currency="USD",pricing_version="v1",provider_request_id="safe-id",created_at=None)
    data.update(changes); return SimpleNamespace(**data)


def test_exact_route_allowlist_and_public_metadata():
    catalog=AuthoringRouteCatalog((ModelRoute("openai","allowed"),ModelRoute("anthropic","solver")))
    assert catalog.get("openai","allowed").model_id=="allowed"
    assert catalog.public()[0]["structured_output"] is True
    with pytest.raises(AuthoringApiError) as error: catalog.get("openai","arbitrary")
    assert error.value.code=="authoring_route_not_allowed"


def test_stable_status_cost_and_safe_attempt_read_models():
    assert execution_status(row(),[])=="created"
    assert execution_status(row(),[attempt(status="running")])=="generator_running"
    assert execution_status(row(),[attempt(status="failed_retryable",failure_code="timeout")])=="generator_retryable_failure"
    assert execution_status(row(validation_result={}),[])=="completed"
    dto=session_dto(row(),[attempt(),attempt(role="solver",attempt_number=1,cost_amount=Decimal(".75"))])
    assert dto["cost_totals"]==[{"currency":"USD","amount":Decimal("2.00")}]
    public=attempt_dto(attempt(failure_code="timeout",status="failed_retryable"))
    assert public["retryable"] is True and "prompt_snapshot" not in public and "response_hash" not in public
