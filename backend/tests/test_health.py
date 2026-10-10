from fastapi.testclient import TestClient

from career_copilot.main import create_app
from career_copilot.settings import AuthSettings, Settings


def test_health_returns_ok(auth_settings: AuthSettings) -> None:
    settings = Settings.from_url("postgresql+psycopg://localhost/health_test")
    with TestClient(create_app(settings, auth_settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
