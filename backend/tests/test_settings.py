import subprocess
import sys

import pytest

from career_copilot.main import create_app
from career_copilot.settings import Settings


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        Settings.from_env()


@pytest.mark.parametrize(
    "value",
    [
        "not a URL",
        "sqlite:///test.db",
        "postgresql+psycopg://localhost",
        "postgresql+psycopg://user:fixture-password@localhost:fixture-password/db",
    ],
)
def test_invalid_url_does_not_expose_credentials(value: str) -> None:
    with pytest.raises(ValueError) as error:
        Settings.from_url(value)
    assert value not in str(error.value)
    assert "fixture-password" not in str(error.value)


def test_settings_repr_hides_password() -> None:
    settings = Settings.from_url(
        "postgresql+psycopg://user:fixture-password@localhost/db"
    )
    assert "fixture-password" not in repr(settings)


async def test_startup_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app()
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must reject missing configuration")


def test_import_does_not_load_configuration_or_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = subprocess.run(
        [sys.executable, "-c", "import career_copilot.main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
