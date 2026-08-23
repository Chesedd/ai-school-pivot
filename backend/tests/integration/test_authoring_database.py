"""PostgreSQL gate for Content Bank-owned Phase 4A.1 persistence."""
import asyncio, os
from decimal import Decimal
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.authoring import *
from app.infrastructure.authoring_models import AuthoringProviderAttempt
from app.infrastructure.authoring_repository import AuthoringRepository

URL=os.environ.get("TEST_DATABASE_URL","")
if URL and not URL.rsplit("/",1)[-1].split("?",1)[0].endswith("_test"): raise RuntimeError("authoring tests require *_test database")
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not URL,reason="TEST_DATABASE_URL is required")]

def req(): return AuthoringRequestV1(schema_version="authoring-request.v1",task_goal="One task",subject="math",grade="g7",topic="fractions",task_type="test",answer_format="single_choice",difficulty=50,skills=("reasoning",),policy_version="authoring-v1")
def execution(key="logical-key",fingerprint=None,provider="fake",model="probe"):
    spec=PromptSpecification("contract-probe",AuthoringRole.GENERATOR,"1.0.0","probe-v1","a"*64,"authoring-provider-probe.v1","authoring-v1")
    return ExecutionRequest(AuthoringRole.GENERATOR,provider,model,{"temperature":"0"},spec,fingerprint or req().fingerprint,1000,"correlation",key,RetryPolicy())

@pytest_asyncio.fixture
async def context():
    engine=create_async_engine(URL); factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as c: await c.execute(text("TRUNCATE authoring_provider_attempts, authoring_sessions CASCADE"))
    yield engine,factory
    await engine.dispose()

async def make_session(factory):
    async with factory() as db,db.begin(): return (await AuthoringRepository(db).create_session(uuid4(),req(),FrozenCatalogContext("math","g7","fractions",None,("reasoning",)))).id

async def test_session_roundtrip_attempt_lifecycle_usage_cost_and_no_tasks(context):
    engine,factory=context
    async with factory() as db:
        initial_task_count=await db.scalar(text("SELECT count(*) FROM tasks"))
        initial_version_count=await db.scalar(text("SELECT count(*) FROM task_versions"))
    sid=await make_session(factory)
    async with factory() as db,db.begin():
        repo=AuthoringRepository(db); row,created=await repo.create_attempt(sid,execution()); assert created and row.status=="pending"; aid=row.id
    async with factory() as db,db.begin(): assert await AuthoringRepository(db).claim(aid)
    result=ProviderResult({"schema_version":"authoring-provider-probe.v1","acknowledged":True},"fake-request",Usage(3,4,1),decimal_cost("1.23000000","USD","price-v1","catalog"),9)
    async with factory() as db,db.begin(): assert await AuthoringRepository(db).finalize_success(aid,result)
    async with factory() as db:
        row=await db.get(AuthoringProviderAttempt,aid); session=(await db.execute(text("SELECT frozen_request,frozen_allowlist FROM authoring_sessions WHERE id=:id"),{"id":sid})).one()
        assert row.status=="succeeded" and row.cost_amount==Decimal("1.23000000") and (row.input_tokens,row.output_tokens,row.cached_tokens)==(3,4,1) and row.response_hash==result.response_hash
        assert session.frozen_request["task_goal"]=="One task" and session.frozen_allowlist["skills"]==["reasoning"]
        assert await db.scalar(text("SELECT count(*) FROM tasks"))==initial_task_count
        assert await db.scalar(text("SELECT count(*) FROM task_versions"))==initial_version_count
        assert await db.scalar(text("SELECT count(*) FROM authoring_sessions WHERE id=:id"),{"id":sid})==1
        assert await db.scalar(text("SELECT count(*) FROM authoring_provider_attempts WHERE session_id=:id AND status='succeeded'"),{"id":sid})==1
    async with factory() as db,db.begin(): assert not await AuthoringRepository(db).claim(aid) and not await AuthoringRepository(db).finalize_failure(aid,FailureCode.TIMEOUT)

async def test_idempotency_conflict_retry_numbering_and_failure_states(context):
    _,factory=context; sid=await make_session(factory)
    async with factory() as db,db.begin():
        repo=AuthoringRepository(db); first,_=await repo.create_attempt(sid,execution()); same,created=await repo.create_attempt(sid,execution()); assert same.id==first.id and not created
        with pytest.raises(AuthoringConflict): await repo.create_attempt(sid,execution(fingerprint="b"*64))
        with pytest.raises(AuthoringConflict): await repo.create_attempt(sid,execution(provider="anthropic"))
        with pytest.raises(AuthoringConflict): await repo.create_attempt(sid,execution(model="other-model"))
    for number,code in enumerate((FailureCode.TIMEOUT,FailureCode.AUTHENTICATION),start=1):
        key="logical-key" if number==1 else "logical-key-retry-2"
        async with factory() as db,db.begin():
            repo=AuthoringRepository(db); row,_=await repo.create_attempt(sid,execution(key)); assert row.attempt_number==number; await repo.claim(row.id); await repo.finalize_failure(row.id,code)
    async with factory() as db:
        rows=(await db.scalars(select(AuthoringProviderAttempt).order_by(AuthoringProviderAttempt.attempt_number))).all()
        assert [r.status for r in rows]==["failed_retryable","failed_terminal"]

async def test_concurrent_same_key_and_claim_are_single_identity(context):
    _,factory=context; sid=await make_session(factory)
    async def create():
        async with factory() as db,db.begin():
            try: return (await AuthoringRepository(db).create_attempt(sid,execution()))[0].id
            except AuthoringConflict:
                row=await db.scalar(select(AuthoringProviderAttempt).where(AuthoringProviderAttempt.session_id==sid,AuthoringProviderAttempt.idempotency_key=="logical-key")); return row.id
    ids=await asyncio.gather(create(),create()); assert ids[0]==ids[1]
    async def claim():
        async with factory() as db,db.begin(): return await AuthoringRepository(db).claim(ids[0])
    assert sorted(await asyncio.gather(claim(),claim()))==[False,True]

async def test_foreign_key_prevents_orphan(context):
    _,factory=context
    async with factory() as db,db.begin():
        with pytest.raises(Exception):
            await AuthoringRepository(db).create_attempt(uuid4(),execution())
