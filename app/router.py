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
    BodyMeasurementHistoryReadyResponse,
    BodyMeasurementReadyResponse,
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
MAX_RANGE_DAYS = 365


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
    max_days: int | None = None,
) -> tuple[datetime, datetime]:
    _require_tz_aware(start, "start")
    resolved_end = end or _now_msk(settings)
    _require_tz_aware(resolved_end, "end")
    if resolved_end < start:
        raise HTTPException(
            status_code=422,
            detail="Query parameter 'end' must be greater than or equal to 'start'",
        )
    if max_days is not None and (resolved_end - start) > timedelta(days=max_days):
        raise HTTPException(
            status_code=422,
            detail=f"Query range must be <= {max_days} days",
        )
    return start, resolved_end


def _range_cache_key(start: datetime, end: datetime, limit: int, next_token: str | None) -> str:
    raw = f"{start.isoformat()}|{end.isoformat()}|{limit}|{next_token or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _should_rollup_weekly(start_day: date, end_day: date) -> bool:
    return ((end_day - start_day).days + 1) > 14


def _week_start(target_day: date) -> date:
    return target_day - timedelta(days=target_day.weekday())


def _average_field(rows: list[dict], field: str) -> float | None:
    values: list[float] = []
    for row in rows:
        raw = row.get(field)
        try:
            if raw is None:
                continue
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)


def _aggregate_body_history_weekly(history: list[dict]) -> list[dict]:
    buckets: dict[date, list[dict]] = {}
    for item in history:
        date_raw = item.get("date")
        if not isinstance(date_raw, str):
            continue
        try:
            target_day = date.fromisoformat(date_raw)
        except ValueError:
            continue
        week = _week_start(target_day)
        buckets.setdefault(week, []).append(item)

    aggregated: list[dict] = []
    for week in sorted(buckets.keys()):
        rows = buckets[week]
        payload: dict[str, object] = {"date": week.isoformat()}
        measured_at_candidates = [row.get("measured_at") for row in rows if isinstance(row.get("measured_at"), str)]
        if measured_at_candidates:
            payload["measured_at"] = measured_at_candidates[-1]

        height_avg = _average_field(rows, "height_meter")
        if height_avg is not None:
            payload["height_meter"] = round(height_avg, 4)

        weight_avg = _average_field(rows, "weight_kilogram")
        if weight_avg is not None:
            payload["weight_kilogram"] = round(weight_avg, 4)

        max_hr_avg = _average_field(rows, "max_heart_rate")
        if max_hr_avg is not None:
            payload["max_heart_rate"] = int(round(max_hr_avg))
        aggregated.append(payload)
    return aggregated


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
    start_dt, end_dt = _resolve_range(start, end, settings, max_days=MAX_RANGE_DAYS)
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
    start_dt, end_dt = _resolve_range(start, end, settings, max_days=MAX_RANGE_DAYS)
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
    "/measurements/body",
    response_model=Union[BodyMeasurementReadyResponse, PendingResponse],
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    response_model_exclude_none=True,
)
async def measurements_body(
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    timezone_offset = _timezone_offset(settings)
    try:
        payload = await whoop_client.fetch_body_measurements(profile_name=profile_name)
    except Exception as exc:
        return _whoop_error_response(exc)

    if payload.get("status") == "ready":
        payload["cached"] = False
        payload["timezone_offset"] = timezone_offset
        measured_at_raw = payload.get("measured_at")
        measured_at = None
        if isinstance(measured_at_raw, str):
            measured_at = WhoopClient._parse_datetime(measured_at_raw)
        snapshot_day = measured_at.astimezone(ZoneInfo(settings.timezone)).date() if measured_at else _now_msk(settings).date()
        cache.save_body_snapshot(profile_name=profile_name, snapshot_date=snapshot_day, payload=payload)
        return payload
    return payload


@router.get(
    "/measurements/body/history",
    response_model=Union[BodyMeasurementHistoryReadyResponse, PendingResponse],
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    response_model_exclude_none=True,
)
async def measurements_body_history(
    start: datetime = Query(...),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=25),
    next_token: str | None = Query(default=None),
    profile_name: str = Depends(resolve_profile_name),
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
):
    start_dt, end_dt = _resolve_range(start, end, settings, max_days=MAX_RANGE_DAYS)
    if next_token is not None:
        try:
            date.fromisoformat(next_token)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Query parameter 'next_token' must use YYYY-MM-DD format",
            ) from exc

    timezone_offset = _timezone_offset(settings)
    start_day = start_dt.astimezone(ZoneInfo(settings.timezone)).date()
    end_day = end_dt.astimezone(ZoneInfo(settings.timezone)).date()
    history = cache.load_body_history(profile_name=profile_name, start_date=start_day, end_date=end_day)
    if _should_rollup_weekly(start_day, end_day):
        history = _aggregate_body_history_weekly(history)

    if not history:
        return {
            "status": "pending",
            "reason": "Body measurements are not available yet.",
        }

    start_index = 0
    if next_token:
        for index, item in enumerate(history):
            if item.get("date") == next_token:
                start_index = index
                break
        else:
            start_index = len(history)

    scoped = history[start_index : start_index + limit]
    computed_next_token = None
    if (start_index + limit) < len(history):
        computed_next_token = str(history[start_index + limit]["date"])

    return {
        "status": "ready",
        "period": {"from": start_day.isoformat(), "to": end_day.isoformat()},
        "measurements": scoped,
        "next_token": computed_next_token,
        "timezone_offset": timezone_offset,
        "cached": True,
    }


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
