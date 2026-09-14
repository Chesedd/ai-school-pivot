from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.auth_errors import AccountAlreadyExistsError
from app.application.login_generation import (
    MAX_GENERATED_LOGIN_ATTEMPTS,
    generated_login_base,
    generated_login_candidate,
)
from app.application.user_administration import AdministrationError, UserAdministrationService
from app.security.passwords import GENERATED_PASSWORD_LENGTH, PasswordHasher, generate_password


@pytest.mark.parametrize(("first", "last", "expected"), [
    ("Иван", "Иванов", "иван.иванов"),
    (" ＡＮＮＡ ", "Ｓｍｉｔｈ", "anna.smith"),
    ("Анна   Мария", "де  Виль", "анна-мария.де-виль"),
    ("Жан--Поль", "Сартр", "жан-поль.сартр"),
    ("O'Neil!", "Smith, Jr.", "oneil.smith-jr"),
])
def test_generated_login_policy(first, last, expected):
    assert generated_login_base(first, last) == expected


def test_generated_login_suffixes_and_maximum_length():
    base = generated_login_base("a" * 100, "b" * 100)
    assert generated_login_candidate(base, 1) == base
    assert generated_login_candidate(base, 2).endswith("-2")
    expanded = generated_login_base("㍻" * 100, "b" * 100)
    assert len(expanded) <= 254
    assert len(generated_login_candidate(expanded, 99)) <= 254


def _service(auth):
    user_id = uuid4()
    row = SimpleNamespace(
        id=user_id, login="иван.иванов-3", display_name="Иван Иванов",
        first_name="Иван", last_name="Иванов", is_active=True,
        created_at=None, updated_at=None,
    )
    repo = SimpleNamespace(
        student_exists=AsyncMock(), link_for_student=AsyncMock(return_value=None),
        replace_roles=AsyncMock(), create_student_link=AsyncMock(),
        get_user=AsyncMock(return_value=row), roles_for_user=AsyncMock(return_value=frozenset()),
        link_for_user=AsyncMock(return_value=None),
    )
    return UserAdministrationService(repo, auth), row


@pytest.mark.asyncio
async def test_collisions_retry_through_database_arbiter():
    account = SimpleNamespace(user_id=uuid4())
    auth = SimpleNamespace(
        password_hasher=PasswordHasher(),
        create_account=AsyncMock(side_effect=[AccountAlreadyExistsError(), AccountAlreadyExistsError(), account]),
    )
    service, row = _service(auth)
    row.id = account.user_id
    result = await service.create(
        first_name="Иван", last_name="Иванов", password="provided",
        roles=set(), student_id=None,
    )
    assert [call.kwargs["login"] for call in auth.create_account.await_args_list] == [
        "иван.иванов", "иван.иванов-2", "иван.иванов-3",
    ]
    assert result.generated_password is None


@pytest.mark.asyncio
async def test_generated_login_retry_is_bounded():
    auth = SimpleNamespace(
        password_hasher=PasswordHasher(),
        create_account=AsyncMock(side_effect=AccountAlreadyExistsError()),
    )
    service, _ = _service(auth)
    with pytest.raises(AdministrationError, match="account_already_exists"):
        await service.create(
            first_name="Иван", last_name="Иванов", password="provided",
            roles=set(), student_id=None,
        )
    assert auth.create_account.await_count == MAX_GENERATED_LOGIN_ATTEMPTS


def test_password_generator_is_secure_policy_shape_and_nondeterministic():
    values = {generate_password() for _ in range(10)}
    assert len(values) == 10
    assert all(len(value) == GENERATED_PASSWORD_LENGTH for value in values)
