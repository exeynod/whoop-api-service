from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import respx

from app.config import get_settings
from app.whoop_client import WhoopClient


def _write_profile(path):
    now = datetime.now(timezone.utc)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "profiles": {
                    "denis": {
                        "api_token": "api-denis",
                        "whoop": {
                            "access_token": "a",
                            "refresh_token": "r",
                            "expires_at": (now + timedelta(hours=1)).isoformat(),
                            "refresh_expires_at": (now + timedelta(days=7)).isoformat(),
                        },
                        "meta": {"active": True},
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_raw_collection_forwards_next_token_and_returns_snake_case(tmp_secrets_dir):
    settings = get_settings()
    _write_profile(settings.token_path)
    client = WhoopClient(settings)
    msk = timezone(timedelta(hours=3))
    start = datetime(2026, 2, 20, 0, 0, tzinfo=msk)
    end = datetime(2026, 2, 26, 23, 59, tzinfo=msk)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{settings.whoop_api_base_url}/v2/cycle").respond(
            200, json={"records": [{"id": 1}, {"id": 2}], "next_token": "page-2"}
        )
        result = await client.fetch_raw_collection(
            "denis", "/v2/cycle", start=start, end=end, limit=25, next_token="page-1"
        )

    # request advances pages via camelCase nextToken
    request_url = str(route.calls[0].request.url)
    assert "nextToken=page-1" in request_url
    # response carries snake_case next_token + passthrough records
    assert result["next_token"] == "page-2"
    assert result["records"] == [{"id": 1}, {"id": 2}]
