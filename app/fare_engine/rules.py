"""요금 규칙 설정 모델. 값은 config/fare_rules.yaml에서 온다 (로딩은 app.config)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class FareRules(BaseModel):
    version: str
    base_fare: int
    base_distance_m: int
    distance_unit_m: int
    time_unit_s: int
    unit_price: int
    night_tiers: list[tuple[str, str, float]]
    out_of_city_rate: float
    combined_cap: float
    detour_tolerance: float
    missing_time_factor: float
    rounding_slack_won: int
    clear_ratio: float
    toll_slack_won: int
    daytime_guard_minutes: int
    out_of_city_exempt_tags: list[str]
    out_of_city_exempt_severity: Literal["info", "check", "clear"]
    low_confidence_threshold: float
