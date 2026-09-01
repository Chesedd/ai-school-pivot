"""J1G: complete real-PostgreSQL acceptance across Image Solving and Content Bank."""
import hashlib
import os
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("J1G cleanup is allowed only for a database ending in _test")
os.environ["DATABASE_URL"] = URL or "postgresql+asyncpg://localhost/j1g_collection_only"

from app.application.image_solving import ImageSolvingService  # noqa: E402
from app.application.image_solving_api import ImageSolvingApplicationService  # noqa: E402
from app.application.image_solving_contracts import ExtractionResultV1, SolutionResultV1  # noqa: E402
from app.application.input_artifacts import ArtifactOwnershipService, ArtifactUploadService  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.infrastructure.artifact_storage import FilesystemArtifactStorage  # noqa: E402
from app.infrastructure.image_solving_repository import SqlAlchemyImageSolvingRepository  # noqa: E402
from app.infrastructure.input_artifact_repository import SqlAlchemyArtifactRepository  # noqa: E402
from app.main import app  # noqa: E402
from app.presentation.image_artifact_routes import service as artifact_service  # noqa: E402
from app.presentation.image_solving_routes import image_solving_service  # noqa: E402
from tests.integration.auth_helpers import (admin_principal, clear_principal_override,  # noqa: E402
    override_principal, teacher_principal)

pytestmark = [pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]

EXTRACTION = ExtractionResultV1(extracted_text="v = s / t", structured_statement="Найдите скорость: 100 м за 20 с.",
    detected_task_type="calculation", detected_answer_format="number", choices=None,
    extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={"title":"Скорость тела",
    "subject":"Физика J1G", "grade":7, "topic":"Механика J1G", "subtopic":"Скорость J1G",
    "skills":("Вычислять скорость J1G",), "task_type":"calculation", "answer_format":"number",
    "difficulty":20, "tags":()})
SOLUTION = SolutionResultV1(status="solved", reasoning_summary="Divide distance by time.",
    final_answer="5 м/с", confidence=Decimal(".98"))
IMAGE = b"\x89PNG\r\n\x1a\nJ1G"

class Extractor:
    def __init__(self): self.calls = []
    async def extract(self, value): self.calls.append(value); return EXTRACTION

class Solver:
    def __init__(self): self.calls = []
    async def solve(self, value): self.calls.append(value); return SOLUTION

class Integrity:
    def __init__(self, storage): self.storage = storage
    async def sha256(self, artifact):
        return hashlib.sha256(await self.storage.read(artifact.storage_reference)).hexdigest()

@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def isolated_database(tmp_path):
    async with async_session_factory() as db, db.begin():
        assert (await db.scalar(text("select current_database()"))).endswith("_test")
        await db.execute(text("TRUNCATE users CASCADE"))
    storage = FilesystemArtifactStorage(str(tmp_path))
    yield storage
    clear_principal_override(app); app.dependency_overrides.clear()
    async with async_session_factory() as db, db.begin():
        await db.execute(text("TRUNCATE users CASCADE"))

@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def dispose_engine():
    yield
    await engine.dispose()

async def user(label):
    async with async_session_factory() as db, db.begin():
        return await db.scalar(text("INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES (:n,:n,:n,'hash') RETURNING id"), {"n":f"{label}-{uuid4()}"})

async def request(method, path, payload=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=payload)

async def test_image_solving_proposals_content_bank_resolution_and_approval_vertical(isolated_database):
    """One transactionally-observed vertical; every mutation uses its public HTTP boundary."""
    storage = isolated_database
    teacher, admin = await user("teacher"), await user("admin")
    extractor, solver = Extractor(), Solver()

    async def upload_dependency():
        async with async_session_factory() as db:
            yield ArtifactUploadService(SqlAlchemyArtifactRepository(db), storage)
    async def solving_dependency():
        async with async_session_factory() as db:
            repo = SqlAlchemyImageSolvingRepository(db)
            flow = ImageSolvingService(repo, ArtifactOwnershipService(SqlAlchemyArtifactRepository(db)),
                Integrity(storage), extractor, solver)
            yield ImageSolvingApplicationService(flow, repo)
    app.dependency_overrides[artifact_service] = upload_dependency
    app.dependency_overrides[image_solving_service] = solving_dependency
    override_principal(app, teacher_principal(teacher))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post("/api/image-solving/artifacts", files={"file":("j1g.png", IMAGE, "image/png")})
        assert uploaded.status_code == 201
        created = await client.post("/api/image-solving/sessions", json={"artifact_id":uploaded.json()["artifact_id"]})
        session_id = UUID(created.json()["session_id"])
        assert created.status_code == 201
        assert (await client.post(f"/api/image-solving/sessions/{session_id}/run")).status_code == 200
        generated = await client.post(f"/api/image-solving/sessions/{session_id}/recommendations")
        retrieved = await client.get(f"/api/image-solving/sessions/{session_id}/recommendations")
    assert generated.status_code == retrieved.status_code == 200 and generated.json() == retrieved.json()
    recommendation = generated.json()
    assert recommendation["subject"] == {"kind":"new", "proposed_name":"Физика J1G", "parent_id":None,
        "confidence":"0", "reason":"Безопасное совпадение в текущем каталоге не найдено."}
    assert recommendation["grade"]["kind"] == recommendation["topic"]["kind"] == "new"
    assert recommendation["subtopic"]["kind"] == recommendation["skills"][0]["kind"] == "new"
    assert len(extractor.calls) + len(solver.calls) == 2
    async with async_session_factory() as db:
        automatic_proposals = 0
        for table in ("subjects", "grades", "topics", "subtopics", "skills"):
            automatic_proposals += await db.scalar(
                text(f"SELECT count(*) FROM {table} WHERE status='provisional'")
            )
        assert automatic_proposals == 0

    proposal_payloads = [
        {"kind":"subject", "name":"Физика J1G"},
        {"kind":"grade", "number":7, "name":"7 класс J1G"},
    ]
    proposal_ids = []
    for payload in proposal_payloads:
        response = await request("POST", "/api/catalog/proposals", payload)
        assert response.status_code == 201 and response.json()["outcome"] == "created_provisional"
        proposal_ids.append(UUID(response.json()["id"]))
    for payload in (
        {"kind":"topic", "name":"Механика J1G", "subject_id":str(proposal_ids[0]), "grade_id":str(proposal_ids[1])},
        None, None):
        if payload is None and len(proposal_ids) == 3:
            payload = {"kind":"subtopic", "name":"Скорость J1G", "topic_id":str(proposal_ids[2])}
        elif payload is None:
            payload = {"kind":"skill", "name":"Вычислять скорость J1G", "subtopic_id":str(proposal_ids[3])}
        response = await request("POST", "/api/catalog/proposals", payload)
        assert response.status_code == 201 and response.json()["outcome"] == "created_provisional"
        proposal_ids.append(UUID(response.json()["id"]))
    assert len(proposal_ids) == 5 and len(extractor.calls) + len(solver.calls) == 2

    async with async_session_factory() as db:
        for table, value in zip(("subjects","grades","topics","subtopics","skills"), proposal_ids):
            assert (await db.execute(text(f"SELECT status,proposed_by FROM {table} WHERE id=:id"), {"id":value})).one() == ("provisional", teacher)

    promotion_payload = {"title":"Скорость тела", "statement":"Найдите скорость: 100 м за 20 с.",
        "task_type":"calculation", "answer_format":"number", "difficulty":20,
        "subject_id":str(proposal_ids[0]), "grade_id":str(proposal_ids[1]), "topic_id":str(proposal_ids[2]),
        "subtopic_id":str(proposal_ids[3]), "skill_ids":[str(proposal_ids[4])], "tag_ids":[],
        "solution":"Разделить 100 на 20.", "final_answer":"5 м/с", "review_confirmed":True}
    promoted = await request("POST", f"/api/image-solving/sessions/{session_id}/promote", promotion_payload)
    assert promoted.status_code == 200 and promoted.json()["status"] == "draft"
    task_id, version_id = UUID(promoted.json()["task_id"]), UUID(promoted.json()["task_version_id"])
    repeated = await request("POST", f"/api/image-solving/sessions/{session_id}/promote", promotion_payload)
    assert repeated.status_code == 200 and repeated.json()["already_existing"] is True and UUID(repeated.json()["task_id"]) == task_id

    assert (await request("POST", f"/api/content-bank/tasks/{task_id}/versions/1/submit-review", {})).status_code == 200
    teacher_denied = await request("POST", f"/api/content-bank/tasks/{task_id}/versions/1/approve", {})
    assert teacher_denied.status_code == 403
    override_principal(app, admin_principal(admin))
    blocked = await request("POST", f"/api/content-bank/tasks/{task_id}/versions/1/approve", {})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "catalog_references_provisional"

    async with async_session_factory() as db:
        state = (await db.execute(text("SELECT status,approved_at,approved_by FROM task_versions WHERE id=:id"), {"id":version_id})).one()
        assert state == ("review", None, None)
        assert await db.scalar(text("SELECT count(*) FROM audit_log WHERE task_version_id=:id AND action='version_approved'"), {"id":version_id}) == 0
    for kind, value in zip(("subject","grade","topic","subtopic","skill"), proposal_ids):
        confirmed = await request("POST", f"/api/catalog/proposals/{kind}/{value}/confirm", {})
        assert confirmed.status_code == 200 and UUID(confirmed.json()["id"]) == value and confirmed.json()["status"] == "active"
    assert len(extractor.calls) + len(solver.calls) == 2

    approved = await request("POST", f"/api/content-bank/tasks/{task_id}/versions/1/approve", {})
    assert approved.status_code == 200 and approved.json()["status"] == "approved" and UUID(approved.json()["approved_by"]) == admin
    async with async_session_factory() as db:
        task = (await db.execute(text("SELECT subject_id,grade_id,topic_id,subtopic_id,created_by FROM tasks WHERE id=:id"), {"id":task_id})).one()
        assert task == (*proposal_ids[:4], teacher)
        assert await db.scalar(text("SELECT skill_id FROM task_skill_links WHERE task_version_id=:id"), {"id":version_id}) == proposal_ids[4]
        for table, value in zip(("subjects","grades","topics","subtopics","skills"), proposal_ids):
            row = (await db.execute(text(f"SELECT status,proposed_by,resolved_by,resolved_at,replacement_id FROM {table} WHERE id=:id"), {"id":value})).one()
            assert row.status == "active" and row.proposed_by == teacher and row.resolved_by == admin and row.resolved_at and row.replacement_id is None
        audit = (await db.execute(text("SELECT created_by,details FROM audit_log WHERE task_id=:id AND action='task_created'"), {"id":task_id})).one()
        assert audit.created_by == teacher and audit.details["image_solving_session_id"] == str(session_id)
        assert audit.details["input_artifact_id"] == uploaded.json()["artifact_id"] and audit.details["human_review_confirmed"] is True
        assert await db.scalar(text("SELECT count(*) FROM audit_log WHERE task_version_id=:id AND action='version_approved'"), {"id":version_id}) == 1
        assert await db.scalar(text("SELECT count(*) FROM image_solving_recommendations WHERE session_id=:id"), {"id":session_id}) == 1
    assert len(extractor.calls) + len(solver.calls) == 2

async def test_merged_proposal_canonicalizes_review_and_allows_approval():
    """The smaller J1G merge vertical reuses J1F's canonical DB builders, not its assertions."""
    from tests.integration.test_catalog_resolution_api import active_chain, content_row, proposal

    admin, teacher = await user("merge-admin"), await user("merge-teacher")
    chain = await active_chain()
    source = await proposal("skill", chain, teacher, name="Merged skill J1G")
    task_id, version_id = await content_row(chain, teacher, "review", skill=source)
    override_principal(app, admin_principal(admin))
    blocked = await request("POST", f"/api/content-bank/tasks/{task_id}/versions/1/approve", {})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "catalog_references_provisional"
    merged = await request("POST", f"/api/catalog/proposals/skill/{source}/merge",
        {"target_id":str(chain["skill"]), "reason":"J1G canonical duplicate"})
    assert merged.status_code == 200 and merged.json()["status"] == "deprecated"
    assert (await request("POST", f"/api/content-bank/tasks/{task_id}/versions/1/approve", {})).status_code == 200
    override_principal(app, teacher_principal(teacher))
    alias = await request("POST", "/api/catalog/proposals", {"kind":"skill", "name":"Merged skill J1G",
        "subtopic_id":str(chain["subtopic"])})
    assert alias.status_code == 200 and alias.json()["outcome"] == "existing_active"
    assert UUID(alias.json()["id"]) == chain["skill"]
    async with async_session_factory() as db:
        assert (await db.execute(text("SELECT status,replacement_id FROM skills WHERE id=:id"), {"id":source})).one() == ("deprecated", chain["skill"])
        assert await db.scalar(text("SELECT skill_id FROM task_skill_links WHERE task_version_id=:id"), {"id":version_id}) == chain["skill"]
        assert await db.scalar(text("SELECT count(*) FROM skills WHERE normalized_name='merged skill j1g' AND status='provisional'")) == 0
