from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import coach_normalize as cn

MSK = ZoneInfo("Europe/Moscow")


# --------------------------------------------------------------------------- #
# unit conversions
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_millis_to_hours_and_minutes():
    assert cn.millis_to_hours(3_600_000) == 1.0
    assert cn.millis_to_hours(None) is None
    assert cn.millis_to_minutes(60_000) == 1.0
    assert cn.millis_to_minutes(None) is None


@pytest.mark.unit
def test_iso_offset_renders_msk_offset_without_microseconds():
    assert cn.iso_offset("2026-05-31T05:59:00Z", MSK) == "2026-05-31T08:59:00+03:00"
    assert cn.iso_offset(None, MSK) is None
    assert cn.iso_offset("not-a-date", MSK) is None


# --------------------------------------------------------------------------- #
# sleep wake_date assignment (CRITICAL)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_sleep_assigned_to_wake_date_across_local_midnight():
    record = {
        "id": "s1",
        "score_state": "SCORED",
        "nap": False,
        "start": "2026-05-30T23:40:00+03:00",
        "end": "2026-05-31T07:20:00+03:00",
        "score": {"stage_summary": {}},
    }
    block = cn.normalize_sleep(record, MSK)
    assert block["assigned_date"] == "2026-05-31"
    assert block["assignment_rule"] == "wake_date"


@pytest.mark.unit
def test_sleep_assigned_to_wake_date_across_utc_boundary():
    # 22:30Z == 01:30 MSK on the next calendar day.
    record = {
        "id": "s1",
        "score_state": "SCORED",
        "nap": False,
        "start": "2026-05-30T18:00:00Z",
        "end": "2026-05-30T22:30:00Z",
        "score": {"stage_summary": {}},
    }
    block = cn.normalize_sleep(record, MSK)
    assert block["assigned_date"] == "2026-05-31"


@pytest.mark.unit
def test_sleep_millis_to_hours_and_canonical_keys():
    record = {
        "id": "s1",
        "score_state": "SCORED",
        "nap": False,
        "start": "2026-05-31T02:00:00+03:00",
        "end": "2026-05-31T08:00:00+03:00",
        "score": {
            "sleep_performance_percentage": 71,
            "sleep_efficiency_percentage": 84,
            "sleep_consistency_percentage": 42,
            "respiratory_rate": 16.1,
            "stage_summary": {
                "total_in_bed_time_milli": 24_660_000,  # 6.85h
                "total_awake_time_milli": 1_620_000,  # 0.45h
                "total_light_sleep_time_milli": 15_480_000,  # 4.3h
                "total_slow_wave_sleep_time_milli": 3_240_000,  # 0.9h
                "total_rem_sleep_time_milli": 4_320_000,  # 1.2h
                "sleep_cycle_count": 3,
                "disturbance_count": 12,
            },
            "sleep_needed": {
                "baseline_milli": 27_360_000,  # 7.6h
                "need_from_sleep_debt_milli": 1_080_000,  # 0.3h
                "need_from_recent_strain_milli": 360_000,  # 0.1h
                "need_from_recent_nap_milli": 0,
            },
        },
    }
    block = cn.normalize_sleep(record, MSK)
    assert block["status"] == "ready"
    assert block["in_bed_hours"] == 6.85
    # total = asleep = light + deep + rem
    assert block["total_hours"] == 6.4
    assert block["efficiency_percentage"] == 84
    assert block["performance_percentage"] == 71
    assert block["consistency_percentage"] == 42
    assert block["stages"] == {
        "deep_hours": 0.9,
        "rem_hours": 1.2,
        "light_hours": 4.3,
        "awake_hours": 0.45,
    }
    assert block["stage_summary"]["sleep_cycle_count"] == 3
    assert block["stage_summary"]["disturbance_count"] == 12
    assert block["stage_summary"]["total_slow_wave_sleep_hours"] == 0.9
    # derived total need = 7.6 + 0.3 + 0.1 + 0.0
    assert block["sleep_needed"]["total_need_hours"] == 8.0
    assert block["sleep_needed"]["baseline_hours"] == 7.6
    assert block["sleep_needed"]["need_from_sleep_debt_hours"] == 0.3
    assert block["respiratory_rate"] == 16.1


@pytest.mark.unit
def test_sleep_pending_when_not_scored():
    record = {"id": "s1", "score_state": "PENDING_SCORE", "nap": False, "end": "2026-05-31T08:00:00+03:00"}
    block = cn.normalize_sleep(record, MSK)
    assert block["status"] == "pending"


@pytest.mark.unit
def test_sleep_detail_surface_drops_drilldown_but_keeps_surface():
    record = {
        "id": "s1",
        "score_state": "SCORED",
        "nap": False,
        "start": "2026-05-31T02:00:00+03:00",
        "end": "2026-05-31T08:00:00+03:00",
        "score": {"sleep_performance_percentage": 71, "respiratory_rate": 16.1, "stage_summary": {}},
    }
    block = cn.normalize_sleep(record, MSK, detail="surface")
    assert "stage_summary" not in block
    assert "sleep_needed" not in block
    assert "respiratory_rate" not in block
    assert block["performance_percentage"] == 71
    assert "stages" in block


# --------------------------------------------------------------------------- #
# recovery
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_recovery_maps_canonical_score_block_and_computes_zone():
    record = {
        "cycle_id": 93845,
        "sleep_id": "123e4567-e89b-12d3-a456-426614174000",
        "score_state": "SCORED",
        "created_at": "2026-05-31T06:00:00Z",
        "updated_at": "2026-05-31T06:10:00Z",
        "score": {
            "recovery_score": 74,
            "resting_heart_rate": 48,
            "hrv_rmssd_milli": 63,
            "spo2_percentage": 98.0,
            "skin_temp_celsius": 36.4,
            "user_calibrating": False,
        },
    }
    block = cn.normalize_recovery(record, MSK)
    assert block["status"] == "ready"
    assert block["score"] == 74
    assert block["zone"] == "green"  # 74 >= 67
    assert block["hrv_ms"] == 63  # passthrough, not converted
    assert block["resting_hr_bpm"] == 48
    assert block["cycle_id"] == 93845
    assert block["sleep_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert block["user_calibrating"] is False  # read from inside score
    assert block["spo2_percentage"] == 98.0
    assert block["skin_temp_celsius"] == 36.4


@pytest.mark.unit
@pytest.mark.parametrize("score,zone", [(74, "green"), (50, "yellow"), (20, "red")])
def test_recovery_zone_thresholds(score, zone):
    record = {"score_state": "SCORED", "score": {"recovery_score": score, "resting_heart_rate": 50, "hrv_rmssd_milli": 40}}
    assert cn.normalize_recovery(record, MSK)["zone"] == zone


@pytest.mark.unit
def test_recovery_pending_when_processing():
    record = {"score_state": "PENDING_SCORE"}
    assert cn.normalize_recovery(record, MSK)["status"] == "pending"
    assert cn.normalize_recovery(None, MSK)["status"] == "missing"


# --------------------------------------------------------------------------- #
# day strain / is_final (CRITICAL)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_day_strain_current_cycle_is_not_final_and_states_zones_unavailable():
    cycle = {
        "id": 93845,
        "score_state": "SCORED",
        "start": "2026-05-31T00:15:00+03:00",
        "end": None,
        "score": {"strain": 4.2, "kilojoule": 650, "average_heart_rate": 78, "max_heart_rate": 122},
    }
    block = cn.normalize_day_strain(cycle, MSK, target_date=date(2026, 5, 31))
    assert block["status"] == "ready"
    assert block["is_final"] is False
    assert block["score"] == 4.2
    assert block["kilojoules"] == 650
    assert block["hr_zones_available"] is False
    assert block["hr_zones_min"] is None


@pytest.mark.unit
def test_day_strain_closed_cycle_is_final():
    cycle = {
        "id": 93844,
        "score_state": "SCORED",
        "start": "2026-05-30T00:15:00+03:00",
        "end": "2026-05-31T00:15:00+03:00",
        "score": {"strain": 12.8, "kilojoule": 2400, "average_heart_rate": 91, "max_heart_rate": 160},
    }
    block = cn.normalize_day_strain(cycle, MSK, target_date=date(2026, 5, 30), surface_only_block=True)
    assert block["is_final"] is True
    assert block["score"] == 12.8
    # previous_day_strain shape omits drilldown
    assert "hr_zones_min" not in block
    assert "cycle_start" not in block


@pytest.mark.unit
def test_day_strain_missing_when_no_cycle():
    block = cn.normalize_day_strain(None, MSK, target_date=date(2026, 5, 31))
    assert block["status"] == "missing"
    assert block["date"] == "2026-05-31"


# --------------------------------------------------------------------------- #
# workout
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_workout_zone_durations_to_minutes_and_duration():
    record = {
        "id": "w1",
        "sport_name": "Volleyball",
        "sport_id": 43,
        "start": "2026-05-31T14:00:00+03:00",
        "end": "2026-05-31T15:45:00+03:00",
        "score_state": "SCORED",
        "score": {
            "strain": 10.4,
            "average_heart_rate": 124,
            "max_heart_rate": 172,
            "kilojoule": 1350,
            "percent_recorded": 100,
            "zone_durations": {
                "zone_zero_milli": 720_000,  # 12 min
                "zone_one_milli": 1_320_000,  # 22 min
                "zone_two_milli": 1_860_000,  # 31 min
                "zone_three_milli": 1_680_000,  # 28 min
                "zone_four_milli": 600_000,  # 10 min
                "zone_five_milli": 120_000,  # 2 min
            },
        },
    }
    block = cn.normalize_workout(record, MSK)
    assert block["workout_id"] == "w1"
    assert block["date"] == "2026-05-31"
    assert block["sport_name"] == "volleyball"
    assert block["sport_id"] == 43
    assert block["duration_min"] == 105.0
    assert block["strain_score"] == 10.4
    assert block["kilojoules"] == 1350
    assert block["percent_recorded"] == 100
    assert block["zone_durations_min"] == {"z0": 12.0, "z1": 22.0, "z2": 31.0, "z3": 28.0, "z4": 10.0, "z5": 2.0}


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [(1.0, 100.0), (0.85, 85.0), (100, 100.0), (90, 90.0)])
def test_workout_percent_recorded_fraction_normalized_to_percentage(raw, expected):
    # WHOOP v2 returns percent_recorded as a 0..1 fraction (confirmed via live data);
    # the contract and the agent's '< 90' rule expect a 0..100 percentage.
    record = {
        "id": "w1",
        "start": "2026-05-31T14:00:00+03:00",
        "end": "2026-05-31T15:00:00+03:00",
        "score_state": "SCORED",
        "score": {"percent_recorded": raw},
    }
    block = cn.normalize_workout(record, MSK)
    assert block["percent_recorded"] == expected


@pytest.mark.unit
def test_workout_without_id_or_times_is_skipped():
    assert cn.normalize_workout({"sport_name": "run"}, MSK) is None
    assert cn.normalize_workout({"id": "w1"}, MSK) is None


# --------------------------------------------------------------------------- #
# body
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_body_ready_and_missing_variants():
    ready = cn.normalize_body(
        {"height_meter": 1.83, "weight_kilogram": 83.2, "max_heart_rate": 195},
        MSK,
        measured_at="2026-05-31T06:05:00Z",
    )
    assert ready["status"] == "ready"
    assert ready["weight_kg"] == 83.2
    assert ready["height_m"] == 1.83
    assert ready["max_heart_rate"] == 195
    assert ready["measured_at"] == "2026-05-31T09:05:00+03:00"
    assert ready["source"] == "whoop"

    missing = cn.normalize_body(None, MSK)
    assert missing["status"] == "missing"
    assert missing["weight_kg"] is None and missing["height_m"] is None and missing["max_heart_rate"] is None


# --------------------------------------------------------------------------- #
# freshness
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_freshness_full_day_fresh_for_recovery_sleep():
    now = datetime(2026, 5, 31, 22, 0, tzinfo=MSK)
    fresh = cn.freshness_entry(updated_at="2026-05-31T06:10:00Z", source="whoop", now=now, tz=MSK, full_day_fresh=True)
    assert fresh["status"] == "fresh"
    stale = cn.freshness_entry(updated_at="2026-05-30T06:10:00Z", source="cache", now=now, tz=MSK, full_day_fresh=True)
    assert stale["status"] == "stale"


@pytest.mark.unit
def test_freshness_heartbeat_window_and_missing_unknown():
    now = datetime(2026, 5, 31, 22, 0, tzinfo=MSK)
    fresh = cn.freshness_entry(
        updated_at="2026-05-31T18:40:00Z", source="whoop", now=now, tz=MSK, stale_after_seconds=2700
    )  # 21:40 MSK, 20 min ago
    assert fresh["status"] == "fresh"
    stale = cn.freshness_entry(
        updated_at="2026-05-31T17:00:00Z", source="whoop", now=now, tz=MSK, stale_after_seconds=2700
    )  # 20:00 MSK, 2h ago
    assert stale["status"] == "stale"
    missing = cn.freshness_entry(updated_at=None, source="whoop", now=now, tz=MSK, stale_after_seconds=2700)
    assert missing["status"] == "missing" and missing["updated_at"] is None
    unknown = cn.freshness_entry(updated_at="bad", source="whoop", now=now, tz=MSK, stale_after_seconds=2700)
    assert unknown["status"] == "unknown"
