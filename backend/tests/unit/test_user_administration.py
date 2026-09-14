from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.user_administration import AdministrationError, UserAdministrationService
from app.presentation.admin_user_routes import CreateUserRequest, PasswordResetRequest, UserResponse, router


def repository(*, roles=frozenset(), active=True, linked=False, admins=1):
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(id=user_id, login="user", display_name="User", first_name=None, last_name=None, is_active=active, created_at=now, updated_at=now)
    repo = SimpleNamespace(
        get_user=AsyncMock(return_value=row), roles_for_user=AsyncMock(return_value=roles),
        link_for_user=AsyncMock(return_value=SimpleNamespace(student_id=uuid4()) if linked else None),
        count_active_admins=AsyncMock(return_value=admins), lock_admin_invariant=AsyncMock(),
        set_user_active=AsyncMock(), revoke_sessions_for_user=AsyncMock(),
        update_user_identity=AsyncMock(return_value=row), replace_roles=AsyncMock(),
        update_password_hash=AsyncMock(),
    )
    auth = SimpleNamespace(password_hasher=SimpleNamespace(hash_password=lambda value: "$argon2id$hash"))
    return UserAdministrationService(repo, auth), repo, row


@pytest.mark.asyncio
async def test_last_active_admin_cannot_be_deactivated():
    service, repo, row = repository(roles=frozenset({"admin"}), admins=1)
    with pytest.raises(AdministrationError, match="last_active_admin"):
        await service.update(row.id, login=None, display_name=None, is_active=False)
    repo.lock_admin_invariant.assert_awaited_once()
    repo.set_user_active.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_admin_can_be_deactivated_and_sessions_are_revoked():
    service, repo, row = repository(roles=frozenset({"admin"}), admins=2)
    await service.update(row.id, login=None, display_name=None, is_active=False)
    repo.set_user_active.assert_awaited_once_with(row.id, False)
    repo.revoke_sessions_for_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_link_prevents_student_role_removal():
    service, repo, row = repository(roles=frozenset({"student"}), linked=True)
    with pytest.raises(AdministrationError, match="student_link_requires_student_role"):
        await service.set_roles(row.id, {"teacher"})
    repo.replace_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_password_reset_hashes_and_revokes_every_session():
    service, repo, row = repository()
    await service.reset_password(row.id, "new-password")
    repo.update_password_hash.assert_awaited_once_with(row.id, "$argon2id$hash")
    repo.revoke_sessions_for_user.assert_awaited_once()


def test_admin_requests_forbid_secret_and_spoofing_fields():
    with pytest.raises(ValueError):
        CreateUserRequest(first_name="X", last_name="Y", password={"mode": "generated"}, roles=set(), password_hash="leak")
    with pytest.raises(ValueError):
        PasswordResetRequest(new_password="p", actor_id=str(uuid4()))
    with pytest.raises(ValueError):
        CreateUserRequest(
            first_name="X", last_name="Y", password={"mode": "generated"},
            roles={"student"}, student_id=str(uuid4()),
        )


def test_admin_contract_omits_student_identity_and_legacy_routes():
    assert "student_id" not in UserResponse.model_fields
    assert not any(route.path.endswith("/student-link") for route in router.routes)
    test_app = FastAPI()
    test_app.include_router(router)
    user_id = uuid4()
    with TestClient(test_app) as client:
        assert client.put(f"/api/admin/users/{user_id}/student-link", json={"student_id": str(uuid4())}).status_code == 404
        assert client.delete(f"/api/admin/users/{user_id}/student-link").status_code == 404


def test_admin_create_request_supports_generated_and_provided_passwords():
    generated = CreateUserRequest(
        first_name="Иван", last_name="Иванов", roles={"student"},
        password={"mode": "generated"},
    )
    assert generated.password.mode == "generated"
    provided = CreateUserRequest(
        first_name="Иван", last_name="Иванов",
        password={"mode": "provided", "value": "secret"},
    )
    assert provided.password.value == "secret"


@pytest.mark.asyncio
async def test_name_edit_composes_display_name_without_changing_login():
    service, repo, row = repository()
    await service.update(row.id, first_name="  Grace ", last_name=" Hopper ")
    repo.update_user_identity.assert_awaited_once_with(
        row.id, login="user", normalized_login="user", display_name="Grace Hopper",
        first_name="Grace", last_name="Hopper",
    )
