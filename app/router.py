from __future__ import annotations

from datetime import datetime, timedelta
from typing import Union
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.cache import FileCache
from app.config import Settings, get_settings
from app.deps import get_cache, get_rate_limiter, get_whoop_client, resolve_profile_name
from app.models import ErrorResponse, PendingResponse, RecoveryReadyResponse, WeekResponse, YesterdayReadyResponse
from app.rate_limiter import EndpointRateLimiter
from app.whoop_client import (
    ReauthorizationRequiredError,
    UnexpectedWhoopResponseError,
    WhoopClient,
    WhoopTimeoutError,
    WhoopUnavailableError,
)

router = APIRouter(tags=["whoop"])


def _now_msk(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _whoop_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, ReauthorizationRequiredError):
        payload = ErrorResponse(reason="Reauthorization required")
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    if isinstance(exc, WhoopTimeoutError):
        payload = ErrorResponse(reason="Whoop API timeout", detail=str(exc))
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    if isinstance(exc, WhoopUnavailableError):
        payload = ErrorResponse(reason="Whoop API unavailable")
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    if isinstance(exc, UnexpectedWhoopResponseError):
        payload = ErrorResponse(reason="Unexpected Whoop response")
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    payload = ErrorResponse(reason="Whoop API unavailable", detail=str(exc))
    return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))


@router.get(
    "/recovery/today",
    response_model=Union[RecoveryReadyResponse, PendingResponse],
    responses={502: {"model": ErrorResponse}},
)
async def recovery_today(
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    rate_limiter: EndpointRateLimiter = Depends(get_rate_limiter),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    now = _now_msk(settings)
    today = now.date()

    cache_key = f"{profile_name}:recovery_today"
    cached = cache.load_ready(profile_name, "recovery", today)
    if cached:
        cached["cached"] = True
        rate_limiter.pop_pending(cache_key)
        return cached

    throttled_pending = rate_limiter.get_pending_if_limited(cache_key, now)
    if throttled_pending:
        return throttled_pending

    try:
        payload = await whoop_client.fetch_recovery(profile_name, today)
    except Exception as exc:
        return _whoop_error_response(exc)

    if payload.get("status") == "ready":
        payload["cached"] = False
        cache.save_ready(profile_name, "recovery", today, payload)
        rate_limiter.pop_pending(cache_key)
        return payload

    rate_limiter.remember_pending(cache_key, now, payload)
    return payload


@router.get(
    "/day/yesterday",
    response_model=YesterdayReadyResponse,
    responses={502: {"model": ErrorResponse}},
)
async def day_yesterday(
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    yesterday = (_now_msk(settings) - timedelta(days=1)).date()
    cached = cache.load_ready(profile_name, "day", yesterday)
    if cached:
        cached["cached"] = True
        return cached

    try:
        payload = await whoop_client.fetch_yesterday_snapshot(profile_name, yesterday)
    except Exception as exc:
        return _whoop_error_response(exc)

    if payload.get("status") != "ready":
        return _whoop_error_response(UnexpectedWhoopResponseError("Expected ready payload"))

    payload["cached"] = False
    cache.save_ready(profile_name, "day", yesterday, payload)
    return payload


@router.get(
    "/week",
    response_model=WeekResponse,
    responses={502: {"model": ErrorResponse}},
)
async def week(
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    end_date = (_now_msk(settings) - timedelta(days=1)).date()
    start_date = end_date - timedelta(days=6)

    days: list[dict] = []
    for offset in range(7):
        target_date = start_date + timedelta(days=offset)
        cached = cache.load_ready(profile_name, "week", target_date)
        if cached:
            days.append(cached)
            continue

        try:
            payload = await whoop_client.fetch_week_day(profile_name, target_date)
        except Exception as exc:
            return _whoop_error_response(exc)

        if payload.get("status") == "ready":
            cache.save_ready(profile_name, "week", target_date, payload)
        days.append(payload)

    return {
        "period": {"from": start_date.isoformat(), "to": end_date.isoformat()},
        "days": days,
    }
