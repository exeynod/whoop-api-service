from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.deps import get_whoop_client
from app.main import create_app
from app.whoop_client import WhoopUnavailableError


class RawFakeClient:
    def __init__(self) -> None:
        self.tokens_valid = True
        self.profile_map = {"test-api-key": "denis"}
        self.calls = 0
        self.last: dict | None = None
        self._exc: Exception | None = None

    def resolve_profile_name(self, api_token: str) -> str | None:
        return self.profile_map.get(api_token)

    async def fetch_raw_collection(self, profile_name, path, start, end, limit, next_token):
        self.calls += 1
        self.last = {"path": path, "limit": limit, "next_token": next_token}
        if self._exc:
            raise self._exc
        return {"records": [{"id": 1}], "next_token": "page-2"}


def _client(fake: RawFakeClient) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_whoop_client] = lambda: fake
    return TestClient(app)


HEADERS = {"X-API-Key": "test-api-key"}
Q = "start=2026-02-20T00:00:00%2B03:00&end=2026-02-26T23:59:59%2B03:00"


@pytest.mark.smoke
def test_raw_routes_require_api_key():
    fake = RawFakeClient()
    with _client(fake) as client:
        for name in ("cycles", "recoveries", "sleeps", "workouts"):
            assert client.get(f"/raw/{name}?{Q}").status_code == 401


@pytest.mark.smoke
def test_raw_cycles_passthrough_and_snake_case_next_token():
    fake = RawFakeClient()
    with _client(fake) as client:
        response = client.get(f"/raw/cycles?{Q}&limit=25&next_token=page-1", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["records"] == [{"id": 1}]
    assert body["next_token"] == "page-2"
    assert fake.last == {"path": "/v2/cycle", "limit": 25, "next_token": "page-1"}


@pytest.mark.smoke
def test_raw_pages_are_cached():
    fake = RawFakeClient()
    with _client(fake) as client:
        client.get(f"/raw/sleeps?{Q}", headers=HEADERS)
        client.get(f"/raw/sleeps?{Q}", headers=HEADERS)

    assert fake.calls == 1  # second served from cache


@pytest.mark.smoke
def test_raw_requires_timezone_aware_start():
    fake = RawFakeClient()
    with _client(fake) as client:
        response = client.get("/raw/workouts?start=2026-02-20T00:00:00", headers=HEADERS)

    assert response.status_code == 422


@pytest.mark.smoke
def test_raw_upstream_error_maps_to_502():
    fake = RawFakeClient()
    fake._exc = WhoopUnavailableError("down")
    with _client(fake) as client:
        response = client.get(f"/raw/recoveries?{Q}", headers=HEADERS)

    assert response.status_code == 502
    assert response.json()["reason"] == "Whoop API unavailable"
