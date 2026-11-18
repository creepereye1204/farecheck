"""docs/fare-rules.md §8 예시를 그대로 옮긴 테스트 + 경계 케이스."""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import load_fare_rules
from app.fare_engine import compute_upper_bound, judge, night_rate
from app.models import Place, ReceiptFields, Route

KST = timezone(timedelta(hours=9))
RULES = load_fare_rules()

SEOUL_A = Place(name="출발", region="서울특별시", x=126.98, y=37.56)
SEOUL_B = Place(name="도착", region="서울특별시", x=127.0, y=37.5)
ROUTE_10K = Route(distance_m=10_000, duration_s=1_500, toll_won=0)


def at(hh, mm, minutes=30, day=18):
    s = datetime(2026, 11, day, hh, mm, tzinfo=KST)
    return s, s + timedelta(minutes=minutes)


def receipt(fare, hh=14, mm=0, minutes=30, **kw):
    s, e = at(hh, mm, minutes)
    return ReceiptFields(total_fare=fare, ride_start=s, ride_end=e, taxi_type="medium", **kw)


def codes(v):
    return [r.code for r in v.reasons]


def test_common_day_meter():
    calc = compute_upper_bound(ROUTE_10K, receipt(17000), SEOUL_A, SEOUL_B, RULES)
    assert calc["d_max"] == 12_000
    assert calc["distance_fare"] == 8_000
    assert calc["time_fare"] == 6_000
    assert calc["day_meter"] == 18_800


@pytest.mark.parametrize("case,rc,kw,upper,ratio,grade,code", [
    ("E1", receipt(17000), {}, 19000, 0.895, "NORMAL", "WITHIN_UPPER_BOUND"),
    ("E2", receipt(24000), {}, 19000, 1.263, "CHECK_NEEDED", "OVER_UPPER_BOUND"),
    ("E3", receipt(35000), {}, 19000, 1.842, "CLEAR_EXCESS", "FAR_OVER_UPPER_BOUND"),
    ("E4", receipt(30000, 23, 30), {}, 26520, 1.131, "CHECK_NEEDED", "OVER_UPPER_BOUND"),
    ("E5", receipt(15000, night_surcharge=True), {}, 19000, 0.789, "CLEAR_EXCESS",
     "NIGHT_SURCHARGE_IN_DAYTIME"),
    ("E6", receipt(17000, out_of_city_surcharge=True), {}, 19000, 0.895, "CLEAR_EXCESS",
     "OUT_OF_CITY_INSIDE_SEOUL"),
    ("E7", receipt(17000), {"meter_used": False}, 19000, 0.895, "CHECK_NEEDED", "METER_NOT_USED"),
])
def test_examples(case, rc, kw, upper, ratio, grade, code):
    v = judge(rc, ROUTE_10K, SEOUL_A, SEOUL_B, RULES, **kw)
    assert v.upper_bound_won == upper, case
    assert v.ratio == pytest.approx(ratio, abs=0.001), case
    assert v.grade == grade, case
    assert code in codes(v), case
    assert v.rule_version == "seoul-medium-2023-02"


def test_e8_myeongdong_to_gimpo():
    route = Route(distance_m=20_000, duration_s=2_100, toll_won=0)
    gmp = Place(name="김포국제공항", region="서울특별시", x=126.80, y=37.56, tags=["GMP_AIRPORT"])
    v = judge(receipt(480_000, minutes=40), route, SEOUL_A, gmp, RULES)
    p = v.reasons[0].params
    assert (p["d_max"], p["distance_fare"], p["time_fare"], p["day_meter"]) == (24_000, 17_100, 8_000, 29_900)
    assert v.upper_bound_won == 30_100
    assert v.ratio == pytest.approx(15.947, abs=0.001)
    assert v.grade == "CLEAR_EXCESS"
    assert codes(v) == ["FAR_OVER_UPPER_BOUND"]


def test_params_have_all_audit_numbers():
    v = judge(receipt(17000), ROUTE_10K, SEOUL_A, SEOUL_B, RULES)
    for k in ["d_max", "T", "day_meter", "multiplier", "meter_upper", "charged_meter", "ratio", "rule_version"]:
        assert k in v.reasons[0].params


# ---------- 경계·예외 ----------

def test_missing_times_use_factor_and_max_night():
    r = ReceiptFields(total_fare=17000, taxi_type="medium")
    calc = compute_upper_bound(ROUTE_10K, r, SEOUL_A, SEOUL_B, RULES)
    assert calc["T"] == 3000  # 1500 × 2.0
    assert calc["night_rate"] == 0.4


def test_night_rate_tiers():
    assert night_rate(*at(21, 50, 20), RULES) == 0.2   # 21:50~22:10
    assert night_rate(*at(22, 50, 20), RULES) == 0.4   # 22:50~23:10
    assert night_rate(*at(1, 50, 20), RULES) == 0.4    # 01:50~02:10
    assert night_rate(*at(3, 0, 30), RULES) == 0.2
    assert night_rate(*at(4, 0, 30), RULES) == 0.0
    assert night_rate(*at(21, 0, 60), RULES) == 0.0    # 22:00 정각 하차는 겹치지 않음


def test_night_surcharge_near_boundary_not_flagged():
    # 21:30~21:58 하차: 21:55 가드 밖 → 규칙 위반으로 보지 않음
    v = judge(receipt(15000, 21, 30, 28, night_surcharge=True), ROUTE_10K, SEOUL_A, SEOUL_B, RULES)
    assert "NIGHT_SURCHARGE_IN_DAYTIME" not in codes(v)


def test_night_surcharge_at_night_ok():
    v = judge(receipt(20000, 23, 10, night_surcharge=True), ROUTE_10K, SEOUL_A, SEOUL_B, RULES)
    assert "NIGHT_SURCHARGE_IN_DAYTIME" not in codes(v)
    assert v.grade == "NORMAL"


def test_out_of_city_destination_raises_upper():
    incheon = Place(name="송도", region="인천광역시", x=126.6, y=37.4)
    v = judge(receipt(17000), ROUTE_10K, SEOUL_A, incheon, RULES)
    assert v.reasons[0].params["multiplier"] == 1.2
    assert v.upper_bound_won == 22_760  # round10(18800×1.2+200)


def test_out_of_city_exempt_icn():
    icn = Place(name="인천국제공항", region="인천광역시", x=126.45, y=37.45, tags=["ICN_AIRPORT"])
    v = judge(receipt(17000, out_of_city_surcharge=True), ROUTE_10K, SEOUL_A, icn, RULES)
    assert v.reasons[0].params["multiplier"] == 1.0
    assert "OUT_OF_CITY_TO_EXEMPT" in codes(v)
    assert v.grade == "CHECK_NEEDED"


def test_combined_cap():
    rules = RULES.model_copy(update={"combined_cap": 0.4})
    incheon = Place(name="송도", region="인천광역시", x=126.6, y=37.4)
    v = judge(receipt(17000, 23, 30), ROUTE_10K, SEOUL_A, incheon, rules)
    assert v.reasons[0].params["multiplier"] == 1.4
    v = judge(receipt(17000, 23, 30), ROUTE_10K, SEOUL_A, incheon, RULES)
    assert v.reasons[0].params["multiplier"] == 1.6


def test_toll_mismatch_and_charged_meter():
    v = judge(receipt(20000, toll_fee=5000), ROUTE_10K, SEOUL_A, SEOUL_B, RULES)
    assert v.charged_won == 15000
    assert "TOLL_MISMATCH" in codes(v)
    assert v.grade == "CHECK_NEEDED"
    v = judge(receipt(20000, toll_fee=3000), ROUTE_10K, SEOUL_A, SEOUL_B, RULES)
    assert "TOLL_MISMATCH" not in codes(v)


def test_meter_fare_preferred():
    v = judge(receipt(99999, meter_fare=17000), ROUTE_10K, SEOUL_A, SEOUL_B, RULES)
    assert v.charged_won == 17000


def test_app_call_is_info_only():
    v = judge(receipt(17000), ROUTE_10K, SEOUL_A, SEOUL_B, RULES, hailed_via_app=True)
    assert v.grade == "NORMAL"
    assert "APP_CALL_FEE_NOT_MODELED" in codes(v)


@pytest.mark.parametrize("rc,route,code", [
    (ReceiptFields(total_fare=17000, taxi_type="deluxe"), ROUTE_10K, "TAXI_TYPE_UNSUPPORTED"),
    (ReceiptFields(total_fare=17000, taxi_type="medium"), None, "ROUTE_UNAVAILABLE"),
    (ReceiptFields(taxi_type="medium"), ROUTE_10K, "AMOUNT_MISSING"),
])
def test_unsupported(rc, route, code):
    v = judge(rc, route, SEOUL_A, SEOUL_B, RULES)
    assert v.grade == "UNSUPPORTED"
    assert codes(v) == [code]


def test_kakao_short_region_counts_as_seoul():
    a = SEOUL_A.model_copy(update={"region": "서울"})
    b = SEOUL_B.model_copy(update={"region": "서울"})
    v = judge(receipt(17000, out_of_city_surcharge=True), ROUTE_10K, a, b, RULES)
    assert "OUT_OF_CITY_INSIDE_SEOUL" in codes(v)
