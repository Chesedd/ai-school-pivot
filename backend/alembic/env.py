"""Alembic environment using the application's async database configuration."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import get_settings
from app.infrastructure.model_registry import register_all_models
from app.infrastructure.models import Base
from app.infrastructure.paper_downgrade_guard import preflight_paper_downgrade

register_all_models()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a database connection."""
    context.configure(
        url=str(get_settings().database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    migration_context = context.get_context()
    starting = migration_context.opts.get("starting_rev")
    steps = list(migration_context._migrations_fn((starting,) if starting else (), migration_context))
    if any(not step.is_upgrade for step in steps):
        from alembic.util import CommandError
        raise CommandError("Offline downgrade refused: paper history requires an online preflight")
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        migration_context = context.get_context()
        steps = list(migration_context._migrations_fn(migration_context.get_current_heads(), migration_context))
        preflight_paper_downgrade(connection, {
            step.revision.revision for step in steps if not step.is_upgrade
        })
        context.run_migrations()


async def run_migrations_online() -> None:
    """Create a temporary async engine solely for the Alembic invocation."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = str(get_settings().database_url)
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
