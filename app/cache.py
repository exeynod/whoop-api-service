from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class FileCache:
    def __init__(self, cache_dir: Path, timezone_name: str, retention_days: int = 30) -> None:
        self.cache_dir = cache_dir
        self.retention_days = retention_days
        self._tz = ZoneInfo(timezone_name)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, endpoint: str, target_date: date) -> Path:
        return self.cache_dir / f"{endpoint}_{target_date.isoformat()}.json"

    def load_ready(self, endpoint: str, target_date: date) -> dict | None:
        path = self._path_for(endpoint, target_date)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

        if payload.get("status") != "ready":
            return None
        return payload

    def save_ready(self, endpoint: str, target_date: date, payload: dict) -> bool:
        if payload.get("status") != "ready":
            return False

        path = self._path_for(endpoint, target_date)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
        return True

    def cleanup_expired(self, today: date | None = None) -> int:
        current_day = today or datetime.now(self._tz).date()
        deleted = 0

        for file_path in self.cache_dir.glob("*.json"):
            extracted_date = self._extract_date(file_path)
            if extracted_date is None:
                continue

            age = (current_day - extracted_date).days
            if age > self.retention_days:
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
