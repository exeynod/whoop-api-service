"""Pure aggregation for the coach week / training / sleep / recovery contexts.

Operates on the neutral bundle produced by ``WhoopClient.fetch_coach_range``
(``period``, ``rows``, ``workouts``, ``nap_count``, ``errors``). Facts only — no
ACWR verdicts or readiness flags; ``strain_ratio_7d_vs_prev_7d`` is a plain ratio
the agent interprets. Documented thresholds (so behavior is deterministic):

  - high strain day:  strain_score >= 14
  - low strain day:   strain_score is not None and < 6
  - rest day:         workout_count == 0
  - late bedtime:     local bedtime time-of-day in [00:30, 06:00] (i.e. past midnight)
  - strength sport:   weightlifting / functional-fitness / strength / powerlifting
  - missing days are skipped from averages (not zero-filled).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.whoop_client import WhoopClient

_parse_dt = WhoopClient._parse_datetime

HIGH_STRAIN = 14.0
LOW_STRAIN = 6.0
_STRENGTH_SPORTS = {"weightlifting", "functional-fitness", "functional_fitness", "strength", "powerlifting"}


def _avg(values: list[Any], digits: int = 1) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), digits)


def _column(rows: list[dict[str, Any]], key: str) -> list[Any]:
    return [r.get(key) for r in rows]


def _is_late_bedtime(started_at: str | None) -> bool:
    parsed = _parse_dt(started_at) if isinstance(started_at, str) else None
    if parsed is None:
        return False
    minutes = parsed.hour * 60 + parsed.minute
    return 30 <= minutes <= 360  # 00:30 .. 06:00 local


def _classify_workouts(workouts: list[dict[str, Any]]) -> dict[str, int]:
    volleyball = strength = other = 0
    for workout in workouts:
        sport = (workout.get("sport_name") or "").lower()
        if sport == "volleyball":
            volleyball += 1
        elif sport in _STRENGTH_SPORTS:
            strength += 1
        else:
            other += 1
    return {
        "workout_count": len(workouts),
        "volleyball_count": volleyball,
        "strength_count": strength,
        "other_count": other,
    }


def _strain_sum(rows: list[dict[str, Any]]) -> float:
    return round(sum(float(r["strain_score"]) for r in rows if r.get("strain_score") is not None), 1)


# --------------------------------------------------------------------------- #
def build_week(bundle: dict[str, Any], *, include_days: bool, include_workouts: bool) -> dict[str, Any]:
    rows = bundle["rows"]
    workouts = bundle["workouts"]
    counts = _classify_workouts(workouts)
    strain_scores = [r["strain_score"] for r in rows if r.get("strain_score") is not None]

    summary = {
        "avg_recovery_score": _avg(_column(rows, "recovery_score")),
        "avg_hrv_ms": _avg(_column(rows, "hrv_ms")),
        "avg_resting_hr_bpm": _avg(_column(rows, "resting_hr_bpm")),
        "avg_sleep_hours": _avg(_column(rows, "sleep_total_hours"), 2),
        "avg_deep_hours": _avg(_column(rows, "sleep_deep_hours"), 2),
        "avg_rem_hours": _avg(_column(rows, "sleep_rem_hours"), 2),
        "avg_sleep_performance_percentage": _avg(_column(rows, "sleep_performance_percentage")),
        "avg_sleep_efficiency_percentage": _avg(_column(rows, "sleep_efficiency_percentage")),
        "avg_sleep_consistency_percentage": _avg(_column(rows, "sleep_consistency_percentage")),
        "avg_respiratory_rate": _avg(_column(rows, "sleep_respiratory_rate")),
        "total_strain": round(sum(float(s) for s in strain_scores), 1),
        "avg_daily_strain": _avg(strain_scores),
        "max_daily_strain": round(max(strain_scores), 1) if strain_scores else None,
        "workout_count": counts["workout_count"],
        "volleyball_count": counts["volleyball_count"],
        "strength_count": counts["strength_count"],
        "rest_day_count": sum(1 for r in rows if r.get("workout_count", 0) == 0),
    }

    result: dict[str, Any] = {
        "status": "partial" if bundle.get("errors") else "ready",
        "period": bundle["period"],
        "summary": summary,
        "errors": bundle.get("errors", []),
    }
    if include_days:
        result["days"] = [_week_day_row(r) for r in rows]
    if include_workouts:
        result["workouts"] = workouts
    return result


def _week_day_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": row["date"],
        "recovery_score": row["recovery_score"],
        "recovery_zone": row["recovery_zone"],
        "hrv_ms": row["hrv_ms"],
        "resting_hr_bpm": row["resting_hr_bpm"],
        "sleep_hours": row["sleep_total_hours"],
        "deep_hours": row["sleep_deep_hours"],
        "rem_hours": row["sleep_rem_hours"],
        "sleep_performance_percentage": row["sleep_performance_percentage"],
        "sleep_efficiency_percentage": row["sleep_efficiency_percentage"],
        "sleep_consistency_percentage": row["sleep_consistency_percentage"],
        "respiratory_rate": row["sleep_respiratory_rate"],
        "strain_score": row["strain_score"],
        "workout_count": row["workout_count"],
        "workout_sports": row["workout_sports"],
    }


def build_training_context(
    bundle: dict[str, Any], *, include_daily: bool, include_workouts: bool
) -> dict[str, Any]:
    rows = bundle["rows"]
    workouts = bundle["workouts"]
    counts = _classify_workouts(workouts)

    last_7 = rows[-7:]
    prev_7 = rows[-14:-7]
    last_7d = _strain_sum(last_7)
    prev_7d = _strain_sum(prev_7)
    ratio = round(last_7d / prev_7d, 2) if prev_7d > 0 else None  # div-by-zero -> null

    load_summary = {
        "last_7d_strain": last_7d,
        "prev_7d_strain": prev_7d,
        "strain_ratio_7d_vs_prev_7d": ratio,
        "high_strain_days": sum(
            1 for r in rows if r.get("strain_score") is not None and r["strain_score"] >= HIGH_STRAIN
        ),
        "low_strain_days": sum(
            1 for r in rows if r.get("strain_score") is not None and r["strain_score"] < LOW_STRAIN
        ),
        "rest_days": sum(1 for r in rows if r.get("workout_count", 0) == 0),
        "workout_count": counts["workout_count"],
        "volleyball_count": counts["volleyball_count"],
        "strength_count": counts["strength_count"],
        "other_count": counts["other_count"],
    }

    result: dict[str, Any] = {
        "status": "partial" if bundle.get("errors") else "ready",
        "period": bundle["period"],
        "load_summary": load_summary,
        "errors": bundle.get("errors", []),
    }
    if include_daily:
        result["daily_load"] = [
            {
                "date": r["date"],
                "strain_score": r["strain_score"],
                "workout_count": r["workout_count"],
                "sports": r["workout_sports"],
            }
            for r in rows
        ]
    if include_workouts:
        result["workouts"] = workouts
    return result


def build_sleep_context(bundle: dict[str, Any]) -> dict[str, Any]:
    rows = bundle["rows"]
    summary = {
        "avg_total_hours": _avg(_column(rows, "sleep_total_hours"), 2),
        "avg_deep_hours": _avg(_column(rows, "sleep_deep_hours"), 2),
        "avg_rem_hours": _avg(_column(rows, "sleep_rem_hours"), 2),
        "avg_efficiency_percentage": _avg(_column(rows, "sleep_efficiency_percentage")),
        "avg_performance_percentage": _avg(_column(rows, "sleep_performance_percentage")),
        "avg_consistency_percentage": _avg(_column(rows, "sleep_consistency_percentage")),
        "avg_resp_rate": _avg(_column(rows, "sleep_respiratory_rate")),
        "avg_disturbance_count": _avg(_column(rows, "sleep_disturbance_count")),
        "late_bedtime_count": sum(1 for r in rows if _is_late_bedtime(r.get("sleep_started_at"))),
        "nap_count": bundle.get("nap_count", 0),
    }
    return {
        "status": "partial" if bundle.get("errors") else "ready",
        "period": bundle["period"],
        "summary": summary,
        "days": [
            {
                "date": r["date"],
                "started_at": r["sleep_started_at"],
                "ended_at": r["sleep_ended_at"],
                "total_hours": r["sleep_total_hours"],
                "deep_hours": r["sleep_deep_hours"],
                "rem_hours": r["sleep_rem_hours"],
                "efficiency_percentage": r["sleep_efficiency_percentage"],
                "performance_percentage": r["sleep_performance_percentage"],
                "consistency_percentage": r["sleep_consistency_percentage"],
                "respiratory_rate": r["sleep_respiratory_rate"],
                "disturbance_count": r["sleep_disturbance_count"],
            }
            for r in rows
        ],
        "errors": bundle.get("errors", []),
    }


def build_recovery_context(bundle: dict[str, Any]) -> dict[str, Any]:
    rows = bundle["rows"]
    zones = [r.get("recovery_zone") for r in rows if r.get("recovery_zone")]
    summary = {
        "avg_recovery_score": _avg(_column(rows, "recovery_score")),
        "green_days": zones.count("green"),
        "yellow_days": zones.count("yellow"),
        "red_days": zones.count("red"),
        "avg_hrv_ms": _avg(_column(rows, "hrv_ms")),
        "avg_resting_hr_bpm": _avg(_column(rows, "resting_hr_bpm")),
        "avg_spo2_percentage": _avg(_column(rows, "spo2_percentage")),
        "avg_skin_temp_celsius": _avg(_column(rows, "skin_temp_celsius")),
    }
    return {
        "status": "partial" if bundle.get("errors") else "ready",
        "period": bundle["period"],
        "summary": summary,
        "days": [
            {
                "date": r["date"],
                "recovery_score": r["recovery_score"],
                "recovery_zone": r["recovery_zone"],
                "hrv_ms": r["hrv_ms"],
                "resting_hr_bpm": r["resting_hr_bpm"],
                "spo2_percentage": r["spo2_percentage"],
                "skin_temp_celsius": r["skin_temp_celsius"],
                "score_state": r["recovery_score_state"],
            }
            for r in rows
        ],
        "errors": bundle.get("errors", []),
    }
