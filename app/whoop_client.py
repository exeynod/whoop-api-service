from __future__ import annotations

import asyncio
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.config import Settings


class ReauthorizationRequiredError(RuntimeError):
    """Refresh token is missing or expired and manual auth is required."""


class WhoopTimeoutError(RuntimeError):
    """Whoop API did not respond in time."""


class WhoopUnavailableError(RuntimeError):
    """Whoop API is unavailable."""


class UnexpectedWhoopResponseError(RuntimeError):
    """Whoop API returned payload with unexpected structure."""


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: datetime
    refresh_expires_at: datetime | None = None


class WhoopClient:
    """Stub client for Iteration 1 scaffolding.

    Real HTTP integration with Whoop API should be implemented in this class later.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._tz = ZoneInfo(settings.timezone)
        self.settings.secrets_dir.mkdir(parents=True, exist_ok=True)

    @property
    def tokens_valid(self) -> bool:
        tokens = self._load_tokens()
        if tokens is None:
            return False
        if self._is_refresh_expired(tokens):
            return False
        return True

    def build_authorization_url(self, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.settings.whoop_client_id,
            "redirect_uri": self.settings.whoop_redirect_uri,
            "scope": "offline read:recovery read:sleep read:workout read:cycles",
            "state": state,
        }
        return f"{self.settings.whoop_oauth_authorize_url}?{urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str) -> None:
        # TODO: Replace with real token exchange request to Whoop OAuth endpoint.
        now = datetime.now(timezone.utc)
        bundle = TokenBundle(
            access_token=f"stub_access_{secrets.token_hex(16)}",
            refresh_token=f"stub_refresh_{secrets.token_hex(16)}",
            expires_at=now + timedelta(hours=1),
            refresh_expires_at=now + timedelta(days=90),
        )
        self._save_tokens(bundle)
        await asyncio.sleep(0)

    async def ping(self, timeout_seconds: float) -> bool:
        # TODO: Implement real lightweight Whoop reachability check with timeout.
        await asyncio.sleep(0)
        _ = timeout_seconds
        return True

    async def fetch_recovery(self, target_date: date) -> dict[str, Any]:
        self._ensure_access_token()
        now = datetime.now(self._tz)
        if target_date == now.date() and now.hour < 6:
            return {
                "status": "pending",
                "reason": "Sleep not yet complete. Recovery will be available after wake.",
            }

        recovery_score = self._bounded_score(target_date, min_value=35, max_value=94)
        if recovery_score >= 67:
            zone = "green"
        elif recovery_score >= 34:
            zone = "yellow"
        else:
            zone = "red"

        return {
            "status": "ready",
            "date": target_date.isoformat(),
            "recovery_score": recovery_score,
            "recovery_zone": zone,
            "hrv_ms": 35 + (target_date.toordinal() % 35),
            "resting_hr_bpm": 44 + (target_date.toordinal() % 11),
        }

    async def fetch_yesterday_snapshot(self, target_date: date) -> dict[str, Any]:
        self._ensure_access_token()
        strain_score = round(8.0 + (target_date.toordinal() % 90) / 10, 1)
        sleep_score = self._bounded_score(target_date, min_value=65, max_value=96)

        return {
            "status": "ready",
            "date": target_date.isoformat(),
            "strain": {
                "score": strain_score,
                "kilojoules": 1200 + (target_date.toordinal() % 1200),
                "avg_hr_bpm": 95 + (target_date.toordinal() % 35),
                "max_hr_bpm": 150 + (target_date.toordinal() % 35),
            },
            "sleep": {
                "score": sleep_score,
                "total_hours": round(6.0 + ((target_date.toordinal() % 25) / 10), 1),
                "performance_percent": 70 + (target_date.toordinal() % 30),
                "respiratory_rate": round(13.5 + ((target_date.toordinal() % 30) / 10), 1),
                "stages": {
                    "deep_hours": 1.4,
                    "rem_hours": 1.8,
                    "light_hours": 3.4,
                    "awake_hours": 0.6,
                },
            },
        }

    async def fetch_week_day(self, target_date: date) -> dict[str, Any]:
        self._ensure_access_token()

        if target_date.day % 6 == 0:
            return {"date": target_date.isoformat(), "status": "missing"}

        recovery_score = self._bounded_score(target_date, min_value=35, max_value=94)
        if recovery_score >= 67:
            zone = "green"
        elif recovery_score >= 34:
            zone = "yellow"
        else:
            zone = "red"

        return {
            "date": target_date.isoformat(),
            "status": "ready",
            "recovery_score": recovery_score,
            "recovery_zone": zone,
            "hrv_ms": 35 + (target_date.toordinal() % 35),
            "resting_hr_bpm": 44 + (target_date.toordinal() % 11),
            "strain_score": round(8.0 + (target_date.toordinal() % 90) / 10, 1),
            "sleep_score": self._bounded_score(target_date, min_value=65, max_value=96),
            "sleep_hours": round(6.0 + ((target_date.toordinal() % 25) / 10), 1),
        }

    def _ensure_access_token(self) -> str:
        tokens = self._load_tokens()
        if tokens is None:
            raise ReauthorizationRequiredError("Reauthorization required")

        if self._is_refresh_expired(tokens):
            raise ReauthorizationRequiredError("Reauthorization required")

        if tokens.expires_at <= datetime.now(timezone.utc):
            tokens = self._refresh_token(tokens)
        return tokens.access_token

    def _refresh_token(self, tokens: TokenBundle) -> TokenBundle:
        # TODO: Replace with real token refresh request to Whoop OAuth endpoint.
        if self._is_refresh_expired(tokens):
            raise ReauthorizationRequiredError("Reauthorization required")

        refreshed = tokens.model_copy(
            update={
                "access_token": f"stub_access_{secrets.token_hex(16)}",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        )
        self._save_tokens(refreshed)
        return refreshed

    def _load_tokens(self) -> TokenBundle | None:
        if not self.settings.token_path.exists():
            return None

        try:
            raw = self.settings.token_path.read_text(encoding="utf-8")
            bundle = TokenBundle.model_validate_json(raw)
        except (OSError, ValueError):
            return None

        if bundle.expires_at.tzinfo is None:
            bundle.expires_at = bundle.expires_at.replace(tzinfo=timezone.utc)
        if bundle.refresh_expires_at and bundle.refresh_expires_at.tzinfo is None:
            bundle.refresh_expires_at = bundle.refresh_expires_at.replace(tzinfo=timezone.utc)
        return bundle

    def _save_tokens(self, bundle: TokenBundle) -> None:
        payload = bundle.model_dump_json(indent=2)
        self.settings.token_path.write_text(payload, encoding="utf-8")

    @staticmethod
    def _is_refresh_expired(bundle: TokenBundle) -> bool:
        if bundle.refresh_expires_at is None:
            return False
        return bundle.refresh_expires_at <= datetime.now(timezone.utc)

    @staticmethod
    def _bounded_score(target_date: date, min_value: int, max_value: int) -> int:
        spread = max_value - min_value + 1
        return min_value + (target_date.toordinal() % spread)
