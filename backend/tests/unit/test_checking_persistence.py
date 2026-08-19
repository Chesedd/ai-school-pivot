import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.application.checking import (CreateRunCommand, InvalidPersistenceCommand,
    safe_event_details, validate_finding, validate_model_result, validate_result, validate_transition)
from app.application.checking_provider import AttemptDisposition, PromptSpec
from app.infrastructure.checking_repository import _attempt_state, _prompt_lock_key


def command(**changes):
    submission = changes.pop("submission_id", uuid4())
    values = dict(submission_id=submission, request_key="request-1", request_hash="a"*64,
        handoff_version=1, input_snapshot={"submission_id": str(submission)}, input_fingerprint="b"*64,
        snapshot_schema_version="1", routing_version="1", checker_set_version="1",
        threshold_policy_version="1", prompt_model_policy_version="none-v1")
    values.update(changes); return CreateRunCommand(**values)


def test_create_run_command_validates_boundary_values():
    command().validate()
    for bad in (command(request_key=" "), command(request_hash="A"*64), command(handoff_version=0), command(routing_version="")):
        with pytest.raises(InvalidPersistenceCommand): bad.validate()


def test_transition_matrix_is_restricted():
    for source, target in (("pending","running"),("running","completed"),("running","failed_retryable"),("failed_retryable","pending")): validate_transition(source,target)
    for source, target in (("pending","completed"),("completed","running"),("running","pending")):
        with pytest.raises(InvalidPersistenceCommand): validate_transition(source,target)


def test_result_must_match_frozen_snapshot():
    item, version = uuid4(), uuid4(); snapshot={"items":[{"assessment_item_id":str(item),"task_version_id":str(version),"points":"2.00"}]}
    validate_result(snapshot,item,version,Decimal("2"),"correct",Decimal("2"))
    for args in ((uuid4(),version,Decimal("2"),"correct",Decimal("2")),(item,uuid4(),Decimal("2"),"correct",Decimal("2")),(item,version,Decimal("3"),"correct",Decimal("3")),(item,version,Decimal("2"),"incorrect",Decimal("1"))):
        with pytest.raises(InvalidPersistenceCommand): validate_result(snapshot,*args)


def test_finding_provenance_uses_snapshot_allowlists():
    item,rubric,error,skill=uuid4(),uuid4(),uuid4(),uuid4(); snapshot={"items":[{"assessment_item_id":str(item),"rubric_item_ids":[str(rubric)],"typical_error_ids":[str(error)],"skill_ids":[str(skill)]}]}
    validate_finding(snapshot,item,rubric,error,skill,{"schema_version":"1","evidence":[]})
    with pytest.raises(InvalidPersistenceCommand): validate_finding(snapshot,item,uuid4(),error,skill,{"schema_version":"1"})


def test_event_details_are_allowlisted_and_exclude_student_data():
    assert safe_event_details({"attempt_no":1}) == {"attempt_no":1}
    for key in ("raw_answer","provider_output","student_id","email","anything_else"):
        with pytest.raises(InvalidPersistenceCommand): safe_event_details({key:"secret"})


def test_model_result_must_belong_to_same_run_and_item():
    run,item=uuid4(),uuid4(); validate_model_result(run,item,run,item)
    with pytest.raises(InvalidPersistenceCommand): validate_model_result(run,item,uuid4(),item)


def test_prompt_advisory_lock_key_is_canonical_unambiguous_and_text_safe():
    normal=PromptSpec("provider-probe","1.0.0","synthetic","probe-v1")
    key=_prompt_lock_key(normal)
    assert key=='["provider-probe","1.0.0"]'
    assert "\x00" not in key and json.loads(key)==["provider-probe","1.0.0"]
    assert _prompt_lock_key(normal)==key
    assert _prompt_lock_key(PromptSpec("a:b","c","x","v")) != _prompt_lock_key(PromptSpec("a","b:c","x","v"))
    controlled=_prompt_lock_key(PromptSpec("provider-probe","1\x00control","x","v"))
    assert "\x00" not in controlled and "\\u0000" in controlled


def test_attempt_state_canonicalizes_driver_uuid_subclass():
    class DriverUUID(UUID): pass
    expected=uuid4(); driver_value=DriverUUID(bytes=expected.bytes)
    row=SimpleNamespace(id=driver_value,attempt_no=1,status="running",
        request_fingerprint="a"*64,validated_output=None,error_code=None)
    state=_attempt_state(row,AttemptDisposition.CLAIMED)
    assert state.attempt_id==expected and state.attempt_id.bytes==driver_value.bytes
    assert type(state.attempt_id) is UUID and type(state.attempt_id) is not DriverUUID
    assert state.attempt_no==1 and state.status=="running"
    assert state.disposition is AttemptDisposition.CLAIMED
    assert state.request_fingerprint=="a"*64
