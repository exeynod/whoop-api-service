from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
import respx
from freezegun import freeze_time

from app.config import get_settings
from app.whoop_client import WhoopClient

MSK = timezone(timedelta(hours=3))


def _write_profile_file(path, *, profile_name="denis", api_token="api-denis"):
    now = datetime.now(timezone.utc)
    payload = {
        "version": 2,
        "profiles": {
            profile_name: {
                "api_token": api_token,
                "whoop": {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                    "refresh_expires_at": (now + timedelta(days=7)).isoformat(),
                },
                "meta": {"active": True, "whoop_user_id": 1, "created_at": now.isoformat(), "updated_at": now.isoformat()},
            }
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _base():
    return get_settings().whoop_api_base_url


# Records for the 2026-02-27 coach day. Cycle 5000 is the current (open) cycle;
# cycle 4999 closed at the 27th 00:00 and belongs to the previous day.
def _cycles():
    return [
        {
            "id": 5000,
            "score_state": "SCORED",
            "start": "2026-02-27T00:00:00Z",
            "end": None,
            "updated_at": "2026-02-27T12:45:00Z",
            "score": {"strain": 4.2, "kilojoule": 650, "average_heart_rate": 78, "max_heart_rate": 122},
        },
        {
            "id": 4999,
            "score_state": "SCORED",
            "start": "2026-02-26T00:00:00Z",
            "end": "2026-02-27T00:00:00Z",
            "updated_at": "2026-02-27T00:05:00Z",
            "score": {"strain": 12.8, "kilojoule": 2400, "average_heart_rate": 91, "max_heart_rate": 160},
        },
    ]


def _recoveries():
    return [
        {
            "cycle_id": 5000,
            "sleep_id": "sleep-today",
            "score_state": "SCORED",
            "created_at": "2026-02-27T06:00:00Z",
            "updated_at": "2026-02-27T06:10:00Z",
            "score": {
                "recovery_score": 74,
                "resting_heart_rate": 48,
                "hrv_rmssd_milli": 63,
                "spo2_percentage": 98.0,
                "skin_temp_celsius": 36.4,
                "user_calibrating": False,
            },
        },
        {
            "cycle_id": 4999,
            "sleep_id": "sleep-yesterday",
            "score_state": "SCORED",
            "created_at": "2026-02-26T06:00:00Z",
            "updated_at": "2026-02-26T06:10:00Z",
            "score": {"recovery_score": 55, "resting_heart_rate": 52, "hrv_rmssd_milli": 40},
        },
    ]


def _sleeps():
    return [
        {
            "id": "sleep-today",
            "cycle_id": 5000,
            "score_state": "SCORED",
            "nap": False,
            "start": "2026-02-26T23:00:00Z",
            "end": "2026-02-27T05:00:00Z",
            "updated_at": "2026-02-27T05:10:00Z",
            "score": {
                "sleep_performance_percentage": 71,
                "sleep_efficiency_percentage": 84,
                "respiratory_rate": 16.1,
                "stage_summary": {
                    "total_in_bed_time_milli": 21_600_000,
                    "total_light_sleep_time_milli": 14_400_000,
                    "total_slow_wave_sleep_time_milli": 3_600_000,
                    "total_rem_sleep_time_milli": 3_600_000,
                    "total_awake_time_milli": 0,
                },
            },
        },
        {
            "id": "sleep-yesterday",
            "cycle_id": 4999,
            "score_state": "SCORED",
            "nap": False,
            "start": "2026-02-25T23:00:00Z",
            "end": "2026-02-26T05:00:00Z",
            "updated_at": "2026-02-26T05:10:00Z",
            "score": {"sleep_performance_percentage": 80, "stage_summary": {"total_in_bed_time_milli": 21_600_000}},
        },
    ]


def _workouts():
    return [
        {
            "id": "w-today",
            "sport_name": "volleyball",
            "sport_id": 43,
            "start": "2026-02-27T11:00:00Z",
            "end": "2026-02-27T12:45:00Z",
            "score_state": "SCORED",
            "updated_at": "2026-02-27T13:00:00Z",
            "score": {"strain": 10.4, "kilojoule": 1350, "average_heart_rate": 124, "max_heart_rate": 172},
        },
        {
            "id": "w-yesterday",
            "sport_name": "weightlifting",
            "start": "2026-02-26T16:00:00Z",
            "end": "2026-02-26T17:00:00Z",
            "score_state": "SCORED",
            "score": {"strain": 8.1},
        },
    ]


def _mock_all(mock, *, cycles=None, recoveries=None, sleeps=None, workouts=None, body=None, workout_status=200):
    mock.get(f"{_base()}/v2/cycle").respond(200, json={"records": cycles if cycles is not None else _cycles()})
    mock.get(f"{_base()}/v2/recovery").respond(200, json={"records": recoveries if recoveries is not None else _recoveries()})
    mock.get(f"{_base()}/v2/activity/sleep").respond(200, json={"records": sleeps if sleeps is not None else _sleeps()})
    if workout_status != 200:
        mock.get(f"{_base()}/v2/activity/workout").respond(workout_status, json={"message": "boom"})
    else:
        mock.get(f"{_base()}/v2/activity/workout").respond(200, json={"records": workouts if workouts is not None else _workouts()})
    mock.get(f"{_base()}/v2/user/measurement/body").respond(
        200, json=body if body is not None else {"height_meter": 1.83, "weight_kilogram": 83.2, "max_heart_rate": 195}
    )


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_coach_day_happy_path_assembles_all_blocks(tmp_secrets_dir):
    settings = get_settings()
    _write_profile_file(settings.token_path)
    client = WhoopClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        _mock_all(mock)
        result = await client.fetch_coach_day("denis", date(2026, 2, 27))

    assert result["status"] == "ready"
    assert result["date"] == "2026-02-27"
    assert result["timezone"] == "Europe/Moscow"
    assert result["day_start"] == "2026-02-27T00:00:00+03:00"
    assert result["day_end"] == "2026-02-27T23:59:59+03:00"
    assert result["errors"] == []

    assert result["recovery"]["status"] == "ready"
    assert result["recovery"]["score"] == 74
    assert result["recovery"]["zone"] == "green"
    assert result["sleep"]["status"] == "ready"
    assert result["sleep"]["assigned_date"] == "2026-02-27"
    assert result["day_strain"]["is_final"] is False  # current open cycle
    assert result["previous_day_strain"]["is_final"] is True
    assert result["previous_day_strain"]["score"] == 12.8

    assert [w["workout_id"] for w in result["workouts_today"]] == ["w-today"]
    assert [w["workout_id"] for w in result["workouts_yesterday"]] == ["w-yesterday"]
    assert result["body"]["status"] == "ready"
    assert result["body"]["weight_kg"] == 83.2


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_coach_day_correlation_links_same_cycle(tmp_secrets_dir):
    """recovery/strain/sleep must resolve to the SAME cycle via cycle_id linkage."""
    settings = get_settings()
    _write_profile_file(settings.token_path)
    client = WhoopClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        _mock_all(mock)
        result = await client.fetch_coach_day("denis", date(2026, 2, 27))

    assert result["recovery"]["cycle_id"] == 5000
    assert result["sleep"]["cycle_id"] == 5000
    assert result["day_strain"]["cycle_id"] == 5000
    # not the previous day's cycle/recovery
    assert result["recovery"]["score"] == 74
    assert result["raw_refs"]["cycle_id"] == 5000
    assert result["raw_refs"]["sleep_id"] == "sleep-today"
    assert result["raw_refs"]["workout_ids"] == ["w-today"]


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_coach_day_partial_when_one_block_fails(tmp_secrets_dir):
    settings = get_settings()
    _write_profile_file(settings.token_path)
    client = WhoopClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        _mock_all(mock, workout_status=502)
        result = await client.fetch_coach_day("denis", date(2026, 2, 27))

    assert result["status"] == "partial"
    assert result["workouts_today"] == []
    blocks_in_errors = {e["block"] for e in result["errors"]}
    assert "workouts_today" in blocks_in_errors
    # other blocks still populated
    assert result["recovery"]["status"] == "ready"
    assert result["sleep"]["status"] == "ready"
    assert result["day_strain"]["status"] == "ready"
    assert result["freshness"]["workouts_today"]["status"] == "missing"


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_coach_day_recovery_pending_does_not_error(tmp_secrets_dir):
    settings = get_settings()
    _write_profile_file(settings.token_path)
    client = WhoopClient(settings)

    pending_recoveries = [
        {"cycle_id": 5000, "sleep_id": "sleep-today", "score_state": "PENDING_SCORE", "updated_at": "2026-02-27T05:30:00Z"}
    ]
    with respx.mock(assert_all_called=False) as mock:
        _mock_all(mock, recoveries=pending_recoveries)
        result = await client.fetch_coach_day("denis", date(2026, 2, 27))

    assert result["recovery"]["status"] == "pending"
    assert not any(e["block"] == "recovery" for e in result["errors"])
    assert result["status"] == "partial"  # some ready, recovery pending


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_coach_day_body_missing_is_not_an_error(tmp_secrets_dir):
    settings = get_settings()
    _write_profile_file(settings.token_path)
    client = WhoopClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_base()}/v2/cycle").respond(200, json={"records": _cycles()})
        mock.get(f"{_base()}/v2/recovery").respond(200, json={"records": _recoveries()})
        mock.get(f"{_base()}/v2/activity/sleep").respond(200, json={"records": _sleeps()})
        mock.get(f"{_base()}/v2/activity/workout").respond(200, json={"records": _workouts()})
        mock.get(f"{_base()}/v2/user/measurement/body").respond(404, json={"message": "Not Found"})
        result = await client.fetch_coach_day("denis", date(2026, 2, 27))

    assert result["body"]["status"] == "missing"
    assert not any(e["block"] == "body" for e in result["errors"])
    assert result["status"] == "ready"  # body missing does not break ready
    assert result["freshness"]["body"]["status"] == "missing"


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_coach_day_freshness_sources_and_windows(tmp_secrets_dir):
    settings = get_settings()
    _write_profile_file(settings.token_path)
    client = WhoopClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        _mock_all(mock)
        result = await client.fetch_coach_day("denis", date(2026, 2, 27))

    fr = result["freshness"]
    assert fr["recovery"]["status"] == "fresh" and fr["recovery"]["source"] == "whoop"
    assert fr["sleep"]["status"] == "fresh"
    # now == 13:00 UTC (freeze base + tz_offset); cycle updated 12:45 UTC -> 15 min, within 45 min
    assert fr["day_strain"]["status"] == "fresh"


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_coach_day_include_raw_adds_raw_block(tmp_secrets_dir):
    settings = get_settings()
    _write_profile_file(settings.token_path)
    client = WhoopClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        _mock_all(mock)
        without = await client.fetch_coach_day("denis", date(2026, 2, 27))
        with_raw = await client.fetch_coach_day("denis", date(2026, 2, 27), include_raw=True)

    assert "raw" not in without
    assert "raw_refs" in without
    assert with_raw["raw"]["cycle"]["id"] == 5000
    assert with_raw["raw"]["recovery"]["cycle_id"] == 5000
    assert [w["id"] for w in with_raw["raw"]["workouts"]] == ["w-today"]
