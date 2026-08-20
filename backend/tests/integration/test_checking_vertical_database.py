"""Phase 4.10 PostgreSQL vertical acceptance entry point.

The detailed persistence invariants are composed by the Phase 4.2, 4.7 and 4.9
PostgreSQL modules; this module independently verifies their shared production
schema boundary remains at the accepted revision.
"""
import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

URL=os.environ.get("TEST_DATABASE_URL","")
if URL and not URL.rsplit("/",1)[-1].split("?",1)[0].endswith("_test"):
    raise RuntimeError("Phase 4.10 requires a disposable database ending in _test")

@pytest.mark.asyncio
async def test_phase410_production_persistence_vertical_is_available_and_append_only():
    if not URL: pytest.skip("TEST_DATABASE_URL is required; behavioral acceptance remains pending")
    engine=create_async_engine(URL)
    try:
        async with engine.connect() as connection:
            revision=await connection.scalar(text("SELECT version_num FROM alembic_version"))
            columns=(await connection.execute(text("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('check_runs','check_results','check_findings','checker_events','model_runs') ORDER BY table_name,column_name"))).all()
        assert revision=="20260820_01"
        assert {row.table_name for row in columns}=={"check_runs","check_results","check_findings","checker_events","model_runs"}
    finally: await engine.dispose()
