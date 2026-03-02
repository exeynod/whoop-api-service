from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

from app.deps import get_cache, get_rate_limiter, get_whoop_client
from app.main import create_app
from app.config import get_settings


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    path = tmp_path / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_secrets_dir(tmp_path: Path) -> Path:
    path = tmp_path / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def app_env(monkeypatch: pytest.MonkeyPatch, tmp_cache_dir: Path, tmp_secrets_dir: Path) -> None:
    monkeypatch.setenv("PROXY_API_KEY", "test-api-key")
    monkeypatch.setenv("WHOOP_CLIENT_ID", "client-id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("WHOOP_REDIRECT_URI", "http://127.0.0.1:8001/auth/callback")
    monkeypatch.setenv("WHOOP_API_BASE_URL", "https://api.prod.whoop.com/developer")
    monkeypatch.setenv("WHOOP_OAUTH_AUTHORIZE_URL", "https://api.prod.whoop.com/oauth/oauth2/auth")
    monkeypatch.setenv("WHOOP_OAUTH_TOKEN_URL", "https://api.prod.whoop.com/oauth/oauth2/token")
    monkeypatch.setenv("CACHE_DIR", str(tmp_cache_dir))
    monkeypatch.setenv("SECRETS_DIR", str(tmp_secrets_dir))
    monkeypatch.setenv("WHOOP_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("HEALTH_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("WHOOP_MIN_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("CACHE_RETENTION_DAYS", "30")
    monkeypatch.setenv("TZ", "Europe/Moscow")

    get_settings.cache_clear()
    get_cache.cache_clear()
    get_rate_limiter.cache_clear()
    get_whoop_client.cache_clear()

    yield

    get_settings.cache_clear()
    get_cache.cache_clear()
    get_rate_limiter.cache_clear()
    get_whoop_client.cache_clear()


@pytest.fixture
def frozen_msk_now() -> None:
    with freeze_time("2026-02-27 10:00:00", tz_offset=3):
        yield


@pytest.fixture
def test_app() -> TestClient:
    app = create_app()
    with TestClient(app) as client:
        yield client
