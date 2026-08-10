from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.checking import (CreateRunCommand, InvalidPersistenceCommand,
    safe_event_details, validate_finding, validate_model_result, validate_result, validate_transition)


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
