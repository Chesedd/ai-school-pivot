"""Small, HTTP-independent object ownership scope.

Role interpretation lives here so object repositories never need to know about
authentication roles (and routers do not grow ad-hoc administrator checks).
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class PrincipalLike(Protocol):
    user_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class ObjectAccessScope:
    actor_id: UUID
    unrestricted: bool = False

    def owns(self, owner_id: UUID) -> bool:
        return self.unrestricted or owner_id == self.actor_id


def object_access_scope(principal: PrincipalLike) -> ObjectAccessScope:
    """Resolve the canonical Admin override in exactly one policy module."""
    return ObjectAccessScope(principal.user_id, unrestricted="admin" in principal.roles)
