"""Alembic environment.

Every module's models are imported so autogenerate sees the full picture. That
is also the trap: autogenerate will emit DDL for tables you do not own. Always
run `upgrade heads` first, and read the diff before committing — delete every
operation that is not about your own prefixed tables.

See docs/engineering/migrations.md.
"""

from __future__ import annotations

from importlib import import_module
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from autune_contracts import MODULES
from autune_core import Base, get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Shared entities, then every module's tables.
import autune_core.entities  # noqa: F401,E402

for _name in MODULES:
    import_module(f"autune_{_name}.models")

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
