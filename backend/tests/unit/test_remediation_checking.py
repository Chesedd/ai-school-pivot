from decimal import Decimal
from uuid import UUID

import pytest

from app.application.checking_deterministic import execute_deterministic
from app.application.checking_items import (CheckingItemKind, InvalidCheckingItemIdentity,
    snapshot_item_key)
from app.application.checking_provider import ProviderExecutionKey
from app.application.checking_routing import CheckerRequest, route_snapshot
from app.application.checking_results import ConfidenceGatePolicy, prepare_result


ITEM=UUID(int=20); VERSION=UUID(int=21); RUN=UUID(int=22); ANSWER=UUID(int=23)


def remediation_item():
    return {"remediation_plan_item_id":str(ITEM),"task_version_id":str(VERSION),"position":0,
        "points":"2.00","answer_format":"short_text","raw_answer":"answer",
        "normalized_answer":{"text":"answer"},"rubric_item_ids":[],"typical_error_ids":[],"skill_ids":[],
        "methodology":{"statement":"Question","task_type":"problem","answer_format":"short_text",
            "skills":[],"expected_solution":None,"accepted_answers":[{"id":str(ANSWER),
                "value_kind":"text","canonical_text":"answer","normalization_policy_code":"exact_text_v1",
                "normalization_policy_version":1,"answer_value":"answer","tolerance":None,"unit":None,
                "normalization_rule":None,"canonical_decimal":None,"absolute_tolerance":None,
                "relative_tolerance":None,"unit_code":None,"option_ids":[]}],"choice_options":[],
            "choice_scoring_policy":None,"rubric":{"id":str(UUID(int=24)),"grading_mode":"points",
                "max_score":"2","notes":None,"items":[]},"typical_errors":[]}}


@pytest.mark.asyncio
async def test_remediation_routes_through_the_unchanged_deterministic_checker_without_provider():
    item=remediation_item(); snapshot={"snapshot_schema_version":"checking_input_remediation_v1",
        "handoff_version":1,"routing_contract_version":"checking_routing_contract_v1","items":[item]}
    decision=route_snapshot(snapshot)[0]
    draft=await execute_deterministic(CheckerRequest(item,decision))
    prepared=prepare_result(item,draft,ConfidenceGatePolicy("confidence_v1",Decimal("0.5"),
        Decimal("0.1"),Decimal("0.1"),Decimal("0.1"),Decimal("0.1")))
    assert decision.assessment_item_id==str(ITEM)
    assert prepared.outcome=="correct" and prepared.max_score==Decimal("2.00")


def test_item_identity_rejects_both_or_neither_and_provider_key_keeps_kind():
    assert snapshot_item_key(remediation_item()).kind is CheckingItemKind.REMEDIATION
    with pytest.raises(InvalidCheckingItemIdentity): snapshot_item_key({})
    with pytest.raises(InvalidCheckingItemIdentity): snapshot_item_key({
        "assessment_item_id":str(UUID(int=1)),"remediation_plan_item_id":str(UUID(int=2))})
    key=ProviderExecutionKey(RUN,ITEM,CheckingItemKind.REMEDIATION)
    assert key.item_kind is CheckingItemKind.REMEDIATION and key.item_id==ITEM
