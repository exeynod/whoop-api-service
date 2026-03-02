from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Union
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.cache import FileCache
from app.config import Settings, get_settings
from app.deps import get_cache, get_rate_limiter, get_whoop_client, resolve_profile_name
from app.models import (
    CyclesResponse,
    ErrorResponse,
    PendingResponse,
    RecoveryReadyResponse,
    WeekResponse,
    WorkoutsResponse,
    YesterdayReadyResponse,
)
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
        return JSONResponse(status_code=401, content=payload.model_dump(exclude_none=True))
    if isinstance(exc, WhoopTimeoutError):
        payload = ErrorResponse(reason="Whoop API timeout", detail=str(exc))
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    if isinstance(exc, WhoopUnavailableError):
        payload = ErrorResponse(reason="Whoop API unavailable")
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    if isinstance(exc, UnexpectedWhoopResponseError):
        payload = ErrorResponse(reason="Unexpected Whoop response", detail=str(exc))
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    payload = ErrorResponse(reason="Whoop API unavailable", detail=str(exc))
    return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))


def _timezone_offset(settings: Settings) -> str:
    offset = _now_msk(settings).utcoffset() or timedelta(0)
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    absolute = abs(total_seconds)
    hours = absolute // 3600
    minutes = (absolute % 3600) // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def _require_tz_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(
            status_code=422,
            detail=f"Query parameter '{field_name}' must include timezone offset",
        )


def _resolve_range(
    start: datetime,
    end: datetime | None,
    settings: Settings,
) -> tuple[datetime, datetime]:
    _require_tz_aware(start, "start")
    resolved_end = end or _now_msk(settings)
    _require_tz_aware(resolved_end, "end")
    if resolved_end < start:
        raise HTTPException(
            status_code=422,
            detail="Query parameter 'end' must be greater than or equal to 'start'",
        )
    return start, resolved_end


def _range_cache_key(start: datetime, end: datetime, limit: int, next_token: str | None) -> str:
    raw = f"{start.isoformat()}|{end.isoformat()}|{limit}|{next_token or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.get(
    "/recovery/today",
    response_model=Union[RecoveryReadyResponse, PendingResponse],
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    response_model_exclude_none=True,
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
    timezone_offset = _timezone_offset(settings)
    cached = cache.load_ready(profile_name, "recovery", today)
    if cached:
        cached["cached"] = True
        cached["timezone_offset"] = timezone_offset
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
        payload["timezone_offset"] = timezone_offset
        cache.save_ready(profile_name, "recovery", today, payload)
        rate_limiter.pop_pending(cache_key)
        return payload

    rate_limiter.remember_pending(cache_key, now, payload)
    return payload


@router.get(
    "/day/yesterday",
    response_model=YesterdayReadyResponse,
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    response_model_exclude_none=True,
)
async def day_yesterday(
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    yesterday = (_now_msk(settings) - timedelta(days=1)).date()
    timezone_offset = _timezone_offset(settings)
    cached = cache.load_ready(profile_name, "day", yesterday)
    if cached:
        cached["cached"] = True
        cached["timezone_offset"] = timezone_offset
        return cached

    try:
        payload = await whoop_client.fetch_yesterday_snapshot(profile_name, yesterday)
    except Exception as exc:
        return _whoop_error_response(exc)

    if payload.get("status") != "ready":
        return _whoop_error_response(UnexpectedWhoopResponseError("Expected ready payload"))

    payload["cached"] = False
    payload["timezone_offset"] = timezone_offset
    cache.save_ready(profile_name, "day", yesterday, payload)
    return payload


@router.get(
    "/cycles",
    response_model=CyclesResponse,
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    response_model_exclude_none=True,
)
async def cycles(
    start: datetime = Query(...),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=25),
    next_token: str | None = Query(default=None),
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    start_dt, end_dt = _resolve_range(start, end, settings)
    if next_token is not None:
        try:
            date.fromisoformat(next_token)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Query parameter 'next_token' must use YYYY-MM-DD format",
            ) from exc

    cache_key = _range_cache_key(start_dt, end_dt, limit, next_token)
    timezone_offset = _timezone_offset(settings)
    cached = cache.load_range_ready(profile_name, "cycles", cache_key, settings.range_ready_ttl_seconds)
    if cached:
        cached["cached"] = True
        cached["timezone_offset"] = timezone_offset
        return cached

    try:
        payload = await whoop_client.fetch_cycles_range(
            profile_name=profile_name,
            start=start_dt,
            end=end_dt,
            limit=limit,
            next_token=next_token,
        )
    except Exception as exc:
        return _whoop_error_response(exc)

    payload["cached"] = False
    payload["timezone_offset"] = timezone_offset
    cache.save_range_ready(profile_name, "cycles", cache_key, payload)
    return payload


@router.get(
    "/workouts",
    response_model=WorkoutsResponse,
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    response_model_exclude_none=True,
)
async def workouts(
    start: datetime = Query(...),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=25),
    next_token: str | None = Query(default=None),
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    start_dt, end_dt = _resolve_range(start, end, settings)
    cache_key = _range_cache_key(start_dt, end_dt, limit, next_token)
    timezone_offset = _timezone_offset(settings)
    cached = cache.load_range_ready(profile_name, "workouts", cache_key, settings.range_ready_ttl_seconds)
    if cached:
        cached["cached"] = True
        cached["timezone_offset"] = timezone_offset
        return cached

    try:
        payload = await whoop_client.fetch_workouts_range(
            profile_name=profile_name,
            start=start_dt,
            end=end_dt,
            limit=limit,
            next_token=next_token,
        )
    except Exception as exc:
        return _whoop_error_response(exc)

    payload["cached"] = False
    payload["timezone_offset"] = timezone_offset
    cache.save_range_ready(profile_name, "workouts", cache_key, payload)
    return payload


@router.get(
    "/week",
    response_model=WeekResponse,
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
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
