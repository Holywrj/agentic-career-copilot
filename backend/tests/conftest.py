"""Disposable schemas in a dedicated PostgreSQL database, built by Alembic."""

import os
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from career_copilot import main
from career_copilot.settings import AuthSettings, ResumeSettings, Settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pdf_bytes():
    """Tiny self-contained PDFs; no system fonts, renderer or network fixtures."""

    def make(*pages, encrypted=False, content=None, compressed=False):
        writer = PdfWriter()
        for page_text in pages or ("Resume Engineer",):
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                    NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            if page_text is not None or content is not None:
                stream = DecodedStreamObject()
                stream.set_data(
                    content
                    if content is not None
                    else (
                        b"BT /F1 12 Tf 36 700 Td <"
                        + page_text.encode("cp1252").hex().encode()
                        + b"> Tj ET"
                    )
                )
                page[NameObject("/Contents")] = (
                    stream.flate_encode() if compressed else stream
                )
        if encrypted:
            writer.encrypt("test-only-password", algorithm="RC4-128")
        output = BytesIO()
        writer.write(output)
        writer.close()
        return output.getvalue()

    return make


@pytest.fixture
def resume_settings(tmp_path) -> ResumeSettings:
    return ResumeSettings(storage_dir=tmp_path / "resumes")


@pytest.fixture
def auth_settings() -> AuthSettings:
    # Deliberately test-only, passed explicitly rather than used as an app default.
    return AuthSettings(jwt_secret="test-only-signing-key-never-use-in-deployment")


def migration_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def migrate(connection: Connection, operation: str, revision: str) -> None:
    config = migration_config()
    config.attributes["connection"] = connection
    getattr(command, operation)(config, revision)


@pytest.fixture
def test_settings() -> Settings:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required; start the dedicated test database")
    settings = Settings.from_url(value)
    database = settings.database_url.database
    if not database or not database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a dedicated database ending in _test")
    production = os.environ.get("DATABASE_URL")
    if production and Settings.from_url(production).database_url.database == database:
        pytest.fail("The application and test databases must have different names")
    return settings


@pytest_asyncio.fixture
async def isolated_engine(test_settings: Settings) -> AsyncIterator[AsyncEngine]:
    schema = f"test_{uuid4().hex}"
    admin = create_async_engine(
        test_settings.database_url, poolclass=NullPool, hide_parameters=True
    )
    engine = create_async_engine(
        test_settings.database_url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 5},
    )
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield engine
    finally:
        await engine.dispose()
        try:
            async with admin.begin() as connection:
                # Only this fixture's generated schema is removed.
                await connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                )
        finally:
            await admin.dispose()


@pytest_asyncio.fixture
async def db_engine(isolated_engine: AsyncEngine) -> AsyncEngine:
    async with isolated_engine.begin() as connection:
        await connection.run_sync(migrate, "upgrade", "head")
    return isolated_engine


@pytest_asyncio.fixture
async def auth_app(
    db_engine, test_settings, auth_settings, resume_settings, monkeypatch
):
    monkeypatch.setattr(main, "create_engine", lambda _: db_engine)
    app = main.create_app(test_settings, auth_settings, resume_settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def auth_client(auth_app):
    transport = httpx2.ASGITransport(app=auth_app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield client
