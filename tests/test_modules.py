"""extractor / geo / report / store 단위 테스트 (외부 호출은 가짜 구현)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import extractor, report
from app.config import load_app_config, load_fare_rules
from app.extractor import ExtractError
from app.fare_engine import judge
from app.geo import KakaoGeo, airport_tags
from app.llm import FakeLLM
from app.models import Place, ReceiptFields, Route
from app.store import CaseStore

KST = timezone(timedelta(hours=9))
RULES = load_fare_rules()
AIRPORTS = load_app_config()["airports"]

GOOD = {
    "total_fare": 480000, "meter_fare": 480000, "toll_fee": 0,
    "ride_start": "2026-11-18T14:00:00+09:00", "ride_end": "2026-11-18T14:40:00+09:00",
    "night_surcharge": False, "out_of_city_surcharge": False, "distance_km": None,
    "vehicle_no": "서울00가0000", "taxi_type": "medium",
    "confidence": {"total_fare": 0.98, "meter_fare": 0.97, "toll_fee": 0.9, "ride_start": 0.95,
                   "ride_end": 0.95, "night_surcharge": 0.9, "out_of_city_surcharge": 0.9,
                   "distance_km": 0.1, "vehicle_no": 0.62, "taxi_type": 0.9},
}


# ---------- extractor ----------

def test_extract_ok_and_low_confidence():
    r = extractor.extract(FakeLLM(GOOD), b"img", "image/png")
    assert r.total_fare == 480000
    assert "distance_km" not in r.confidence  # null 필드의 confidence는 버린다
    assert extractor.low_confidence_fields(r, 0.8) == ["vehicle_no"]


def test_extract_retry_then_unreadable():
    bad = {**GOOD, "total_fare": "abc"}
    llm = FakeLLM([bad, GOOD])
    assert extractor.extract(llm, b"", "image/png").total_fare == 480000
    assert llm.calls == 2
    with pytest.raises(ExtractError) as e:
        extractor.extract(FakeLLM([bad, bad]), b"", "image/png")
    assert e.value.code == "IMAGE_UNREADABLE"


def test_extract_no_llm():
    with pytest.raises(ExtractError) as e:
        extractor.extract(None, b"", "image/png")
    assert e.value.code == "LLM_UNAVAILABLE"


def test_postprocess_amount_mismatch_and_overnight():
    r = ReceiptFields(total_fare=20000, meter_fare=15000, toll_fee=0,
                      ride_start=datetime(2026, 11, 18, 23, 50, tzinfo=KST),
                      ride_end=datetime(2026, 11, 18, 0, 20, tzinfo=KST),
                      confidence={"total_fare": 0.99, "meter_fare": 0.99, "ride_end": 0.99})
    p = extractor.postprocess(r)
    assert p.confidence["total_fare"] == 0.5 and p.confidence["meter_fare"] == 0.5
    assert p.ride_end == datetime(2026, 11, 19, 0, 20, tzinfo=KST)
    assert p.confidence["ride_end"] == 0.6


# ---------- geo ----------

def test_airport_tags():
    assert airport_tags("김포국제공항 국내선", 126.80, 37.56, AIRPORTS) == ["GMP_AIRPORT"]
    assert airport_tags("어딘가", 126.4507, 37.4490, AIRPORTS) == ["ICN_AIRPORT"]
    assert airport_tags("Incheon International Airport T2", 0, 0, AIRPORTS) == ["ICN_AIRPORT"]
    assert airport_tags("명동역", 126.986, 37.561, AIRPORTS) == []


def _kakao(handler):
    return KakaoGeo("k", AIRPORTS, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_kakao_place_and_route_with_cache():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.host)
        assert req.headers["Authorization"] == "KakaoAK k"
        if req.url.host == "dapi.kakao.com":
            return httpx.Response(200, json={"documents": [
                {"place_name": "김포국제공항", "address_name": "서울 강서구 공항동 1373",
                 "road_address_name": "서울 강서구 하늘길 38", "x": "126.8013", "y": "37.5583"}]})
        return httpx.Response(200, json={"routes": [{"result_code": 0, "summary": {
            "distance": 20000, "duration": 2100, "fare": {"taxi": 27000, "toll": 0}}}]})

    g = _kakao(handler)
    [p] = g.search_place("김포공항")
    assert (p.region, p.tags) == ("서울", ["GMP_AIRPORT"])
    o = Place(name="명동", region="서울", x=126.98, y=37.56)
    r1 = g.get_route(o, p)
    r2 = g.get_route(o, p)
    assert r1 == r2 == Route(distance_m=20000, duration_s=2100, toll_won=0, kakao_taxi_estimate_won=27000)
    assert calls.count("apis-navi.kakaomobility.com") == 1


def test_kakao_route_failure_returns_none():
    g = _kakao(lambda req: httpx.Response(200, json={"routes": [{"result_code": 104}]}))
    o = Place(name="a", x=1, y=1)
    assert g.get_route(o, o) is None
    g = _kakao(lambda req: httpx.Response(500))
    assert g.get_route(o, o) is None


# ---------- report ----------

ORIGIN = Place(name="명동역", address="서울 중구 명동", region="서울특별시", x=126.98, y=37.56)
GMP = Place(name="김포국제공항", address="서울 강서구 하늘길 38", region="서울특별시", x=126.80, y=37.56,
            tags=["GMP_AIRPORT"])
E8_ROUTE = Route(distance_m=20000, duration_s=2100, toll_won=0)


def _e8():
    r = ReceiptFields(**{**GOOD, "confidence": {}})
    return r, judge(r, E8_ROUTE, ORIGIN, GMP, RULES)


def test_build_ko_snapshot():
    r, v = _e8()
    text = report.build_ko(v, r, ORIGIN, GMP)
    expected = """[외국인 관광객 택시 요금 확인 요청]

1. 탑승 정보
 - 승차: 2026-11-18 14:00 / 하차: 2026-11-18 14:40
 - 출발: 명동역 (서울 중구 명동)
 - 도착: 김포국제공항 (서울 강서구 하늘길 38)
 - 차량번호: 서울00가0000
 - 결제 금액: 480,000원 (미터기 480,000원, 통행료 0원)

2. 정상 요금 상한 계산 (서울 중형택시 요금표, seoul-medium-2023-02)
 - 도로 경로 거리 20,000m, 우회 허용 1.2배 → 24,000m
 - 실제 소요시간 2,400초
 - 기본·거리·시간 요금 합산(주간 기준) 29,900원 × 할증 1.0 = 상한 30,100원
 - 시간요금과 거리요금을 전 구간에 모두 더한 값으로, 실제 미터기 요금보다 높게 잡은 보수적 상한입니다.

3. 확인 결과: 명백한 초과
 - 청구 미터기 요금 480,000원은 상한의 15.947배입니다.
 - 청구 금액이 정상 요금 상한(30,100원)의 15.947배로, 판정 기준(1.5배)을 넘습니다.

4. 비고
 - 본 문서는 자동 계산 결과이며, 위반 여부의 최종 판단은 담당 기관에 있습니다.
 - 영수증 원본은 신고인이 보관하고 있습니다.
"""
    assert text == expected


def test_every_reason_code_has_ko_text():
    import yaml
    from app.report import TEMPLATE_DIR
    keys = set(yaml.safe_load(open(TEMPLATE_DIR / "reasons_ko.yaml", encoding="utf-8")))
    assert {"WITHIN_UPPER_BOUND", "OVER_UPPER_BOUND", "FAR_OVER_UPPER_BOUND", "NIGHT_SURCHARGE_IN_DAYTIME",
            "OUT_OF_CITY_INSIDE_SEOUL", "OUT_OF_CITY_TO_EXEMPT", "TOLL_MISMATCH", "METER_NOT_USED",
            "APP_CALL_FEE_NOT_MODELED", "TAXI_TYPE_UNSUPPORTED", "ROUTE_UNAVAILABLE", "AMOUNT_MISSING"} <= keys


def test_unsupported_report_renders():
    r = ReceiptFields(total_fare=10000, taxi_type="medium")
    v = judge(r, None, ORIGIN, GMP, RULES)
    text = report.build_ko(v, r, ORIGIN, GMP)
    assert "자동 판정 불가" in text and "도로 경로를 조회하지 못해" in text


def test_report_has_no_forbidden_words():
    r, v = _e8()
    assert not report.FORBIDDEN.search(report.build_ko(v, r, ORIGIN, GMP))
    assert not report.FORBIDDEN.search(report.fallback_explanation(v))


def test_summarize_uses_llm_then_falls_back():
    _, v = _e8()
    out = report.summarize(FakeLLM(explain_text="Clearly above the normal range."), v, E8_ROUTE, ORIGIN, GMP, "en")
    assert out["source"] == "llm"
    out = report.summarize(FakeLLM(explain_text="This looks like a scam."), v, E8_ROUTE, ORIGIN, GMP, "en")
    assert out["source"] == "template" and "scam" not in out["text"]
    out = report.summarize(FakeLLM(fail_explain=True), v, E8_ROUTE, ORIGIN, GMP, "ja")
    assert out["source"] == "template" and "30,100" in out["text"]
    assert report.summarize(None, v, E8_ROUTE, ORIGIN, GMP, "en")["source"] == "template"


# ---------- store ----------

def test_store_roundtrip_without_vehicle_no(tmp_path):
    s = CaseStore(str(tmp_path / "t.db"))
    _, v = _e8()
    cid = s.save_case("명동역", "김포국제공항", v, "ja")
    c = s.get_case(cid)
    assert c["grade"] == "CLEAR_EXCESS" and c["reason_codes"] == ["FAR_OVER_UPPER_BOUND"]
    assert "서울00가0000" not in str(c)
    assert s.get_case("nope") is None
