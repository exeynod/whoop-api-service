from __future__ import annotations

import pytest

from app.main import create_app


@pytest.mark.unit
def test_all_documented_coach_and_raw_routes_are_registered():
    app = create_app()
    paths = {route.path for route in app.routes}

    expected = {
        # coach v2 contract
        "/coach/status",
        "/coach/today",
        "/coach/day/{target_date}",
        "/coach/body/latest",
        "/coach/week",
        "/coach/training-context",
        "/coach/sleep-context",
        "/coach/recovery-context",
        # raw passthrough
        "/raw/cycles",
        "/raw/recoveries",
        "/raw/sleeps",
        "/raw/workouts",
        # backward-compatible legacy routes must remain
        "/recovery/today",
        "/day/yesterday",
        "/week",
        "/cycles",
        "/workouts",
        "/measurements/body",
        "/measurements/body/history",
        "/health",
    }
    missing = expected - paths
    assert not missing, f"missing routes: {sorted(missing)}"
