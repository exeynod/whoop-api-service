from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class FileCache:
    def __init__(self, cache_dir: Path, timezone_name: str, retention_days: int = 30) -> None:
        self.cache_dir = cache_dir
        self.retention_days = retention_days
        self._tz = ZoneInfo(timezone_name)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _profile_dir(self, profile_name: str) -> Path:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", profile_name).strip("._")
        if not normalized:
            normalized = "unknown"
        profile_dir = self.cache_dir / normalized
        profile_dir.mkdir(parents=True, exist_ok=True)
        return profile_dir

    def _path_for(self, profile_name: str, endpoint: str, target_date: date) -> Path:
        return self._profile_dir(profile_name) / f"{endpoint}_{target_date.isoformat()}.json"

    def _range_path_for(self, profile_name: str, endpoint: str, range_key: str) -> Path:
        safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", range_key).strip("._")
        if not safe_key:
            safe_key = "default"
        return self._profile_dir(profile_name) / f"{endpoint}_range_{safe_key}.json"

    def load_ready(self, profile_name: str, endpoint: str, target_date: date) -> dict | None:
        path = self._path_for(profile_name, endpoint, target_date)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

        if payload.get("status") != "ready":
            return None
        return payload

    def save_ready(self, profile_name: str, endpoint: str, target_date: date, payload: dict) -> bool:
        if payload.get("status") != "ready":
            return False

        path = self._path_for(profile_name, endpoint, target_date)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
        return True

    def load_range_ready(
        self,
        profile_name: str,
        endpoint: str,
        range_key: str,
        ttl_seconds: int,
    ) -> dict | None:
        path = self._range_path_for(profile_name, endpoint, range_key)
        if not path.exists():
            return None

        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

        if not isinstance(envelope, dict):
            return None
        saved_at_raw = envelope.get("saved_at")
        payload = envelope.get("payload")
        if not isinstance(saved_at_raw, str) or not isinstance(payload, dict):
            return None

        saved_at = self._parse_datetime(saved_at_raw)
        if saved_at is None:
            return None
        if payload.get("status") != "ready":
            return None

        now_utc = datetime.now(timezone.utc)
        if ttl_seconds >= 0 and (now_utc - saved_at) > timedelta(seconds=ttl_seconds):
            return None
        return payload

    def save_range_ready(self, profile_name: str, endpoint: str, range_key: str, payload: dict) -> bool:
        if payload.get("status") != "ready":
            return False

        path = self._range_path_for(profile_name, endpoint, range_key)
        temp_path = path.with_suffix(".tmp")
        envelope = {
            "saved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "payload": payload,
        }
        temp_path.write_text(
            json.dumps(envelope, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
        return True

    def cleanup_expired(self, today: date | None = None) -> int:
        current_day = today or datetime.now(self._tz).date()
        current_utc = datetime.now(timezone.utc)
        deleted = 0

        for file_path in self.cache_dir.rglob("*.json"):
            extracted_date = self._extract_date(file_path)
            if extracted_date is not None:
                age = (current_day - extracted_date).days
                if age > self.retention_days:
                    file_path.unlink(missing_ok=True)
                    deleted += 1
                continue

            if "_range_" in file_path.stem:
                if self._is_range_cache_expired(file_path, current_utc):
                    file_path.unlink(missing_ok=True)
                    deleted += 1

        return deleted

    @staticmethod
    def _extract_date(file_path: Path) -> date | None:
        stem = file_path.stem
        if "_" not in stem:
            return None

        date_fragment = stem.rsplit("_", maxsplit=1)[-1]
        try:
            return date.fromisoformat(date_fragment)
        except ValueError:
            return None

    def _is_range_cache_expired(self, file_path: Path, current_utc: datetime) -> bool:
        try:
            envelope = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True

        if not isinstance(envelope, dict):
            return True
        saved_at_raw = envelope.get("saved_at")
        if not isinstance(saved_at_raw, str):
            return True

        saved_at = self._parse_datetime(saved_at_raw)
        if saved_at is None:
            return True
        return (current_utc - saved_at).days > self.retention_days

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
        return parsed.astimezone(timezone.utc)
