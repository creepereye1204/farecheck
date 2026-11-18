"""공용 데이터 모델. ARCHITECTURE.md '핵심 데이터 모델'."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReceiptFields(BaseModel):
    total_fare: int | None = None
    meter_fare: int | None = None
    toll_fee: int | None = None
    ride_start: datetime | None = None
    ride_end: datetime | None = None
    night_surcharge: bool | None = None
    out_of_city_surcharge: bool | None = None
    distance_km: float | None = None
    vehicle_no: str | None = None
    taxi_type: Literal["medium", "deluxe", "platform", "unknown"] = "unknown"
    confidence: dict[str, float] = Field(default_factory=dict)


class Place(BaseModel):
    name: str
    address: str = ""
    region: str = ""
    x: float
    y: float
    tags: list[str] = Field(default_factory=list)


class TripInput(BaseModel):
    receipt: ReceiptFields
    origin: Place
    destination: Place
    meter_used: bool = True
    hailed_via_app: bool = False
    user_lang: str = "en"


class Route(BaseModel):
    distance_m: int
    duration_s: int
    toll_won: int
    kakao_taxi_estimate_won: int | None = None


class Reason(BaseModel):
    code: str
    severity: Literal["info", "check", "clear"]
    params: dict = Field(default_factory=dict)


Grade = Literal["NORMAL", "CHECK_NEEDED", "CLEAR_EXCESS", "UNSUPPORTED"]


class Verdict(BaseModel):
    grade: Grade
    upper_bound_won: int
    charged_won: int
    ratio: float
    reasons: list[Reason]
    rule_version: str
