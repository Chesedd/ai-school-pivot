# ruff: noqa: E501, E701, E702
from copy import deepcopy
from decimal import Decimal
import hashlib
from uuid import uuid4

import pytest

from app.application.paper_checking_execution import (
    PAPER_CHECK_PROMPT_HASH, PaperCheckingExecutionError, compile_paper_request,
    verify_png,
)
from app.application.scan_checking_contracts import PaperCheckResponse, validate_checking_response


def snapshot():
    item,task,page,artifact,policy=uuid4(),uuid4(),uuid4(),uuid4(),uuid4()
    return {"snapshot_schema_version":"checking_input_paper_v1","paper_submission_id":str(uuid4()),
        "batch_id":str(uuid4()),"assignment_id":str(uuid4()),"pages":[{"scan_page_id":str(page),
        "page_order":0,"width":100,"height":200,"coordinate_space":"normalized_upright_v1",
        "content_fingerprint":"a"*64,"derived_render_artifact_id":str(artifact)}],
        "items":[{"assessment_item_id":str(item),"task_version_id":str(task),"position":0,
        "points":"2.00","methodology":{"statement":"Add 1+1","expected_solution":{"solution_text":"2","id":str(uuid4())},
        "rubric":{"items":[{"id":str(uuid4()),"criterion":"answer is 2"}]},"typical_errors":[],"skills":[]}}],
        "grading_policy":{"revision_id":str(policy),"revision":1,"schema_version":"paper_grading_policy.v1",
        "fingerprint":"b"*64,"policy":{"assessment_items":[{"assessment_item_id":str(item),"max_score":"2.00"}]}}}


def response(request,status="correct",score=Decimal("2.00"),page="page-0001"):
    return PaperCheckResponse.model_validate({"schema_version":"paper_check_output.v1","items":({
        "item_token":"item-0001","status":status,"rubric_score":score,"rubric_max_score":Decimal("2.00"),
        "confidence":Decimal("0.9"),"requires_human_review":True,"findings":({"finding_token":"finding-1",
        "category":"work","short_explanation":"Visible work","severity":"info","page_token":page,
        "region":{"kind":"point","coordinate_space":"normalized_upright_v1","x":Decimal("0.5"),"y":Decimal("0.5")},
        "confidence":Decimal("0.8"),"requires_human_review":True},)},),"summary_draft":"Draft",
        "overall_confidence":Decimal("0.9"),"requires_human_review":True})


def test_compilation_is_anonymous_deterministic_and_semantic():
    source=snapshot(); first=compile_paper_request(source,provider_id="fake",model_id="vision")
    second=compile_paper_request(deepcopy(source),provider_id="fake",model_id="vision")
    assert first.request_fingerprint==second.request_fingerprint
    assert first.request.paper_work_token=="paper-0001"
    assert first.request.pages[0].page_token=="page-0001"
    assert first.request.assessment_items[0].item_token=="item-0001"
    serialized=first.request.model_dump_json()
    for forbidden in (source["paper_submission_id"],source["batch_id"],source["assignment_id"],
                      source["pages"][0]["scan_page_id"],source["pages"][0]["derived_render_artifact_id"],"storage_reference"):
        assert forbidden not in serialized
    assert "solution_text" in serialized and PAPER_CHECK_PROMPT_HASH==first.request.prompt_contract.fingerprint
    changed=deepcopy(source); changed["items"][0]["methodology"]["statement"]="Add two"
    assert compile_paper_request(changed,provider_id="fake",model_id="vision").request_fingerprint!=first.request_fingerprint


def test_png_signature_and_hash_are_verified():
    value=b"\x89PNG\r\n\x1a\ncontent"; verify_png(value,hashlib.sha256(value).hexdigest())
    with pytest.raises(PaperCheckingExecutionError,match="hash_mismatch"): verify_png(value,"0"*64)
    with pytest.raises(PaperCheckingExecutionError,match="not_png"): verify_png(b"%PDF",hashlib.sha256(b"%PDF").hexdigest())


@pytest.mark.parametrize("status,score",[("correct",Decimal("2.00")),("partially_correct",Decimal("1.00")),
    ("incorrect",Decimal("0")),("not_attempted",None),("unreadable",None),("insufficient_evidence",None)])
def test_status_score_semantics(status,score):
    request=compile_paper_request(snapshot(),provider_id="fake",model_id="vision").request
    validate_checking_response(request,response(request,status,score))


def test_unknown_page_and_duplicate_finding_are_rejected():
    request=compile_paper_request(snapshot(),provider_id="fake",model_id="vision").request
    with pytest.raises(ValueError,match="unknown_page_token"): validate_checking_response(request,response(request,page="page-9999"))
    value=response(request).model_dump(); value["items"][0]["findings"] += (value["items"][0]["findings"][0],)
    with pytest.raises(ValueError,match="duplicate_finding_token"): validate_checking_response(request,PaperCheckResponse.model_validate(value))


@pytest.mark.parametrize("status,score",[("correct",Decimal("1")),("incorrect",Decimal("1")),
    ("partially_correct",Decimal("0")),("not_attempted",Decimal("0")),("unreadable",Decimal("0")),("insufficient_evidence",Decimal("0"))])
def test_invalid_status_score_pairs_rejected(status,score):
    request=compile_paper_request(snapshot(),provider_id="fake",model_id="vision").request
    with pytest.raises(ValueError): response(request,status,score)
