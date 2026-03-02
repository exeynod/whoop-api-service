from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import respx

from app.config import get_settings
from app.whoop_client import ReauthorizationRequiredError, WhoopClient


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
                        },
                    }
                ]
            },
        )

        result = await client.fetch_recovery("denis", date(2026, 2, 27))

    assert result["status"] == "ready"
    assert result["recovery_score"] == 74
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
