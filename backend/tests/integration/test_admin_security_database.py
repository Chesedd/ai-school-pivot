"""PostgreSQL acceptance for the cross-process active-Admin invariant."""

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Admin security tests require a database ending in _test")

from app.application.authentication import AuthenticationService
from app.application.user_administration import AdministrationError, UserAdministrationService
from app.infrastructure.auth_repository import SQLAlchemyAuthRepository

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database():
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text(
            "TRUNCATE auth_sessions, user_roles, student_user_links, users CASCADE"
        ))
    try:
        yield engine, factory
    finally:
        await engine.dispose()


def service(session):
    repository = SQLAlchemyAuthRepository(session)
    return UserAdministrationService(repository, AuthenticationService(repository))


async def test_concurrent_admin_reductions_leave_one_active_admin(database):
    """Separate transactions serialize through the PostgreSQL advisory lock."""
    engine, factory = database
    async with factory() as session:
        first = await service(session).create(login="admin-a", display_name="Admin A",
            password="acceptance-password-a", roles={"admin"}, student_id=None)
        second = await service(session).create(login="admin-b", display_name="Admin B",
            password="acceptance-password-b", roles={"admin"}, student_id=None)
        await session.commit()

    gate = asyncio.Event()
    ready = 0
    guard = asyncio.Lock()

    async def dangerous(user_id, deactivate):
        nonlocal ready
        async with factory() as session:
            async with guard:
                ready += 1
                if ready == 2:
                    gate.set()
            await gate.wait()
            try:
                if deactivate:
                    await service(session).update(user_id, login=None,
                        display_name=None, is_active=False)
                else:
                    await service(session).set_roles(user_id, set())
                await session.commit()
                return "success"
            except AdministrationError as exc:
                await session.rollback()
                return exc.code

    outcomes = await asyncio.gather(
        dangerous(first.user_id, True), dangerous(second.user_id, False)
    )
    assert sorted(outcomes) == ["last_active_admin", "success"]
    async with engine.connect() as connection:
        active_admins = await connection.scalar(text(
            "SELECT count(*) FROM users u JOIN user_roles r ON r.user_id=u.id "
            "WHERE u.is_active AND r.role='admin'"
        ))
    assert active_admins == 1


async def test_bootstrap_real_database_boundaries(database):
    """Bootstrap is explicit, refuses existing Admins, and never promotes conflicts."""
    engine, factory = database
    async with factory() as session:
        bootstrapped = await service(session).bootstrap(login="bootstrap-admin",
            display_name="Bootstrap Admin", password="acceptance-password")
        await session.commit()
    assert bootstrapped.roles == ("admin",)

    async with factory() as session:
        with pytest.raises(AdministrationError, match="bootstrap_not_required"):
            await service(session).bootstrap(login="another-admin",
                display_name="Another", password="acceptance-password")
        await session.rollback()

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE auth_sessions, user_roles, users CASCADE"))
        await connection.execute(text(
            "INSERT INTO users(login,normalized_login,display_name,password_hash) "
            "VALUES ('conflict','conflict','Existing account','$argon2id$opaque')"
        ))
    async with factory() as session:
        with pytest.raises(AdministrationError, match="account_already_exists"):
            await service(session).bootstrap(login="conflict", display_name="Bootstrap",
                password="acceptance-password")
        await session.rollback()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM user_roles")) == 0
