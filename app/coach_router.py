"""Coach (v2) routes — the primary normalized contract for the Ukai agent.

Facts only: these endpoints never return coach flags, training_readiness,
should_train or recommendations. They expose objective metrics with per-block
status + freshness and an optional raw drilldown. ``/coach/today`` is heartbeat
safe — responses are cached per (profile, date, detail, include_raw) so repeated
polling does not hammer WHOOP.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app import coach_normalize as cn
from app.cache import FileCache
from app.config import Settings, get_settings
from app.deps import get_cache, get_whoop_client, resolve_profile_name
from app.router import _whoop_error_response
from app.whoop_client import WhoopClient

router = APIRouter(prefix="/coach", tags=["coach"])

_COACH_DAY_ENDPOINT = "coach_day"


def _now_msk(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _coach_cache_key(target_date: date, detail: str, include_raw: bool) -> str:
    return f"{target_date.isoformat()}|{detail}|{int(include_raw)}"


async def _serve_coach_day(
    *,
    profile_name: str,
    target_date: date,
    settings: Settings,
    cache: FileCache,
    client: WhoopClient,
    include_raw: bool,
    detail: str,
    refresh: bool,
    with_aliases: bool,
):
    cache_key = _coach_cache_key(target_date, detail, include_raw)
    ttl = settings.coach_day_strain_ttl_seconds

    payload = None
    if not refresh:
        payload = cache.load_range_ready(
            profile_name, _COACH_DAY_ENDPOINT, cache_key, ttl, require_ready=False
        )
    if payload is None:
        try:
            payload = await client.fetch_coach_day(
                profile_name, target_date, include_raw=include_raw, detail=detail
            )
        except Exception as exc:  # noqa: BLE001 - mapped to a safe status code
            return _whoop_error_response(exc)
        cache.save_range_ready(
            profile_name, _COACH_DAY_ENDPOINT, cache_key, payload, require_ready=False
        )

    if with_aliases:
        payload = {
            **payload,
            "today_strain": payload.get("day_strain"),
            "yesterday_strain": payload.get("previous_day_strain"),
        }
    return payload


@router.get("/today")
async def coach_today(
    include_raw: bool = Query(default=False),
    refresh: bool = Query(default=False),
    detail: Literal["surface", "full"] = Query(default="full"),
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    client: WhoopClient = Depends(get_whoop_client),
):
    target_date = _now_msk(settings).date()
    return await _serve_coach_day(
        profile_name=profile_name,
        target_date=target_date,
        settings=settings,
        cache=cache,
        client=client,
        include_raw=include_raw,
        detail=detail,
        refresh=refresh,
        with_aliases=True,
    )


@router.get("/day/{target_date}")
async def coach_day(
    target_date: date,
    include_raw: bool = Query(default=False),
    refresh: bool = Query(default=False),
    detail: Literal["surface", "full"] = Query(default="full"),
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    client: WhoopClient = Depends(get_whoop_client),
):
    return await _serve_coach_day(
        profile_name=profile_name,
        target_date=target_date,
        settings=settings,
        cache=cache,
        client=client,
        include_raw=include_raw,
        detail=detail,
        refresh=refresh,
        with_aliases=False,
    )


@router.get("/status")
async def coach_status(
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    client: WhoopClient = Depends(get_whoop_client),
):
    status = await client.fetch_coach_status(profile_name)

    today = _now_msk(settings).date()
    cached = cache.load_range_ready(
        profile_name,
        _COACH_DAY_ENDPOINT,
        _coach_cache_key(today, "full", False),
        settings.coach_recovery_ttl_seconds,
        require_ready=False,
    )
    freshness = cached.get("freshness", {}) if cached else {}

    status["cache"] = {
        "recovery_today": _cache_entry(freshness.get("recovery")),
        "sleep_latest": _cache_entry(freshness.get("sleep")),
        "strain_today": _cache_entry(freshness.get("day_strain")),
        "workouts": _cache_entry(freshness.get("workouts_today")),
        "body": _cache_entry(freshness.get("body")),
    }
    status["available_blocks"] = {
        "recovery": _block_ready(cached, "recovery"),
        "sleep": _block_ready(cached, "sleep"),
        "strain": _block_ready(cached, "day_strain"),
        "workouts": bool(cached and cached.get("workouts_today")),
        "body": _block_ready(cached, "body"),
    }
    return status


@router.get("/body/latest")
async def coach_body_latest(
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    client: WhoopClient = Depends(get_whoop_client),
):
    try:
        payload = await client.fetch_body_measurements(profile_name=profile_name)
    except Exception as exc:  # noqa: BLE001
        return _whoop_error_response(exc)

    tz = ZoneInfo(settings.timezone)
    if payload.get("status") == "ready":
        return cn.normalize_body(payload, tz, source="whoop", measured_at=payload.get("measured_at"))
    return cn.normalize_body(None, tz)


def _cache_entry(freshness_entry: dict | None) -> dict:
    if not isinstance(freshness_entry, dict):
        return {"status": "missing", "updated_at": None}
    status = freshness_entry.get("status")
    if status in ("fresh", "stale"):
        return {"status": status, "updated_at": freshness_entry.get("updated_at")}
    return {"status": "missing", "updated_at": freshness_entry.get("updated_at")}


def _block_ready(cached: dict | None, block: str) -> bool:
    if not cached:
        return False
    value = cached.get(block)
    return isinstance(value, dict) and value.get("status") == "ready"
