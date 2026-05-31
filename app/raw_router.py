"""Raw WHOOP passthrough routes (/raw/*).

Low-level access for debugging, mapping verification, or when a normalized field
looks wrong. Protected by X-API-Key like every data route. Pagination keeps the
documented asymmetric contract: the response body carries snake_case
``next_token`` while the request advances pages with the ``next_token`` query
param (forwarded to WHOOP as ``nextToken`` by the client). Pages are cached
conservatively to respect WHOOP rate limits.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.cache import FileCache
from app.config import Settings, get_settings
from app.deps import get_cache, get_whoop_client, resolve_profile_name
from app.router import _range_cache_key, _resolve_range, _whoop_error_response
from app.whoop_client import WhoopClient

router = APIRouter(prefix="/raw", tags=["raw"])

MAX_RANGE_DAYS = 365

_PATHS = {
    "cycles": "/v2/cycle",
    "recoveries": "/v2/recovery",
    "sleeps": "/v2/activity/sleep",
    "workouts": "/v2/activity/workout",
}


async def _serve_raw(
    *,
    name: str,
    start: datetime,
    end: datetime | None,
    limit: int,
    next_token: str | None,
    profile_name: str,
    settings: Settings,
    cache: FileCache,
    client: WhoopClient,
):
    start_dt, end_dt = _resolve_range(start, end, settings, max_days=MAX_RANGE_DAYS)
    endpoint = f"raw_{name}"
    cache_key = _range_cache_key(start_dt, end_dt, limit, next_token)
    ttl = settings.coach_aggregate_ttl_seconds

    cached = cache.load_range_ready(profile_name, endpoint, cache_key, ttl, require_ready=False)
    if cached is not None:
        return cached

    try:
        payload = await client.fetch_raw_collection(
            profile_name=profile_name,
            path=_PATHS[name],
            start=start_dt,
            end=end_dt,
            limit=limit,
            next_token=next_token,
        )
    except Exception as exc:  # noqa: BLE001
        return _whoop_error_response(exc)

    cache.save_range_ready(profile_name, endpoint, cache_key, payload, require_ready=False)
    return payload


def _make_route(name: str):
    async def handler(
        start: datetime = Query(...),
        end: datetime | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=25),
        next_token: str | None = Query(default=None),
        profile_name: str = Depends(resolve_profile_name),
        settings: Settings = Depends(get_settings),
        cache: FileCache = Depends(get_cache),
        client: WhoopClient = Depends(get_whoop_client),
    ):
        return await _serve_raw(
            name=name,
            start=start,
            end=end,
            limit=limit,
            next_token=next_token,
            profile_name=profile_name,
            settings=settings,
            cache=cache,
            client=client,
        )

    return handler


for _name in _PATHS:
    router.add_api_route(f"/{_name}", _make_route(_name), methods=["GET"], name=f"raw_{_name}")
