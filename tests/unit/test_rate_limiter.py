from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.rate_limiter import EndpointRateLimiter


@pytest.mark.unit
def test_returns_last_pending_within_interval():
    limiter = EndpointRateLimiter(min_interval_seconds=300)
    now = datetime(2026, 2, 27, 7, 0, tzinfo=timezone.utc)
    payload = {"status": "pending", "reason": "Sleep not yet complete."}

    limiter.remember_pending("recovery_today", now, payload)

    replay = limiter.get_pending_if_limited("recovery_today", now + timedelta(seconds=120))
    assert replay == payload


@pytest.mark.unit
def test_stops_returning_pending_after_interval():
    limiter = EndpointRateLimiter(min_interval_seconds=300)
    now = datetime(2026, 2, 27, 7, 0, tzinfo=timezone.utc)
    limiter.remember_pending("recovery_today", now, {"status": "pending", "reason": "Wait"})

    replay = limiter.get_pending_if_limited("recovery_today", now + timedelta(seconds=301))
    assert replay is None


@pytest.mark.unit
def test_pop_pending_clears_state():
    limiter = EndpointRateLimiter(min_interval_seconds=300)
    now = datetime(2026, 2, 27, 7, 0, tzinfo=timezone.utc)
    limiter.remember_pending("recovery_today", now, {"status": "pending", "reason": "Wait"})

    limiter.pop_pending("recovery_today")

    assert limiter.get_pending_if_limited("recovery_today", now + timedelta(seconds=10)) is None
