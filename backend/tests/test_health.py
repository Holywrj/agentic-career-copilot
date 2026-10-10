from fastapi.testclient import TestClient

from career_copilot.main import create_app
from career_copilot.settings import Settings


def test_health_returns_ok() -> None:
    settings = Settings.from_url("postgresql+psycopg://localhost/health_test")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
