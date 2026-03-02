from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import respx

from app.config import get_settings
from app.whoop_client import ReauthorizationRequiredError, UnexpectedWhoopResponseError, WhoopClient


def _write_profile_file(
    path,
    *,
    profile_name: str,
    api_token: str,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
    refresh_expires_at: datetime | None,
    active: bool = True,
):
    payload = {
        "version": 2,
        "profiles": {
            profile_name: {
                "api_token": api_token,
                "whoop": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": expires_at.isoformat(),
                    "refresh_expires_at": refresh_expires_at.isoformat() if refresh_expires_at else None,
                },
                "meta": {
                    "active": active,
                    "whoop_user_id": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            }
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_authorization_url_contains_expected_oauth_params():
    settings = get_settings()
    client = WhoopClient(settings)

    url = client.build_authorization_url("state-123")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [settings.whoop_client_id]
    assert query["redirect_uri"] == [settings.whoop_redirect_uri]
    assert query["state"] == ["state-123"]
    assert "read:recovery" in query["scope"][0]
    assert "read:workout" in query["scope"][0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tokens_valid_false_without_file(tmp_secrets_dir):
    settings = get_settings()
    client = WhoopClient(settings)

    assert client.tokens_valid is False
    assert not settings.token_path.exists()


@pytest.mark.unit
def test_resolve_profile_name_by_api_token():
    settings = get_settings()
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    client = WhoopClient(settings)
    assert client.resolve_profile_name("api-denis") == "denis"
    assert client.resolve_profile_name("wrong") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exchange_code_saves_tokens_for_target_profile():
    settings = get_settings()
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    client = WhoopClient(settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(settings.whoop_oauth_token_url).respond(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "refresh_token_expires_in": 86400,
            },
        )

        await client.exchange_code_for_tokens(profile_name="denis", code="auth-code")

    raw = json.loads(settings.token_path.read_text(encoding="utf-8"))
    profile = raw["profiles"]["denis"]
    assert profile["api_token"] == "api-denis"
    assert profile["whoop"]["access_token"] == "new-access"
    assert profile["whoop"]["refresh_token"] == "new-refresh"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_recovery_refreshes_expired_access_token():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="expired-access",
        refresh_token="refresh-token",
        expires_at=now - timedelta(seconds=10),
        refresh_expires_at=now + timedelta(days=5),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.post(settings.whoop_oauth_token_url).respond(
            200,
            json={
                "access_token": "fresh-access",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "refresh_token_expires_in": 86400,
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/recovery").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "start": "2026-02-26T22:00:00Z",
                        "end": "2026-02-27T06:00:00Z",
                        "score": {
                            "recovery_score": 74,
                            "resting_heart_rate": 48,
                            "hrv_rmssd_milli": 52,
                            "spo2_percentage": 97.1,
                            "skin_temp_celsius": 33.8,
                        },
                        "user_calibrating": False,
                    }
                ]
            },
        )

        result = await client.fetch_recovery("denis", date(2026, 2, 27))

    assert result["status"] == "ready"
    assert result["recovery_score"] == 74
    assert result["spo2_percentage"] == 97.1
    assert result["skin_temp_celsius"] == 33.8
    assert result["user_calibrating"] is False
    saved = json.loads(settings.token_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["denis"]["whoop"]["access_token"] == "fresh-access"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_recovery_returns_pending_when_not_scored():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/recovery").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "PROCESSING",
                        "start": "2026-02-26T22:00:00Z",
                        "end": "2026-02-27T06:00:00Z",
                    }
                ]
            },
        )

        result = await client.fetch_recovery("denis", date(2026, 2, 27))

    assert result == {
        "status": "pending",
        "reason": "Sleep not yet complete. Recovery will be available after wake.",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_recovery_ignores_records_from_other_day():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/recovery").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "start": "2026-02-28T01:00:00Z",
                        "end": "2026-02-28T05:00:00Z",
                        "score": {
                            "recovery_score": 80,
                            "resting_heart_rate": 45,
                            "hrv_rmssd_milli": 60,
                        },
                    }
                ]
            },
        )

        result = await client.fetch_recovery("denis", date(2026, 2, 27))

    assert result == {
        "status": "pending",
        "reason": "Sleep not yet complete. Recovery will be available after wake.",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_yesterday_snapshot_maps_cycle_and_sleep_payloads():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "end": "2026-02-26T20:00:00Z",
                        "score": {
                            "strain": 14.2,
                            "kilojoule": 1823,
                            "average_heart_rate": 112,
                            "max_heart_rate": 171,
                        },
                    }
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/sleep").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "nap": False,
                        "end": "2026-02-26T05:30:00Z",
                        "score": {
                            "sleep_performance_percentage": 88,
                            "respiratory_rate": 15.2,
                            "disturbance_count": 3,
                            "sleep_cycle_count": 5,
                            "sleep_consistency_percentage": 92,
                            "sleep_efficiency_percentage": 89,
                            "sleep_needed": {
                                "baseline_milli": 27000000,
                                "sleep_debt_milli": 720000,
                                "strain_related_need_milli": 1800000,
                            },
                            "stage_summary": {
                                "total_in_bed_time_milli": 26640000,
                                "total_awake_time_milli": 2520000,
                                "total_light_sleep_time_milli": 11520000,
                                "total_rem_sleep_time_milli": 6840000,
                                "total_slow_wave_sleep_time_milli": 5760000,
                            },
                        },
                    }
                ]
            },
        )

        result = await client.fetch_yesterday_snapshot("denis", date(2026, 2, 26))

    assert result["status"] == "ready"
    assert result["strain"]["score"] == 14.2
    assert result["strain"]["kilojoules"] == 1823
    assert result["sleep"]["respiratory_rate"] == 15.2
    assert result["sleep"]["stages"]["deep_hours"] == 1.6
    assert result["sleep"]["disturbance_count"] == 3
    assert result["sleep"]["sleep_cycle_count"] == 5
    assert result["sleep"]["consistency_percentage"] == 92
    assert result["sleep"]["efficiency_percentage"] == 89
    assert result["sleep"]["sleep_needed_hours"] == 7.5
    assert result["sleep"]["sleep_debt_hours"] == 0.2
    assert result["sleep"]["strain_related_need_hours"] == 0.5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_yesterday_snapshot_rejects_other_day_cycle():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "end": "2026-02-27T02:00:00Z",
                        "score": {
                            "strain": 15.6,
                            "kilojoule": 1999,
                            "average_heart_rate": 120,
                            "max_heart_rate": 176,
                        },
                    }
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/sleep").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "nap": False,
                        "end": "2026-02-26T05:30:00Z",
                        "score": {
                            "sleep_performance_percentage": 88,
                            "respiratory_rate": 15.2,
                            "stage_summary": {
                                "total_in_bed_time_milli": 26640000,
                                "total_awake_time_milli": 2520000,
                                "total_light_sleep_time_milli": 11520000,
                                "total_rem_sleep_time_milli": 6840000,
                                "total_slow_wave_sleep_time_milli": 5760000,
                            },
                        },
                    }
                ]
            },
        )

        with pytest.raises(UnexpectedWhoopResponseError):
            await client.fetch_yesterday_snapshot("denis", date(2026, 2, 26))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_yesterday_snapshot_uses_sleep_cycle_for_target_day():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(
            200,
            json={
                "records": [
                    {
                        "id": 1341472781,
                        "score_state": "SCORED",
                        "start": "2026-03-01T19:45:51.001Z",
                        "end": None,
                        "score": {
                            "strain": 4.6107326,
                            "kilojoule": 5110,
                            "average_heart_rate": 58,
                            "max_heart_rate": 128,
                        },
                    },
                    {
                        "id": 1339692348,
                        "score_state": "SCORED",
                        "start": "2026-02-28T23:45:42.865Z",
                        "end": "2026-03-01T19:45:51.001Z",
                        "score": {
                            "strain": 15.648103,
                            "kilojoule": 1823,
                            "average_heart_rate": 112,
                            "max_heart_rate": 171,
                        },
                    },
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/sleep").respond(
            200,
            json={
                "records": [
                    {
                        "id": "sleep-today",
                        "score_state": "SCORED",
                        "nap": False,
                        "start": "2026-03-01T19:45:51.001Z",
                        "end": "2026-03-02T03:53:12.237Z",
                        "cycle_id": 1341472781,
                        "score": {
                            "sleep_performance_percentage": 74,
                            "respiratory_rate": 13.8,
                            "stage_summary": {
                                "total_in_bed_time_milli": 29160000,
                                "total_awake_time_milli": 2520000,
                                "total_light_sleep_time_milli": 12960000,
                                "total_rem_sleep_time_milli": 5760000,
                                "total_slow_wave_sleep_time_milli": 8280000,
                            },
                        },
                    },
                    {
                        "id": "sleep-yesterday",
                        "score_state": "SCORED",
                        "nap": False,
                        "start": "2026-02-28T23:45:42.865Z",
                        "end": "2026-03-01T06:05:45.129Z",
                        "cycle_id": 1339692348,
                        "score": {
                            "sleep_performance_percentage": 72,
                            "respiratory_rate": 14.8,
                            "stage_summary": {
                                "total_in_bed_time_milli": 18000000,
                                "total_awake_time_milli": 1800000,
                                "total_light_sleep_time_milli": 9000000,
                                "total_rem_sleep_time_milli": 3600000,
                                "total_slow_wave_sleep_time_milli": 3600000,
                            },
                        },
                    },
                ]
            },
        )

        result = await client.fetch_yesterday_snapshot("denis", date(2026, 3, 1))

    assert result["status"] == "ready"
    assert result["strain"]["score"] == 15.6
    assert result["sleep"]["score"] == 72
    assert result["sleep"]["total_hours"] == 5.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_week_day_returns_missing_when_any_source_absent():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(200, json={"records": []})
        mock.get(f"{settings.whoop_api_base_url}/v2/recovery").respond(200, json={"records": []})
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/sleep").respond(200, json={"records": []})

        result = await client.fetch_week_day("denis", date(2026, 2, 25))

    assert result == {"date": "2026-02-25", "status": "missing"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_week_day_uses_recovery_and_cycle_linked_to_target_sleep():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(
            200,
            json={
                "records": [
                    {
                        "id": 1341472781,
                        "score_state": "SCORED",
                        "start": "2026-03-01T19:45:51.001Z",
                        "end": None,
                        "score": {
                            "strain": 4.6107326,
                            "kilojoule": 5110,
                            "average_heart_rate": 58,
                            "max_heart_rate": 128,
                        },
                    },
                    {
                        "id": 1339692348,
                        "score_state": "SCORED",
                        "start": "2026-02-28T23:45:42.865Z",
                        "end": "2026-03-01T19:45:51.001Z",
                        "score": {
                            "strain": 15.648103,
                            "kilojoule": 1823,
                            "average_heart_rate": 112,
                            "max_heart_rate": 171,
                        },
                    },
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/recovery").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "cycle_id": 1341472781,
                        "score": {
                            "recovery_score": 86,
                            "resting_heart_rate": 45,
                            "hrv_rmssd_milli": 110,
                        },
                    },
                    {
                        "score_state": "SCORED",
                        "cycle_id": 1339692348,
                        "score": {
                            "recovery_score": 70,
                            "resting_heart_rate": 49,
                            "hrv_rmssd_milli": 101,
                        },
                    },
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/sleep").respond(
            200,
            json={
                "records": [
                    {
                        "id": "sleep-today",
                        "score_state": "SCORED",
                        "nap": False,
                        "start": "2026-03-01T19:45:51.001Z",
                        "end": "2026-03-02T03:53:12.237Z",
                        "cycle_id": 1341472781,
                        "score": {
                            "sleep_performance_percentage": 74,
                            "respiratory_rate": 13.8,
                            "stage_summary": {
                                "total_in_bed_time_milli": 29160000,
                                "total_awake_time_milli": 2520000,
                                "total_light_sleep_time_milli": 12960000,
                                "total_rem_sleep_time_milli": 5760000,
                                "total_slow_wave_sleep_time_milli": 8280000,
                            },
                        },
                    },
                    {
                        "id": "sleep-yesterday",
                        "score_state": "SCORED",
                        "nap": False,
                        "start": "2026-02-28T23:45:42.865Z",
                        "end": "2026-03-01T06:05:45.129Z",
                        "cycle_id": 1339692348,
                        "score": {
                            "sleep_performance_percentage": 72,
                            "respiratory_rate": 14.8,
                            "stage_summary": {
                                "total_in_bed_time_milli": 18000000,
                                "total_awake_time_milli": 1800000,
                                "total_light_sleep_time_milli": 9000000,
                                "total_rem_sleep_time_milli": 3600000,
                                "total_slow_wave_sleep_time_milli": 3600000,
                            },
                        },
                    },
                ]
            },
        )

        result = await client.fetch_week_day("denis", date(2026, 3, 1))

    assert result["status"] == "ready"
    assert result["strain_score"] == 15.6
    assert result["sleep_score"] == 72
    assert result["sleep_hours"] == 5.0
    assert result["recovery_score"] == 70


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_week_day_returns_missing_when_records_are_other_day():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "end": "2026-02-26T01:00:00Z",
                        "score": {
                            "strain": 12.0,
                            "kilojoule": 1000,
                            "average_heart_rate": 100,
                            "max_heart_rate": 170,
                        },
                    }
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/recovery").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "end": "2026-02-26T01:00:00Z",
                        "score": {
                            "recovery_score": 70,
                            "resting_heart_rate": 50,
                            "hrv_rmssd_milli": 45,
                        },
                    }
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/sleep").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "nap": False,
                        "end": "2026-02-26T01:00:00Z",
                        "score": {
                            "sleep_performance_percentage": 85,
                            "respiratory_rate": 14.4,
                            "stage_summary": {
                                "total_in_bed_time_milli": 25200000,
                                "total_awake_time_milli": 1800000,
                                "total_light_sleep_time_milli": 10800000,
                                "total_rem_sleep_time_milli": 5400000,
                                "total_slow_wave_sleep_time_milli": 4500000,
                            },
                        },
                    }
                ]
            },
        )

        result = await client.fetch_week_day("denis", date(2026, 2, 25))

    assert result == {"date": "2026-02-25", "status": "missing"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_cycles_range_aggregates_and_paginates_by_local_day_token():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )
    msk = timezone(timedelta(hours=3))
    start = datetime(2026, 3, 1, 0, 0, tzinfo=msk)
    end = datetime(2026, 3, 2, 23, 59, tzinfo=msk)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(
            200,
            json={
                "records": [
                    {
                        "id": 1341472781,
                        "score_state": "SCORED",
                        "start": "2026-03-01T19:45:51.001Z",
                        "end": None,
                        "score": {
                            "strain": 4.6107326,
                            "kilojoule": 5110,
                            "average_heart_rate": 58,
                            "max_heart_rate": 128,
                        },
                    },
                    {
                        "id": 1339692348,
                        "score_state": "SCORED",
                        "start": "2026-02-28T23:45:42.865Z",
                        "end": "2026-03-01T19:45:51.001Z",
                        "score": {
                            "strain": 15.648103,
                            "kilojoule": 1823,
                            "average_heart_rate": 112,
                            "max_heart_rate": 171,
                        },
                    },
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/recovery").respond(
            200,
            json={
                "records": [
                    {
                        "score_state": "SCORED",
                        "cycle_id": 1341472781,
                        "score": {
                            "recovery_score": 86,
                            "resting_heart_rate": 45,
                            "hrv_rmssd_milli": 110,
                            "spo2_percentage": 97.2,
                            "skin_temp_celsius": 33.9,
                        },
                    },
                    {
                        "score_state": "SCORED",
                        "cycle_id": 1339692348,
                        "score": {
                            "recovery_score": 70,
                            "resting_heart_rate": 49,
                            "hrv_rmssd_milli": 101,
                            "spo2_percentage": 96.5,
                            "skin_temp_celsius": 33.6,
                        },
                    },
                ]
            },
        )
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/sleep").respond(
            200,
            json={
                "records": [
                    {
                        "id": "sleep-today",
                        "score_state": "SCORED",
                        "nap": False,
                        "start": "2026-03-01T19:45:51.001Z",
                        "end": "2026-03-02T03:53:12.237Z",
                        "cycle_id": 1341472781,
                        "score": {
                            "sleep_performance_percentage": 74,
                            "disturbance_count": 2,
                            "sleep_consistency_percentage": 91,
                            "sleep_efficiency_percentage": 88,
                            "respiratory_rate": 13.8,
                            "stage_summary": {
                                "total_in_bed_time_milli": 29160000,
                                "total_awake_time_milli": 2520000,
                                "total_light_sleep_time_milli": 12960000,
                                "total_rem_sleep_time_milli": 5760000,
                                "total_slow_wave_sleep_time_milli": 8280000,
                            },
                        },
                    },
                    {
                        "id": "sleep-yesterday",
                        "score_state": "SCORED",
                        "nap": False,
                        "start": "2026-02-28T23:45:42.865Z",
                        "end": "2026-03-01T06:05:45.129Z",
                        "cycle_id": 1339692348,
                        "score": {
                            "sleep_performance_percentage": 72,
                            "disturbance_count": 3,
                            "sleep_consistency_percentage": 89,
                            "sleep_efficiency_percentage": 84,
                            "respiratory_rate": 14.8,
                            "stage_summary": {
                                "total_in_bed_time_milli": 18000000,
                                "total_awake_time_milli": 1800000,
                                "total_light_sleep_time_milli": 9000000,
                                "total_rem_sleep_time_milli": 3600000,
                                "total_slow_wave_sleep_time_milli": 3600000,
                            },
                        },
                    },
                ]
            },
        )

        first = await client.fetch_cycles_range(
            profile_name="denis",
            start=start,
            end=end,
            limit=1,
            next_token=None,
        )
        second = await client.fetch_cycles_range(
            profile_name="denis",
            start=start,
            end=end,
            limit=1,
            next_token=first["next_token"],
        )

    assert first["status"] == "ready"
    assert first["period"] == {"from": "2026-03-01", "to": "2026-03-02"}
    assert len(first["days"]) == 1
    assert first["days"][0]["date"] == "2026-03-01"
    assert first["days"][0]["cycle_id"] == 1339692348
    assert first["days"][0]["recovery_score"] == 70
    assert first["days"][0]["sleep_disturbance_count"] == 3
    assert first["next_token"] == "2026-03-02"

    assert second["status"] == "ready"
    assert len(second["days"]) == 1
    assert second["days"][0]["date"] == "2026-03-02"
    assert second["days"][0]["cycle_id"] == 1341472781
    assert second["days"][0]["recovery_score"] == 86
    assert second["next_token"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_workouts_range_maps_payload_and_next_token():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )
    msk = timezone(timedelta(hours=3))
    start = datetime(2026, 3, 1, 0, 0, tzinfo=msk)
    end = datetime(2026, 3, 2, 23, 59, tzinfo=msk)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/activity/workout").respond(
            200,
            json={
                "records": [
                    {
                        "id": "w1",
                        "sport_name": "hockey",
                        "start": "2026-03-01T15:00:00Z",
                        "end": "2026-03-01T17:00:00Z",
                        "score": {
                            "strain": 12.43,
                            "kilojoule": 1823,
                            "average_heart_rate": 112,
                            "max_heart_rate": 171,
                            "distance_meter": 1772.77,
                            "altitude_gain_meter": 46.64,
                            "percent_recorded": 100,
                            "zone_duration": {
                                "zone_zero_milli": 300000,
                                "zone_one_milli": 600000,
                                "zone_two_milli": 900000,
                                "zone_three_milli": 900000,
                                "zone_four_milli": 600000,
                                "zone_five_milli": 300000,
                            },
                        },
                    },
                    {
                        "id": "w2",
                        "start": "2026-03-02T16:00:00Z",
                        "end": "2026-03-02T17:00:00Z",
                        "score": {"strain": 4.1},
                    },
                ],
                "nextToken": "next-page-2",
            },
        )

        result = await client.fetch_workouts_range(
            profile_name="denis",
            start=start,
            end=end,
            limit=10,
            next_token=None,
        )

    assert result["status"] == "ready"
    assert result["period"] == {"from": "2026-03-01", "to": "2026-03-02"}
    assert result["next_token"] == "next-page-2"
    assert len(result["workouts"]) == 2
    assert result["workouts"][0]["workout_id"] == "w1"
    assert result["workouts"][0]["sport_name"] == "hockey"
    assert result["workouts"][0]["zone_durations"]["zone_three_milli"] == 900000
    assert result["workouts"][1]["workout_id"] == "w2"
    assert result["workouts"][1]["sport_name"] == "unknown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_recovery_raises_reauthorization_when_refresh_expired():
    settings = get_settings()
    client = WhoopClient(settings)
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="access",
        refresh_token="refresh",
        expires_at=now - timedelta(hours=1),
        refresh_expires_at=now - timedelta(seconds=1),
    )

    with pytest.raises(ReauthorizationRequiredError):
        await client.fetch_recovery("denis", date(2026, 2, 27))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ping_returns_true_when_upstream_responds_even_unauthorized():
    settings = get_settings()
    client = WhoopClient(settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{settings.whoop_api_base_url}/v2/user/profile/basic").respond(
            401,
            json={"message": "Unauthorized"},
        )
        assert await client.ping(timeout_seconds=1.0) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_whoop_http_logging_contains_request_and_response_events(caplog: pytest.LogCaptureFixture):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    _write_profile_file(
        settings.token_path,
        profile_name="denis",
        api_token="api-denis",
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=7),
    )
    client = WhoopClient(settings)

    caplog.set_level(logging.INFO, logger="app.whoop_client")
    with respx.mock(assert_all_called=True) as mock:
        mock.post(settings.whoop_oauth_token_url).respond(
            200,
            json={
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
            },
        )
        await client.exchange_code_for_tokens(profile_name="denis", code="auth-code-123")

    events = [json.loads(record.message) for record in caplog.records if record.name == "app.whoop_client"]
    event_types = [event.get("event") for event in events]
    assert "whoop_http_request" in event_types
    assert "whoop_http_response" in event_types

    request_event = next(event for event in events if event.get("event") == "whoop_http_request")
    response_event = next(event for event in events if event.get("event") == "whoop_http_response")

    assert request_event["channel"] == "whoop_oauth"
    assert request_event["method"] == "POST"
    assert "client_secret" in request_event["data"]
    assert request_event["data"]["client_secret"].startswith("clie***")
    assert request_event["data"]["code"].startswith("auth***")

    assert response_event["channel"] == "whoop_oauth"
    assert response_event["status_code"] == 200
    assert response_event["body_truncated"] is False
    assert "access_token" in response_event["body"]
    assert "new-access-token" not in response_event["body"]


@pytest.mark.unit
def test_day_bounds_are_computed_in_msk_timezone():
    settings = get_settings()
    client = WhoopClient(settings)

    start_utc, end_utc = client._day_bounds_utc(date(2026, 2, 27))

    assert start_utc.isoformat() == "2026-02-26T21:00:00+00:00"
    assert end_utc.isoformat() == "2026-02-27T21:00:00+00:00"
