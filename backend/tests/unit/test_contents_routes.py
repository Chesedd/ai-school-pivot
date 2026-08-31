import os
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unit:unit@localhost/unit")
os.environ.setdefault("CONTENT_BANK_DEV_ACTOR_ID", "00000000-0000-4000-8000-000000000001")

import httpx
import pytest

from app.main import app
from app.presentation import routes


class SessionFactory:
    def __call__(self): return self
    async def __aenter__(self): return object()
    async def __aexit__(self, *args): return None


@pytest.mark.asyncio
async def test_content_bank_mutation_requires_authentication():
    payload = {
        "subject_id": str(uuid4()), "grade_id": str(uuid4()),
        "topic_id": str(uuid4()), "subtopic_id": str(uuid4()),
        "initial_version": {
            "statement": "Test", "task_type": "calculation",
            "answer_format": "number", "difficulty": 25,
            "skills": [{"skill_id": str(uuid4()), "weight": "1.0000",
                        "is_primary": True}],
        },
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url="http://test") as client:
        response = await client.post("/api/content-bank/tasks", json=payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["subject", "folder"])
@pytest.mark.parametrize("params,expected_sort,expected_q", [
    ({"offset": 0, "limit": 20}, "created_at", None),
    ({"offset": 0, "limit": 20, "q": " текст "}, "relevance", "текст"),
    ({"offset": 0, "limit": 20, "difficulty_min": 1, "difficulty_max": 100}, "created_at", None),
    ({"offset": 3, "limit": 10, "grade_id": str(uuid4()), "topic_id": str(uuid4()), "subtopic_id": str(uuid4()), "skill_id": str(uuid4()), "task_type": "calculation", "status": "approved", "sort_by": "difficulty", "sort_order": "asc"}, "difficulty", None),
])
async def test_contents_routes_construct_valid_default_sort(monkeypatch, kind, params, expected_sort, expected_q):
    subject_id, folder_id, captured = uuid4(), uuid4(), []
    payload = {"subject": {"id": str(subject_id), "name": "Математика"}, "folder": None,
               "breadcrumb": [], "folders": [],
               "tasks": {"items": [], "total": 0, "offset": params["offset"], "limit": params["limit"]},
               "level_task_total": 0, "subject_task_total": 0}

    class Repo:
        def __init__(self, _session): pass
        async def get_folder(self, _id): return SimpleNamespace(subject_id=subject_id)
        async def get_level_contents(self, _subject_id, _folder_id, query):
            captured.append(query)
            return payload

    monkeypatch.setattr(routes, "async_session_factory", SessionFactory())
    monkeypatch.setattr(routes, "SQLAlchemyContentBankRepository", Repo)
    path = f"/api/content-bank/{'subjects/' + str(subject_id) if kind == 'subject' else 'folders/' + str(folder_id)}/contents"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path, params=params)
    assert response.status_code == 200
    assert response.json()["tasks"] == payload["tasks"]
    assert response.json()["folders"] == []
    assert response.json()["breadcrumb"] == []
    assert response.json()["level_task_total"] == response.json()["subject_task_total"] == 0
    assert captured[0].sort_by == expected_sort and captured[0].sort_by is not None
    assert captured[0].sort_order == params.get("sort_order", "desc")
    assert captured[0].q == expected_q
    assert (captured[0].difficulty_min, captured[0].difficulty_max) == (params.get("difficulty_min"), params.get("difficulty_max"))
    for name in ("grade_id", "topic_id", "subtopic_id", "skill_id", "task_type", "status"):
        assert str(getattr(captured[0], name) or "") == params.get(name, "")
    assert captured[0].sort_order == params.get("sort_order", "desc")

@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"task_type": "made_up"}, {"status": "made_up"},
    {"sort_by": "made_up"}, {"sort_order": "sideways"},
    {"difficulty_min": 0}, {"difficulty_max": 101},
])
async def test_contents_reject_invalid_public_query_values(params):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/content-bank/subjects/{uuid4()}/contents", params=params)
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_subject_path_cannot_be_overridden(monkeypatch):
    path_subject, attempted_subject, captured = uuid4(), uuid4(), []
    class Repo:
        def __init__(self, _session): pass
        async def get_level_contents(self, subject_id, folder_id, query):
            captured.append((subject_id, folder_id, query))
            return {"subject":{"id":str(subject_id),"name":"Subject"},"folder":None,"breadcrumb":[],"folders":[],"tasks":{"items":[],"total":0,"offset":0,"limit":20},"level_task_total":0,"subject_task_total":0}
    monkeypatch.setattr(routes, "async_session_factory", SessionFactory())
    monkeypatch.setattr(routes, "SQLAlchemyContentBankRepository", Repo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/content-bank/subjects/{path_subject}/contents", params={"subject_id":str(attempted_subject),"folder_id":str(uuid4()),"folder_scope":"subtree","root_only":"true"})
    assert response.status_code == 200
    assert captured[0][0] == path_subject and captured[0][1] is None
    assert captured[0][2].subject_id == path_subject
    assert captured[0][2].folder_id is None and captured[0][2].folder_scope is None and not captured[0][2].root_only
