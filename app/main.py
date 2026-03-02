from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI

from app.auth_router import router as auth_router
from app.config import Settings, get_settings
from app.deps import get_cache, get_whoop_client
from app.models import HealthResponse
from app.router import router as data_router
from app.whoop_client import WhoopClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    cache = get_cache()

    cache.cleanup_expired()
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(cache.cleanup_expired, CronTrigger(hour=3, minute=0))
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Whoop Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(auth_router)
    app.include_router(data_router)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health(
        settings: Settings = Depends(get_settings),
        whoop_client: WhoopClient = Depends(get_whoop_client),
    ) -> HealthResponse:
        whoop_reachable = False
        try:
            whoop_reachable = await asyncio.wait_for(
                whoop_client.ping(timeout_seconds=settings.health_timeout_seconds),
                timeout=settings.health_timeout_seconds,
            )
        except (asyncio.TimeoutError, Exception):
            whoop_reachable = False

        return HealthResponse(
            whoop_reachable=whoop_reachable,
            tokens_valid=whoop_client.tokens_valid,
        )

    return app


app = create_app()
