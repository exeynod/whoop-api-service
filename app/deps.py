from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from app.cache import FileCache
from app.config import Settings, get_settings
from app.rate_limiter import EndpointRateLimiter
from app.whoop_client import WhoopClient


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not x_api_key or x_api_key != settings.proxy_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@lru_cache(maxsize=1)
def get_cache() -> FileCache:
    settings = get_settings()
    return FileCache(
        cache_dir=settings.cache_dir,
        timezone_name=settings.timezone,
        retention_days=settings.cache_retention_days,
    )


@lru_cache(maxsize=1)
def get_rate_limiter() -> EndpointRateLimiter:
    settings = get_settings()
    return EndpointRateLimiter(min_interval_seconds=settings.whoop_min_interval_seconds)


@lru_cache(maxsize=1)
def get_whoop_client() -> WhoopClient:
    settings = get_settings()
    return WhoopClient(settings=settings)
