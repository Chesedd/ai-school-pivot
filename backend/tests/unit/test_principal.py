from types import SimpleNamespace
from uuid import uuid4

from app.application.capabilities import ROLE_CAPABILITIES
from app.application.principal import PrincipalResolver


class AuthenticatedAccount:
    def __init__(self, user_id, login, display_name, is_active):
        self.user_id = user_id
        self.login = login
        self.display_name = display_name
        self.is_active = is_active


class Authentication:
    def __init__(self, account): self.account = account
    async def resolve_session(self, secret):
        assert secret == "opaque"
        return self.account


class Repository:
    def __init__(self):
        self.roles = frozenset({"teacher"})
        self.link = None
    async def roles_for_user(self, user_id): return self.roles
    async def link_for_user(self, user_id): return self.link


async def test_resolution_loads_current_roles_and_link_on_every_request():
    user_id, student_id = uuid4(), uuid4()
    account = AuthenticatedAccount(user_id, "teacher", "Teacher", True)
    repository = Repository()
    resolver = PrincipalResolver(Authentication(account), repository)
    first = await resolver.resolve("opaque")
    assert first.roles == frozenset({"teacher"})
    assert first.capabilities == ROLE_CAPABILITIES["teacher"]
    assert first.student_id is None

    repository.roles = frozenset({"teacher", "student"})
    repository.link = SimpleNamespace(student_id=student_id)
    second = await resolver.resolve("opaque")
    assert second.student_id == student_id
    assert second.roles == frozenset({"teacher", "student"})
    assert second.capabilities == ROLE_CAPABILITIES["teacher"] | ROLE_CAPABILITIES["student"]
    assert first != second


async def test_no_roles_forms_empty_immutable_principal():
    account = AuthenticatedAccount(uuid4(), "user", "User", True)
    repository = Repository()
    repository.roles = frozenset()
    principal = await PrincipalResolver(Authentication(account), repository).for_account(account)
    assert principal.roles == frozenset()
    assert principal.capabilities == frozenset()
