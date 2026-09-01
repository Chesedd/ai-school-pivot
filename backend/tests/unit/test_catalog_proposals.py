from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.catalog_proposals import CatalogProposalCommand, CatalogProposalError, CatalogProposalService


class Nested:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False


class Repository:
    def __init__(self, rows=None, parents=None):
        self.rows = rows or {}
        self.parents = parents or {}
        self.created = []
        self.session = SimpleNamespace(begin_nested=lambda: Nested())

    async def get(self, kind, entity_id): return self.parents.get((kind, entity_id))

    async def find_exact(self, kind, name, *, status, **identity):
        key = (kind, identity.get("number")) if kind == "grade" else (
            kind, name.casefold(), identity.get("subject_id"), identity.get("grade_id"),
            identity.get("topic_id"), identity.get("subtopic_id"),
        )
        return self.rows.get((status.value, key))

    async def create_provisional(self, kind, **values):
        row = SimpleNamespace(id=uuid4(), status="provisional", **values)
        self.created.append(row)
        return row


def row(name, status="active", **values):
    return SimpleNamespace(id=uuid4(), name=name, status=status, **values)


async def test_reuses_active_without_create():
    existing = row("Математика")
    repo = Repository({("active", ("subject", "математика", None, None, None, None)): existing})
    result = await CatalogProposalService(repo).propose_catalog_value(uuid4(), CatalogProposalCommand("subject", "Математика"))
    assert result.outcome == "existing_active" and result.id == existing.id
    assert repo.created == []


async def test_reuses_provisional_without_rewriting_attribution():
    first = uuid4()
    existing = row("Алгебра", "provisional", proposed_by=first)
    repo = Repository({("provisional", ("subject", "алгебра", None, None, None, None)): existing})
    result = await CatalogProposalService(repo).propose_catalog_value(uuid4(), CatalogProposalCommand("subject", "Алгебра"))
    assert result.outcome == "existing_provisional"
    assert existing.proposed_by == first and repo.created == []


async def test_creates_provisional_with_actor():
    actor = uuid4(); repo = Repository()
    result = await CatalogProposalService(repo).propose_catalog_value(actor, CatalogProposalCommand("subject", "Физика"))
    assert result.outcome == "created_provisional"
    assert repo.created[0].proposed_by == actor


async def test_grade_reuses_number_despite_different_label():
    existing = row("8 класс", number=8)
    repo = Repository({("active", ("grade", 8)): existing})
    result = await CatalogProposalService(repo).propose_catalog_value(uuid4(), CatalogProposalCommand("grade", "Восьмой класс", number=8))
    assert result.id == existing.id and result.outcome == "existing_active"


@pytest.mark.parametrize("parent", [None, SimpleNamespace(status="deprecated")])
async def test_rejects_missing_or_deprecated_parent(parent):
    subject_id, grade_id = uuid4(), uuid4()
    parents = {("subject", subject_id): parent, ("grade", grade_id): SimpleNamespace(status="active")}
    with pytest.raises(CatalogProposalError) as caught:
        await CatalogProposalService(Repository(parents=parents)).propose_catalog_value(
            uuid4(), CatalogProposalCommand("topic", "Тема", subject_id=subject_id, grade_id=grade_id)
        )
    assert caught.value.code in {"catalog_parent_not_found", "catalog_parent_deprecated"}
