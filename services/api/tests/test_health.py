from fastapi.testclient import TestClient

import app.core.config as config_module
from app.main import app

client = TestClient(app)


def _set_env(monkeypatch, environment: str) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    config_module.get_settings.cache_clear()


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_videos_require_auth_outside_local(monkeypatch):
    _set_env(monkeypatch, "dev")
    response = client.get("/videos")
    assert response.status_code == 401


def test_chat_requires_auth_outside_local(monkeypatch):
    _set_env(monkeypatch, "dev")
    response = client.post("/videos/abc/chat", json={"question": "hi"})
    assert response.status_code == 401


def test_local_mode_allows_anonymous(monkeypatch):
    _set_env(monkeypatch, "local")
    import asyncio

    from app.auth.dependencies import get_current_user

    user = asyncio.run(get_current_user(None))
    assert user["user_id"] == "local-dev-user"
