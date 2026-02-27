from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any


@dataclass
class PendingEntry:
    created_at: datetime
    payload: dict[str, Any]


class EndpointRateLimiter:
    def __init__(self, min_interval_seconds: int) -> None:
        self._min_interval = timedelta(seconds=min_interval_seconds)
        self._last_attempt: dict[str, datetime] = {}
        self._pending_entries: dict[str, PendingEntry] = {}
        self._lock = Lock()

    def should_call(self, endpoint_key: str, now: datetime) -> bool:
        with self._lock:
            last_attempt = self._last_attempt.get(endpoint_key)
            if last_attempt is not None and (now - last_attempt) < self._min_interval:
                return False

            self._last_attempt[endpoint_key] = now
            return True

    def remember_pending(self, endpoint_key: str, now: datetime, payload: dict[str, Any]) -> None:
        with self._lock:
            self._pending_entries[endpoint_key] = PendingEntry(created_at=now, payload=payload)

    def pop_pending(self, endpoint_key: str) -> None:
        with self._lock:
            self._pending_entries.pop(endpoint_key, None)

    def get_pending_if_limited(self, endpoint_key: str, now: datetime) -> dict[str, Any] | None:
        with self._lock:
            last_attempt = self._last_attempt.get(endpoint_key)
            pending_entry = self._pending_entries.get(endpoint_key)

            if last_attempt is None or pending_entry is None:
                return None

            if (now - last_attempt) < self._min_interval:
                return dict(pending_entry.payload)
            return None
