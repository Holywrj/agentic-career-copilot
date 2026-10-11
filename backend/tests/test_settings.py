import subprocess
import sys

import pytest

from career_copilot.main import create_app
from career_copilot.settings import AuthSettings, ResumeSettings, Settings


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
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
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("RESUME_STORAGE_DIR", raising=False)
    result = subprocess.run(
        [sys.executable, "-c", "import career_copilot.main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_missing_jwt_secret_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValueError, match="JWT_SECRET is required"):
        AuthSettings.from_env()


@pytest.mark.parametrize("secret", ["", "short", " " * 40, "non-ascii-秘密" * 8])
def test_invalid_jwt_secret_is_not_exposed(secret: str) -> None:
    with pytest.raises(ValueError) as error:
        AuthSettings(jwt_secret=secret)
    if secret:
        assert secret not in str(error.value)


def test_jwt_secret_is_hidden(auth_settings: AuthSettings) -> None:
    assert auth_settings.jwt_secret not in repr(auth_settings)
    assert auth_settings.session_ttl_seconds == 1800


@pytest.mark.parametrize("ttl", [0, -1, 3601, True])
def test_invalid_ttl_is_rejected(ttl, auth_settings: AuthSettings) -> None:
    with pytest.raises(ValueError, match="JWT_SESSION_TTL_SECONDS"):
        AuthSettings(jwt_secret=auth_settings.jwt_secret, session_ttl_seconds=ttl)


def test_auth_settings_load_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-only-signing-key-never-use-in-deployment")
    monkeypatch.setenv("JWT_SESSION_TTL_SECONDS", "900")
    assert AuthSettings.from_env().session_ttl_seconds == 900
    monkeypatch.setenv("JWT_SESSION_TTL_SECONDS", "invalid-secret-like-input")
    with pytest.raises(ValueError) as error:
        AuthSettings.from_env()
    assert "invalid-secret-like-input" not in str(error.value)


async def test_startup_requires_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    app = create_app(Settings.from_url("postgresql+psycopg://localhost/health_test"))
    with pytest.raises(ValueError, match="JWT_SECRET is required"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must reject missing JWT configuration")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_resume_storage_configuration_required(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RESUME_STORAGE_DIR", raising=False)
    else:
        monkeypatch.setenv("RESUME_STORAGE_DIR", value)
    with pytest.raises(ValueError, match="RESUME_STORAGE_DIR is required"):
        ResumeSettings.from_env()


def test_resume_storage_configuration_is_explicit_and_private(monkeypatch, tmp_path):
    root = tmp_path / "private-resumes"
    monkeypatch.setenv("RESUME_STORAGE_DIR", str(root))
    configured = ResumeSettings.from_env()
    assert configured.storage_dir == root
    assert str(root) not in repr(configured)
    assert not root.exists()


async def test_startup_requires_resume_storage(monkeypatch, auth_settings):
    monkeypatch.delenv("RESUME_STORAGE_DIR", raising=False)
    app = create_app(
        Settings.from_url("postgresql+psycopg://localhost/health_test"), auth_settings
    )
    with pytest.raises(ValueError, match="RESUME_STORAGE_DIR is required"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must reject missing storage configuration")
