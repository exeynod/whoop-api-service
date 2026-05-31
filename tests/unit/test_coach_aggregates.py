from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import json

import pytest
import respx
from freezegun import freeze_time

from app import coach_aggregate as agg
from app.config import get_settings
from app.whoop_client import WhoopClient


def _row(d, *, recovery=None, zone=None, hrv=None, rhr=None, spo2=None, skin=None, sleep_h=None,
         deep=None, rem=None, light=None, eff=None, perf=None, cons=None, resp=None, dist=None,
         started=None, strain=None, kj=None, workouts=None):
    return {
        "date": d,
        "recovery_score": recovery,
        "recovery_zone": zone,
        "hrv_ms": hrv,
        "resting_hr_bpm": rhr,
        "spo2_percentage": spo2,
        "skin_temp_celsius": skin,
        "recovery_score_state": "SCORED" if recovery is not None else None,
        "sleep_started_at": started,
        "sleep_ended_at": None,
        "sleep_total_hours": sleep_h,
        "sleep_deep_hours": deep,
        "sleep_rem_hours": rem,
        "sleep_light_hours": light,
        "sleep_efficiency_percentage": eff,
        "sleep_performance_percentage": perf,
        "sleep_consistency_percentage": cons,
        "sleep_respiratory_rate": resp,
        "sleep_disturbance_count": dist,
        "strain_score": strain,
        "strain_is_final": True,
        "kilojoules": kj,
        "workout_count": len(workouts or []),
        "workout_sports": workouts or [],
    }


def _bundle(rows, workouts=None, nap_count=0, errors=None, days=None):
    return {
        "period": {"from": rows[0]["date"], "to": rows[-1]["date"], "days": days or len(rows), "timezone": "Europe/Moscow"},
        "rows": rows,
        "workouts": workouts or [],
        "nap_count": nap_count,
        "errors": errors or [],
    }


@pytest.mark.unit
def test_week_averages_skip_missing_days():
    rows = [
        _row("2026-05-25", recovery=60, zone="yellow", strain=10.0, workouts=["volleyball"]),
        _row("2026-05-26", recovery=70, zone="green", strain=14.0, workouts=[]),
        _row("2026-05-27", recovery=None, zone=None, strain=None, workouts=["weightlifting"]),
    ]
    workouts = [{"sport_name": "volleyball"}, {"sport_name": "weightlifting"}]
    result = agg.build_week(_bundle(rows, workouts), include_days=True, include_workouts=True)

    assert result["status"] == "ready"
    assert result["summary"]["avg_recovery_score"] == 65.0  # (60+70)/2, None skipped
    assert result["summary"]["total_strain"] == 24.0
    assert result["summary"]["max_daily_strain"] == 14.0
    assert result["summary"]["workout_count"] == 2
    assert result["summary"]["volleyball_count"] == 1
    assert result["summary"]["strength_count"] == 1
    assert result["summary"]["rest_day_count"] == 1  # one row with no workouts
    assert len(result["days"]) == 3
    assert result["days"][0]["recovery_score"] == 60


@pytest.mark.unit
def test_week_partial_when_bundle_has_errors():
    rows = [_row("2026-05-25", recovery=60, zone="yellow")]
    result = agg.build_week(_bundle(rows, errors=[{"block": "cycles", "reason": "x"}]), include_days=False, include_workouts=False)
    assert result["status"] == "partial"
    assert "days" not in result and "workouts" not in result


@pytest.mark.unit
def test_training_context_ratio_and_div_by_zero():
    # 14 days: prev 7 strain all 0, last 7 strain 10 each -> ratio None (div by zero)
    rows = [_row(f"2026-05-{10 + i:02d}", strain=0.0) for i in range(7)]
    rows += [_row(f"2026-05-{17 + i:02d}", strain=10.0) for i in range(7)]
    result = agg.build_training_context(_bundle(rows, days=14), include_daily=True, include_workouts=False)
    assert result["load_summary"]["last_7d_strain"] == 70.0
    assert result["load_summary"]["prev_7d_strain"] == 0.0
    assert result["load_summary"]["strain_ratio_7d_vs_prev_7d"] is None
    assert len(result["daily_load"]) == 14


@pytest.mark.unit
def test_training_context_ratio_normal_and_day_counts():
    rows = [_row(f"2026-05-{10 + i:02d}", strain=10.0) for i in range(7)]
    rows += [_row(f"2026-05-{17 + i:02d}", strain=15.0, workouts=["volleyball"]) for i in range(7)]
    result = agg.build_training_context(_bundle(rows, days=14), include_daily=False, include_workouts=False)
    assert result["load_summary"]["strain_ratio_7d_vs_prev_7d"] == round(105.0 / 70.0, 2)
    assert result["load_summary"]["high_strain_days"] == 7  # the 15.0 days
    assert result["load_summary"]["rest_days"] == 7  # first 7 have no workouts


@pytest.mark.unit
def test_sleep_context_late_bedtime_and_nap_count():
    rows = [
        _row("2026-05-25", started="2026-05-25T02:00:00+03:00", sleep_h=6.0),  # late (past midnight)
        _row("2026-05-26", started="2026-05-25T22:30:00+03:00", sleep_h=7.5),  # not late
    ]
    result = agg.build_sleep_context(_bundle(rows, nap_count=2))
    assert result["summary"]["late_bedtime_count"] == 1
    assert result["summary"]["nap_count"] == 2
    assert result["summary"]["avg_total_hours"] == 6.75
    assert len(result["days"]) == 2


@pytest.mark.unit
def test_recovery_context_zone_counts():
    rows = [
        _row("2026-05-25", recovery=80, zone="green", hrv=60, rhr=48),
        _row("2026-05-26", recovery=50, zone="yellow", hrv=50, rhr=50),
        _row("2026-05-27", recovery=20, zone="red", hrv=40, rhr=55),
    ]
    result = agg.build_recovery_context(_bundle(rows))
    assert result["summary"]["green_days"] == 1
    assert result["summary"]["yellow_days"] == 1
    assert result["summary"]["red_days"] == 1
    assert result["summary"]["avg_recovery_score"] == 50.0
    assert result["days"][0]["recovery_zone"] == "green"


# --------------------------------------------------------------------------- #
# fetch_coach_range builds rows from raw collections
# --------------------------------------------------------------------------- #
def _write_profile(path):
    now = datetime.now(timezone.utc)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "profiles": {
                    "denis": {
                        "api_token": "api-denis",
                        "whoop": {
                            "access_token": "a",
                            "refresh_token": "r",
                            "expires_at": (now + timedelta(hours=1)).isoformat(),
                            "refresh_expires_at": (now + timedelta(days=7)).isoformat(),
                        },
                        "meta": {"active": True},
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.unit
@pytest.mark.asyncio
@freeze_time("2026-02-27 10:00:00", tz_offset=3)
async def test_fetch_coach_range_builds_daily_rows(tmp_secrets_dir):
    settings = get_settings()
    _write_profile(settings.token_path)
    client = WhoopClient(settings)
    base = settings.whoop_api_base_url

    cycle = {
        "id": 700,
        "score_state": "SCORED",
        "start": "2026-02-26T00:00:00Z",
        "end": "2026-02-27T00:00:00Z",
        "score": {"strain": 12.0, "kilojoule": 1500, "average_heart_rate": 90, "max_heart_rate": 150},
    }
    recovery = {
        "cycle_id": 700,
        "score_state": "SCORED",
        "created_at": "2026-02-26T06:00:00Z",
        "score": {"recovery_score": 65, "resting_heart_rate": 49, "hrv_rmssd_milli": 55, "spo2_percentage": 97.0},
    }
    sleep = {
        "id": "s700",
        "cycle_id": 700,
        "score_state": "SCORED",
        "nap": False,
        "start": "2026-02-25T23:00:00Z",
        "end": "2026-02-26T05:00:00Z",
        "score": {
            "sleep_performance_percentage": 80,
            "respiratory_rate": 15.0,
            "stage_summary": {
                "total_in_bed_time_milli": 21_600_000,
                "total_light_sleep_time_milli": 14_400_000,
                "total_slow_wave_sleep_time_milli": 3_600_000,
                "total_rem_sleep_time_milli": 3_600_000,
                "disturbance_count": 5,
            },
        },
    }
    workout = {
        "id": "wk",
        "sport_name": "volleyball",
        "start": "2026-02-26T15:00:00Z",
        "end": "2026-02-26T16:30:00Z",
        "score_state": "SCORED",
        "score": {"strain": 9.0},
    }

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{base}/v2/cycle").respond(200, json={"records": [cycle]})
        mock.get(f"{base}/v2/recovery").respond(200, json={"records": [recovery]})
        mock.get(f"{base}/v2/activity/sleep").respond(200, json={"records": [sleep]})
        mock.get(f"{base}/v2/activity/workout").respond(200, json={"records": [workout]})
        bundle = await client.fetch_coach_range("denis", date(2026, 2, 27), days=7)

    assert bundle["period"] == {"from": "2026-02-21", "to": "2026-02-27", "days": 7, "timezone": "Europe/Moscow"}
    assert len(bundle["rows"]) == 7
    feb26 = next(r for r in bundle["rows"] if r["date"] == "2026-02-26")
    assert feb26["recovery_score"] == 65
    assert feb26["strain_score"] == 12.0
    assert feb26["sleep_total_hours"] == 6.0
    assert feb26["sleep_disturbance_count"] == 5
    assert feb26["workout_count"] == 1
    assert feb26["workout_sports"] == ["volleyball"]
    assert len(bundle["workouts"]) == 1
