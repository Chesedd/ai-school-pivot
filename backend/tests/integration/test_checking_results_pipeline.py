from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID
import pytest
from app.application.checking_results import *
from app.application.checking_routing import CheckerOutcome,CheckerResultDraft,CheckerType,ResultReason

I="00000000-0000-0000-0000-000000000001"; T="00000000-0000-0000-0000-000000000002"
def policy(version="p1"): return ConfidenceGatePolicy(version,Decimal(".5"),Decimal(".1"),Decimal(".1"),Decimal(".1"),Decimal(".1"))
def item(): return {"assessment_item_id":I,"task_version_id":T,"points":"2.00","rubric_item_ids":[],"typical_error_ids":[],"skill_ids":[],"methodology":{"rubric":{"items":[]},"typical_errors":[],"skills":[]}}
def draft(checker=CheckerType.EXACT,outcome=CheckerOutcome.CORRECT,reason=ResultReason.EXACT_MATCH,score=Decimal("2.00"),review=False,evidence=MappingProxyType({})):
 return CheckerResultDraft(I,T,outcome,checker,"v1",reason,score,Decimal("2.00"),Decimal("1.0000"),"summary",None,None,review,"review" if review else None,(),evidence,(),())
class Store:
 def __init__(self,items=(item(),),version="p1"): self.items=items;self.version=version;self.saved=None;self.events=[];self.replay=None
 async def finalize(self,run,row,gate,drafts):
  if gate.semantic_version!=self.version: raise ConfidencePolicyConflict("confidence_policy_mismatch")
  prepared=tuple(prepare_result(x,d,gate) for x,d in zip(self.items,drafts))
  envelope=tuple(dict(x.validated_result) for x in prepared)
  if self.replay is not None and self.replay!=envelope: raise ResultReplayConflict("prepared result differs")
  self.replay=envelope;self.saved=prepared
  self.events=[{"checker_type":x.checker_type,"result_status":x.outcome,"confidence":format(x.confidence.effective,".4f")} for x in prepared]
  review=sum(x.confidence.needs_human_review for x in prepared); status="completed_with_review_required" if review else "completed"
  return RunObservability(OBSERVABILITY_SCHEMA_VERSION,run,status,gate.semantic_version,len(self.items),len(prepared),review,sum(len(x.findings) for x in prepared),tuple(sorted({x.outcome for x in prepared})),(),())
async def finish(store,drafts,gate=None): return await CheckingResultFinalizationService(store).finalize(UUID(int=9),1,gate or policy(),tuple(drafts))

@pytest.mark.asyncio
async def test_exact_route_check_prepare_finalize(): assert (await finish(Store(),[draft()])).run_status=="completed"
@pytest.mark.asyncio
async def test_numeric_route_check_prepare_finalize(): assert (await finish(Store(),[draft(CheckerType.NUMERIC,CheckerOutcome.INCORRECT,ResultReason.NUMERIC_MISMATCH,Decimal("0.00"))])).result_count==1
@pytest.mark.asyncio
async def test_choice_route_check_prepare_finalize(): assert (await finish(Store(),[draft(CheckerType.MULTIPLE_CHOICE,CheckerOutcome.PARTIALLY_CORRECT,ResultReason.CHOICE_PARTIAL,Decimal("1.00"))])).review_required_count==0
@pytest.mark.asyncio
async def test_expression_manual_result_finalization(): assert (await finish(Store(),[draft(CheckerType.STRUCTURED_EXPRESSION,CheckerOutcome.MANUAL_REQUIRED,ResultReason.EXPRESSION_EQUIVALENCE_UNPROVEN,None,True)])).run_status=="completed_with_review_required"
@pytest.mark.asyncio
async def test_llm_rubric_fake_provider_finalization():
 d=draft(CheckerType.LLM_RUBRIC,CheckerOutcome.PARTIALLY_CORRECT,ResultReason.LLM_RUBRIC_EVALUATED,Decimal("1.00"),True,MappingProxyType({"confidence_policy_version":"p1","confidence_reason_codes":("base",)})); assert (await finish(Store(),[d])).review_required_count==1
@pytest.mark.asyncio
async def test_mixed_snapshot_order_preservation():
 second=item()|{"assessment_item_id":"00000000-0000-0000-0000-000000000003"}; d2=replace(draft(),assessment_item_id=second["assessment_item_id"]); store=Store((item(),second)); await finish(store,[draft(),d2]); assert [str(x.assessment_item_id) for x in store.saved]==[I,second["assessment_item_id"]]
@pytest.mark.asyncio
async def test_completed_versus_completed_with_review_decision():
 assert (await finish(Store(),[draft()])).run_status=="completed"; assert (await finish(Store(),[draft(CheckerType.MANUAL_REQUIRED,CheckerOutcome.MANUAL_REQUIRED,ResultReason.ROUTING_MANUAL_REQUIRED,None,True)])).run_status.endswith("review_required")
@pytest.mark.asyncio
async def test_exact_replay():
 store=Store(); first=await finish(store,[draft()]); second=await finish(store,[draft()]); assert first==second
@pytest.mark.asyncio
async def test_changed_replay_conflict():
 store=Store(); await finish(store,[draft()]);
 with pytest.raises(ResultReplayConflict): await finish(store,[draft(CheckerType.EXACT,CheckerOutcome.INCORRECT,ResultReason.EXACT_MISMATCH,Decimal("0.00"))])
@pytest.mark.asyncio
async def test_policy_mismatch():
 with pytest.raises(ConfidencePolicyConflict): await finish(Store(version="p1"),[draft()],policy("p2"))
@pytest.mark.asyncio
async def test_source_draft_is_not_mutated():
 d=draft(); before=d.evidence; await finish(Store(),[d]); assert d.evidence is before
@pytest.mark.asyncio
async def test_events_and_observability_exclude_answers_and_provider_output():
 store=Store(); value=await finish(store,[draft()]); serialized=str((store.events,value)); assert "raw_answer" not in serialized and "provider_output" not in serialized
