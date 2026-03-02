from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    whoop_reachable: bool
    tokens_valid: bool


class AuthCallbackResponse(BaseModel):
    status: Literal["authorized"] = "authorized"
    message: str = "Tokens saved. You can close this tab."


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    reason: str
    detail: str | None = None


class PendingResponse(BaseModel):
    status: Literal["pending"] = "pending"
    reason: str


class RecoveryReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    date: str
    recovery_score: int = Field(ge=0, le=100)
    recovery_zone: Literal["green", "yellow", "red"]
    hrv_ms: int | None = None
    resting_hr_bpm: int | None = None
    spo2_percentage: float | None = None
    skin_temp_celsius: float | None = None
    user_calibrating: bool | None = None
    timezone_offset: str
    cached: bool = False


class StrainResponse(BaseModel):
    score: float
    kilojoules: int
    avg_hr_bpm: int
    max_hr_bpm: int


class SleepStagesResponse(BaseModel):
    deep_hours: float
    rem_hours: float
    light_hours: float
    awake_hours: float


class SleepResponse(BaseModel):
    score: int
    total_hours: float
    performance_percent: int
    respiratory_rate: float
    stages: SleepStagesResponse
    disturbance_count: int | None = None
    sleep_cycle_count: int | None = None
    consistency_percentage: int | None = None
    efficiency_percentage: int | None = None
    sleep_needed_hours: float | None = None
    sleep_debt_hours: float | None = None
    strain_related_need_hours: float | None = None


class YesterdayReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    date: str
    strain: StrainResponse
    sleep: SleepResponse
    timezone_offset: str
    cached: bool = False


class PeriodResponse(BaseModel):
    from_: str = Field(alias="from")
    to: str


class WeekDayReadyResponse(BaseModel):
    date: str
    status: Literal["ready"] = "ready"
    recovery_score: int = Field(ge=0, le=100)
    recovery_zone: Literal["green", "yellow", "red"]
    hrv_ms: int
    resting_hr_bpm: int
    strain_score: float
    sleep_score: int
    sleep_hours: float


class WeekDayMissingResponse(BaseModel):
    date: str
    status: Literal["missing"] = "missing"


WeekDayResponse = Union[WeekDayReadyResponse, WeekDayMissingResponse]


class WeekResponse(BaseModel):
    period: PeriodResponse
    days: list[WeekDayResponse]


class CycleDayResponse(BaseModel):
    date: str
    cycle_id: int | None = None
    recovery_score: int | None = Field(default=None, ge=0, le=100)
    recovery_zone: Literal["green", "yellow", "red"] | None = None
    hrv_ms: int | None = None
    resting_hr_bpm: int | None = None
    spo2_percentage: float | None = None
    skin_temp_celsius: float | None = None
    strain_score: float | None = None
    kilojoules: int | None = None
    sleep_score: int | None = None
    sleep_hours: float | None = None
    sleep_disturbance_count: int | None = None
    sleep_consistency_percentage: int | None = None
    sleep_efficiency_percentage: int | None = None


class CyclesResponse(BaseModel):
    status: Literal["ready"] = "ready"
    period: PeriodResponse
    days: list[CycleDayResponse]
    next_token: str | None = None
    cached: bool = False
    timezone_offset: str


class ZoneDurationsResponse(BaseModel):
    zone_zero_milli: int | None = None
    zone_one_milli: int | None = None
    zone_two_milli: int | None = None
    zone_three_milli: int | None = None
    zone_four_milli: int | None = None
    zone_five_milli: int | None = None


class WorkoutItemResponse(BaseModel):
    workout_id: str
    date: str
    sport_name: str
    start: str | None = None
    end: str | None = None
    strain_score: float | None = None
    kilojoules: int | None = None
    average_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    distance_meter: float | None = None
    altitude_gain_meter: float | None = None
    percent_recorded: int | None = None
    zone_durations: ZoneDurationsResponse | None = None


class WorkoutsResponse(BaseModel):
    status: Literal["ready"] = "ready"
    period: PeriodResponse
    workouts: list[WorkoutItemResponse]
    next_token: str | None = None
    cached: bool = False
    timezone_offset: str


class BodyMeasurementReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    measured_at: str
    height_meter: float | None = None
    weight_kilogram: float | None = None
    max_heart_rate: int | None = None
    timezone_offset: str
    cached: bool = False


class BodyMeasurementHistoryItemResponse(BaseModel):
    date: str
    measured_at: str
    height_meter: float | None = None
    weight_kilogram: float | None = None
    max_heart_rate: int | None = None


class BodyMeasurementHistoryReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    period: PeriodResponse
    measurements: list[BodyMeasurementHistoryItemResponse]
    next_token: str | None = None
    cached: bool = True
    timezone_offset: str
