"""Pure catalog option normalization, lifecycle, and ranking tests."""
from types import SimpleNamespace
from uuid import UUID

from app.application.catalog_options import catalog_option_rank
from app.infrastructure.catalog_lifecycle import (MAX_CATALOG_REPLACEMENT_DEPTH,
    resolve_effective_catalog_target)
from app.infrastructure.models import normalize_catalog_name


def _row(value, name, normalized=None):
    return SimpleNamespace(id=UUID(int=value), name=name,
        normalized_name=normalized or normalize_catalog_name(name))


def test_catalog_normalization_is_casefolded_yo_and_punctuation_insensitive():
    assert normalize_catalog_name("  ЁЖИК: Алгебра! ") == "ежик алгебра"
    assert normalize_catalog_name("ЛИНЕЙНЫЕ—УРАВНЕНИЯ") == "линейные уравнения"


def test_ranking_exact_alias_prefix_substring_then_fuzzy_and_limit():
    normalized = "линей"
    rows = [
        _row(5, "Нелинейный", "нелинейный"),
        _row(4, "Совсем другое", "совсем другое"),
        _row(3, "Линейные функции", "линейные функции"),
        _row(2, "Каноническое имя", "каноническое имя"),
        _row(1, "Линей", "линей"),
    ]
    ranked = sorted(rows, key=lambda row: catalog_option_rank(row, normalized, {UUID(int=2)}))
    assert [row.id.int for row in ranked] == [1, 2, 3, 5, 4]
    assert [row.id.int for row in ranked[:3]] == [1, 2, 3]


def test_ranking_ties_are_deterministic_by_casefolded_name_then_uuid():
    rows = [_row(2, "Бета"), _row(3, "Альфа"), _row(1, "Альфа")]
    ranked = sorted(rows, key=lambda row: catalog_option_rank(row, "zzz", set()))
    assert [row.id.int for row in ranked] == [1, 3, 2]


class _Db:
    def __init__(self, rows):
        self.rows = rows

    async def get(self, _model, row_id):
        return self.rows.get(row_id)


async def test_effective_target_live_dead_missing_loop_and_depth_bound():
    active = SimpleNamespace(id=UUID(int=1), status="active", replacement_id=None)
    provisional = SimpleNamespace(id=UUID(int=2), status="provisional", replacement_id=None)
    dead = SimpleNamespace(id=UUID(int=3), status="deprecated", replacement_id=None)
    loop_a = SimpleNamespace(id=UUID(int=4), status="deprecated", replacement_id=UUID(int=5))
    loop_b = SimpleNamespace(id=UUID(int=5), status="deprecated", replacement_id=UUID(int=4))
    db = _Db({row.id: row for row in (active, provisional, dead, loop_a, loop_b)})
    assert await resolve_effective_catalog_target(db, object, active.id) is active
    assert await resolve_effective_catalog_target(db, object, provisional.id) is provisional
    assert await resolve_effective_catalog_target(db, object, dead.id) is None
    assert await resolve_effective_catalog_target(db, object, UUID(int=99)) is None
    assert await resolve_effective_catalog_target(db, object, loop_a.id) is None
    chain = {}
    for value in range(100, 100 + MAX_CATALOG_REPLACEMENT_DEPTH + 1):
        chain[UUID(int=value)] = SimpleNamespace(id=UUID(int=value), status="deprecated",
            replacement_id=UUID(int=value + 1))
    chain[UUID(int=100 + MAX_CATALOG_REPLACEMENT_DEPTH + 1)] = active
    assert await resolve_effective_catalog_target(_Db(chain), object, UUID(int=100)) is None


def test_catalog_normalization_preserves_mathematical_function_identity():
    assert normalize_catalog_name("Функция y = √x") != normalize_catalog_name("Функция y = |x|")
