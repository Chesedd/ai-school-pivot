"""Phase 4.10 genuine PostgreSQL production-persistence vertical."""
import json,os
from dataclasses import replace
import pytest
from sqlalchemy import text
from app.application.checking_results import ResultReplayConflict
from tests.integration.test_checking_results_database import db,running,gate,deterministic,llm,terminal_attempt,counts
URL=os.environ.get('TEST_DATABASE_URL','')
if URL and not URL.rsplit('/',1)[-1].split('?',1)[0].endswith('_test'): raise RuntimeError('Phase 4.10 requires a disposable database ending in _test')
@pytest.mark.asyncio
async def test_phase410_production_persistence_vertical(db):
    store,ids=await running(db); await terminal_attempt(db,ids)
    drafts=(deterministic(ids),llm(ids)); first=await store.finalize(ids['run'],ids['row_version'],gate(),drafts)
    before=await counts(db[0],ids['run']); replay=await store.finalize(ids['run'],ids['row_version'],gate(),drafts)
    assert replay==first and before==await counts(db[0],ids['run'])==(2,1,1)
    with pytest.raises(ResultReplayConflict): await store.finalize(ids['run'],ids['row_version'],gate(),(deterministic(ids),llm(ids,finding=False)))
    assert before==await counts(db[0],ids['run'])
    async with db[0].connect() as c:
      run=(await c.execute(text('SELECT status FROM check_runs WHERE id=:r'),{'r':ids['run']})).one()
      results=(await c.execute(text('SELECT checker_type,result_status,score_suggested,needs_human_review,confidence FROM check_results WHERE check_run_id=:r ORDER BY assessment_item_id'),{'r':ids['run']})).all()
      findings=(await c.execute(text('SELECT finding_type,rubric_item_id,evidence FROM check_findings f JOIN check_results x ON x.id=f.check_result_id WHERE x.check_run_id=:r'),{'r':ids['run']})).all()
      events=(await c.execute(text('SELECT event_type,details FROM checker_events WHERE check_run_id=:r'),{'r':ids['run']})).all()
      models=(await c.execute(text('SELECT status,check_result_id,raw_output FROM model_runs WHERE check_run_id=:r'),{'r':ids['run']})).all()
    assert run.status=='completed_with_review_required' and len(results)==2
    assert {x.checker_type for x in results}=={'exact','llm_rubric'} and any(x.needs_human_review for x in results)
    assert len(findings)==1 and findings[0].rubric_item_id==ids['rubric_item'] and models[0].check_result_id is not None
    safe=json.dumps({'results':[tuple(x) for x in results],'findings':[(x.finding_type,x.evidence) for x in findings],'events':[tuple(x) for x in events], 'observability':first.__dict__},default=str)
    assert not any(x in safe for x in ('PRIVATE','raw_answer','solution','provider_output',str(ids['student']),str(ids['assignment'])))
