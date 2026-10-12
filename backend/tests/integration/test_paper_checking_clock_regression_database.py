"""A database wall-clock reversal must not discard a completed paper attempt."""

import os
from datetime import timedelta

import pytest
from app.application.paper_checking_execution import PaperCheckingExecutionError
from app.infrastructure.checking_models import ModelRun
from app.infrastructure.checking_repository import CheckingRepository
from sqlalchemy import text
from tests.integration.test_paper_checking_execution_database import (
    FakePaperProvider,
    service,
    start,
)
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


@pytest.mark.parametrize("mode", ["valid", "unknown_item"])
@pytest.mark.parametrize("stage", ["attempt", "run"])
async def test_paper_attempt_finish_survives_database_clock_reversal(
    execution_database, monkeypatch, mode, stage
):
    c, ids = execution_database, execution_database[2]
    run = await start(c)
    # Record a start on the old clock, then observe an earlier database clock at
    # completion. A fixed offset gives deterministic coverage without sleeps or
    # changing audit history after insertion.
    recorded_start = await c[0].scalar(text("SELECT clock_timestamp()")) + timedelta(
        hours=1
    )
    original = ModelRun.__init__

    def old_clock_start(self, *args, **kwargs):
        kwargs["started_at"] = recorded_start
        original(self, *args, **kwargs)

    if stage == "attempt":
        monkeypatch.setattr(ModelRun, "__init__", old_clock_start)
    else:
        transition = CheckingRepository.transition_run

        async def old_clock_transition(self, *args, **kwargs):
            if args[2] != "running":
                return await transition(self, *args, **kwargs)
            scalar = self.session.scalar

            async def old_clock(statement, *values, **options):
                if str(statement).startswith("SELECT clock_timestamp()"):
                    return recorded_start
                return await scalar(statement, *values, **options)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(self.session, "scalar", old_clock)
                return await transition(self, *args, **kwargs)

        monkeypatch.setattr(CheckingRepository, "transition_run", old_clock_transition)
    operation = service(c, FakePaperProvider(mode)).execute(ids["paper_a"], run.id)
    if mode == "valid":
        assert (await operation)["status"] == "completed_with_review_required"
    else:
        with pytest.raises(PaperCheckingExecutionError, match="paper_response_invalid"):
            await operation
    row = (
        await c[0].execute(
            text(
                "SELECT status,started_at,finished_at FROM model_runs WHERE check_run_id=:id"
            ),
            {"id": run.id},
        )
    ).one()
    assert row.status == ("succeeded" if mode == "valid" else "invalid")
    if stage == "attempt":
        assert row.finished_at == row.started_at == recorded_start
    else:
        run_times = (
            await c[0].execute(
                text("SELECT started_at,finished_at FROM check_runs WHERE id=:id"),
                {"id": run.id},
            )
        ).one()
        assert run_times.finished_at == run_times.started_at == recorded_start
