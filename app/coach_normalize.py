"""Pure normalizers for the WHOOP Service v2 *coach* layer.

This module turns raw WHOOP v2 records into the normalized, surface+drilldown
coach objects described in the v2 spec. It is FACTS ONLY: it never emits coach
flags, training_readiness, should_train, recommendations or any interpretation —
only objective metrics and technical statuses. Interpretation is the agent's job.

Field mapping follows the canonical WHOOP v2 names confirmed against the developer
docs (see whoop_service_v2_tz.md section 21):
  - sleep score percentages are ``sleep_performance_percentage`` /
    ``sleep_consistency_percentage`` / ``sleep_efficiency_percentage``;
  - sleep need deltas are ``need_from_sleep_debt_milli`` /
    ``need_from_recent_strain_milli`` / ``need_from_recent_nap_milli`` plus
    ``baseline_milli`` (no raw ``total_need_milli`` — total is derived);
  - ``user_calibrating`` lives inside the recovery ``score`` block;
  - recovery has no color in v2 — the zone is computed from the score;
  - a cycle is the *current* one (``is_final == False``) when its ``end`` is absent.

The pure record-extraction helpers are reused verbatim from :class:`WhoopClient`
(they are static methods), so the coach layer stays byte-consistent with the
existing ``/cycles`` and ``/workouts`` endpoints.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.whoop_client import WhoopClient

# Reused static extraction helpers (no instance / HTTP state required).
_first_number = WhoopClient._first_number
_first_bool = WhoopClient._first_bool
_extract_zone = WhoopClient._extract_zone
_parse_dt = WhoopClient._parse_datetime
_score_state = WhoopClient._score_state

SCORED = "SCORED"
PENDING_SCORE = "PENDING_SCORE"
UNSCORABLE = "UNSCORABLE"


# --------------------------------------------------------------------------- #
# small unit / timestamp helpers
# --------------------------------------------------------------------------- #
def millis_to_hours(value: Any, digits: int = 2) -> float | None:
    number = _first_number({"v": value}, ["v"])
    if number is None:
        return None
    return round(number / 3_600_000, digits)


def millis_to_minutes(value: Any, digits: int = 1) -> float | None:
    number = _first_number({"v": value}, ["v"])
    if number is None:
        return None
    return round(number / 60_000, digits)


def iso_offset(value: Any, tz: ZoneInfo) -> str | None:
    """Parse a WHOOP timestamp and render it ISO8601 in ``tz`` with offset."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return parsed.astimezone(tz).replace(microsecond=0).isoformat()


def source_timezone_offset(record: dict[str, Any], tz: ZoneInfo, *, instant: str | None = None) -> str:
    raw = record.get("timezone_offset")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    moment = _parse_dt(instant) if isinstance(instant, str) else None
    if moment is None:
        moment = datetime.now(tz)
    offset = moment.astimezone(tz).utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def assigned_wake_date(sleep_record: dict[str, Any], tz: ZoneInfo) -> str | None:
    """Sleep is assigned to its WAKE date (``end`` in coach tz), not bedtime."""
    end = sleep_record.get("end")
    parsed = _parse_dt(end) if isinstance(end, str) else None
    if parsed is None:
        return None
    return parsed.astimezone(tz).date().isoformat()


# --------------------------------------------------------------------------- #
# recovery
# --------------------------------------------------------------------------- #
def normalize_recovery(record: dict[str, Any] | None, tz: ZoneInfo, *, detail: str = "full") -> dict[str, Any]:
    if record is None:
        return {"status": "missing", "reason": "No recovery record for this day."}

    state = _score_state(record)
    score_block = record.get("score") if isinstance(record.get("score"), dict) else None

    if state == PENDING_SCORE or (state != SCORED and score_block is None):
        return {
            "status": "pending",
            "score_state": state or PENDING_SCORE,
            "reason": "Recovery is not available yet after sleep.",
        }
    if state == UNSCORABLE or score_block is None:
        return {
            "status": "missing",
            "score_state": state or UNSCORABLE,
            "reason": "Recovery could not be scored.",
        }

    raw_score = _first_number(score_block, ["recovery_score"])
    score = int(round(raw_score)) if raw_score is not None else None
    created = record.get("created_at")
    updated = record.get("updated_at")

    block: dict[str, Any] = {
        "status": "ready",
        "score_state": state,
        "score": score,
        "zone": _extract_zone(score_block, score if score is not None else 0),
        "hrv_ms": _opt_int(_first_number(score_block, ["hrv_rmssd_milli"])),
        "resting_hr_bpm": _opt_int(_first_number(score_block, ["resting_heart_rate"])),
        "cycle_id": _opt_int(record.get("cycle_id")),
        "sleep_id": _opt_str(record.get("sleep_id")),
        "measured_at": iso_offset(created or updated, tz),
    }
    if detail == "full":
        calibrating = _first_bool(score_block, ["user_calibrating"])
        if calibrating is None:
            calibrating = _first_bool(record, ["user_calibrating"])
        spo2 = _first_number(score_block, ["spo2_percentage", "spo2"])
        skin = _first_number(score_block, ["skin_temp_celsius", "skin_temperature_celsius"])
        block.update(
            {
                "user_calibrating": calibrating,
                "spo2_percentage": round(spo2, 1) if spo2 is not None else None,
                "skin_temp_celsius": round(skin, 1) if skin is not None else None,
                "created_at": iso_offset(created, tz),
                "updated_at": iso_offset(updated, tz),
                "source_timezone_offset": source_timezone_offset(record, tz, instant=created or updated),
            }
        )
    return block


# --------------------------------------------------------------------------- #
# sleep
# --------------------------------------------------------------------------- #
def normalize_sleep(record: dict[str, Any] | None, tz: ZoneInfo, *, detail: str = "full") -> dict[str, Any]:
    if record is None:
        return {"status": "missing", "reason": "No sleep record for this day."}

    state = _score_state(record)
    score_block = record.get("score") if isinstance(record.get("score"), dict) else None

    if state == PENDING_SCORE or (state != SCORED and score_block is None):
        return {
            "status": "pending",
            "score_state": state or PENDING_SCORE,
            "reason": "Sleep is not scored yet.",
        }
    if state == UNSCORABLE or score_block is None:
        return {
            "status": "missing",
            "score_state": state or UNSCORABLE,
            "reason": "Sleep could not be scored.",
        }

    stages = score_block.get("stage_summary") if isinstance(score_block.get("stage_summary"), dict) else {}
    deep_h = millis_to_hours(stages.get("total_slow_wave_sleep_time_milli"))
    rem_h = millis_to_hours(stages.get("total_rem_sleep_time_milli"))
    light_h = millis_to_hours(stages.get("total_light_sleep_time_milli"))
    awake_h = millis_to_hours(stages.get("total_awake_time_milli"))
    in_bed_h = millis_to_hours(stages.get("total_in_bed_time_milli"), digits=2)
    total_h = _sum_hours([deep_h, rem_h, light_h])

    block: dict[str, Any] = {
        "status": "ready",
        "score_state": state,
        "assigned_date": assigned_wake_date(record, tz),
        "assignment_rule": "wake_date",
        "sleep_id": _opt_str(record.get("id")),
        "cycle_id": _opt_int(record.get("cycle_id")),
        "nap": bool(record.get("nap")),
        "started_at": iso_offset(record.get("start"), tz),
        "ended_at": iso_offset(record.get("end"), tz),
        "total_hours": total_h,
        "in_bed_hours": in_bed_h,
        "efficiency_percentage": _opt_round(_first_number(score_block, ["sleep_efficiency_percentage"]), 1),
        "performance_percentage": _opt_round(_first_number(score_block, ["sleep_performance_percentage"]), 1),
        "consistency_percentage": _opt_round(_first_number(score_block, ["sleep_consistency_percentage"]), 1),
        "stages": {
            "deep_hours": deep_h,
            "rem_hours": rem_h,
            "light_hours": light_h,
            "awake_hours": awake_h,
        },
    }
    if detail == "full":
        need = score_block.get("sleep_needed") if isinstance(score_block.get("sleep_needed"), dict) else {}
        baseline = millis_to_hours(need.get("baseline_milli"))
        debt = millis_to_hours(need.get("need_from_sleep_debt_milli"))
        strain = millis_to_hours(need.get("need_from_recent_strain_milli"))
        nap_need = millis_to_hours(need.get("need_from_recent_nap_milli"))
        block["stage_summary"] = {
            "total_in_bed_hours": in_bed_h,
            "total_awake_hours": awake_h,
            "total_no_data_hours": millis_to_hours(stages.get("total_no_data_time_milli")),
            "total_light_sleep_hours": light_h,
            "total_slow_wave_sleep_hours": deep_h,
            "total_rem_sleep_hours": rem_h,
            "sleep_cycle_count": _opt_int(_first_number(stages, ["sleep_cycle_count"])),
            "disturbance_count": _opt_int(_first_number(stages, ["disturbance_count"])),
        }
        block["sleep_needed"] = {
            "total_need_hours": _sum_hours([baseline, debt, strain, nap_need]),
            "baseline_hours": baseline,
            "need_from_sleep_debt_hours": debt,
            "need_from_recent_strain_hours": strain,
            "need_from_recent_nap_hours": nap_need,
        }
        block["respiratory_rate"] = _opt_round(_first_number(score_block, ["respiratory_rate"]), 1)
        block["created_at"] = iso_offset(record.get("created_at"), tz)
        block["updated_at"] = iso_offset(record.get("updated_at"), tz)
        block["source_timezone_offset"] = source_timezone_offset(record, tz, instant=record.get("end"))
    return block


# --------------------------------------------------------------------------- #
# day strain (cycle)
# --------------------------------------------------------------------------- #
def normalize_day_strain(
    cycle: dict[str, Any] | None,
    tz: ZoneInfo,
    *,
    target_date: date,
    detail: str = "full",
    surface_only_block: bool = False,
) -> dict[str, Any]:
    if cycle is None:
        return {"status": "missing", "date": target_date.isoformat(), "reason": "No cycle for this day."}

    state = _score_state(cycle)
    score_block = cycle.get("score") if isinstance(cycle.get("score"), dict) else {}
    end_raw = cycle.get("end")
    is_final = isinstance(end_raw, str) and bool(end_raw.strip())

    block: dict[str, Any] = {
        "status": "ready" if state == SCORED else "pending",
        "date": target_date.isoformat(),
        "cycle_id": _opt_int(cycle.get("id")),
        "score_state": state or PENDING_SCORE,
        "score": _opt_round(_first_number(score_block, ["strain"]), 4),
        "is_final": is_final,
        "kilojoules": _opt_round(_first_number(score_block, ["kilojoule"]), 1),
        "average_hr_bpm": _opt_int(_first_number(score_block, ["average_heart_rate"])),
        "max_hr_bpm": _opt_int(_first_number(score_block, ["max_heart_rate"])),
        "updated_at": iso_offset(cycle.get("updated_at"), tz),
    }
    if state != SCORED:
        block["reason"] = "Cycle is not scored yet."
    # `previous_day_strain` omits the heavy drilldown per the spec example.
    if detail == "full" and not surface_only_block:
        block["cycle_start"] = iso_offset(cycle.get("start"), tz)
        block["cycle_end"] = iso_offset(end_raw, tz)
        block["created_at"] = iso_offset(cycle.get("created_at"), tz)
        block["source_timezone_offset"] = source_timezone_offset(cycle, tz, instant=cycle.get("start"))
        # WHOOP v2 cycle does not expose day-level HR zones; state this explicitly.
        block["hr_zones_available"] = False
        block["hr_zones_min"] = None
    return block


# --------------------------------------------------------------------------- #
# workout
# --------------------------------------------------------------------------- #
def normalize_workout(record: dict[str, Any], tz: ZoneInfo, *, detail: str = "full") -> dict[str, Any] | None:
    workout_id = record.get("id", record.get("workout_id"))
    if workout_id is None or not str(workout_id).strip():
        return None

    start_raw = record.get("start")
    end_raw = record.get("end")
    start_dt = _parse_dt(start_raw) if isinstance(start_raw, str) else None
    end_dt = _parse_dt(end_raw) if isinstance(end_raw, str) else None
    anchor = start_dt or end_dt
    if anchor is None:
        return None

    score_block = record.get("score") if isinstance(record.get("score"), dict) else {}
    duration_min = None
    if start_dt is not None and end_dt is not None:
        duration_min = round((end_dt - start_dt).total_seconds() / 60, 1)

    block: dict[str, Any] = {
        "workout_id": str(workout_id).strip(),
        "date": anchor.astimezone(tz).date().isoformat(),
        "sport_name": _resolve_sport_name(record),
        "sport_id": _opt_int(record.get("sport_id")),
        "started_at": iso_offset(start_raw, tz),
        "ended_at": iso_offset(end_raw, tz),
        "duration_min": duration_min,
        "score_state": _score_state(record) or None,
        "strain_score": _opt_round(_first_number(score_block, ["strain"]) or _first_number(record, ["strain"]), 4),
        "average_hr_bpm": _opt_int(_first_number(score_block, ["average_heart_rate"])),
        "max_hr_bpm": _opt_int(_first_number(score_block, ["max_heart_rate"])),
        "kilojoules": _opt_round(_first_number(score_block, ["kilojoule"]), 1),
    }
    if detail == "full":
        zones = _extract_zone_minutes(record, score_block)
        block.update(
            {
                "v1_id": _opt_int(record.get("v1_id")),
                "created_at": iso_offset(record.get("created_at"), tz),
                "updated_at": iso_offset(record.get("updated_at"), tz),
                "source_timezone_offset": source_timezone_offset(record, tz, instant=start_raw),
                "percent_recorded": _percent(_first_number(score_block, ["percent_recorded"])),
                "distance_meter": _opt_round(_first_number(score_block, ["distance_meter"]), 2),
                "altitude_gain_meter": _opt_round(_first_number(score_block, ["altitude_gain_meter"]), 2),
                "altitude_change_meter": _opt_round(_first_number(score_block, ["altitude_change_meter"]), 2),
                "zone_durations_min": zones,
            }
        )
    return block


# --------------------------------------------------------------------------- #
# body
# --------------------------------------------------------------------------- #
def normalize_body(
    payload: dict[str, Any] | None,
    tz: ZoneInfo,
    *,
    source: str = "whoop",
    measured_at: str | None = None,
    detail: str = "full",
) -> dict[str, Any]:
    if not payload:
        return {
            "status": "missing",
            "measured_at": None,
            "weight_kg": None,
            "height_m": None,
            "max_heart_rate": None,
        }

    weight = _first_number(payload, ["weight_kilogram", "weight_kg", "weight"])
    height = _first_number(payload, ["height_meter", "height_m", "height"])
    max_hr = _first_number(payload, ["max_heart_rate", "maximum_heart_rate"])

    if weight is None and height is None and max_hr is None:
        return {
            "status": "missing",
            "measured_at": None,
            "weight_kg": None,
            "height_m": None,
            "max_heart_rate": None,
        }

    measured = measured_at or payload.get("measured_at")
    block: dict[str, Any] = {
        "status": "ready",
        "measured_at": iso_offset(measured, tz) if isinstance(measured, str) else None,
        "weight_kg": round(weight, 4) if weight is not None else None,
        "height_m": round(height, 4) if height is not None else None,
        "max_heart_rate": _opt_int(max_hr),
    }
    if detail == "full":
        block["source"] = source
    return block


# --------------------------------------------------------------------------- #
# freshness
# --------------------------------------------------------------------------- #
def freshness_entry(
    *,
    updated_at: str | None,
    source: str,
    now: datetime,
    tz: ZoneInfo,
    stale_after_seconds: int | None = None,
    full_day_fresh: bool = False,
) -> dict[str, Any]:
    """Build one freshness record (spec section 6.3).

    ``full_day_fresh`` marks blocks (recovery/sleep) that stay fresh the whole day
    once ready. ``stale_after_seconds`` drives the heartbeat fresh/stale window for
    day_strain/workouts/body.
    """
    if updated_at is None:
        return {"status": "missing", "updated_at": None, "source": source}

    parsed = _parse_dt(updated_at)
    if parsed is None:
        return {"status": "unknown", "updated_at": updated_at, "source": source}

    rendered = parsed.astimezone(tz).replace(microsecond=0).isoformat()
    if full_day_fresh:
        status = "fresh" if parsed.astimezone(tz).date() == now.astimezone(tz).date() else "stale"
        return {"status": status, "updated_at": rendered, "source": source}

    if stale_after_seconds is None:
        return {"status": "fresh", "updated_at": rendered, "source": source}

    age = (now - parsed).total_seconds()
    status = "fresh" if age <= stale_after_seconds else "stale"
    return {"status": status, "updated_at": rendered, "source": source}


# --------------------------------------------------------------------------- #
# tiny internal helpers
# --------------------------------------------------------------------------- #
def _resolve_sport_name(record: dict[str, Any]) -> str:
    for key in ("sport_name", "sport", "sport_type"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return "unknown"


def _extract_zone_minutes(record: dict[str, Any], score_block: dict[str, Any]) -> dict[str, float] | None:
    candidates = [
        score_block.get("zone_durations"),
        score_block.get("zone_duration"),
        record.get("zone_durations"),
        record.get("zone_duration"),
    ]
    source: dict[str, Any] | None = next((c for c in candidates if isinstance(c, dict)), None)
    if source is None:
        return None

    mapping = [
        ("z0", ["zone_zero_milli", "zone_0_milli"]),
        ("z1", ["zone_one_milli", "zone_1_milli"]),
        ("z2", ["zone_two_milli", "zone_2_milli"]),
        ("z3", ["zone_three_milli", "zone_3_milli"]),
        ("z4", ["zone_four_milli", "zone_4_milli"]),
        ("z5", ["zone_five_milli", "zone_5_milli"]),
    ]
    out: dict[str, float] = {}
    for target_key, source_keys in mapping:
        value = _first_number(source, source_keys)
        if value is not None:
            out[target_key] = round(value / 60_000, 1)
    return out or None


def _sum_hours(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present), 2)


def _opt_int(value: Any) -> int | None:
    number = _first_number({"v": value}, ["v"])
    return int(round(number)) if number is not None else None


def _opt_round(value: Any, digits: int) -> float | None:
    number = _first_number({"v": value}, ["v"])
    return round(number, digits) if number is not None else None


def _percent(value: Any) -> float | None:
    """WHOOP v2 returns workout percent_recorded as a 0..1 fraction; the coach
    contract and the agent's '< 90' rule expect a 0..100 percentage (a fully
    recorded workout is 100.0, not 1.0). Values already on a 0..100 scale pass
    through unchanged."""
    number = _first_number({"v": value}, ["v"])
    if number is None:
        return None
    return round(number * 100, 1) if number <= 1.0 else round(number, 1)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
