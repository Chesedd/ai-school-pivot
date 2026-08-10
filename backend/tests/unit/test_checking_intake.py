from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.checking_handoff import CheckingHandoff, CheckingHandoffItem
from app.application.checking_intake import (CheckingIntakeRequest, InvalidCheckingInput,
    build_snapshot, canonical_json_bytes, canonical_run_request, sha256_hex)

SID=UUID("00000000-0000-0000-0000-000000000001")
IID=UUID("00000000-0000-0000-0000-000000000002")
VID=UUID("00000000-0000-0000-0000-000000000003")
SKILL=UUID("00000000-0000-0000-0000-000000000004")


def data(raw=None, normalized=None):
    handoff=CheckingHandoff(SID,datetime(2026,8,10,12,tzinfo=timezone.utc),(
        CheckingHandoffItem(IID,VID,1,Decimal("2.50"),"short_text",raw,normalized),))
    methodology={VID:{"statement":"Решите\nточно","task_type":"problem","answer_format":"short_text",
        "skills":[{"id":SKILL,"code":"ALG","name":"Алгебра","weight":Decimal("1.0000"),"is_primary":True}],
        "expected_solution":None,"accepted_answers":[],"choice_options":[],"choice_scoring_policy":None,
        "rubric":None,"typical_errors":[]}}
    return handoff,methodology


def test_golden_snapshot_bytes_and_fingerprint():
    snapshot=build_snapshot(*data())
    expected=(__import__("pathlib").Path(__file__).parents[1]/"fixtures"/"checking_input_v1_canonical.json").read_bytes()
    assert canonical_json_bytes(snapshot)==expected
    assert sha256_hex(snapshot)=="1c5f8d4e8cca96d4f2ec470860ac1b91d91f3957544185506e4a6e8612befe89"


def test_answers_are_forwarded_and_unanswered_retained():
    raw={"values":[2,1],"text":" A "}; normalized={"stored":True}
    item=build_snapshot(*data(raw,normalized))["items"][0]
    assert item["raw_answer"] is raw and item["normalized_answer"] is normalized


def test_half_present_answer_rejected():
    with pytest.raises(InvalidCheckingInput,match="presence"):
        build_snapshot(*data("answer",None))


def test_answer_format_mismatch():
    handoff,methods=data(); methods[VID]["answer_format"]="number"
    with pytest.raises(InvalidCheckingInput,match="format"): build_snapshot(handoff,methods)


def test_decimal_signed_zero_plain_and_timestamp_z():
    assert canonical_json_bytes({"a":Decimal("-0.000"),"b":Decimal("1E+3")})==b'{"a":"0","b":"1000"}'
    assert build_snapshot(*data())["submitted_at"].endswith("Z")


def test_privacy_is_an_explicit_allowlist():
    handoff,methods=data(); methods[VID].update(student_id="secret",created_by="actor",assignment_id="assignment")
    encoded=canonical_json_bytes(build_snapshot(handoff,methods))
    assert b"student_id" not in encoded and b"created_by" not in encoded and b"assignment_id" not in encoded
    assert str(SID).encode() in encoded and str(IID).encode() in encoded


def test_run_request_hash_changes_for_each_policy_and_rerun():
    base=CheckingIntakeRequest(SID,"key","r","c","t","p")
    fingerprint="a"*64; initial=sha256_hex(canonical_run_request(base,fingerprint))
    assert initial=="7b768d925ae582bf70e4d22290879f65b11b8e98c27ceb1a27a23fec73d1cf79"
    fields=("routing_version","checker_set_version","threshold_policy_version","prompt_model_policy_version")
    for field in fields:
        values=base.__dict__|{field:"changed"}
        assert sha256_hex(canonical_run_request(CheckingIntakeRequest(**values),fingerprint))!=initial
    rerun=CheckingIntakeRequest(**(base.__dict__|{"supersedes_run_id":IID}))
    assert sha256_hex(canonical_run_request(rerun,fingerprint))!=initial


def test_float_is_never_silently_canonicalized():
    with pytest.raises(InvalidCheckingInput): canonical_json_bytes({"bad":1.0})


def test_dictionary_order_never_changes_canonical_bytes_but_arrays_are_preserved():
    first={"z":{"b":2,"a":1},"raw_answer":{"values":[3,1,2]}}
    second={"raw_answer":{"values":[3,1,2]},"z":{"a":1,"b":2}}
    assert canonical_json_bytes(first)==canonical_json_bytes(second)
    assert b'"values":[3,1,2]' in canonical_json_bytes(first)


def test_all_known_methodology_collections_have_semantic_ordering():
    handoff,methods=data(); one=UUID(int=10); two=UUID(int=11); three=UUID(int=12)
    methods[VID].update(
        skills=[{"id":two,"code":"2","name":"2","weight":Decimal(".5000"),"is_primary":False},
                {"id":one,"code":"1","name":"1","weight":Decimal(".5000"),"is_primary":True}],
        choice_options=[{"id":two,"option_key":"B","content":"b","order_index":1},
                        {"id":one,"option_key":"A","content":"a","order_index":0}],
        accepted_answers=[{"id":three,"answer_value":"x","option_ids":[two,one]},
                          {"id":one,"answer_value":"a","option_ids":[]}],
        choice_scoring_policy={"mode":"weighted","policy_version":1,"option_rules":[
            {"option_id":two,"option_key":"B","role":"correct","weight":Decimal("1.00")},
            {"option_id":one,"option_key":"A","role":"incorrect","weight":Decimal("-0.00")}]},
        rubric={"id":one,"grading_mode":"points","max_score":Decimal("2.000"),"notes":None,"items":[
            {"id":three,"criterion":"later","max_points":Decimal("1.5000"),"required":False,"common_failure":None,"order_index":1},
            {"id":two,"criterion":"first","max_points":Decimal(".5000"),"required":True,"common_failure":None,"order_index":0}]},
        typical_errors=[{"id":three,"skill_id":one,"code":"z","severity":"low","remediation_hint":None,"detection_hint":None},
                        {"id":one,"skill_id":one,"code":"a","severity":"low","remediation_hint":None,"detection_hint":None}])
    methodology=build_snapshot(handoff,methods)["items"][0]["methodology"]
    assert [x["id"] for x in methodology["skills"]]==[one,two]
    assert [x["id"] for x in methodology["choice_options"]]==[one,two]
    assert [x["id"] for x in methodology["accepted_answers"]]==[one,three]
    assert methodology["accepted_answers"][1]["option_ids"]==[str(one),str(two)]
    assert [x["id"] for x in methodology["rubric"]["items"]]==[two,three]
    assert [x["id"] for x in methodology["typical_errors"]]==[one,three]
    encoded=canonical_json_bytes(methodology)
    assert b'"max_score":"2"' in encoded and b'"max_points":"0.5"' in encoded
    assert b'"weight":"0"' in encoded
