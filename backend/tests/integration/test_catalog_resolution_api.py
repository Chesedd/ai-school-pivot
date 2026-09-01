"""J1F acceptance through real PostgreSQL, Alembic-head schema, and HTTP transactions."""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
HAS_TEST_DATABASE = bool(DATABASE_URL)
if DATABASE_URL and not DATABASE_URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith(
    "_test"
):
    raise RuntimeError("J1F cleanup is allowed only for a database ending in _test")
os.environ["DATABASE_URL"] = (
    DATABASE_URL or "postgresql+asyncpg://localhost/catalog_collection_only"
)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.application.principal import Principal  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402
from tests.integration.auth_helpers import (  # noqa: E402
    admin_principal,
    clear_principal_override,
    override_principal,
    student_principal,
    teacher_principal,
)

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not HAS_TEST_DATABASE, reason="TEST_DATABASE_URL is required"),
]


async def clean():
    async with async_session_factory() as session, session.begin():
        assert (await session.scalar(text("select current_database()"))).endswith(
            "_test"
        )
        await session.execute(
            text("TRUNCATE skills, subtopics, topics, grades, subjects, users CASCADE")
        )


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def isolated_database():
    await clean()
    clear_principal_override(app)
    yield
    clear_principal_override(app)
    await clean()


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def close_shared_engine():
    yield
    await engine.dispose()


async def user(name: str) -> UUID:
    async with async_session_factory() as session, session.begin():
        return await session.scalar(
            text(
                "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES (:n,:n,:n,'hash') RETURNING id"
            ),
            {"n": f"{name}-{uuid4()}"},
        )


async def seed(table: str, columns: str, values: str, **params) -> UUID:
    async with async_session_factory() as session, session.begin():
        return await session.scalar(
            text(f"INSERT INTO {table} ({columns}) VALUES ({values}) RETURNING id"),
            params,
        )


async def request(method: str, path: str, payload=None, origin=None):
    headers = {"Origin": origin} if origin else None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, json=payload, headers=headers)


async def provisional_subject(name: str, proposer: UUID) -> UUID:
    return await seed(
        "subjects",
        "code,name,normalized_name,status,proposed_by",
        ":c,:n,:nn,'provisional',:p",
        c=f"p-{uuid4()}",
        n=name,
        nn=name.casefold(),
        p=proposer,
    )


async def active_subject(name: str) -> UUID:
    return await seed(
        "subjects",
        "code,name,normalized_name,status",
        ":c,:n,:nn,'active'",
        c=f"a-{uuid4()}",
        n=name,
        nn=name.casefold(),
    )


async def test_real_http_authorization_and_trusted_origin_matrix():
    admin, teacher = await user("admin"), await user("teacher")
    source, target, rejected = (
        await provisional_subject("Confirm", teacher),
        await active_subject("Canonical"),
        await provisional_subject("Reject", teacher),
    )
    override_principal(app, admin_principal(admin))
    assert (await request("GET", "/api/catalog/proposals")).status_code == 200
    evil = await request(
        "POST",
        f"/api/catalog/proposals/subject/{source}/confirm",
        {},
        "https://evil.example",
    )
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "foreign_origin"
    for principal in (
        teacher_principal(teacher),
        student_principal(uuid4(), uuid4()),
        Principal(uuid4(), "none", "None", frozenset(), frozenset(), None),
    ):
        override_principal(app, principal)
        assert (await request("GET", "/api/catalog/proposals")).status_code == 403
        for action, body in (
            ("confirm", {}),
            ("merge", {"target_id": str(target), "reason": "x"}),
            ("reject", {"reason": "x"}),
        ):
            assert (
                await request(
                    "POST", f"/api/catalog/proposals/subject/{source}/{action}", body
                )
            ).status_code == 403
    clear_principal_override(app)
    assert (await request("GET", "/api/catalog/proposals")).status_code == 401
    override_principal(app, admin_principal(admin))
    assert (
        await request("POST", f"/api/catalog/proposals/subject/{source}/confirm", {})
    ).status_code == 200
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/subject/{rejected}/reject",
            {"reason": "unused"},
        )
    ).status_code == 200
    merge_source = await provisional_subject("Merge", teacher)
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/subject/{merge_source}/merge",
            {"target_id": str(target), "reason": "duplicate"},
        )
    ).status_code == 200


async def test_proposal_list_filter_paging_and_lifecycle_exclusion():
    admin, teacher = await user("admin"), await user("teacher")
    subject = await provisional_subject("Visible subject", teacher)
    grade = await seed(
        "grades",
        "number,name,normalized_name,status,proposed_by",
        "7,'Seven','seven','provisional',:p",
        p=teacher,
    )
    await active_subject("Active")
    deprecated = await provisional_subject("Deprecated", teacher)
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE subjects SET status='deprecated',resolved_by=:a,resolved_at=now(),resolution_reason='no' WHERE id=:id"
            ),
            {"a": admin, "id": deprecated},
        )
    override_principal(app, admin_principal(admin))
    all_rows = (
        await request("GET", "/api/catalog/proposals?offset=0&limit=100")
    ).json()
    assert {x["id"] for x in all_rows} == {str(subject), str(grade)}
    assert all(
        x["status"] == "provisional" and x["proposed_by"] == str(teacher)
        for x in all_rows
    )
    filtered = (
        await request("GET", "/api/catalog/proposals?kind=grade&limit=1")
    ).json()
    assert (
        len(filtered) == 1
        and filtered[0]["id"] == str(grade)
        and filtered[0]["number"] == 7
    )
    assert (await request("GET", "/api/catalog/proposals?offset=1&limit=1")).json()[0][
        "id"
    ] in {str(subject), str(grade)}


async def test_confirm_preserves_identity_and_attribution_and_becomes_visible():
    admin, teacher = await user("admin"), await user("teacher")
    subject = await provisional_subject("Geometry", teacher)
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST", f"/api/catalog/proposals/subject/{subject}/confirm", {}
    )
    assert (
        response.status_code == 200
        and response.json()["id"] == str(subject)
        and response.json()["status"] == "active"
    )
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status,proposed_by,resolved_by,resolved_at,replacement_id FROM subjects WHERE id=:id"
                ),
                {"id": subject},
            )
        ).one()
    assert (
        row.status == "active"
        and row.proposed_by == teacher
        and row.resolved_by == admin
        and row.resolved_at is not None
        and row.replacement_id is None
    )
    visible = await request("GET", "/api/content-bank/catalog/subjects")
    assert str(subject) in {x["id"] for x in visible.json()["items"]}


async def test_parent_first_full_hierarchy_and_effective_parent_canonicalization():
    admin, teacher = await user("admin"), await user("teacher")
    subject = await provisional_subject("Subject A", teacher)
    grade = await seed(
        "grades",
        "number,name,normalized_name,status,proposed_by",
        "8,'Eight','eight','provisional',:p",
        p=teacher,
    )
    topic = await seed(
        "topics",
        "subject_id,grade_id,code,name,normalized_name,status,proposed_by",
        ":s,:g,'t','Topic','topic','provisional',:p",
        s=subject,
        g=grade,
        p=teacher,
    )
    subtopic = await seed(
        "subtopics",
        "topic_id,code,name,normalized_name,status,proposed_by",
        ":t,'st','Subtopic','subtopic','provisional',:p",
        t=topic,
        p=teacher,
    )
    skill = await seed(
        "skills",
        "subtopic_id,code,name,normalized_name,status,proposed_by",
        ":st,'sk','Skill','skill','provisional',:p",
        st=subtopic,
        p=teacher,
    )
    override_principal(app, admin_principal(admin))
    for kind, value in (("topic", topic), ("subtopic", subtopic), ("skill", skill)):
        blocked = await request(
            "POST", f"/api/catalog/proposals/{kind}/{value}/confirm", {}
        )
        assert (
            blocked.status_code == 409
            and blocked.json()["error"]["code"] == "catalog_parent_unresolved"
        )
    for kind, value in (
        ("subject", subject),
        ("grade", grade),
        ("topic", topic),
        ("subtopic", subtopic),
        ("skill", skill),
    ):
        assert (
            await request("POST", f"/api/catalog/proposals/{kind}/{value}/confirm", {})
        ).status_code == 200
    async with async_session_factory() as session:
        for table, value in (
            ("subjects", subject),
            ("grades", grade),
            ("topics", topic),
            ("subtopics", subtopic),
            ("skills", skill),
        ):
            row = (
                await session.execute(
                    text(
                        f"SELECT status,proposed_by,resolved_by,resolved_at FROM {table} WHERE id=:id"
                    ),
                    {"id": value},
                )
            ).one()
            assert (
                row.status == "active"
                and row.proposed_by == teacher
                and row.resolved_by == admin
                and row.resolved_at is not None
            )


async def test_parent_merge_then_child_confirm_uses_active_replacement():
    admin, teacher = await user("admin"), await user("teacher")
    source, target = (
        await provisional_subject("Alias", teacher),
        await active_subject("Canonical"),
    )
    grade = await seed(
        "grades", "number,name,normalized_name,status", "9,'Nine','nine','active'"
    )
    topic = await seed(
        "topics",
        "subject_id,grade_id,code,name,normalized_name,status,proposed_by",
        ":s,:g,'t','Child','child','provisional',:p",
        s=source,
        g=grade,
        p=teacher,
    )
    override_principal(app, admin_principal(admin))
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/subject/{source}/merge",
            {"target_id": str(target), "reason": "same"},
        )
    ).status_code == 200
    assert (
        await request("POST", f"/api/catalog/proposals/topic/{topic}/confirm", {})
    ).status_code == 200
    async with async_session_factory() as session:
        source_row = (
            await session.execute(
                text("SELECT status,replacement_id FROM subjects WHERE id=:id"),
                {"id": source},
            )
        ).one()
        topic_row = (
            await session.execute(
                text("SELECT status,subject_id FROM topics WHERE id=:id"), {"id": topic}
            )
        ).one()
    assert source_row == ("deprecated", target) and topic_row == ("active", target)


async def test_merge_alias_reuse_rejected_reproposal_and_target_validation():
    admin, teacher = await user("admin"), await user("teacher")
    source, target = (
        await provisional_subject("Old label", teacher),
        await active_subject("Canonical"),
    )
    override_principal(app, admin_principal(admin))
    for target_id, expected in (
        (source, "catalog_merge_target_invalid"),
        (uuid4(), "catalog_merge_target_not_found"),
    ):
        response = await request(
            "POST",
            f"/api/catalog/proposals/subject/{source}/merge",
            {"target_id": str(target_id), "reason": "duplicate"},
        )
        assert response.json()["error"]["code"] == expected
    merged = await request(
        "POST",
        f"/api/catalog/proposals/subject/{source}/merge",
        {"target_id": str(target), "reason": "duplicate"},
    )
    assert merged.status_code == 200
    override_principal(app, teacher_principal(teacher))
    alias = await request(
        "POST", "/api/catalog/proposals", {"kind": "subject", "name": " OLD LABEL "}
    )
    assert (
        alias.status_code == 200
        and alias.json()["outcome"] == "existing_active"
        and alias.json()["id"] == str(target)
    )
    rejected = await provisional_subject("Try again", teacher)
    override_principal(app, admin_principal(admin))
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/subject/{rejected}/reject",
            {"reason": "bad"},
        )
    ).status_code == 200
    override_principal(app, teacher_principal(teacher))
    reproposed = await request(
        "POST", "/api/catalog/proposals", {"kind": "subject", "name": "Try again"}
    )
    assert (
        reproposed.status_code == 201
        and reproposed.json()["outcome"] == "created_provisional"
        and reproposed.json()["id"] != str(rejected)
    )
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM subjects WHERE normalized_name='old label'")
            )
            == 1
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM subjects WHERE normalized_name='try again'")
            )
            == 2
        )


async def test_reject_in_use_descendant_and_concurrent_terminal_actions_are_atomic():
    admin, teacher = await user("admin"), await user("teacher")
    parent = await provisional_subject("Parent", teacher)
    grade = await seed(
        "grades", "number,name,normalized_name,status", "10,'Ten','ten','active'"
    )
    await seed(
        "topics",
        "subject_id,grade_id,code,name,normalized_name,status,proposed_by",
        ":s,:g,'child','Child','child','provisional',:p",
        s=parent,
        g=grade,
        p=teacher,
    )
    override_principal(app, admin_principal(admin))
    blocked = await request(
        "POST", f"/api/catalog/proposals/subject/{parent}/reject", {"reason": "unused"}
    )
    assert (
        blocked.status_code == 409
        and blocked.json()["error"]["code"] == "catalog_proposal_in_use"
    )
    raced = await provisional_subject("Race", teacher)
    confirm, reject = await asyncio.gather(
        request("POST", f"/api/catalog/proposals/subject/{raced}/confirm", {}),
        request(
            "POST", f"/api/catalog/proposals/subject/{raced}/reject", {"reason": "race"}
        ),
    )
    outcomes = sorted((confirm.status_code, reject.status_code))
    assert outcomes == [200, 409]
    loser = confirm if confirm.status_code == 409 else reject
    assert loser.json()["error"]["code"] == "catalog_proposal_already_resolved"
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status,resolved_by,resolved_at,replacement_id FROM subjects WHERE id=:id"
                ),
                {"id": raced},
            )
        ).one()
    assert (
        row.status in {"active", "deprecated"}
        and row.resolved_by == admin
        and row.resolved_at is not None
        and row.replacement_id is None
    )


async def active_chain():
    """Seed a complete active hierarchy suitable for Content Bank rows."""
    subject = await active_subject(f"Subject {uuid4()}")
    async with async_session_factory() as session:
        used = set(
            (
                await session.execute(
                    text(
                        "SELECT number FROM grades WHERE status IN ('active','provisional')"
                    )
                )
            ).scalars()
        )
    number = next(value for value in range(1, 12) if value not in used)
    grade = await seed(
        "grades",
        "number,name,normalized_name,status",
        ":number,:n,:nn,'active'",
        number=number,
        n=f"Grade {uuid4()}",
        nn=str(uuid4()),
    )
    topic = await seed(
        "topics",
        "subject_id,grade_id,code,name,normalized_name,status",
        ":s,:g,:c,:n,:nn,'active'",
        s=subject,
        g=grade,
        c=str(uuid4()),
        n=f"Topic {uuid4()}",
        nn=str(uuid4()),
    )
    subtopic = await seed(
        "subtopics",
        "topic_id,code,name,normalized_name,status",
        ":t,:c,:n,:nn,'active'",
        t=topic,
        c=str(uuid4()),
        n=f"Subtopic {uuid4()}",
        nn=str(uuid4()),
    )
    skill = await seed(
        "skills",
        "subtopic_id,code,name,normalized_name,status",
        ":st,:c,:n,:nn,'active'",
        st=subtopic,
        c=str(uuid4()),
        n=f"Skill {uuid4()}",
        nn=str(uuid4()),
    )
    return {
        "subject": subject,
        "grade": grade,
        "topic": topic,
        "subtopic": subtopic,
        "skill": skill,
    }


async def proposal(kind: str, parent: dict, proposer: UUID, name=None):
    name = name or f"Proposal {uuid4()}"
    specs = {
        "topic": (
            "topics",
            "subject_id,grade_id,code,name,normalized_name,status,proposed_by",
            ":subject,:grade,:code,:name,:normalized,'provisional',:proposer",
        ),
        "subtopic": (
            "subtopics",
            "topic_id,code,name,normalized_name,status,proposed_by",
            ":topic,:code,:name,:normalized,'provisional',:proposer",
        ),
        "skill": (
            "skills",
            "subtopic_id,code,name,normalized_name,status,proposed_by",
            ":subtopic,:code,:name,:normalized,'provisional',:proposer",
        ),
    }
    table, columns, values = specs[kind]
    return await seed(
        table,
        columns,
        values,
        **parent,
        code=str(uuid4()),
        name=name,
        normalized=name.casefold(),
        proposer=proposer,
    )


async def content_row(
    chain: dict,
    actor: UUID,
    status="draft",
    *,
    topic=None,
    subtopic=None,
    skill=None,
    subject=None,
    add_primary_skill=True,
):
    task = await seed(
        "tasks",
        "subject_id,grade_id,topic_id,subtopic_id,created_by",
        ":s,:g,:t,:st,:a",
        s=subject or chain["subject"],
        g=chain["grade"],
        t=topic or chain["topic"],
        st=subtopic if subtopic is not None else chain["subtopic"],
        a=actor,
    )
    version = await seed(
        "task_versions",
        "task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by",
        ":t,1,'Complete','Statement','open_question','short_text',50,:status,:a",
        t=task,
        status=status,
        a=actor,
    )
    # Complete methodology ensures approval reaches the catalog gate.
    await seed(
        "expected_solutions",
        "task_version_id,solution_text,final_answer,solution_steps_json",
        ":v,'Solution','Answer','[]'::jsonb",
        v=version,
    )
    rubric = await seed(
        "rubrics", "task_version_id,max_score,grading_mode", ":v,1,'points'", v=version
    )
    await seed(
        "rubric_items",
        "rubric_id,criterion,max_points,required,order_index",
        ":r,'Criterion',1,true,0",
        r=rubric,
    )
    if add_primary_skill:
        await skill_link(version, skill or chain["skill"])
    return task, version


async def provisional_topic_branch(chain: dict, proposer: UUID):
    """Create a coherent provisional Topic -> Subtopic -> Skill branch."""
    topic = await proposal("topic", chain, proposer)
    branch = {**chain, "topic": topic}
    subtopic = await proposal("subtopic", branch, proposer)
    branch["subtopic"] = subtopic
    skill = await proposal("skill", branch, proposer)
    return topic, subtopic, skill


async def skill_link(version: UUID, skill: UUID, weight="1", primary=True):
    return await seed(
        "task_skill_links",
        "task_version_id,skill_id,weight,is_primary",
        ":v,:s,:w,:p",
        v=version,
        s=skill,
        w=weight,
        p=primary,
    )


async def typical_error(skill: UUID, code="error"):
    return await seed(
        "typical_errors",
        "skill_id,code,title,description,severity",
        ":s,:c,'Title','Description','medium'",
        s=skill,
        c=code,
    )


async def link_error(version: UUID, error: UUID):
    return await seed(
        "task_error_links",
        "task_version_id,typical_error_id",
        ":v,:e",
        v=version,
        e=error,
    )


async def assert_unresolved(table: str, value: UUID):
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    f"SELECT status,resolved_by,resolved_at,replacement_id FROM {table} WHERE id=:id"
                ),
                {"id": value},
            )
        ).one()
    assert row == ("provisional", None, None, None)


async def assert_approval_atomic(version: UUID):
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status,approved_at,approved_by FROM task_versions WHERE id=:v"
                ),
                {"v": version},
            )
        ).one()
        audits = await session.scalar(
            text(
                "SELECT count(*) FROM audit_log WHERE task_version_id=:v AND action='version_approved'"
            ),
            {"v": version},
        )
    assert row == ("review", None, None) and audits == 0


@pytest.mark.parametrize("target_state", ["provisional", "deprecated"])
async def test_merge_rejects_non_active_target_without_mutation(target_state):
    admin, teacher = await user("admin"), await user("teacher")
    source = await provisional_subject(f"Source {uuid4()}", teacher)
    target = await provisional_subject(f"Target {uuid4()}", teacher)
    if target_state == "deprecated":
        async with async_session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE subjects SET status='deprecated',resolved_by=:a,resolved_at=now() WHERE id=:id"
                ),
                {"a": admin, "id": target},
            )
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST",
        f"/api/catalog/proposals/subject/{source}/merge",
        {"target_id": str(target), "reason": "invalid target"},
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_merge_target_invalid"
    )
    await assert_unresolved("subjects", source)


@pytest.mark.parametrize(
    "kind,table", [("topic", "topics"), ("subtopic", "subtopics"), ("skill", "skills")]
)
async def test_merge_rejects_hierarchy_mismatch_without_mutation(kind, table):
    admin, teacher = await user("admin"), await user("teacher")
    left, right = await active_chain(), await active_chain()
    source = await proposal(kind, left, teacher)
    target = right[kind]
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST",
        f"/api/catalog/proposals/{kind}/{source}/merge",
        {"target_id": str(target), "reason": "wrong hierarchy"},
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_merge_hierarchy_mismatch"
    )
    await assert_unresolved(table, source)


async def test_reject_blocks_task_level_reference_atomically():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await provisional_subject(f"Used {uuid4()}", teacher)
    task, _ = await content_row(chain, teacher, subject=source)
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST", f"/api/catalog/proposals/subject/{source}/reject", {"reason": "used"}
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_proposal_in_use"
    )
    await assert_unresolved("subjects", source)
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT subject_id FROM tasks WHERE id=:id"), {"id": task}
            )
            == source
        )


async def test_reject_blocks_mutable_skill_link_reference_atomically():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("skill", chain, teacher)
    _, version = await content_row(chain, teacher, add_primary_skill=False)
    link = await skill_link(version, source)
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST", f"/api/catalog/proposals/skill/{source}/reject", {"reason": "used"}
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_proposal_in_use"
    )
    await assert_unresolved("skills", source)
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT skill_id FROM task_skill_links WHERE id=:id"), {"id": link}
            )
            == source
        )


async def test_reject_blocks_real_typical_error_skill_reference():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("skill", chain, teacher)
    error = await typical_error(source)
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST", f"/api/catalog/proposals/skill/{source}/reject", {"reason": "used"}
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_proposal_in_use"
    )
    await assert_unresolved("skills", source)
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT skill_id FROM typical_errors WHERE id=:id"), {"id": error}
            )
            == source
        )


@pytest.mark.parametrize("reference", ["folder", "tag"])
async def test_subject_structural_reference_reject_and_merge_policy(reference):
    admin, teacher = await user("admin"), await user("teacher")
    source = await provisional_subject(f"Structural {uuid4()}", teacher)
    target = await active_subject(f"Target {uuid4()}")
    if reference == "folder":
        ref = await seed(
            "task_folders",
            "subject_id,parent_id,name,created_by,updated_by",
            ":s,NULL,:n,:a,:a",
            s=source,
            n=f"Folder {uuid4()}",
            a=teacher,
        )
        column = "task_folders"
    else:
        category = f"c{uuid4().hex[:8]}"
        async with async_session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO tag_categories(code,display_name,sort_order) VALUES (:c,:n,(SELECT coalesce(max(sort_order),-1)+1 FROM tag_categories))"
                ),
                {"c": category, "n": "Managed"},
            )
        ref = await seed(
            "tags",
            "category_code,subject_id,name,normalized_name,created_by,updated_by",
            ":c,:s,:n,:nn,:a,:a",
            c=category,
            s=source,
            n=f"Tag {uuid4()}",
            nn=str(uuid4()),
            a=teacher,
        )
        column = "tags"
    override_principal(app, admin_principal(admin))
    rejected = await request(
        "POST",
        f"/api/catalog/proposals/subject/{source}/reject",
        {"reason": "structural"},
    )
    assert (
        rejected.status_code == 409
        and rejected.json()["error"]["code"] == "catalog_proposal_in_use"
    )
    merged = await request(
        "POST",
        f"/api/catalog/proposals/subject/{source}/merge",
        {"target_id": str(target), "reason": "safe historical structure"},
    )
    assert (
        merged.status_code == 200
    )  # Current policy preserves structural ownership rather than rewriting it.
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text(f"SELECT subject_id FROM {column} WHERE id=:id"), {"id": ref}
            )
            == source
        )
        assert (
            await session.execute(
                text("SELECT status,replacement_id FROM subjects WHERE id=:id"),
                {"id": source},
            )
        ).one() == ("deprecated", target)


@pytest.mark.parametrize("reference", ["task", "skill", "methodology"])
async def test_approve_blocks_each_provisional_reference_atomically(reference):
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("topic" if reference == "task" else "skill", chain, teacher)
    subtopic = skill = None
    if reference == "task":
        source, subtopic, skill = await provisional_topic_branch(chain, teacher)
    task, version = await content_row(
        chain,
        teacher,
        topic=source if reference == "task" else None,
        subtopic=subtopic,
        skill=source if reference == "skill" else skill,
    )
    if reference == "methodology":
        await link_error(version, await typical_error(source))
    override_principal(app, teacher_principal(teacher))
    submitted = await request(
        "POST", f"/api/content-bank/tasks/{task}/versions/1/submit-review", {}
    )
    assert submitted.status_code == 200
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {}
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_references_provisional"
    )
    await assert_approval_atomic(version)


async def test_confirm_then_approve_vertical_emits_one_audit():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source, subtopic, skill = await provisional_topic_branch(chain, teacher)
    task, version = await content_row(
        chain, teacher, topic=source, subtopic=subtopic, skill=skill
    )
    override_principal(app, teacher_principal(teacher))
    assert (
        await request(
            "POST", f"/api/content-bank/tasks/{task}/versions/1/submit-review", {}
        )
    ).status_code == 200
    override_principal(app, admin_principal(admin))
    assert (
        await request("POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {})
    ).status_code == 409
    assert (
        await request("POST", f"/api/catalog/proposals/topic/{source}/confirm", {})
    ).status_code == 200
    assert (
        await request(
            "POST", f"/api/catalog/proposals/subtopic/{subtopic}/confirm", {}
        )
    ).status_code == 200
    assert (
        await request("POST", f"/api/catalog/proposals/skill/{skill}/confirm", {})
    ).status_code == 200
    approved = await request(
        "POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {}
    )
    assert (
        approved.status_code == 200
        and approved.json()["approved_by"] == str(admin)
        and approved.json()["approved_at"]
    )
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM audit_log WHERE task_version_id=:v AND action='version_approved'"
                ),
                {"v": version},
            )
            == 1
        )


async def test_merge_then_approve_task_reference_vertical_and_lifecycle():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source, subtopic, skill = await provisional_topic_branch(chain, teacher)
    task, version = await content_row(
        chain,
        teacher,
        "review",
        topic=source,
        subtopic=subtopic,
        skill=skill,
    )
    override_principal(app, admin_principal(admin))
    assert (
        await request("POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {})
    ).status_code == 409
    reason = "canonical topic"
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/topic/{source}/merge",
            {"target_id": str(chain["topic"]), "reason": reason},
        )
    ).status_code == 200
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/subtopic/{subtopic}/merge",
            {"target_id": str(chain["subtopic"]), "reason": reason},
        )
    ).status_code == 200
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/skill/{skill}/merge",
            {"target_id": str(chain["skill"]), "reason": reason},
        )
    ).status_code == 200
    assert (
        await request("POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {})
    ).status_code == 200
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT topic_id FROM tasks WHERE id=:id"), {"id": task}
            )
            == chain["topic"]
        )
        row = (
            await session.execute(
                text(
                    "SELECT status,replacement_id,resolved_by,resolved_at,resolution_reason,proposed_by FROM topics WHERE id=:id"
                ),
                {"id": source},
            )
        ).one()
    assert (
        row == ("deprecated", chain["topic"], admin, row.resolved_at, reason, teacher)
        and row.resolved_at is not None
    )


async def test_merge_then_approve_skill_link_vertical():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("skill", chain, teacher)
    task, version = await content_row(chain, teacher, "review", skill=source)
    async with async_session_factory() as session:
        link = await session.scalar(
            text("SELECT id FROM task_skill_links WHERE task_version_id=:v"),
            {"v": version},
        )
    override_principal(app, admin_principal(admin))
    assert (
        await request("POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {})
    ).status_code == 409
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/skill/{source}/merge",
            {"target_id": str(chain["skill"]), "reason": "canonical skill"},
        )
    ).status_code == 200
    assert (
        await request("POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {})
    ).status_code == 200
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT skill_id FROM task_skill_links WHERE id=:id"), {"id": link}
            )
            == chain["skill"]
        )
        assert (
            await session.execute(
                text("SELECT status,replacement_id FROM skills WHERE id=:id"),
                {"id": source},
            )
        ).one() == ("deprecated", chain["skill"])


@pytest.mark.parametrize("reference", ["task", "skill"])
async def test_merge_refuses_historical_reference_without_partial_mutation(reference):
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("topic" if reference == "task" else "skill", chain, teacher)
    task, version = await content_row(
        chain,
        teacher,
        "approved",
        topic=source if reference == "task" else None,
        skill=source if reference == "skill" else None,
    )
    if reference == "skill":
        async with async_session_factory() as session:
            link = await session.scalar(
                text("SELECT id FROM task_skill_links WHERE task_version_id=:v"),
                {"v": version},
            )
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST",
        f"/api/catalog/proposals/{'topic' if reference == 'task' else 'skill'}/{source}/merge",
        {
            "target_id": str(chain[reference if reference == "skill" else "topic"]),
            "reason": "unsafe",
        },
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_historical_reference_conflict"
    )
    await assert_unresolved("topics" if reference == "task" else "skills", source)
    async with async_session_factory() as session:
        if reference == "task":
            assert (
                await session.scalar(
                    text("SELECT topic_id FROM tasks WHERE id=:id"), {"id": task}
                )
                == source
            )
        else:
            assert (
                await session.scalar(
                    text("SELECT skill_id FROM task_skill_links WHERE id=:id"),
                    {"id": link},
                )
                == source
            )
        assert (
            await session.scalar(
                text("SELECT status FROM task_versions WHERE id=:id"), {"id": version}
            )
            == "approved"
        )


async def test_mutable_skill_merge_conflict_preserves_both_links():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("skill", chain, teacher)
    _, version = await content_row(chain, teacher, add_primary_skill=False)
    first, second = (
        await skill_link(version, source, ".25", False),
        await skill_link(version, chain["skill"], ".75", True),
    )
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST",
        f"/api/catalog/proposals/skill/{source}/merge",
        {"target_id": str(chain["skill"]), "reason": "collision"},
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_merge_reference_conflict"
    )
    await assert_unresolved("skills", source)
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id,skill_id,weight,is_primary FROM task_skill_links WHERE id IN (:a,:b) ORDER BY id"
                ),
                {"a": first, "b": second},
            )
        ).all()
    assert {row.skill_id for row in rows} == {source, chain["skill"]} and len(rows) == 2


async def test_typical_error_merge_conflict_preserves_methodology():
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("skill", chain, teacher)
    old, canonical = (
        await typical_error(source, "same-code"),
        await typical_error(chain["skill"], "same-code"),
    )
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST",
        f"/api/catalog/proposals/skill/{source}/merge",
        {"target_id": str(chain["skill"]), "reason": "collision"},
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "catalog_merge_reference_conflict"
    )
    await assert_unresolved("skills", source)
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT id,skill_id FROM typical_errors WHERE id IN (:a,:b)"),
                {"a": old, "b": canonical},
            )
        ).all()
    assert {row.skill_id for row in rows} == {source, chain["skill"]}


@pytest.mark.parametrize("resolution", ["merged", "rejected"])
async def test_deprecated_stale_content_bank_write_is_rejected(resolution):
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source = await proposal("topic", chain, teacher)
    override_principal(app, admin_principal(admin))
    if resolution == "merged":
        response = await request(
            "POST",
            f"/api/catalog/proposals/topic/{source}/merge",
            {"target_id": str(chain["topic"]), "reason": "duplicate"},
        )
    else:
        response = await request(
            "POST",
            f"/api/catalog/proposals/topic/{source}/reject",
            {"reason": "invalid"},
        )
    assert response.status_code == 200
    override_principal(app, teacher_principal(teacher))
    payload = {
        "subject_id": str(chain["subject"]),
        "grade_id": str(chain["grade"]),
        "topic_id": str(source),
        "subtopic_id": None,
        "initial_version": {
            "title": "Stale",
            "statement": "Statement",
            "task_type": "problem",
            "answer_format": "short_text",
            "difficulty": 50,
            "skills": [],
        },
        "tag_ids": [],
    }
    created = await request("POST", "/api/content-bank/tasks", payload)
    assert (
        created.status_code == 422
        and created.json()["error"]["code"] == "validation_error"
    )
    async with async_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM tasks WHERE topic_id=:id"), {"id": source}
            )
            == 0
        )


@pytest.mark.parametrize(
    "resolution,code",
    [
        ("merged", "catalog_reference_requires_canonicalization"),
        ("rejected", "catalog_reference_rejected"),
    ],
)
async def test_approval_classifies_deprecated_reference_atomically(resolution, code):
    admin, teacher = await user("admin"), await user("teacher")
    chain = await active_chain()
    source, subtopic, skill = await provisional_topic_branch(chain, teacher)
    task, version = await content_row(
        chain, teacher, "review", topic=source, subtopic=subtopic, skill=skill
    )
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE subtopics SET status='active' WHERE id=:id"),
            {"id": subtopic},
        )
        await session.execute(
            text("UPDATE skills SET status='active' WHERE id=:id"), {"id": skill}
        )
        await session.execute(
            text(
                "UPDATE topics SET status='deprecated',replacement_id=:replacement,resolved_by=:a,resolved_at=now(),resolution_reason='legacy' WHERE id=:id"
            ),
            {
                "replacement": chain["topic"] if resolution == "merged" else None,
                "a": admin,
                "id": source,
            },
        )
    override_principal(app, admin_principal(admin))
    response = await request(
        "POST", f"/api/content-bank/tasks/{task}/versions/1/approve", {}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == code
    await assert_approval_atomic(version)


async def test_reject_persists_complete_lifecycle_metadata():
    admin, teacher = await user("admin"), await user("teacher")
    source = await provisional_subject(f"Reject lifecycle {uuid4()}", teacher)
    reason = "Exact rejection reason"
    override_principal(app, admin_principal(admin))
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/subject/{source}/reject",
            {"reason": reason},
        )
    ).status_code == 200
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status,replacement_id,resolved_by,resolved_at,resolution_reason,proposed_by FROM subjects WHERE id=:id"
                ),
                {"id": source},
            )
        ).one()
    assert (
        row == ("deprecated", None, admin, row.resolved_at, reason, teacher)
        and row.resolved_at is not None
    )


async def test_merge_persists_complete_lifecycle_without_mutating_target_metadata():
    admin, teacher = await user("admin"), await user("teacher")
    source = await provisional_subject(f"Merge lifecycle {uuid4()}", teacher)
    target = await active_subject(f"Canonical {uuid4()}")
    reason = "Exact merge reason"
    async with async_session_factory() as session:
        target_before = (
            await session.execute(
                text(
                    "SELECT status,resolved_by,resolved_at,replacement_id,resolution_reason FROM subjects WHERE id=:id"
                ),
                {"id": target},
            )
        ).one()
    override_principal(app, admin_principal(admin))
    assert (
        await request(
            "POST",
            f"/api/catalog/proposals/subject/{source}/merge",
            {"target_id": str(target), "reason": reason},
        )
    ).status_code == 200
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status,replacement_id,resolved_by,resolved_at,resolution_reason,proposed_by FROM subjects WHERE id=:id"
                ),
                {"id": source},
            )
        ).one()
        target_after = (
            await session.execute(
                text(
                    "SELECT status,resolved_by,resolved_at,replacement_id,resolution_reason FROM subjects WHERE id=:id"
                ),
                {"id": target},
            )
        ).one()
    assert (
        row == ("deprecated", target, admin, row.resolved_at, reason, teacher)
        and row.resolved_at is not None
    )
    assert target_before == target_after == ("active", None, None, None, None)
