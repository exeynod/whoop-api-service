from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.coach_router as coach_router
from app.deps import get_whoop_client
from app.main import create_app
from app.whoop_client import ReauthorizationRequiredError, WhoopTimeoutError


def _coach_day_payload(target_date: date, *, include_raw: bool = False, detail: str = "full") -> dict:
    payload = {
        "status": "ready",
        "date": target_date.isoformat(),
        "timezone": "Europe/Moscow",
        "day_start": f"{target_date.isoformat()}T00:00:00+03:00",
        "day_end": f"{target_date.isoformat()}T23:59:59+03:00",
        "generated_at": f"{target_date.isoformat()}T22:45:00+03:00",
        "freshness": {
            "recovery": {"status": "fresh", "updated_at": f"{target_date.isoformat()}T09:10:00+03:00", "source": "whoop"},
            "sleep": {"status": "fresh", "updated_at": f"{target_date.isoformat()}T09:10:00+03:00", "source": "whoop"},
            "day_strain": {"status": "fresh", "updated_at": f"{target_date.isoformat()}T22:30:00+03:00", "source": "whoop"},
            "workouts_today": {"status": "missing", "updated_at": None, "source": "whoop"},
            "body": {"status": "missing", "updated_at": None, "source": "whoop"},
        },
        "recovery": {"status": "ready", "score": 74, "zone": "green"},
        "sleep": {"status": "ready", "assigned_date": target_date.isoformat(), "assignment_rule": "wake_date"},
        "day_strain": {"status": "ready", "date": target_date.isoformat(), "score": 4.2, "is_final": False},
        "previous_day_strain": {"status": "ready", "score": 12.8, "is_final": True},
        "workouts_today": [],
        "workouts_yesterday": [],
        "body": {"status": "missing", "measured_at": None, "weight_kg": None, "height_m": None, "max_heart_rate": None},
        "raw_refs": {"cycle_id": 5000, "sleep_id": "s-today", "workout_ids": [], "body_measurement_id": None},
        "errors": [],
    }
    if detail == "surface":
        payload["recovery"].pop("created_at", None)
    if include_raw:
        payload["raw"] = {"cycle": {"id": 5000}, "recovery": {}, "sleep": {}, "workouts": [], "body": None}
    return payload


class CoachFakeClient:
    def __init__(self) -> None:
        self.tokens_valid = True
        self.profile_map = {"test-api-key": "denis"}
        self.coach_day_calls = 0
        self.body_calls = 0
        self.last_kwargs: dict | None = None
        self._coach_exc: Exception | None = None
        self._body_payload: dict | None = None

    def resolve_profile_name(self, api_token: str) -> str | None:
        return self.profile_map.get(api_token)

    async def fetch_coach_day(self, profile_name, target_date, *, include_raw=False, detail="full"):
        self.coach_day_calls += 1
        self.last_kwargs = {"include_raw": include_raw, "detail": detail, "date": target_date}
        if self._coach_exc:
            raise self._coach_exc
        return _coach_day_payload(target_date, include_raw=include_raw, detail=detail)

    async def fetch_coach_range(self, profile_name, end_date, days):
        self.range_calls = getattr(self, "range_calls", 0) + 1
        self.last_range = {"end_date": end_date, "days": days}
        rows = [
            {
                "date": (end_date).isoformat(),
                "recovery_score": 74,
                "recovery_zone": "green",
                "hrv_ms": 63,
                "resting_hr_bpm": 48,
                "spo2_percentage": 98.0,
                "skin_temp_celsius": 36.4,
                "recovery_score_state": "SCORED",
                "sleep_started_at": f"{end_date.isoformat()}T02:00:00+03:00",
                "sleep_ended_at": f"{end_date.isoformat()}T08:00:00+03:00",
                "sleep_total_hours": 6.4,
                "sleep_deep_hours": 0.9,
                "sleep_rem_hours": 1.2,
                "sleep_light_hours": 4.3,
                "sleep_efficiency_percentage": 84,
                "sleep_performance_percentage": 71,
                "sleep_consistency_percentage": 42,
                "sleep_respiratory_rate": 16.1,
                "sleep_disturbance_count": 12,
                "strain_score": 14.2,
                "strain_is_final": True,
                "kilojoules": 2400,
                "workout_count": 1,
                "workout_sports": ["volleyball"],
            }
        ]
        return {
            "period": {"from": end_date.isoformat(), "to": end_date.isoformat(), "days": days, "timezone": "Europe/Moscow"},
            "rows": rows,
            "workouts": [{"workout_id": "w1", "sport_name": "volleyball", "started_at": f"{end_date.isoformat()}T14:00:00+03:00"}],
            "nap_count": 0,
            "errors": [],
        }

    async def fetch_coach_status(self, profile_name):
        return {
            "status": "ok",
            "service_time": "2026-02-27T10:00:00+03:00",
            "timezone": "Europe/Moscow",
            "whoop": {"authorized": True, "reauthorization_required": False, "last_successful_sync": None},
        }

    async def fetch_body_measurements(self, profile_name):
        self.body_calls += 1
        if self._body_payload is not None:
            return self._body_payload
        return {
            "status": "ready",
            "measured_at": "2026-02-27T06:05:00Z",
            "height_meter": 1.83,
            "weight_kilogram": 83.2,
            "max_heart_rate": 195,
        }


def _client(fake: CoachFakeClient, now_dt: datetime) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_whoop_client] = lambda: fake
    coach_router._now_msk = lambda _settings: now_dt
    return TestClient(app)


NOW = datetime(2026, 2, 27, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
HEADERS = {"X-API-Key": "test-api-key"}


@pytest.mark.smoke
def test_coach_today_returns_full_object_with_aliases():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        response = client.get("/coach/today", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-02-27"
    assert body["day_start"] == "2026-02-27T00:00:00+03:00"
    # aliases equal canonical blocks
    assert body["today_strain"] == body["day_strain"]
    assert body["yesterday_strain"] == body["previous_day_strain"]
    assert body["raw_refs"]["cycle_id"] == 5000


@pytest.mark.smoke
def test_coach_today_is_heartbeat_safe_via_cache():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        first = client.get("/coach/today", headers=HEADERS)
        second = client.get("/coach/today", headers=HEADERS)

    assert first.status_code == 200 and second.status_code == 200
    assert fake.coach_day_calls == 1  # second served from cache


@pytest.mark.smoke
def test_coach_today_refresh_bypasses_cache():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        client.get("/coach/today", headers=HEADERS)
        client.get("/coach/today?refresh=true", headers=HEADERS)

    assert fake.coach_day_calls == 2


@pytest.mark.smoke
def test_coach_today_passes_include_raw_and_detail():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        response = client.get("/coach/today?include_raw=true&detail=surface", headers=HEADERS)

    assert response.status_code == 200
    assert "raw" in response.json()
    assert fake.last_kwargs == {"include_raw": True, "detail": "surface", "date": date(2026, 2, 27)}


@pytest.mark.smoke
def test_coach_day_past_date_and_invalid_date():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        ok = client.get("/coach/day/2026-02-20", headers=HEADERS)
        bad = client.get("/coach/day/not-a-date", headers=HEADERS)

    assert ok.status_code == 200
    assert ok.json()["date"] == "2026-02-20"
    assert "today_strain" not in ok.json()  # no aliases on /day
    assert bad.status_code == 422


@pytest.mark.smoke
def test_coach_status_reports_auth_and_blocks():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        # warm the cache so available_blocks reflect data
        client.get("/coach/today", headers=HEADERS)
        response = client.get("/coach/status", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["whoop"]["authorized"] is True
    assert set(body["cache"].keys()) == {"recovery_today", "sleep_latest", "strain_today", "workouts", "body"}
    assert body["available_blocks"]["recovery"] is True
    assert body["available_blocks"]["body"] is False


@pytest.mark.smoke
def test_coach_body_latest_ready_and_missing():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        ready = client.get("/coach/body/latest", headers=HEADERS)

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["weight_kg"] == 83.2

    fake2 = CoachFakeClient()
    fake2._body_payload = {"status": "pending", "reason": "Body measurements are not available yet."}
    with _client(fake2, NOW) as client:
        missing = client.get("/coach/body/latest", headers=HEADERS)

    assert missing.status_code == 200
    assert missing.json()["status"] == "missing"


@pytest.mark.smoke
def test_coach_routes_require_api_key():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        for path in ("/coach/today", "/coach/day/2026-02-27", "/coach/status", "/coach/body/latest"):
            assert client.get(path).status_code == 401


@pytest.mark.smoke
def test_coach_reauthorization_maps_to_401_without_secret_leak():
    fake = CoachFakeClient()
    fake._coach_exc = ReauthorizationRequiredError("Reauthorization required")
    with _client(fake, NOW) as client:
        response = client.get("/coach/today", headers=HEADERS)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "error"
    assert body["reason"] == "Reauthorization required"
    assert "test-api-key" not in response.text


@pytest.mark.smoke
def test_coach_upstream_timeout_maps_to_502():
    fake = CoachFakeClient()
    fake._coach_exc = WhoopTimeoutError("Connection timeout after 10s")
    with _client(fake, NOW) as client:
        response = client.get("/coach/today", headers=HEADERS)

    assert response.status_code == 502
    assert response.json()["reason"] == "Whoop API timeout"


@pytest.mark.smoke
def test_coach_week_returns_summary_days_and_workouts():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        response = client.get("/coach/week", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["period"]["days"] == 7
    assert body["summary"]["avg_recovery_score"] == 74.0
    assert body["summary"]["volleyball_count"] == 1
    assert len(body["days"]) == 1
    assert len(body["workouts"]) == 1
    assert fake.last_range == {"end_date": date(2026, 2, 27), "days": 7}


@pytest.mark.smoke
def test_coach_week_defaults_to_today_and_caches():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        client.get("/coach/week", headers=HEADERS)
        client.get("/coach/week", headers=HEADERS)

    assert fake.range_calls == 1  # shared bundle cache


@pytest.mark.smoke
def test_coach_training_context_load_summary():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        response = client.get("/coach/training-context?days=14", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert "load_summary" in body
    assert "strain_ratio_7d_vs_prev_7d" in body["load_summary"]
    assert len(body["daily_load"]) == 1
    assert fake.last_range == {"end_date": date(2026, 2, 27), "days": 14}


@pytest.mark.smoke
def test_coach_sleep_and_recovery_context():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        sleep_ctx = client.get("/coach/sleep-context", headers=HEADERS)
        recovery_ctx = client.get("/coach/recovery-context", headers=HEADERS)

    assert sleep_ctx.status_code == 200
    assert sleep_ctx.json()["summary"]["avg_total_hours"] == 6.4
    assert recovery_ctx.status_code == 200
    assert recovery_ctx.json()["summary"]["green_days"] == 1


@pytest.mark.smoke
def test_coach_context_routes_require_api_key():
    fake = CoachFakeClient()
    with _client(fake, NOW) as client:
        for path in ("/coach/week", "/coach/training-context", "/coach/sleep-context", "/coach/recovery-context"):
            assert client.get(path).status_code == 401
