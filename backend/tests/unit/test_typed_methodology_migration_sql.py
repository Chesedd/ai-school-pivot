"""Regression guard for SQLAlchemy/asyncpg's one-command execute contract."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).parents[2] / "alembic" / "versions"
MIGRATIONS = {
    "20260810_02_typed_methodology_foundation.py": 8,
    "20260810_03_typed_methodology_integrity.py": 8,
    "20260810_04_typed_methodology_constraint_name.py": 2,
}
_DOLLAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def _render_static(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "identifier" for part in node.values)
    raise AssertionError(f"op.execute SQL must be a literal or f-string, got {ast.dump(node)}")


def _top_level_statements(sql: str) -> list[str]:
    """Split only on top-level semicolons, ignoring quotes and dollar bodies."""
    statements: list[str] = []
    start = index = 0
    quote: str | None = None
    dollar: str | None = None
    while index < len(sql):
        if dollar:
            if sql.startswith(dollar, index):
                index += len(dollar); dollar = None
            else:
                index += 1
            continue
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2; continue
                quote = None
            index += 1; continue
        match = _DOLLAR.match(sql, index)
        if match:
            dollar = match.group(0); index = match.end(); continue
        if char in {"'", '"'}:
            quote = char; index += 1; continue
        if char == ";":
            value = sql[start:index].strip()
            if value: statements.append(value)
            start = index + 1
        index += 1
    value = sql[start:].strip()
    if value: statements.append(value)
    return statements


@pytest.mark.parametrize("filename,expected_calls", MIGRATIONS.items())
def test_each_execute_call_contains_one_top_level_postgresql_statement(filename, expected_calls):
    tree = ast.parse((VERSIONS / filename).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"]
    assert len(calls) == expected_calls, "Audit new execute sites and update the explicit count"
    for call in calls:
        sql = _render_static(call.args[0])
        statements = _top_level_statements(sql)
        assert len(statements) == 1, f"{filename}:{call.lineno} sends {len(statements)} commands: {statements}"


def test_splitter_does_not_treat_plpgsql_body_semicolons_as_top_level_commands():
    sql = "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 1; PERFORM 2; END $$;"
    assert len(_top_level_statements(sql)) == 1
    assert len(_top_level_statements(sql + " CREATE TRIGGER t AFTER INSERT ON x EXECUTE FUNCTION f();")) == 2
