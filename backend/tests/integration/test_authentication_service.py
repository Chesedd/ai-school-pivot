"""D1 service proof against the actual C1 PostgreSQL repository."""

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError(
        "authentication service tests require a database ending in _test"
    )
pytestmark = pytest.mark.asyncio

from app.application.auth_errors import (  # noqa: E402
    AccountAlreadyExistsError,
    InactiveAccountError,
    InvalidCredentialsError,
    InvalidSessionError,
)
from app.application.authentication import AuthenticationService  # noqa: E402
from app.infrastructure.auth_models import AuthSession, User  # noqa: E402
from app.infrastructure.auth_repository import SQLAlchemyAuthRepository  # noqa: E402
from app.security.session_tokens import hash_session_secret  # noqa: E402


@pytest_asyncio.fixture
async def engine():
    value = create_async_engine(URL)
    try:
        yield value
    finally:
        await value.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean(engine):
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE auth_sessions, user_roles, student_user_links, users CASCADE"
            )
        )


async def test_account_login_session_logout_and_disabled_user(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        service = AuthenticationService(SQLAlchemyAuthRepository(db))
        account = await service.create_account(
            login=" Teacher ", display_name="Teacher", password=" correct "
        )
        await db.commit()
        assert account.login == "Teacher"

        with pytest.raises(AccountAlreadyExistsError):
            await service.create_account(
                login="ＴＥＡＣＨＥＲ", display_name="Duplicate", password="different"
            )
        await db.rollback()
        with pytest.raises(InvalidCredentialsError):
            await service.login("teacher", "wrong")

        issued = await service.login("TEACHER", " correct ")
        await db.commit()
        persisted = await db.scalar(select(AuthSession))
        assert persisted is not None
        assert persisted.token_hash == hash_session_secret(issued.session_secret)
        assert len(persisted.token_hash) == 32
        assert issued.session_secret.encode() != persisted.token_hash
        assert await service.resolve_session(issued.session_secret) == account

        await service.logout(issued.session_secret)
        await db.commit()
        with pytest.raises(InvalidSessionError):
            await service.resolve_session(issued.session_secret)

        second = await service.login("teacher", " correct ")
        await db.execute(
            update(User)
            .where(User.id == account.user_id)
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )
        await db.commit()
        with pytest.raises(InactiveAccountError):
            await service.resolve_session(second.session_secret)
