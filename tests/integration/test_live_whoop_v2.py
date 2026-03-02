from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest


LIVE_SECRETS_PATH = Path("tests/secrets/live_whoop.local.json")


def _developer_base() -> str:
    return os.getenv("WHOOP_API_BASE_URL", "https://api.prod.whoop.com/developer").rstrip("/")


def _load_live_secrets() -> dict:
    if not LIVE_SECRETS_PATH.exists():
        pytest.skip(
            "Live integration secrets are missing. Create tests/secrets/live_whoop.local.json from the example."
        )

    payload = json.loads(LIVE_SECRETS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        pytest.skip("Invalid live secrets format")

    if not payload.get("access_token"):
        pytest.skip("access_token is empty in live secrets")

    if payload.get("whoop_user_id") is None:
        pytest.skip("whoop_user_id is missing in live secrets")

    return payload


def _zulu(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_profile_and_user_id_contract():
    secrets = _load_live_secrets()
    token = secrets["access_token"]
    expected_user_id = int(secrets["whoop_user_id"])

    url = f"{_developer_base()}/v2/user/profile/basic"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    reported_user_id = payload.get("user_id", payload.get("id"))
    assert int(reported_user_id) == expected_user_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_v2_collections_contract_shape():
    secrets = _load_live_secrets()
    token = secrets["access_token"]

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=8)
    params = {"start": _zulu(start), "end": _zulu(end), "limit": "25"}
    headers = {"Authorization": f"Bearer {token}"}

    base = f"{_developer_base()}/v2"
    async with httpx.AsyncClient(timeout=10.0) as client:
        recovery_resp = await client.get(f"{base}/recovery", headers=headers, params=params)
        cycle_resp = await client.get(f"{base}/cycle", headers=headers, params=params)
        sleep_resp = await client.get(f"{base}/activity/sleep", headers=headers, params=params)
        workout_resp = await client.get(f"{base}/workout", headers=headers, params=params)

    assert recovery_resp.status_code == 200, recovery_resp.text
    assert cycle_resp.status_code == 200, cycle_resp.text
    assert sleep_resp.status_code == 200, sleep_resp.text
    assert workout_resp.status_code == 200, workout_resp.text

    recovery_payload = recovery_resp.json()
    cycle_payload = cycle_resp.json()
    sleep_payload = sleep_resp.json()
    workout_payload = workout_resp.json()

    for payload in (recovery_payload, cycle_payload, sleep_payload, workout_payload):
        assert isinstance(payload, dict)
        assert isinstance(payload.get("records"), list)

    if recovery_payload["records"]:
        first = recovery_payload["records"][0]
        assert isinstance(first.get("score_state"), str)

    if cycle_payload["records"]:
        first = cycle_payload["records"][0]
        assert isinstance(first.get("score_state"), str)

    if sleep_payload["records"]:
        first = sleep_payload["records"][0]
        assert isinstance(first.get("score_state"), str)
        assert "nap" in first

    if workout_payload["records"]:
        first = workout_payload["records"][0]
        assert isinstance(first.get("id"), (str, int))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_v2_cycle_30_day_window_contract_shape():
    secrets = _load_live_secrets()
    token = secrets["access_token"]

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    params = {"start": _zulu(start), "end": _zulu(end), "limit": "25"}
    headers = {"Authorization": f"Bearer {token}"}

    base = f"{_developer_base()}/v2"
    async with httpx.AsyncClient(timeout=10.0) as client:
        cycle_resp = await client.get(f"{base}/cycle", headers=headers, params=params)

    assert cycle_resp.status_code == 200, cycle_resp.text
    payload = cycle_resp.json()
    assert isinstance(payload, dict)
    assert isinstance(payload.get("records"), list)
