"""PostgreSQL acceptance proofs for the typed methodology foundation.

No module-level skip is used: all cases remain collectable without PostgreSQL.
Execution requires a migrated disposable database ending in ``_test``.
"""
import os
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

URL=os.environ.get("TEST_DATABASE_URL","")
if URL and not URL.rsplit("/",1)[-1].split("?",1)[0].endswith("_test"):
    raise RuntimeError("typed methodology tests require a database ending in _test")
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not URL,reason="TEST_DATABASE_URL is required")]

@pytest_asyncio.fixture
async def engine():
    value=create_async_engine(URL); yield value; await value.dispose()

@pytest_asyncio.fixture
async def api_client(engine,monkeypatch):
    os.environ["DATABASE_URL"]=URL
    from httpx import ASGITransport,AsyncClient
    from app.application.principal import Principal
    from app.main import app
    from app.presentation.auth_dependencies import require_principal
    import app.presentation.routes as routes
    test_factory=async_sessionmaker(engine,class_=AsyncSession,expire_on_commit=False)
    monkeypatch.setattr(routes,"async_session_factory",test_factory)
    user_id=uuid4()
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id,"user-a","User A",frozenset(),frozenset(),None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(require_principal,None)

@pytest_asyncio.fixture
async def seeded(engine):
    ids={name:uuid4() for name in ("actor","subject","grade","topic","task","v1","v2")}
    async with engine.begin() as c:
        current=await c.scalar(text("SELECT current_database()"))
        assert current.endswith("_test")
        await c.execute(text("TRUNCATE choice_option_rules,choice_scoring_policies,accepted_answer_options,choice_options,accepted_answers,audit_log,task_skill_links,task_versions,tasks,skills,subtopics,topics,grades,subjects CASCADE"))
        for sql in (
            "INSERT INTO subjects(id,code,name) VALUES (:subject,'typed','Typed')",
            "INSERT INTO grades(id,number,name) VALUES (:grade,7,'7')",
            "INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,'typed','Typed')",
            "INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:actor)",
            "INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:v1,:task,1,'S','problem','multiple_choice',50,'approved',:actor),(:v2,:task,2,'S2','problem','multiple_choice',50,'draft',:actor)",
        ): await c.execute(text(sql),ids)
    return ids

async def rejected(c,sql,params):
    with pytest.raises((IntegrityError,DBAPIError)):
        async with c.begin_nested():
            await c.execute(text(sql),params)
            await c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

async def add_option(c,version,key,index,content=None):
    return await c.scalar(text("INSERT INTO choice_options(task_version_id,option_key,content,order_index) VALUES (:v,:k,:c,:i) RETURNING id"),{"v":version,"k":key,"c":content or key,"i":index})

async def add_choice_answer(c,version,value="display"):
    return await c.scalar(text("INSERT INTO accepted_answers(task_version_id,answer_value,value_kind) VALUES (:v,:a,'choice_set') RETURNING id"),{"v":version,"a":value})

async def test_schema_columns_tables_named_policy_constraint_and_corrective_head(engine,seeded):
    async with engine.connect() as c:
        columns=set((await c.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_name='accepted_answers'"))).all())
        assert {"value_kind","canonical_text","canonical_decimal","absolute_tolerance","relative_tolerance","unit_code","normalization_policy_code","normalization_policy_version"}<=columns
        tables=set((await c.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"))).all())
        assert {"choice_options","accepted_answer_options","choice_scoring_policies","choice_option_rules"}<=tables
        assert await c.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='choice_option_rules' AND column_name='role')"))
        rows=(await c.execute(text("""SELECT constraint_row.conname,
                array_agg(attribute.attname ORDER BY constraint_column.ordinality) AS column_names
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation ON relation.oid=constraint_row.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
            CROSS JOIN LATERAL unnest(constraint_row.conkey)
                WITH ORDINALITY AS constraint_column(attnum, ordinality)
            JOIN pg_attribute AS attribute
              ON attribute.attrelid=constraint_row.conrelid
             AND attribute.attnum=constraint_column.attnum
            WHERE namespace.nspname=current_schema()
              AND relation.relname='choice_scoring_policies'
              AND constraint_row.contype='u'
            GROUP BY constraint_row.conname
            ORDER BY constraint_row.conname"""))).all()
        constraints={row.conname:list(row.column_names) for row in rows}
        assert sorted(name for name,columns in constraints.items() if columns==["task_version_id"]) == ["uq_choice_scoring_policy_version"]
        assert constraints["uq_choice_scoring_policy_version"] == ["task_version_id"]
        assert constraints["uq_choice_scoring_policy_id_version"] == ["id","task_version_id"]
        assert "choice_scoring_policies_task_version_id_key" not in constraints

    async with engine.begin() as c:
        policy=await c.scalar(text("INSERT INTO choice_scoring_policies(task_version_id,mode) VALUES (:v,'all_or_nothing') RETURNING id"),{"v":seeded["v2"]})
        assert policy is not None
        await rejected(c,"INSERT INTO choice_scoring_policies(task_version_id,mode) VALUES (:v,'all_or_nothing')",{"v":seeded["v2"]})
        assert await c.scalar(text("SELECT mode FROM choice_scoring_policies WHERE id=:id"),{"id":policy})=="all_or_nothing"

async def test_legacy_bytes_and_backfill_are_unchanged(engine,seeded):
    raw="  A\r\nB  "; rule="do NOT execute (.*)"
    async with engine.begin() as c:
        row=(await c.execute(text("INSERT INTO accepted_answers(task_version_id,answer_value,tolerance,unit,normalization_rule) VALUES (:v,:a,.125,:u,:r) RETURNING answer_value,tolerance::text,unit,normalization_rule,value_kind"),{"v":seeded["v2"],"a":raw,"u":" kg ","r":rule})).one()
        assert tuple(row)==(raw,"0.125"," kg ",rule,"legacy_untyped")

async def test_decimal_round_trip_is_exact_and_constraints_reject_bad_shapes(engine,seeded):
    async with engine.begin() as c:
        value=await c.scalar(text("INSERT INTO accepted_answers(task_version_id,answer_value,value_kind,canonical_decimal,absolute_tolerance,relative_tolerance,normalization_policy_code,normalization_policy_version) VALUES (:v,'x','decimal',.000000000000000000123,0,.000001,'decimal_v1',1) RETURNING canonical_decimal::text"),{"v":seeded["v2"]})
        assert value=="0.000000000000000000123"
        base="INSERT INTO accepted_answers(task_version_id,answer_value,value_kind,canonical_decimal,absolute_tolerance,relative_tolerance,normalization_policy_code,normalization_policy_version) VALUES (:v,'bad','decimal',1,:a,:r,'decimal_v1',1)"
        await rejected(c,base,{"v":seeded["v2"],"a":-1,"r":0}); await rejected(c,base,{"v":seeded["v2"],"a":0,"r":-1})
        await rejected(c,"INSERT INTO accepted_answers(task_version_id,answer_value,value_kind,canonical_text,normalization_policy_code,normalization_policy_version) VALUES (:v,'bad','decimal','extra','decimal_v1',1)",{"v":seeded["v2"]})
        await rejected(c,"INSERT INTO accepted_answers(task_version_id,answer_value,value_kind,canonical_text,normalization_policy_code) VALUES (:v,'bad','text','x','exact_text_v1')",{"v":seeded["v2"]})

async def test_choice_catalogue_uniqueness_and_nonblank_constraints(engine,seeded):
    async with engine.begin() as c:
        await add_option(c,seeded["v2"],"a",0,"A")
        await rejected(c,"INSERT INTO choice_options(task_version_id,option_key,content,order_index) VALUES (:v,'a','dup',1)",{"v":seeded["v2"]})
        await rejected(c,"INSERT INTO choice_options(task_version_id,option_key,content,order_index) VALUES (:v,'b','dup',0)",{"v":seeded["v2"]})
        await rejected(c,"INSERT INTO choice_options(task_version_id,option_key,content,order_index) VALUES (:v,'','x',2)",{"v":seeded["v2"]})
        await rejected(c,"INSERT INTO choice_options(task_version_id,option_key,content,order_index) VALUES (:v,'c','   ',2)",{"v":seeded["v2"]})

async def test_membership_is_relational_unique_nonempty_and_cross_version_safe(engine,seeded):
    async with engine.begin() as c:
        a=await add_option(c,seeded["v2"],"a",0); foreign=await add_option(c,seeded["v1"],"f",0); answer=await add_choice_answer(c,seeded["v2"])
        await c.execute(text("INSERT INTO accepted_answer_options(accepted_answer_id,choice_option_id,task_version_id) VALUES (:a,:o,:v)"),{"a":answer,"o":a,"v":seeded["v2"]})
        await rejected(c,"INSERT INTO accepted_answer_options(accepted_answer_id,choice_option_id,task_version_id) VALUES (:a,:o,:v)",{"a":answer,"o":a,"v":seeded["v2"]})
        await rejected(c,"INSERT INTO accepted_answer_options(accepted_answer_id,choice_option_id,task_version_id) VALUES (:a,:o,:v)",{"a":answer,"o":foreign,"v":seeded["v2"]})
    with pytest.raises((IntegrityError,DBAPIError)):
        async with engine.begin() as c:
            await add_choice_answer(c,seeded["v2"],"empty")

def legacy_payload():
    return {"expected_solution":None,"rubric":None,"accepted_answers":[{"answer_value":" old ","tolerance":None,"unit":" kg ","normalization_rule":"opaque"}],"typical_errors":[],"hints":[]}

def choice_payload(weighted=True):
    rules=[{"option_key":"a","role":"correct","weight":"1.000000"},{"option_key":"x","role":"distractor","weight":"-0.250000"}] if weighted else []
    return {"expected_solution":None,"rubric":None,"choice_options":[{"option_key":"a","content":"A","order_index":0},{"option_key":"x","content":"X","order_index":1}],"choice_scoring_policy":{"mode":"per_option" if weighted else "all_or_nothing","policy_version":1,"option_rules":rules},"accepted_answers":[{"answer_value":"display","value_kind":"choice_set","option_keys":["a"]}],"typical_errors":[],"hints":[]}

async def test_api_legacy_put_and_typed_decimal_plain_round_trip(api_client,engine,seeded):
    legacy=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=legacy_payload())
    assert legacy.status_code==200 and legacy.json()["accepted_answers"][0]["value_kind"]=="legacy_untyped"
    async with engine.begin() as c: await c.execute(text("UPDATE task_versions SET answer_format='number' WHERE id=:v2"),seeded)
    payload=legacy_payload(); payload["accepted_answers"]=[{"answer_value":"1e-21","value_kind":"decimal","canonical_decimal":"1e-21","absolute_tolerance":"0","relative_tolerance":"0","normalization_policy_code":"decimal_v1","normalization_policy_version":1}]
    typed=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=payload)
    assert typed.status_code==200 and typed.json()["accepted_answers"][0]["canonical_decimal"]=="0.000000000000000000001"

async def test_api_choice_put_resolves_keys_to_ids_and_reads_or_alternatives(api_client,seeded):
    data=choice_payload(); data["accepted_answers"].append({"answer_value":"alternative","value_kind":"choice_set","option_keys":["x"]})
    # Adjust roles because both alternatives are accepted correctness options.
    data["choice_scoring_policy"]["option_rules"]=[{"option_key":"a","role":"correct","weight":"0.400000"},{"option_key":"x","role":"correct","weight":"0.600000"}]
    response=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=data)
    assert response.status_code==200, response.text
    body=response.json(); assert len(body["choice_options"])==2 and len(body["accepted_answers"])==2
    ids={x["id"] for x in body["choice_options"]}; assert all(set(answer["option_ids"])<=ids for answer in body["accepted_answers"])

async def test_api_unknown_option_is_atomic_and_statuses_are_immutable(api_client,engine,seeded):
    good=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=choice_payload(False)); assert good.status_code==200
    bad=choice_payload(False); bad["accepted_answers"][0]["option_keys"]=["missing"]
    rejected_response=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=bad); assert rejected_response.status_code==422
    async with engine.connect() as c: assert await c.scalar(text("SELECT count(*) FROM choice_options WHERE task_version_id=:v2"),seeded)==2
    async with engine.begin() as c: await c.execute(text("UPDATE task_versions SET status='review' WHERE id=:v2"),seeded)
    locked=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=legacy_payload()); assert locked.status_code==409

async def test_weighted_policy_application_rejects_sum_roles_penalties_and_single_choice(api_client,engine,seeded):
    valid=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=choice_payload()); assert valid.status_code==200,valid.text
    for mutate in ("sum","role","penalty"):
        data=choice_payload()
        if mutate=="sum": data["choice_scoring_policy"]["option_rules"][0]["weight"]=".9"
        if mutate=="role": data["choice_scoring_policy"]["option_rules"][1].update(role="correct",weight=".1")
        if mutate=="penalty": data["choice_scoring_policy"]["option_rules"][1]["weight"]=".1"
        response=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=data); assert response.status_code==422
    async with engine.begin() as c: await c.execute(text("UPDATE task_versions SET answer_format='single_choice' WHERE id=:v2"),seeded)
    response=await api_client.put(f"/api/content-bank/task-versions/{seeded['v2']}/methodology",json=choice_payload()); assert response.status_code==422

async def test_database_weighted_integrity_rejects_unknown_cross_version_and_invalid_roles(engine,seeded):
    async with engine.begin() as c:
        a=await add_option(c,seeded["v2"],"a",0); x=await add_option(c,seeded["v2"],"x",1); accepted=await add_choice_answer(c,seeded["v2"])
        await c.execute(text("INSERT INTO accepted_answer_options VALUES (:a,:o,:v)"),{"a":accepted,"o":a,"v":seeded["v2"]})
        policy=await c.scalar(text("INSERT INTO choice_scoring_policies(task_version_id,mode) VALUES (:v,'per_option') RETURNING id"),{"v":seeded["v2"]})
        await c.execute(text("INSERT INTO choice_option_rules(policy_id,choice_option_id,task_version_id,role,weight) VALUES (:p,:a,:v,'correct',1),(:p,:x,:v,'distractor',-.2)"),{"p":policy,"a":a,"x":x,"v":seeded["v2"]})
        await c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    async with engine.begin() as c:
        await rejected(c,"UPDATE choice_option_rules SET role='correct',weight=.1 WHERE task_version_id=:v2 AND role='distractor'",seeded)

async def test_clone_rekeys_options_memberships_rules_and_preserves_typed_and_legacy(engine,seeded):
    from app.application.content_bank import ActorContext
    from app.infrastructure.repository import SQLAlchemyContentBankRepository
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM task_versions WHERE id=:v2"),seeded)
        a=await add_option(c,seeded["v1"],"a",0); x=await add_option(c,seeded["v1"],"x",1); accepted=await add_choice_answer(c,seeded["v1"]); await c.execute(text("INSERT INTO accepted_answer_options VALUES (:a,:o,:v)"),{"a":accepted,"o":a,"v":seeded["v1"]})
        await c.execute(text("INSERT INTO accepted_answers(task_version_id,answer_value,tolerance,unit,normalization_rule) VALUES (:v,' legacy ',.1,' kg ','opaque')"),{"v":seeded["v1"]})
        p=await c.scalar(text("INSERT INTO choice_scoring_policies(task_version_id,mode) VALUES (:v,'per_option') RETURNING id"),{"v":seeded["v1"]}); await c.execute(text("INSERT INTO choice_option_rules(policy_id,choice_option_id,task_version_id,role,weight) VALUES (:p,:a,:v,'correct',1),(:p,:x,:v,'distractor',-.2)"),{"p":p,"a":a,"x":x,"v":seeded["v1"]})
    async with AsyncSession(engine) as s:
        async with s.begin(): clone=await SQLAlchemyContentBankRepository(s).clone_version(seeded["task"],1,ActorContext(seeded["actor"]))
    async with engine.connect() as c:
        source=dict((await c.execute(text("SELECT option_key,id FROM choice_options WHERE task_version_id=:v1"),seeded)).all()); target=dict((await c.execute(text("SELECT option_key,id FROM choice_options WHERE task_version_id=:v"),{"v":clone.task_version_id})).all())
        assert source.keys()==target.keys() and all(source[k]!=target[k] for k in source)
        assert await c.scalar(text("SELECT count(*) FROM accepted_answer_options ao JOIN choice_options o ON o.id=ao.choice_option_id WHERE ao.task_version_id=:v AND o.task_version_id=:v"),{"v":clone.task_version_id})==1
        assert await c.scalar(text("SELECT count(*) FROM choice_option_rules r JOIN choice_options o ON o.id=r.choice_option_id WHERE r.task_version_id=:v AND o.task_version_id=:v"),{"v":clone.task_version_id})==2
        legacy=(await c.execute(text("SELECT answer_value,tolerance::text,unit,normalization_rule,value_kind FROM accepted_answers WHERE task_version_id=:v AND value_kind='legacy_untyped'"),{"v":clone.task_version_id})).one(); assert tuple(legacy)==(" legacy ","0.1"," kg ","opaque","legacy_untyped")
        assert await c.scalar(text("SELECT status::text FROM task_versions WHERE id=:v1"),seeded)=="approved"
