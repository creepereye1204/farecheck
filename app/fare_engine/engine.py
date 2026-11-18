"""요금 상한 계산과 등급 판정. 명세: docs/fare-rules.md

순수 함수만 둔다. 외부 호출·IO·geo/extractor import 금지.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.fare_engine.rules import FareRules
from app.models import Place, ReceiptFields, Reason, Route, Verdict

KST = timezone(timedelta(hours=9))
_SEVERITY_ORDER = {"info": 0, "check": 1, "clear": 2}
_GRADE_BY_SEVERITY = {"info": "NORMAL", "check": "CHECK_NEEDED", "clear": "CLEAR_EXCESS"}


# ---------- 보조 함수 ----------

def is_seoul(region: str | None) -> bool:
    """카카오 address_name 첫 토큰은 '서울'이고 문서 예시는 '서울특별시'다. 둘 다 받는다."""
    return bool(region) and region.strip().startswith("서울")


def round10(value: Decimal) -> int:
    return int((value / 10).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 10)


def _to_kst(dt: datetime) -> datetime:
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def ride_seconds(receipt: ReceiptFields) -> float | None:
    if receipt.ride_start is None or receipt.ride_end is None:
        return None
    return (_to_kst(receipt.ride_end) - _to_kst(receipt.ride_start)).total_seconds()


def night_rate(start: datetime | None, end: datetime | None, rules: FareRules) -> float:
    """승차~하차 구간과 겹치는 심야 시간대 중 가장 높은 할증률.
    시각이 없으면 가장 높은 할증률을 가정한다 (상한이 커지는 쪽)."""
    max_rate = max((t[2] for t in rules.night_tiers), default=0.0)
    if start is None or end is None:
        return max_rate
    s, e = _to_kst(start), _to_kst(end)
    best = 0.0
    day = s.date() - timedelta(days=1)
    while day <= e.date():
        for t_start, t_end, rate in rules.night_tiers:
            ts = datetime.combine(day, _hm(t_start), KST)
            te = datetime.combine(day, _hm(t_end), KST)
            if te <= ts:  # 자정을 넘는 시간대
                te += timedelta(days=1)
            if s < te and e > ts:
                best = max(best, rate)
        day += timedelta(days=1)
    return best


def within_daytime(start: datetime, end: datetime, rules: FareRules) -> bool:
    """승차~하차가 전부 주간(심야 시간대 경계에서 guard 분만큼 안쪽) 안에 있는가.
    기본 설정에서 04:05~21:55."""
    s, e = _to_kst(start), _to_kst(end)
    if s.date() != e.date() or e < s:
        return False
    guard = timedelta(minutes=rules.daytime_guard_minutes)
    # 주간 = 심야 시간대 어디에도 속하지 않는 구간. 기본 설정에서는 04:00~22:00.
    day_start = max(_hm(t[1]) for t in rules.night_tiers if _hm(t[1]) < time(12))
    day_end = min(_hm(t[0]) for t in rules.night_tiers if _hm(t[0]) >= time(12))
    lo = datetime.combine(s.date(), day_start, KST) + guard
    hi = datetime.combine(s.date(), day_end, KST) - guard
    return lo <= s and e <= hi


def _unsupported(code: str, rules: FareRules, charged: int = 0, **params) -> Verdict:
    return Verdict(
        grade="UNSUPPORTED", upper_bound_won=0, charged_won=charged, ratio=0.0,
        reasons=[Reason(code=code, severity="info", params=params)],
        rule_version=rules.version,
    )


# ---------- 상한 계산 ----------

def compute_upper_bound(
    route: Route, receipt: ReceiptFields, origin: Place, destination: Place, rules: FareRules
) -> dict:
    """docs/fare-rules.md §4. 검산용 숫자를 전부 dict로 돌려준다."""
    d_max = route.distance_m * rules.detour_tolerance
    measured = ride_seconds(receipt)
    if measured is not None and measured >= 0:
        T = measured
        time_source = "receipt"
    else:
        T = route.duration_s * rules.missing_time_factor
        time_source = "route_x_factor"

    dist_units = math.ceil(max(0.0, d_max - rules.base_distance_m) / rules.distance_unit_m)
    time_units = math.ceil(T / rules.time_unit_s)
    day_meter = rules.base_fare + dist_units * rules.unit_price + time_units * rules.unit_price

    n_rate = night_rate(receipt.ride_start, receipt.ride_end, rules)
    exempt = bool(set(destination.tags) & set(rules.out_of_city_exempt_tags))
    o_rate = rules.out_of_city_rate if (not is_seoul(destination.region) and not exempt) else 0.0

    surcharge = min(Decimal(str(n_rate)) + Decimal(str(o_rate)), Decimal(str(rules.combined_cap)))
    multiplier = Decimal(1) + surcharge
    meter_upper = round10(Decimal(day_meter) * multiplier + rules.rounding_slack_won)

    return {
        "distance_m": route.distance_m,
        "detour_tolerance": rules.detour_tolerance,
        "d_max": round(d_max),
        "T": round(T),
        "time_source": time_source,
        "distance_fare": dist_units * rules.unit_price,
        "time_fare": time_units * rules.unit_price,
        "day_meter": day_meter,
        "night_rate": n_rate,
        "out_of_city_rate": o_rate,
        "multiplier": float(multiplier),
        "meter_upper": meter_upper,
        "rule_version": rules.version,
    }


def charged_meter(receipt: ReceiptFields) -> int | None:
    """docs/fare-rules.md §5."""
    if receipt.meter_fare is not None:
        return receipt.meter_fare
    if receipt.total_fare is not None:
        return receipt.total_fare - (receipt.toll_fee or 0)
    return None


# ---------- 판정 ----------

def judge(
    receipt: ReceiptFields,
    route: Route | None,
    origin: Place,
    destination: Place,
    rules: FareRules,
    meter_used: bool = True,
    hailed_via_app: bool = False,
) -> Verdict:
    """docs/fare-rules.md §6. 금액 비율과 규칙 근거 중 더 높은 severity로 등급을 정한다."""
    if receipt.taxi_type != "medium":
        return _unsupported("TAXI_TYPE_UNSUPPORTED", rules, taxi_type=receipt.taxi_type)
    if route is None:
        return _unsupported("ROUTE_UNAVAILABLE", rules)
    charged = charged_meter(receipt)
    if charged is None:
        return _unsupported("AMOUNT_MISSING", rules)

    calc = compute_upper_bound(route, receipt, origin, destination, rules)
    upper = calc["meter_upper"]
    ratio = round(charged / upper, 3)
    calc_params = {**calc, "charged_meter": charged, "ratio": ratio}

    reasons: list[Reason] = []

    # (a) 금액 비율
    if ratio <= 1.0:
        reasons.append(Reason(code="WITHIN_UPPER_BOUND", severity="info", params=calc_params))
    elif ratio <= rules.clear_ratio:
        reasons.append(Reason(code="OVER_UPPER_BOUND", severity="check", params=calc_params))
    else:
        reasons.append(Reason(code="FAR_OVER_UPPER_BOUND", severity="clear",
                              params={**calc_params, "clear_ratio": rules.clear_ratio}))

    # (b) 규칙 근거
    if (receipt.night_surcharge and receipt.ride_start and receipt.ride_end
            and within_daytime(receipt.ride_start, receipt.ride_end, rules)):
        reasons.append(Reason(code="NIGHT_SURCHARGE_IN_DAYTIME", severity="clear", params={
            "ride_start": _to_kst(receipt.ride_start).strftime("%H:%M"),
            "ride_end": _to_kst(receipt.ride_end).strftime("%H:%M"),
        }))

    if receipt.out_of_city_surcharge:
        if is_seoul(origin.region) and is_seoul(destination.region):
            reasons.append(Reason(code="OUT_OF_CITY_INSIDE_SEOUL", severity="clear", params={
                "origin_region": origin.region, "dest_region": destination.region,
            }))
        elif set(destination.tags) & set(rules.out_of_city_exempt_tags):
            reasons.append(Reason(code="OUT_OF_CITY_TO_EXEMPT",
                                  severity=rules.out_of_city_exempt_severity,
                                  params={"dest_tags": sorted(destination.tags)}))

    if receipt.toll_fee is not None and receipt.toll_fee > route.toll_won + rules.toll_slack_won:
        reasons.append(Reason(code="TOLL_MISMATCH", severity="check", params={
            "receipt_toll": receipt.toll_fee, "route_toll": route.toll_won,
            "toll_slack": rules.toll_slack_won,
        }))

    if not meter_used:
        reasons.append(Reason(code="METER_NOT_USED", severity="check", params={}))

    if hailed_via_app:
        reasons.append(Reason(code="APP_CALL_FEE_NOT_MODELED", severity="info", params={}))

    top = max(reasons, key=lambda r: _SEVERITY_ORDER[r.severity]).severity
    return Verdict(
        grade=_GRADE_BY_SEVERITY[top],
        upper_bound_won=upper,
        charged_won=charged,
        ratio=ratio,
        reasons=reasons,
        rule_version=rules.version,
    )


def calc_params(verdict: Verdict) -> dict:
    """판정 근거 중 상한 계산 숫자를 담은 params (없으면 빈 dict)."""
    for r in verdict.reasons:
        if "meter_upper" in r.params:
            return r.params
    return {}
