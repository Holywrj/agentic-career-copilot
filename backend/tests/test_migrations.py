from uuid import uuid4

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from career_copilot.db.models import Base

pytestmark = pytest.mark.database


async def test_incremental_migration_preserves_existing_users_and_sessions(
    isolated_engine: AsyncEngine,
) -> None:
    user_id, session_id = uuid4(), uuid4()
    async with isolated_engine.begin() as connection:
        await connection.run_sync(migrate, "upgrade", "0001")
        await connection.execute(
            text(
                "INSERT INTO users (id, identity_issuer, identity_subject, status) "
                "VALUES (:id, 'local', 'existing-user', 'active')"
            ),
            {"id": user_id},
        )
        await connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, token_hash, expires_at) "
                "VALUES (:id, :user_id, :hash, now() + interval '30 minutes')"
            ),
            {"id": session_id, "user_id": user_id, "hash": "a" * 64},
        )
        before = (await connection.execute(text("SELECT * FROM sessions"))).one()
        await connection.run_sync(migrate, "upgrade", "head")
        await connection.execute(
            text(
                "INSERT INTO local_credentials (user_id, password_hash) "
                "VALUES (:id, :hash)"
            ),
            {"id": user_id, "hash": "migration-test-only"},
        )
        assert await connection.scalar(
            text("SELECT created_at IS NOT NULL FROM local_credentials")
        )
        await connection.run_sync(migrate, "downgrade", "0001")
        assert "local_credentials" not in await connection.run_sync(
            lambda conn: inspect(conn).get_table_names()
        )
        assert (
            await connection.execute(text("SELECT * FROM sessions"))
        ).one() == before
        assert await connection.scalar(text("SELECT id FROM users")) == user_id
        await connection.run_sync(migrate, "upgrade", "head")
        assert (
            await connection.scalar(text("SELECT count(*) FROM local_credentials")) == 0
        )


async def test_local_credentials_primary_foreign_key_and_not_null(db_engine):
    user_id = uuid4()
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, identity_issuer, identity_subject, status) "
                "VALUES (:id, 'local', 'credential-owner', 'active')"
            ),
            {"id": user_id},
        )
        insert = text(
            "INSERT INTO local_credentials (user_id, password_hash) VALUES (:id, :hash)"
        )
        for parameters in [
            {"id": uuid4(), "hash": "test-only"},
            {"id": user_id, "hash": None},
            {"id": None, "hash": "test-only"},
        ]:
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(insert, parameters)
        await connection.execute(insert, {"id": user_id, "hash": "test-only"})
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(insert, {"id": user_id, "hash": "test-only"})


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
            == "0002"
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
