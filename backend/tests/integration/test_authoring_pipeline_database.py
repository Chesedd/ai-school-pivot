"""PostgreSQL durability gate for the resumable semantic authoring pipeline.

These tests deliberately use independent ``AsyncSession`` instances.  They are
not a replacement for the fast repository fakes in the unit suite: their job is
to prove commit visibility, restart behaviour, and PostgreSQL CAS semantics.
"""
import asyncio
import json
import os
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.authoring import (
    AuthoringConflict,
    AuthoringError,
    AuthoringRequestV1,
    AuthoringRole,
    Cost,
    FailureCode,
    FrozenCatalogContext,
    ModelRoute,
    ProviderCapabilities,
    ProviderFailure,
    ProviderRegistry,
    ProviderResult,
    Usage,
)
from app.application.authoring_pipeline import (
    GeneratedTaskDraftV1,
    PipelineResumeState,
    SemanticPipelineService,
    ValidatedGeneratedTaskV1,
)
from app.infrastructure.authoring_models import AuthoringProviderAttempt, AuthoringSession
from app.infrastructure.authoring_repository import AuthoringRepository


URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("authoring tests require a *_test database")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]


def request() -> AuthoringRequestV1:
    return AuthoringRequestV1(
        schema_version="authoring-request.v1", task_goal="Create a durable task", subject="math",
        grade="g7", topic="arithmetic", task_type="calculation", answer_format="number",
        difficulty=50, skills=("reasoning",), policy_version="authoring-v1",
    )


DRAFT = {
    "schema_version": "generated_task_draft.v1", "title": "Multiply", "statement": "What is 6 times 7?",
    "task_type": "calculation", "answer_format": "number", "choice_options": [], "expected_answer": "42",
    "solution": "Six times seven is 42.", "hints": ["Multiply."],
}
SOLVED = {
    "schema_version": "solver_result.v1", "status": "solvable", "proposed_answer": "42",
    "reasoning_summary": "Independent multiplication check.",
}
GENERATOR = ModelRoute("fake-generator", "generator-v1")
SOLVER = ModelRoute("fake-solver", "solver-v1")


class StopProcess(BaseException):
    """Models abrupt process loss and intentionally bypasses Exception handlers."""


class CountingProvider:
    capabilities = ProviderCapabilities()

    def __init__(self, payload, *, failure=None, before_execute=None):
        self.payload = payload
        self.failure = failure
        self.before_execute = before_execute
        self.calls = 0

    async def execute(self, execution):
        self.calls += 1
        if self.before_execute is not None:
            await self.before_execute(execution)
        if self.failure is not None:
            raise ProviderFailure(self.failure)
        return ProviderResult(
            self.payload, f"fake-{self.calls}", Usage(1, 1), Cost(Decimal("0"), "USD", "test-v1", "test"), 1,
        )


def providers(generator=None, solver=None):
    generator = generator or CountingProvider(DRAFT)
    solver = solver or CountingProvider(SOLVED)
    registry = ProviderRegistry()
    registry.register(GENERATOR.provider_id, generator)
    registry.register(SOLVER.provider_id, solver)
    return registry, generator, solver


@pytest_asyncio.fixture
async def database():
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE authoring_provider_attempts, authoring_sessions CASCADE"))
    yield engine, factory
    await engine.dispose()


async def new_authoring_session(factory):
    async with factory() as db:
        row = await AuthoringRepository(db).create_session(
            uuid4(), request(), FrozenCatalogContext("math", "g7", "arithmetic", None, ("reasoning",)),
        )
        await db.commit()
        return row.id


async def run(factory, session_id, registry, *, key="pipeline-key", generator=GENERATOR, solver=SOLVER):
    async with factory() as db:
        return await SemanticPipelineService(AuthoringRepository(db), registry).run(
            session_id, request(), generator, solver, correlation_id="integration-test", idempotency_key=key,
        )


async def counts(factory):
    async with factory() as db:
        return (
            await db.scalar(text("SELECT count(*) FROM tasks")),
            await db.scalar(text("SELECT count(*) FROM task_versions")),
        )


async def test_normal_pipeline_commits_all_checkpoints_and_roundtrips_artifact(database):
    _, factory = database
    before = await counts(factory)
    session_id = await new_authoring_session(factory)
    registry, generator, solver = providers()
    artifact = await run(factory, session_id, registry)

    # Read back only after the service's session has been closed.
    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        attempts = (await db.scalars(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id).order_by(AuthoringProviderAttempt.attempt_number))).all()
        # JSONB correctly returns JSON arrays as lists.  Reconstruct strict DTOs
        # through the production JSON boundary rather than validating DB Python
        # objects directly (the draft intentionally has strict tuple fields).
        assert isinstance(row.generated_draft["choice_options"], list)
        assert isinstance(row.generated_draft["hints"], list)
        state = PipelineResumeState.from_persisted(
            row.generated_draft, row.generator_attempt_id, row.solver_result,
            row.solver_attempt_id, row.validation_result,
        )
        persisted = state.artifact
        assert row.generator_attempt_id and row.solver_attempt_id
        assert row.semantic_status == "validated"
        assert [attempt.status for attempt in attempts] == ["succeeded", "succeeded"]
        assert persisted is not None
        assert persisted.canonical_bytes() == artifact.canonical_bytes()
    assert (generator.calls, solver.calls) == (1, 1)
    assert await counts(factory) == before


async def test_generator_checkpoint_is_visible_to_solver_in_independent_session(database):
    _, factory = database
    session_id = await new_authoring_session(factory)

    async def observe_committed_generator(_execution):
        async with factory() as observer:
            row = await observer.get(AuthoringSession, session_id)
            assert row.generated_draft is not None and row.generator_attempt_id is not None
            attempt = await observer.get(AuthoringProviderAttempt, row.generator_attempt_id)
            assert attempt.status == "succeeded"

    registry, _, solver = providers(solver=CountingProvider(SOLVED, before_execute=observe_committed_generator))
    await run(factory, session_id, registry)
    assert solver.calls == 1


class CrashBeforeSolverAttemptRepository(AuthoringRepository):
    async def create_attempt(self, session_id, execution):
        if execution.role is AuthoringRole.SOLVER:
            raise StopProcess()
        return await super().create_attempt(session_id, execution)


async def test_restart_after_generator_checkpoint_before_solver_attempt_skips_generator(database):
    _, factory = database
    before = await counts(factory)
    session_id = await new_authoring_session(factory)
    generator = CountingProvider(DRAFT)
    registry, _, _ = providers(generator, CountingProvider(SOLVED))
    async with factory() as db:
        with pytest.raises(StopProcess):
            await SemanticPipelineService(CrashBeforeSolverAttemptRepository(db), registry).run(
                session_id, request(), GENERATOR, SOLVER, correlation_id="integration-test",
                idempotency_key="pipeline-key",
            )

    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        attempt = await db.get(AuthoringProviderAttempt, row.generator_attempt_id)
        assert row.generated_draft is not None and row.generator_attempt_id is not None
        assert attempt.status == "succeeded"
        assert await db.scalar(select(func.count()).select_from(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == "solver",
        )) == 0

    resumed_solver = CountingProvider(SOLVED)
    resumed, _, _ = providers(generator, resumed_solver)
    artifact = await run(factory, session_id, resumed)
    assert artifact.validation_result.status == "validated"
    assert generator.calls == 1 and resumed_solver.calls == 1
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == "solver",
        )) == 1
    assert await counts(factory) == before


async def test_restart_during_solver_execution_waits_then_recovers_stale_attempt(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    generator = CountingProvider(DRAFT)
    crashing_solver = CountingProvider(SOLVED, before_execute=lambda _execution: _stop())
    registry, _, _ = providers(generator, crashing_solver)
    with pytest.raises(StopProcess):
        await run(factory, session_id, registry)

    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        attempts = (await db.scalars(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == AuthoringRole.SOLVER.value,
        ))).all()
        assert row.generated_draft is not None
        assert row.generator_attempt_id is not None
        assert row.solver_result is None
        assert row.solver_attempt_id is None
        assert len(attempts) == 1
        running = attempts[0]
        running_id = running.id
        assert running.status == "running"
        assert running.started_at is not None

    resumed_solver = CountingProvider(SOLVED)
    resumed, _, _ = providers(generator, resumed_solver)
    with pytest.raises(AuthoringError, match="pipeline_in_progress"):
        await run(factory, session_id, resumed)
    assert generator.calls == crashing_solver.calls == 1
    assert resumed_solver.calls == 0
    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        attempts = (await db.scalars(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == AuthoringRole.SOLVER.value,
        ))).all()
        assert row.solver_result is None
        assert row.solver_attempt_id is None
        assert len(attempts) == 1
        assert attempts[0].attempt_number == 1
        assert attempts[0].status == "running"
        await db.execute(update(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.id == running_id,
        ).values(started_at=func.clock_timestamp() - text("interval '130 seconds'")))
        await db.commit()

    artifact = await run(factory, session_id, resumed)
    assert artifact.validation_result.status == "validated"
    assert generator.calls == 1 and resumed_solver.calls == 1
    async with factory() as db:
        attempts = (await db.scalars(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == "solver",
        ).order_by(AuthoringProviderAttempt.attempt_number))).all()
        assert [(attempt.attempt_number, attempt.status, attempt.failure_code) for attempt in attempts] == [
            (1, "failed_retryable", "timeout"), (2, "succeeded", None),
        ]
        row = await db.get(AuthoringSession, session_id)
        successful = attempts[1]
        assert row.solver_result is not None
        assert row.solver_attempt_id == successful.id
        assert successful.status == "succeeded"
        assert row.validation_result is not None
        assert row.semantic_status == "validated"


async def _stop():
    raise StopProcess()


class CrashBeforeValidationRepository(AuthoringRepository):
    async def checkpoint_validation(self, session_id, identity, validation):
        raise StopProcess()


async def test_restart_after_solver_checkpoint_only_cross_checks_and_validates(database):
    _, factory = database
    before = await counts(factory)
    session_id = await new_authoring_session(factory)
    registry, generator, solver = providers()
    async with factory() as db:
        with pytest.raises(StopProcess):
            await SemanticPipelineService(CrashBeforeValidationRepository(db), registry).run(
                session_id, request(), GENERATOR, SOLVER, correlation_id="integration-test", idempotency_key="pipeline-key",
            )
    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        assert row.generated_draft and row.solver_result and row.validation_result is None

    artifact = await run(factory, session_id, registry)
    assert artifact.validation_result.status == "validated"
    assert (generator.calls, solver.calls) == (1, 1)
    assert await counts(factory) == before


async def test_terminal_replay_creates_no_attempts_or_provider_calls(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    registry, generator, solver = providers()
    first = await run(factory, session_id, registry)
    async with factory() as db:
        attempts_before = await db.scalar(select(func.count()).select_from(AuthoringProviderAttempt))
    replay = await run(factory, session_id, registry)
    async with factory() as db:
        attempts_after = await db.scalar(select(func.count()).select_from(AuthoringProviderAttempt))
    assert replay.canonical_bytes() == first.canonical_bytes()
    assert (generator.calls, solver.calls, attempts_after) == (1, 1, attempts_before)


async def test_retryable_failure_survives_restart_and_uses_next_attempt_number(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    first_provider = CountingProvider(DRAFT, failure=FailureCode.TIMEOUT)
    # Persist exactly the state left after failure #1, before an application can create #2.
    async with factory() as db:
        repo = AuthoringRepository(db)
        await repo.configure_pipeline(session_id, _identity(), GENERATOR, SOLVER)
        attempt, _ = await repo.create_attempt(session_id, _generator_execution())
        assert await repo.claim(attempt.id)
        with pytest.raises(ProviderFailure):
            await first_provider.execute(_generator_execution())
        assert await repo.finalize_failure(attempt.id, FailureCode.TIMEOUT)
        await repo.commit()

    generator = CountingProvider(DRAFT)
    resumed, _, solver = providers(generator)
    artifact = await run(factory, session_id, resumed)
    async with factory() as db:
        attempts = (await db.scalars(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == "generator").order_by(AuthoringProviderAttempt.attempt_number))).all()
    assert artifact.validation_result.status == "validated"
    assert first_provider.calls == generator.calls == solver.calls == 1
    assert [(a.attempt_number, a.status) for a in attempts] == [(1, "failed_retryable"), (2, "succeeded")]


async def test_recent_running_attempt_returns_in_progress_without_execution(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    await _persist_running(factory, session_id)
    registry, generator, solver = providers()
    with pytest.raises(AuthoringError, match="pipeline_in_progress"):
        await run(factory, session_id, registry)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AuthoringProviderAttempt)) == 1
    assert generator.calls == solver.calls == 0


async def test_stale_running_attempt_is_cas_recovered_then_retried(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    old_id = await _persist_running(factory, session_id, stale=True)
    registry, generator, solver = providers()
    await run(factory, session_id, registry)
    async with factory() as db:
        old = await db.get(AuthoringProviderAttempt, old_id)
        attempts = (await db.scalars(select(AuthoringProviderAttempt).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == "generator").order_by(AuthoringProviderAttempt.attempt_number))).all()
        assert old.status == "failed_retryable" and old.failure_code == "timeout"
        assert [a.attempt_number for a in attempts] == [1, 2]
    assert generator.calls == solver.calls == 1


async def test_concurrent_stale_recovery_has_one_provider_winner(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    old_id = await _persist_running(factory, session_id, stale=True)
    registry, generator, solver = providers()
    results = await asyncio.gather(
        run(factory, session_id, registry), run(factory, session_id, registry), return_exceptions=True,
    )
    assert sum(isinstance(result, ValidatedGeneratedTaskV1) for result in results) == 1
    assert all(isinstance(result, (ValidatedGeneratedTaskV1, AuthoringError)) for result in results)
    async with factory() as db:
        old = await db.get(AuthoringProviderAttempt, old_id)
        numbers = (await db.scalars(select(AuthoringProviderAttempt.attempt_number).where(
            AuthoringProviderAttempt.session_id == session_id,
            AuthoringProviderAttempt.role == "generator").order_by(AuthoringProviderAttempt.attempt_number))).all()
        assert old.status == "failed_retryable" and numbers == [1, 2]
    assert generator.calls == solver.calls == 1


async def test_concurrent_new_pipeline_never_duplicates_provider_execution(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    registry, generator, solver = providers()
    results = await asyncio.gather(
        run(factory, session_id, registry), run(factory, session_id, registry), return_exceptions=True,
    )
    assert generator.calls <= 1 and solver.calls <= 1
    assert all(isinstance(result, (ValidatedGeneratedTaskV1, AuthoringError)) for result in results)
    # Regardless of whether caller two observed in-progress or replay, one terminal identity is stored.
    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        assert row.validation_result is not None and row.pipeline_identity == _identity()
        assert await db.scalar(select(func.count()).select_from(AuthoringProviderAttempt)) == 2


@pytest.mark.parametrize("values", [
    {"generated_draft": DRAFT, "generator_attempt_id": None},
    {"solver_result": SOLVED, "solver_attempt_id": None},
    {"validation_result": {"schema_version": "task_validation_result.v1", "status": "validated", "comparator": "decimal_v1"}},
])
async def test_inconsistent_postgresql_checkpoints_fail_closed(database, values):
    _, factory = database
    session_id = await new_authoring_session(factory)
    async with factory() as db:
        await db.execute(update(AuthoringSession).where(AuthoringSession.id == session_id).values(
            pipeline_identity=_identity(), generator_route={"provider_id": GENERATOR.provider_id, "model_id": GENERATOR.model_id},
            solver_route={"provider_id": SOLVER.provider_id, "model_id": SOLVER.model_id}, **values,
        ))
        await db.commit()
    registry, generator, solver = providers()
    with pytest.raises(AuthoringError, match="inconsistent_pipeline_checkpoint"):
        await run(factory, session_id, registry)
    assert generator.calls == solver.calls == 0


async def test_solver_checkpoint_without_generator_uses_real_attempt_fk_and_fails_closed(database):
    _, factory = database
    session_id = await new_authoring_session(factory)
    async with factory() as db:
        repo = AuthoringRepository(db)
        await repo.configure_pipeline(session_id, _identity(), GENERATOR, SOLVER)
        attempt, _ = await repo.create_attempt(session_id, _solver_execution())
        await db.execute(update(AuthoringSession).where(AuthoringSession.id == session_id).values(
            solver_result=SOLVED, solver_attempt_id=attempt.id,
        ))
        await db.commit()

    registry, generator, solver = providers()
    with pytest.raises(AuthoringError, match="inconsistent_pipeline_checkpoint"):
        await run(factory, session_id, registry)
    assert generator.calls == solver.calls == 0


@pytest.mark.parametrize("generator,solver,key", [
    (ModelRoute("other-generator", "generator-v1"), SOLVER, "pipeline-key"),
    (ModelRoute("fake-generator", "generator-v2"), SOLVER, "pipeline-key"),
    (GENERATOR, ModelRoute("other-solver", "solver-v1"), "pipeline-key"),
    (GENERATOR, ModelRoute("fake-solver", "solver-v2"), "pipeline-key"),
    (GENERATOR, SOLVER, "other-key"),
])
async def test_pipeline_identity_conflicts_never_execute_or_overwrite(database, generator, solver, key):
    _, factory = database
    session_id = await new_authoring_session(factory)
    initial_registry, initial_generator, initial_solver = providers()
    await run(factory, session_id, initial_registry)
    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        snapshot = (row.pipeline_identity, row.generator_route, row.solver_route, row.generated_draft, row.solver_result)

    registry = ProviderRegistry()
    candidate_generator, candidate_solver = CountingProvider(DRAFT), CountingProvider(SOLVED)
    for provider_id in {generator.provider_id, solver.provider_id}:
        registry.register(provider_id, candidate_generator if provider_id == generator.provider_id else candidate_solver)
    with pytest.raises(AuthoringConflict):
        await run(factory, session_id, registry, key=key, generator=generator, solver=solver)
    async with factory() as db:
        row = await db.get(AuthoringSession, session_id)
        assert (row.pipeline_identity, row.generator_route, row.solver_route, row.generated_draft, row.solver_result) == snapshot
    assert candidate_generator.calls == candidate_solver.calls == 0
    assert initial_generator.calls == initial_solver.calls == 1


def _identity():
    # Compute identity through the same public configuration path without duplicating its hash algorithm.
    import hashlib
    from app.application.authoring import canonical_json_bytes
    from app.application.authoring_pipeline import semantic_prompt_registry
    prompts = semantic_prompt_registry(request().policy_version)
    gp, sp = prompts.get("generator.task", "1.0.0"), prompts.get("solver.task", "1.0.0")
    return hashlib.sha256(canonical_json_bytes({
        "request": request().fingerprint,
        "generator": [GENERATOR.provider_id, GENERATOR.model_id], "solver": [SOLVER.provider_id, SOLVER.model_id],
        "prompts": [gp.template_hash, sp.template_hash], "key": "pipeline-key",
    })).hexdigest()


def _generator_execution():
    from app.application.authoring_pipeline import _execution, semantic_prompt_registry
    prompt = semantic_prompt_registry(request().policy_version).get("generator.task", "1.0.0")
    return _execution(AuthoringRole.GENERATOR, GENERATOR, prompt, request().fingerprint,
                      "integration-test", "pipeline-key-generator", request())


def _solver_execution():
    from app.application.authoring_pipeline import _execution, semantic_prompt_registry
    prompt = semantic_prompt_registry(request().policy_version).get("solver.task", "1.0.0")
    solver_input = GeneratedTaskDraftV1.model_validate_json(json.dumps(DRAFT)).sanitize_for_solver()
    return _execution(AuthoringRole.SOLVER, SOLVER, prompt, solver_input.fingerprint,
                      "integration-test", "pipeline-key-solver", solver_input)


async def _persist_running(factory, session_id, *, stale=False):
    async with factory() as db:
        repo = AuthoringRepository(db)
        await repo.configure_pipeline(session_id, _identity(), GENERATOR, SOLVER)
        attempt, _ = await repo.create_attempt(session_id, _generator_execution())
        assert await repo.claim(attempt.id)
        if stale:
            await db.execute(update(AuthoringProviderAttempt).where(AuthoringProviderAttempt.id == attempt.id).values(
                started_at=func.clock_timestamp() - text("interval '130 seconds'"),
            ))
        await repo.commit()
        return attempt.id
