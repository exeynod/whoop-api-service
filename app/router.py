from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.cache import FileCache
from app.config import Settings, get_settings
from app.deps import get_cache, get_rate_limiter, get_whoop_client, verify_api_key
from app.models import ErrorResponse, PendingResponse, RecoveryReadyResponse, WeekResponse, YesterdayReadyResponse
from app.rate_limiter import EndpointRateLimiter
from app.whoop_client import (
    ReauthorizationRequiredError,
    UnexpectedWhoopResponseError,
    WhoopClient,
    WhoopTimeoutError,
    WhoopUnavailableError,
)

router = APIRouter(tags=["whoop"], dependencies=[Depends(verify_api_key)])


def _now_msk(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _whoop_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, ReauthorizationRequiredError):
        payload = ErrorResponse(reason="Reauthorization required")
        return JSONResponse(status_code=502, content=payload.model_dump(exclude_none=True))
    if isinstance(exc, WhoopTimeoutError):
        payload = ErrorResponse(reason="Whoop API timeout")
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
    response_model=RecoveryReadyResponse | PendingResponse,
    responses={502: {"model": ErrorResponse}},
)
async def recovery_today(
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    rate_limiter: EndpointRateLimiter = Depends(get_rate_limiter),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    now = _now_msk(settings)
    today = now.date()

    cached = cache.load_ready("recovery", today)
    if cached:
        cached["cached"] = True
        rate_limiter.pop_pending("recovery_today")
        return cached

    throttled_pending = rate_limiter.get_pending_if_limited("recovery_today", now)
    if throttled_pending:
        return throttled_pending

    if not rate_limiter.should_call("recovery_today", now):
        return PendingResponse(
            reason="Sleep not yet complete. Recovery will be available after wake."
        ).model_dump()

    try:
        payload = await whoop_client.fetch_recovery(today)
    except Exception as exc:
        return _whoop_error_response(exc)

    if payload.get("status") == "ready":
        payload["cached"] = False
        cache.save_ready("recovery", today, payload)
        rate_limiter.pop_pending("recovery_today")
        return payload

    rate_limiter.remember_pending("recovery_today", now, payload)
    return payload


@router.get(
    "/day/yesterday",
    response_model=YesterdayReadyResponse,
    responses={502: {"model": ErrorResponse}},
)
async def day_yesterday(
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    yesterday = (_now_msk(settings) - timedelta(days=1)).date()
    cached = cache.load_ready("day", yesterday)
    if cached:
        cached["cached"] = True
        return cached

    try:
        payload = await whoop_client.fetch_yesterday_snapshot(yesterday)
    except Exception as exc:
        return _whoop_error_response(exc)

    if payload.get("status") != "ready":
        return _whoop_error_response(UnexpectedWhoopResponseError("Expected ready payload"))

    payload["cached"] = False
    cache.save_ready("day", yesterday, payload)
    return payload


@router.get(
    "/week",
    response_model=WeekResponse,
    responses={502: {"model": ErrorResponse}},
)
async def week(
    settings: Settings = Depends(get_settings),
    cache: FileCache = Depends(get_cache),
    whoop_client: WhoopClient = Depends(get_whoop_client),
):
    end_date = (_now_msk(settings) - timedelta(days=1)).date()
    start_date = end_date - timedelta(days=6)

    days: list[dict] = []
    for offset in range(7):
        target_date = start_date + timedelta(days=offset)
        cached = cache.load_ready("week", target_date)
        if cached:
            days.append(cached)
            continue

        try:
            payload = await whoop_client.fetch_week_day(target_date)
        except Exception as exc:
            return _whoop_error_response(exc)

        if payload.get("status") == "ready":
            cache.save_ready("week", target_date, payload)
        days.append(payload)

    return {
        "period": {"from": start_date.isoformat(), "to": end_date.isoformat()},
        "days": days,
    }
