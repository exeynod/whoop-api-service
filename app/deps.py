from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from app.cache import FileCache
from app.config import Settings, get_settings
from app.rate_limiter import EndpointRateLimiter
from app.whoop_client import WhoopClient


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


def resolve_profile_name(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    whoop_client: WhoopClient = Depends(get_whoop_client),
) -> str:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    profile_name = whoop_client.resolve_profile_name(api_token=x_api_key)
    if profile_name is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return profile_name
