from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text


def migration_config(database_url: str) -> Config:
    migrations = Path(__file__).with_name("migrations")
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_central_database(database_url: str, revision: str = "head") -> None:
    command.upgrade(migration_config(database_url), revision)


def central_database_is_current(engine: Engine, database_url: str) -> bool:
    if "alembic_version" not in inspect(engine).get_table_names():
        return False
    expected = ScriptDirectory.from_config(migration_config(database_url)).get_current_head()
    if expected is None:
        return False
    with engine.connect() as connection:
        actual = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    return actual == expected


def current_central_database_revision(database_url: str) -> str:
    revision = ScriptDirectory.from_config(migration_config(database_url)).get_current_head()
    if revision is None:
        raise RuntimeError("fleet database migration head is unavailable")
    return revision
