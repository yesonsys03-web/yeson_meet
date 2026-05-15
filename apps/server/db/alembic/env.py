# === ANCHOR: ENV_START ===
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from apps.server.db.base import Base
from apps.server.db import models as _models  # noqa: F401  (register tables with Base.metadata)

config = context.config

db_url = os.environ.get("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# === ANCHOR: ENV_RUN_MIGRATIONS_OFFLINE_START ===
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
# === ANCHOR: ENV_RUN_MIGRATIONS_OFFLINE_END ===


# === ANCHOR: ENV_DO_RUN_MIGRATIONS_START ===
def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
# === ANCHOR: ENV_DO_RUN_MIGRATIONS_END ===


# === ANCHOR: ENV_RUN_ASYNC_MIGRATIONS_START ===
async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
# === ANCHOR: ENV_RUN_ASYNC_MIGRATIONS_END ===


# === ANCHOR: ENV_RUN_MIGRATIONS_ONLINE_START ===
def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())
# === ANCHOR: ENV_RUN_MIGRATIONS_ONLINE_END ===


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
# === ANCHOR: ENV_END ===
