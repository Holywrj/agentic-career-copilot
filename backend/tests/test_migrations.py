import pytest
from alembic import command
from conftest import migrate, migration_config
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from career_copilot.db.models import Base

pytestmark = pytest.mark.database


async def test_empty_schema_upgrade_downgrade_upgrade(
    isolated_engine: AsyncEngine,
) -> None:
    async with isolated_engine.begin() as connection:
        assert (
            await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            == []
        )
        await connection.run_sync(migrate, "upgrade", "head")
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        assert set(tables) == set(Base.metadata.tables) | {"alembic_version"}
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0001"
        )
        await connection.run_sync(migrate, "downgrade", "base")
        assert set(
            await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        ) == {"alembic_version"}
        await connection.run_sync(migrate, "upgrade", "head")


async def test_migration_matches_models_and_has_no_rls(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as connection:

        def check(conn):
            config = migration_config()
            config.attributes["connection"] = conn
            command.check(config)
            inspector = inspect(conn)
            for name, table in Base.metadata.tables.items():
                columns = {
                    column["name"]: column for column in inspector.get_columns(name)
                }
                assert set(columns) == set(table.columns.keys())
                for column in table.columns:
                    assert columns[column.name]["nullable"] == column.nullable
                assert set(
                    inspector.get_pk_constraint(name)["constrained_columns"]
                ) == {column.name for column in table.primary_key}
                assert {index["name"] for index in inspector.get_indexes(name)} >= {
                    index.name for index in table.indexes
                }
                assert {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints(name)
                } == {
                    constraint.name
                    for constraint in table.constraints
                    if isinstance(constraint, CheckConstraint)
                }
                assert {
                    tuple(constraint["column_names"])
                    for constraint in inspector.get_unique_constraints(name)
                } == {
                    tuple(constraint.columns.keys())
                    for constraint in table.constraints
                    if isinstance(constraint, UniqueConstraint)
                }
                assert {
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                    )
                    for fk in inspector.get_foreign_keys(name)
                } == {
                    (
                        tuple(fk.columns.keys()),
                        fk.referred_table.name,
                        tuple(element.column.name for element in fk.elements),
                    )
                    for fk in table.constraints
                    if isinstance(fk, ForeignKeyConstraint)
                }

        await connection.run_sync(check)
        enabled = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND (c.relrowsecurity OR c.relforcerowsecurity)"
            )
        )
        assert enabled == 0
