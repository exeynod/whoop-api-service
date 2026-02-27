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


class YesterdayReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    date: str
    strain: StrainResponse
    sleep: SleepResponse
    cached: bool = False


class WeekPeriodResponse(BaseModel):
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
    period: WeekPeriodResponse
    days: list[WeekDayResponse]
