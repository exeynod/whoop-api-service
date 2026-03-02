from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.router as data_router
from app.config import get_settings
from app.deps import get_cache, get_rate_limiter, get_whoop_client
from app.main import create_app
from app.whoop_client import ReauthorizationRequiredError, WhoopTimeoutError, WhoopUnavailableError


class FakeWhoopClient:
    def __init__(self) -> None:
        self.tokens_valid = True
        self.recovery_calls = 0
        self.day_calls = 0
        self.week_calls = 0
        self.cycles_calls = 0
        self.workouts_calls = 0
        self.profile_resolve_calls = 0
        self.profile_map = {"test-api-key": "denis"}
        self._recovery_payloads: list[dict] = []
        self._day_payloads: list[dict] = []
        self._week_payloads: list[dict] = []
        self._cycles_payloads: list[dict] = []
        self._workouts_payloads: list[dict] = []
        self._recovery_exc: Exception | None = None
        self._day_exc: Exception | None = None
        self._week_exc: Exception | None = None
        self._cycles_exc: Exception | None = None
        self._workouts_exc: Exception | None = None
        self.last_cycles_args: dict | None = None
        self.last_workouts_args: dict | None = None

    def resolve_profile_name(self, api_token: str) -> str | None:
        self.profile_resolve_calls += 1
        return self.profile_map.get(api_token)

    def build_authorization_url(self, state: str) -> str:
        return f"https://example.test/oauth?state={state}"

    async def exchange_code_for_tokens(self, profile_name: str, code: str) -> None:
        _ = profile_name
        _ = code

    async def ping(self, timeout_seconds: float) -> bool:
        _ = timeout_seconds
        return True

    async def fetch_recovery(self, profile_name: str, target_date: date) -> dict:
        _ = profile_name
        _ = target_date
        self.recovery_calls += 1
        if self._recovery_exc:
            raise self._recovery_exc
        if self._recovery_payloads:
            return self._recovery_payloads.pop(0)
        return {
            "status": "pending",
            "reason": "Sleep not yet complete. Recovery will be available after wake.",
        }

    async def fetch_yesterday_snapshot(self, profile_name: str, target_date: date) -> dict:
        _ = profile_name
        _ = target_date
        self.day_calls += 1
        if self._day_exc:
            raise self._day_exc
        if self._day_payloads:
            return self._day_payloads.pop(0)
        return {
            "status": "ready",
            "date": "2026-02-26",
            "strain": {
                "score": 14.2,
                "kilojoules": 1823,
                "avg_hr_bpm": 112,
                "max_hr_bpm": 171,
            },
            "sleep": {
                "score": 81,
                "total_hours": 7.4,
                "performance_percent": 88,
                "respiratory_rate": 15.2,
                "stages": {
                    "deep_hours": 1.6,
                    "rem_hours": 1.9,
                    "light_hours": 3.2,
                    "awake_hours": 0.7,
                },
            },
        }

    async def fetch_week_day(self, profile_name: str, target_date: date) -> dict:
        _ = profile_name
        _ = target_date
        self.week_calls += 1
        if self._week_exc:
            raise self._week_exc
        if self._week_payloads:
            return self._week_payloads.pop(0)
        return {
            "date": target_date.isoformat(),
            "status": "ready",
            "recovery_score": 74,
            "recovery_zone": "yellow",
            "hrv_ms": 52,
            "resting_hr_bpm": 48,
            "strain_score": 14.2,
            "sleep_score": 81,
            "sleep_hours": 7.4,
        }

    async def fetch_cycles_range(
        self,
        profile_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        next_token: str | None,
    ) -> dict:
        _ = profile_name
        self.cycles_calls += 1
        self.last_cycles_args = {
            "start": start,
            "end": end,
            "limit": limit,
            "next_token": next_token,
        }
        if self._cycles_exc:
            raise self._cycles_exc
        if self._cycles_payloads:
            return self._cycles_payloads.pop(0)
        return {
            "status": "ready",
            "period": {"from": "2026-02-20", "to": "2026-02-26"},
            "days": [
                {
                    "date": "2026-02-26",
                    "cycle_id": 123456,
                    "recovery_score": 73,
                    "recovery_zone": "yellow",
                    "hrv_ms": 52,
                    "resting_hr_bpm": 48,
                    "strain_score": 14.1,
                    "sleep_score": 85,
                    "sleep_hours": 7.4,
                }
            ],
            "next_token": None,
        }

    async def fetch_workouts_range(
        self,
        profile_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        next_token: str | None,
    ) -> dict:
        _ = profile_name
        self.workouts_calls += 1
        self.last_workouts_args = {
            "start": start,
            "end": end,
            "limit": limit,
            "next_token": next_token,
        }
        if self._workouts_exc:
            raise self._workouts_exc
        if self._workouts_payloads:
            return self._workouts_payloads.pop(0)
        return {
            "status": "ready",
            "period": {"from": "2026-02-20", "to": "2026-02-26"},
            "workouts": [
                {
                    "workout_id": "workout-1",
                    "date": "2026-02-26",
                    "sport_name": "hockey",
                    "start": "2026-02-26T18:00:00Z",
                    "end": "2026-02-26T19:30:00Z",
                    "strain_score": 12.4,
                    "kilojoules": 1210,
                    "zone_durations": {
                        "zone_zero_milli": 1000,
                        "zone_one_milli": 2000,
                        "zone_two_milli": 3000,
                        "zone_three_milli": 4000,
                        "zone_four_milli": 5000,
                        "zone_five_milli": 6000,
                    },
                }
            ],
            "next_token": None,
        }


def _client_with_fake_whoop(fake: FakeWhoopClient, now_dt: datetime) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_whoop_client] = lambda: fake
    data_router._now_msk = lambda _settings: now_dt
    return TestClient(app)


@pytest.mark.smoke
def test_health_is_public_and_returns_payload():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "whoop_reachable": True,
        "tokens_valid": True,
    }


@pytest.mark.smoke
def test_health_timeout_is_non_blocking(monkeypatch: pytest.MonkeyPatch):
    class SlowWhoopClient(FakeWhoopClient):
        async def ping(self, timeout_seconds: float) -> bool:
            await asyncio.sleep(timeout_seconds * 10)
            return True

    monkeypatch.setenv("HEALTH_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    get_cache.cache_clear()
    get_rate_limiter.cache_clear()
    get_whoop_client.cache_clear()

    fake = SlowWhoopClient()
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    with _client_with_fake_whoop(fake, now_dt) as client:
        started = time.monotonic()
        response = client.get("/health")
        elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json()["whoop_reachable"] is False
    assert elapsed < 0.6


@pytest.mark.smoke
def test_auth_routes_are_public():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        init_response = client.get("/auth/init", params={"profile": "denis"}, follow_redirects=False)
        callback_response = client.get("/auth/callback", params={"code": "abc", "profile": "denis"})

    assert init_response.status_code == 307
    assert callback_response.status_code == 200
    assert callback_response.json()["status"] == "authorized"


@pytest.mark.smoke
def test_auth_callback_validates_query_params():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        missing_code = client.get("/auth/callback")
        oauth_error = client.get("/auth/callback", params={"error": "access_denied"})

    assert missing_code.status_code == 400
    assert oauth_error.status_code == 400


@pytest.mark.smoke
def test_data_routes_require_api_key():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get("/recovery/today")

    assert response.status_code == 401


@pytest.mark.smoke
def test_recovery_is_cached_after_first_ready(tmp_cache_dir):
    fake = FakeWhoopClient()
    fake._recovery_payloads = [
        {
            "status": "ready",
            "date": "2026-02-27",
            "recovery_score": 74,
            "recovery_zone": "yellow",
            "hrv_ms": 52,
            "resting_hr_bpm": 48,
        }
    ]
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        headers = {"X-API-Key": "test-api-key"}
        first = client.get("/recovery/today", headers=headers)
        second = client.get("/recovery/today", headers=headers)

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert fake.recovery_calls == 1

    cache_file = tmp_cache_dir / "denis" / "recovery_2026-02-27.json"
    assert cache_file.exists()
    cached_payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached_payload["status"] == "ready"


@pytest.mark.smoke
def test_recovery_rate_limit_replays_last_pending():
    fake = FakeWhoopClient()
    fake._recovery_payloads = [
        {
            "status": "pending",
            "reason": "Sleep not yet complete. Recovery will be available after wake.",
        }
    ]
    now_dt = datetime(2026, 2, 27, 4, 10, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        headers = {"X-API-Key": "test-api-key"}
        first = client.get("/recovery/today", headers=headers)
        second = client.get("/recovery/today", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    assert second.status_code == 200
    assert second.json()["status"] == "pending"
    assert fake.recovery_calls == 1


@pytest.mark.smoke
def test_recovery_timeout_maps_to_502_error_payload():
    fake = FakeWhoopClient()
    fake._recovery_exc = WhoopTimeoutError("Connection timeout after 10s")
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get("/recovery/today", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert response.json()["reason"] == "Whoop API timeout"


@pytest.mark.smoke
def test_recovery_reauthorization_maps_to_401_error_payload():
    fake = FakeWhoopClient()
    fake._recovery_exc = ReauthorizationRequiredError("Reauthorization required")
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get("/recovery/today", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 401
    assert response.json()["status"] == "error"
    assert response.json()["reason"] == "Reauthorization required"


@pytest.mark.smoke
def test_yesterday_cached_after_first_success():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        headers = {"X-API-Key": "test-api-key"}
        first = client.get("/day/yesterday", headers=headers)
        second = client.get("/day/yesterday", headers=headers)

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert fake.day_calls == 1


@pytest.mark.smoke
def test_yesterday_returns_502_when_upstream_fails_without_cache():
    fake = FakeWhoopClient()
    fake._day_exc = WhoopUnavailableError("upstream unavailable")
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get("/day/yesterday", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert response.json()["reason"] == "Whoop API unavailable"


@pytest.mark.smoke
def test_week_partial_cache_merge(tmp_cache_dir):
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    cached_day = {
        "date": "2026-02-20",
        "status": "ready",
        "recovery_score": 80,
        "recovery_zone": "green",
        "hrv_ms": 60,
        "resting_hr_bpm": 46,
        "strain_score": 11.1,
        "sleep_score": 84,
        "sleep_hours": 7.8,
    }
    profile_dir = tmp_cache_dir / "denis"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "week_2026-02-20.json").write_text(json.dumps(cached_day), encoding="utf-8")

    fake._week_payloads = [
        {
            "date": "2026-02-21",
            "status": "ready",
            "recovery_score": 70,
            "recovery_zone": "yellow",
            "hrv_ms": 50,
            "resting_hr_bpm": 49,
            "strain_score": 12.0,
            "sleep_score": 82,
            "sleep_hours": 7.1,
        },
        {"date": "2026-02-22", "status": "missing"},
        {
            "date": "2026-02-23",
            "status": "ready",
            "recovery_score": 68,
            "recovery_zone": "yellow",
            "hrv_ms": 49,
            "resting_hr_bpm": 50,
            "strain_score": 10.8,
            "sleep_score": 80,
            "sleep_hours": 7.0,
        },
        {
            "date": "2026-02-24",
            "status": "ready",
            "recovery_score": 72,
            "recovery_zone": "green",
            "hrv_ms": 53,
            "resting_hr_bpm": 47,
            "strain_score": 13.4,
            "sleep_score": 86,
            "sleep_hours": 7.5,
        },
        {
            "date": "2026-02-25",
            "status": "ready",
            "recovery_score": 75,
            "recovery_zone": "green",
            "hrv_ms": 54,
            "resting_hr_bpm": 46,
            "strain_score": 13.8,
            "sleep_score": 87,
            "sleep_hours": 7.7,
        },
        {
            "date": "2026-02-26",
            "status": "ready",
            "recovery_score": 73,
            "recovery_zone": "yellow",
            "hrv_ms": 52,
            "resting_hr_bpm": 48,
            "strain_score": 14.1,
            "sleep_score": 85,
            "sleep_hours": 7.4,
        },
    ]

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get("/week", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == {"from": "2026-02-20", "to": "2026-02-26"}
    assert len(payload["days"]) == 7
    assert fake.week_calls == 6


@pytest.mark.smoke
def test_week_returns_502_if_upstream_error_and_missing_cache():
    fake = FakeWhoopClient()
    fake._week_exc = WhoopUnavailableError("Whoop down")
    now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get("/week", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert response.json()["reason"] == "Whoop API unavailable"


@pytest.mark.smoke
def test_cycles_is_cached_after_first_ready():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 3, 2, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    query = "?start=2026-02-20T00:00:00%2B03:00&end=2026-02-26T23:59:59%2B03:00&limit=10"

    with _client_with_fake_whoop(fake, now_dt) as client:
        headers = {"X-API-Key": "test-api-key"}
        first = client.get(f"/cycles{query}", headers=headers)
        second = client.get(f"/cycles{query}", headers=headers)

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert first.json()["timezone_offset"] == "+03:00"
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert second.json()["timezone_offset"] == "+03:00"
    assert fake.cycles_calls == 1


@pytest.mark.smoke
def test_cycles_rejects_invalid_next_token():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 3, 2, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    with _client_with_fake_whoop(fake, now_dt) as client:
        response = client.get(
            "/cycles?start=2026-02-20T00:00:00%2B03:00&end=2026-02-26T23:59:59%2B03:00&next_token=bad-value",
            headers={"X-API-Key": "test-api-key"},
        )

    assert response.status_code == 422


@pytest.mark.smoke
def test_workouts_is_cached_after_first_ready_and_passes_query_params():
    fake = FakeWhoopClient()
    now_dt = datetime(2026, 3, 2, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    query = "?start=2026-02-20T00:00:00%2B03:00&end=2026-02-26T23:59:59%2B03:00&limit=5&next_token=abc"

    with _client_with_fake_whoop(fake, now_dt) as client:
        headers = {"X-API-Key": "test-api-key"}
        first = client.get(f"/workouts{query}", headers=headers)
        second = client.get(f"/workouts{query}", headers=headers)

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert fake.workouts_calls == 1
    assert fake.last_workouts_args is not None
    assert fake.last_workouts_args["limit"] == 5
    assert fake.last_workouts_args["next_token"] == "abc"
