from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict, Union
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
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


class RecoveryPendingResult(TypedDict):
    status: Literal["pending"]
    reason: str


class RecoveryReadyResult(TypedDict):
    status: Literal["ready"]
    date: str
    recovery_score: int
    recovery_zone: Literal["green", "yellow", "red"]
    hrv_ms: int
    resting_hr_bpm: int


class YesterdayResult(TypedDict):
    status: Literal["ready"]
    date: str
    strain: dict[str, Any]
    sleep: dict[str, Any]


class WeekDayMissingResult(TypedDict):
    date: str
    status: Literal["missing"]


class WeekDayReadyResult(TypedDict):
    date: str
    status: Literal["ready"]
    recovery_score: int
    recovery_zone: Literal["green", "yellow", "red"]
    hrv_ms: int
    resting_hr_bpm: int
    strain_score: float
    sleep_score: int
    sleep_hours: float


RecoveryResult = Union[RecoveryPendingResult, RecoveryReadyResult]
WeekDayResult = Union[WeekDayMissingResult, WeekDayReadyResult]


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: datetime
    refresh_expires_at: datetime | None = None


class WhoopClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._tz = ZoneInfo(settings.timezone)
        self._token_lock = asyncio.Lock()
        self._logger = logging.getLogger("app.whoop_client")
        self._http_log_enabled = settings.whoop_http_log_enabled
        self._http_log_redact_sensitive = settings.whoop_http_log_redact_sensitive
        self._http_log_body_max_chars = max(200, settings.whoop_http_log_body_max_chars)
        self._http_log_level = self._resolve_log_level(settings.whoop_http_log_level)
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
            "scope": "offline read:recovery read:sleep read:cycles",
            "state": state,
        }
        return f"{self.settings.whoop_oauth_authorize_url}?{urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str) -> None:
        token_payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.settings.whoop_client_id,
            "client_secret": self.settings.whoop_client_secret,
            "redirect_uri": self.settings.whoop_redirect_uri,
        }

        response = await self._oauth_post(token_payload)
        token_bundle = self._bundle_from_oauth_response(response)
        self._save_tokens(token_bundle)

    async def ping(self, timeout_seconds: float) -> bool:
        url = f"{self.settings.whoop_api_base_url.rstrip('/')}/v2/user/profile/basic"
        headers: dict[str, str] = {}
        self._log_http_request(channel="whoop_data_ping", method="GET", url=url, headers=headers)
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(url)
            self._log_http_response(
                channel="whoop_data_ping",
                method="GET",
                url=url,
                status_code=response.status_code,
                headers=dict(response.headers),
                body_text=response.text,
            )
            return True
        except (httpx.RequestError, httpx.TimeoutException):
            self._log_http_error(
                channel="whoop_data_ping",
                method="GET",
                url=url,
                error="RequestError or TimeoutException",
            )
            return False

    async def fetch_recovery(self, target_date: date) -> RecoveryResult:
        _ = await self._ensure_access_token()
        start_utc, end_utc = self._day_bounds_utc(target_date)
        records = await self._fetch_collection("/v2/recovery", start_utc, end_utc)

        if not records:
            return {
                "status": "pending",
                "reason": "Sleep not yet complete. Recovery will be available after wake.",
            }

        record = self._pick_record_for_day(records, target_date)
        if self._score_state(record) != "SCORED":
            return {
                "status": "pending",
                "reason": "Sleep not yet complete. Recovery will be available after wake.",
            }

        score = record.get("score")
        if not isinstance(score, dict):
            raise UnexpectedWhoopResponseError("Missing recovery score block")

        recovery_score = score.get("recovery_score")
        resting_hr = score.get("resting_heart_rate")
        hrv_rmssd = score.get("hrv_rmssd_milli")

        if recovery_score is None or resting_hr is None or hrv_rmssd is None:
            raise UnexpectedWhoopResponseError("Missing recovery score fields")

        recovery_score_int = int(round(float(recovery_score)))
        return {
            "status": "ready",
            "date": target_date.isoformat(),
            "recovery_score": recovery_score_int,
            "recovery_zone": self._extract_zone(score, recovery_score_int),
            "hrv_ms": int(round(float(hrv_rmssd))),
            "resting_hr_bpm": int(round(float(resting_hr))),
        }

    async def fetch_yesterday_snapshot(self, target_date: date) -> YesterdayResult:
        _ = await self._ensure_access_token()
        start_utc, end_utc = self._day_bounds_utc(target_date)

        cycle_records, sleep_records = await asyncio.gather(
            self._fetch_collection("/v2/cycle", start_utc, end_utc),
            self._fetch_collection("/v2/activity/sleep", start_utc, end_utc),
        )

        cycle = self._pick_scored_cycle(cycle_records, target_date)
        sleep = self._pick_scored_sleep(sleep_records, target_date)

        if cycle is None or sleep is None:
            raise UnexpectedWhoopResponseError("Expected scored cycle and sleep for the day")

        return {
            "status": "ready",
            "date": target_date.isoformat(),
            "strain": self._map_strain(cycle),
            "sleep": self._map_sleep(sleep),
        }

    async def fetch_week_day(self, target_date: date) -> WeekDayResult:
        _ = await self._ensure_access_token()
        start_utc, end_utc = self._day_bounds_utc(target_date)

        cycle_records, recovery_records, sleep_records = await asyncio.gather(
            self._fetch_collection("/v2/cycle", start_utc, end_utc),
            self._fetch_collection("/v2/recovery", start_utc, end_utc),
            self._fetch_collection("/v2/activity/sleep", start_utc, end_utc),
        )

        cycle = self._pick_scored_cycle(cycle_records, target_date)
        recovery = self._pick_scored_recovery(recovery_records, target_date)
        sleep = self._pick_scored_sleep(sleep_records, target_date)

        if cycle is None or recovery is None or sleep is None:
            return {"date": target_date.isoformat(), "status": "missing"}

        cycle_score = cycle.get("score")
        recovery_score = recovery.get("score")
        sleep_score = sleep.get("score")
        if not isinstance(cycle_score, dict) or not isinstance(recovery_score, dict) or not isinstance(
            sleep_score, dict
        ):
            raise UnexpectedWhoopResponseError("Missing score blocks in week day payload")

        recovery_value = int(round(self._require_number(recovery_score.get("recovery_score"), "recovery_score")))
        stage_summary = self._expect_stage_summary(sleep_score)

        return {
            "date": target_date.isoformat(),
            "status": "ready",
            "recovery_score": recovery_value,
            "recovery_zone": self._extract_zone(recovery_score, recovery_value),
            "hrv_ms": int(round(self._require_number(recovery_score.get("hrv_rmssd_milli"), "hrv_rmssd_milli"))),
            "resting_hr_bpm": int(
                round(self._require_number(recovery_score.get("resting_heart_rate"), "resting_heart_rate"))
            ),
            "strain_score": round(self._require_number(cycle_score.get("strain"), "strain"), 1),
            "sleep_score": int(
                round(
                    self._require_number(
                        sleep_score.get("sleep_performance_percentage"),
                        "sleep_performance_percentage",
                    )
                )
            ),
            "sleep_hours": self._millis_to_hours(stage_summary.get("total_in_bed_time_milli")),
        }

    async def _ensure_access_token(self, force_refresh: bool = False) -> str:
        async with self._token_lock:
            tokens = self._load_tokens()
            if tokens is None:
                raise ReauthorizationRequiredError("Reauthorization required")
            if self._is_refresh_expired(tokens):
                raise ReauthorizationRequiredError("Reauthorization required")

            if force_refresh or tokens.expires_at <= datetime.now(timezone.utc):
                tokens = await self._refresh_token(tokens)
            return tokens.access_token

    async def _refresh_token(self, tokens: TokenBundle) -> TokenBundle:
        if self._is_refresh_expired(tokens):
            raise ReauthorizationRequiredError("Reauthorization required")

        token_payload = {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": self.settings.whoop_client_id,
            "client_secret": self.settings.whoop_client_secret,
            "redirect_uri": self.settings.whoop_redirect_uri,
        }

        response = await self._oauth_post(token_payload)
        refreshed = self._bundle_from_oauth_response(response, current=tokens)
        self._save_tokens(refreshed)
        return refreshed

    async def _oauth_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = httpx.Timeout(self.settings.whoop_timeout_seconds)
        self._log_http_request(
            channel="whoop_oauth",
            method="POST",
            url=self.settings.whoop_oauth_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self.settings.whoop_oauth_token_url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.TimeoutException as exc:
            self._log_http_error(
                channel="whoop_oauth",
                method="POST",
                url=self.settings.whoop_oauth_token_url,
                error=f"TimeoutException: {exc}",
            )
            raise WhoopTimeoutError("Connection timeout after 10s") from exc
        except httpx.RequestError as exc:
            self._log_http_error(
                channel="whoop_oauth",
                method="POST",
                url=self.settings.whoop_oauth_token_url,
                error=f"RequestError: {exc}",
            )
            raise WhoopUnavailableError("Unable to reach Whoop OAuth endpoint") from exc

        self._log_http_response(
            channel="whoop_oauth",
            method="POST",
            url=self.settings.whoop_oauth_token_url,
            status_code=response.status_code,
            headers=dict(response.headers),
            body_text=response.text,
        )

        if response.status_code in (400, 401):
            raise ReauthorizationRequiredError("Reauthorization required")
        if response.status_code >= 500:
            raise WhoopUnavailableError("Whoop OAuth unavailable")
        if response.status_code >= 300:
            raise UnexpectedWhoopResponseError(f"Unexpected OAuth status code: {response.status_code}")

        try:
            payload_json = response.json()
        except ValueError as exc:
            raise UnexpectedWhoopResponseError("Invalid OAuth response JSON") from exc

        if not isinstance(payload_json, dict):
            raise UnexpectedWhoopResponseError("Unexpected OAuth response format")
        return payload_json

    async def _fetch_collection(
        self,
        path: str,
        start_utc: datetime,
        end_utc: datetime,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        payload = await self._authorized_get(
            path=path,
            params={
                "start": self._to_zulu(start_utc),
                "end": self._to_zulu(end_utc),
                "limit": str(limit),
            },
        )

        records = payload.get("records")
        if not isinstance(records, list):
            raise UnexpectedWhoopResponseError("Collection response missing records")

        normalized: list[dict[str, Any]] = []
        for item in records:
            if isinstance(item, dict):
                normalized.append(item)
        return normalized

    async def _authorized_get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        access_token = await self._ensure_access_token()
        response = await self._raw_get(path=path, access_token=access_token, params=params)

        if response.status_code == 401:
            refreshed_token = await self._ensure_access_token(force_refresh=True)
            response = await self._raw_get(path=path, access_token=refreshed_token, params=params)

        if response.status_code == 401:
            raise ReauthorizationRequiredError("Reauthorization required")
        if response.status_code >= 500:
            raise WhoopUnavailableError("Whoop API unavailable")
        if response.status_code >= 300:
            raise UnexpectedWhoopResponseError(f"Unexpected Whoop status code: {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise UnexpectedWhoopResponseError("Invalid Whoop response JSON") from exc

        if not isinstance(payload, dict):
            raise UnexpectedWhoopResponseError("Unexpected Whoop response")
        return payload

    async def _raw_get(self, path: str, access_token: str, params: dict[str, str]) -> httpx.Response:
        timeout = httpx.Timeout(self.settings.whoop_timeout_seconds)
        url = f"{self.settings.whoop_api_base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {access_token}"}
        self._log_http_request(
            channel="whoop_data",
            method="GET",
            url=url,
            headers=headers,
            params=params,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            self._log_http_error(
                channel="whoop_data",
                method="GET",
                url=url,
                error=f"TimeoutException: {exc}",
            )
            raise WhoopTimeoutError("Connection timeout after 10s") from exc
        except httpx.RequestError as exc:
            self._log_http_error(
                channel="whoop_data",
                method="GET",
                url=url,
                error=f"RequestError: {exc}",
            )
            raise WhoopUnavailableError("Whoop API unavailable") from exc

        self._log_http_response(
            channel="whoop_data",
            method="GET",
            url=url,
            status_code=response.status_code,
            headers=dict(response.headers),
            body_text=response.text,
        )
        return response

    def _bundle_from_oauth_response(
        self,
        payload: dict[str, Any],
        current: TokenBundle | None = None,
    ) -> TokenBundle:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token") or (current.refresh_token if current else None)
        expires_in = payload.get("expires_in")
        refresh_expires_in = payload.get("refresh_token_expires_in") or payload.get("refresh_expires_in")

        if not isinstance(access_token, str) or not access_token:
            raise UnexpectedWhoopResponseError("OAuth response missing access token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise UnexpectedWhoopResponseError("OAuth response missing refresh token")

        now = datetime.now(timezone.utc)
        try:
            access_seconds = int(expires_in) if expires_in is not None else 3600
        except (TypeError, ValueError):
            access_seconds = 3600

        refresh_expires_at: datetime | None = current.refresh_expires_at if current else None
        if refresh_expires_in is not None:
            try:
                refresh_expires_at = now + timedelta(seconds=int(refresh_expires_in))
            except (TypeError, ValueError):
                refresh_expires_at = current.refresh_expires_at if current else None

        return TokenBundle(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=now + timedelta(seconds=access_seconds),
            refresh_expires_at=refresh_expires_at,
        )

    def _load_tokens(self) -> TokenBundle | None:
        token_path = self.settings.token_path
        if not token_path.exists():
            return None

        try:
            payload = json.loads(token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        if not isinstance(payload, dict):
            return None

        try:
            bundle = TokenBundle.model_validate(payload)
        except ValueError:
            return None

        if bundle.expires_at.tzinfo is None:
            bundle.expires_at = bundle.expires_at.replace(tzinfo=timezone.utc)
        if bundle.refresh_expires_at and bundle.refresh_expires_at.tzinfo is None:
            bundle.refresh_expires_at = bundle.refresh_expires_at.replace(tzinfo=timezone.utc)
        return bundle

    def _save_tokens(self, bundle: TokenBundle) -> None:
        payload = bundle.model_dump(mode="json")
        raw = json.dumps(payload, ensure_ascii=True, indent=2)
        token_path = self.settings.token_path
        self._atomic_write_text(token_path, raw)

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)

    @staticmethod
    def _is_refresh_expired(bundle: TokenBundle) -> bool:
        if bundle.refresh_expires_at is None:
            return False
        return bundle.refresh_expires_at <= datetime.now(timezone.utc)

    def _day_bounds_utc(self, target_date: date) -> tuple[datetime, datetime]:
        start_msk = datetime.combine(target_date, datetime.min.time(), tzinfo=self._tz)
        end_msk = start_msk + timedelta(days=1)
        return start_msk.astimezone(timezone.utc), end_msk.astimezone(timezone.utc)

    @staticmethod
    def _to_zulu(dt: datetime) -> str:
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _pick_record_for_day(self, records: list[dict[str, Any]], target_date: date) -> dict[str, Any]:
        if not records:
            raise UnexpectedWhoopResponseError("No records to pick from")

        for record in records:
            if self._record_matches_day(record, target_date):
                return record
        return records[0]

    def _pick_scored_cycle(
        self,
        records: list[dict[str, Any]],
        target_date: date,
    ) -> dict[str, Any] | None:
        return self._pick_scored(records, target_date, filter_out_naps=False)

    def _pick_scored_recovery(
        self,
        records: list[dict[str, Any]],
        target_date: date,
    ) -> dict[str, Any] | None:
        return self._pick_scored(records, target_date, filter_out_naps=False)

    def _pick_scored_sleep(
        self,
        records: list[dict[str, Any]],
        target_date: date,
    ) -> dict[str, Any] | None:
        return self._pick_scored(records, target_date, filter_out_naps=True)

    def _pick_scored(
        self,
        records: list[dict[str, Any]],
        target_date: date,
        filter_out_naps: bool,
    ) -> dict[str, Any] | None:
        scoped: list[dict[str, Any]] = []
        for record in records:
            if filter_out_naps and bool(record.get("nap")):
                continue
            if self._record_matches_day(record, target_date):
                scoped.append(record)

        if not scoped:
            for record in records:
                if filter_out_naps and bool(record.get("nap")):
                    continue
                scoped.append(record)

        for record in scoped:
            if self._score_state(record) == "SCORED":
                return record
        return None

    def _record_matches_day(self, record: dict[str, Any], target_date: date) -> bool:
        for key in ("end", "start", "created_at", "updated_at"):
            value = record.get(key)
            if not isinstance(value, str):
                continue
            parsed = self._parse_datetime(value)
            if parsed is None:
                continue
            if parsed.astimezone(self._tz).date() == target_date:
                return True
        return False

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        safe = value.strip()
        if safe.endswith("Z"):
            safe = safe[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(safe)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _score_state(record: dict[str, Any]) -> str:
        value = record.get("score_state")
        if isinstance(value, str):
            return value.upper()
        return ""

    @staticmethod
    def _extract_zone(score_block: dict[str, Any], recovery_score: int) -> Literal["green", "yellow", "red"]:
        if isinstance(score_block.get("recovery_color"), str):
            color = str(score_block["recovery_color"]).lower()
            if color in {"green", "yellow", "red"}:
                return color  # type: ignore[return-value]
        if isinstance(score_block.get("recovery_zone"), str):
            zone = str(score_block["recovery_zone"]).lower()
            if zone in {"green", "yellow", "red"}:
                return zone  # type: ignore[return-value]

        if recovery_score >= 67:
            return "green"
        if recovery_score >= 34:
            return "yellow"
        return "red"

    def _map_strain(self, cycle: dict[str, Any]) -> dict[str, Any]:
        score = cycle.get("score")
        if not isinstance(score, dict):
            raise UnexpectedWhoopResponseError("Cycle score block missing")

        strain = score.get("strain")
        kilojoule = score.get("kilojoule")
        avg_hr = score.get("average_heart_rate")
        max_hr = score.get("max_heart_rate")

        if strain is None or kilojoule is None or avg_hr is None or max_hr is None:
            raise UnexpectedWhoopResponseError("Cycle score fields missing")

        return {
            "score": round(self._require_number(strain, "strain"), 1),
            "kilojoules": int(round(self._require_number(kilojoule, "kilojoule"))),
            "avg_hr_bpm": int(round(self._require_number(avg_hr, "average_heart_rate"))),
            "max_hr_bpm": int(round(self._require_number(max_hr, "max_heart_rate"))),
        }

    def _map_sleep(self, sleep: dict[str, Any]) -> dict[str, Any]:
        score = sleep.get("score")
        if not isinstance(score, dict):
            raise UnexpectedWhoopResponseError("Sleep score block missing")

        stage_summary = self._expect_stage_summary(score)
        respiratory = score.get("respiratory_rate")
        performance = score.get("sleep_performance_percentage")

        if respiratory is None or performance is None:
            raise UnexpectedWhoopResponseError("Sleep score fields missing")

        total_in_bed = stage_summary.get("total_in_bed_time_milli")
        deep_milli = stage_summary.get("total_slow_wave_sleep_time_milli")
        rem_milli = stage_summary.get("total_rem_sleep_time_milli")
        light_milli = stage_summary.get("total_light_sleep_time_milli")
        awake_milli = stage_summary.get("total_awake_time_milli")

        return {
            "score": int(round(self._require_number(performance, "sleep_performance_percentage"))),
            "total_hours": self._millis_to_hours(total_in_bed),
            "performance_percent": int(round(self._require_number(performance, "sleep_performance_percentage"))),
            "respiratory_rate": round(self._require_number(respiratory, "respiratory_rate"), 1),
            "stages": {
                "deep_hours": self._millis_to_hours(deep_milli),
                "rem_hours": self._millis_to_hours(rem_milli),
                "light_hours": self._millis_to_hours(light_milli),
                "awake_hours": self._millis_to_hours(awake_milli),
            },
        }

    @staticmethod
    def _expect_stage_summary(score: dict[str, Any]) -> dict[str, Any]:
        stage_summary = score.get("stage_summary")
        if not isinstance(stage_summary, dict):
            raise UnexpectedWhoopResponseError("Sleep stage summary missing")
        return stage_summary

    @staticmethod
    def _millis_to_hours(value: Any) -> float:
        if value is None:
            raise UnexpectedWhoopResponseError("Missing milliseconds value")
        hours = WhoopClient._require_number(value, "milliseconds") / 3_600_000
        return round(hours, 1)

    @staticmethod
    def _require_number(value: Any, field_name: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise UnexpectedWhoopResponseError(f"Invalid numeric field: {field_name}") from exc

    def _log_http_request(
        self,
        channel: str,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._log_http_event(
            event={
                "event": "whoop_http_request",
                "channel": channel,
                "method": method,
                "url": url,
                "headers": self._sanitize_mapping(headers or {}),
                "params": self._sanitize_mapping(params or {}),
                "data": self._sanitize_mapping(data or {}),
            }
        )

    def _log_http_response(
        self,
        channel: str,
        method: str,
        url: str,
        status_code: int,
        headers: dict[str, str] | None,
        body_text: str,
    ) -> None:
        body = self._sanitize_response_body(body_text)
        self._log_http_event(
            event={
                "event": "whoop_http_response",
                "channel": channel,
                "method": method,
                "url": url,
                "status_code": status_code,
                "headers": self._sanitize_mapping(headers or {}),
                "body": body,
                "body_truncated": len(body_text) > self._http_log_body_max_chars,
            }
        )

    def _log_http_error(self, channel: str, method: str, url: str, error: str) -> None:
        self._log_http_event(
            event={
                "event": "whoop_http_error",
                "channel": channel,
                "method": method,
                "url": url,
                "error": error,
            }
        )

    def _log_http_event(self, event: dict[str, Any]) -> None:
        if not self._http_log_enabled:
            return
        self._logger.log(self._http_log_level, json.dumps(event, ensure_ascii=True, default=str))

    def _sanitize_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if not self._http_log_redact_sensitive:
            return dict(mapping)

        masked: dict[str, Any] = {}
        for key, value in mapping.items():
            lower_key = key.lower()
            if lower_key in {
                "authorization",
                "client_secret",
                "refresh_token",
                "access_token",
                "code",
            }:
                masked[key] = self._mask_value(value)
            elif lower_key == "client_id":
                masked[key] = self._mask_value(value)
            else:
                masked[key] = value
        return masked

    def _sanitize_response_body(self, body_text: str) -> str:
        truncated = body_text[: self._http_log_body_max_chars]
        if not self._http_log_redact_sensitive:
            return truncated

        try:
            payload = json.loads(truncated)
        except ValueError:
            return truncated

        if isinstance(payload, dict):
            sanitized_payload = self._sanitize_mapping(payload)
            return json.dumps(sanitized_payload, ensure_ascii=True)
        return truncated

    @staticmethod
    def _mask_value(value: Any) -> str:
        if value is None:
            return ""
        raw = str(value)
        if len(raw) <= 8:
            return "***"
        return f"{raw[:4]}***{raw[-4:]}"

    @staticmethod
    def _resolve_log_level(raw_level: str) -> int:
        normalized = (raw_level or "INFO").upper().strip()
        return getattr(logging, normalized, logging.INFO)
