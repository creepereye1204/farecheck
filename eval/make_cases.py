"""평가셋 30건 생성 (docs/eval.md §1).

- 정상 12 / 확인 필요 8 / 명백한 초과 10 (비율 5 + 할증 규칙 위반 5)
- 영수증 이미지는 합성한다. 실제 회사 로고·실제 차량번호를 쓰지 않는다 (차량번호는 형식만 맞춘 가짜 값).
- 경로 수치: 카카오 키가 없어 **가정값**으로 만든다 (route_source: "assumed").
  `--refresh-routes`로 카카오모빌리티를 한 번 조회해 fixture를 실측값으로 교체할 수 있다 (KAKAO_REST_API_KEY 필요).

실행: python eval/make_cases.py [--refresh-routes]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.config import load_app_config, load_fare_rules  # noqa: E402
from app.fare_engine import compute_upper_bound  # noqa: E402
from app.geo import airport_tags  # noqa: E402
from app.models import Place, ReceiptFields, Route  # noqa: E402

KST = timezone(timedelta(hours=9))
CASES = ROOT / "eval" / "cases"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
RULES = load_fare_rules()
AIRPORTS = load_app_config()["airports"]

# 좌표는 근사값. region은 카카오 address_name 첫 토큰 형식.
P = {
    "명동역": ("서울 중구 명동2가", "서울", 126.9860, 37.5609),
    "홍대입구역": ("서울 마포구 동교동", "서울", 126.9245, 37.5572),
    "경복궁": ("서울 종로구 사직로 161", "서울", 126.9770, 37.5796),
    "동대문디자인플라자": ("서울 중구 을지로 281", "서울", 127.0095, 37.5665),
    "이태원역": ("서울 용산구 이태원동", "서울", 126.9945, 37.5345),
    "강남역": ("서울 강남구 역삼동", "서울", 127.0276, 37.4979),
    "잠실 롯데월드": ("서울 송파구 올림픽로 240", "서울", 127.0982, 37.5111),
    "서울역": ("서울 용산구 한강대로 405", "서울", 126.9707, 37.5547),
    "인사동": ("서울 종로구 인사동", "서울", 126.9850, 37.5740),
    "여의도 한강공원": ("서울 영등포구 여의동로 330", "서울", 126.9340, 37.5284),
    "김포국제공항": ("서울 강서구 하늘길 38", "서울", 126.8013, 37.5583),
    "인천국제공항 제1여객터미널": ("인천 중구 공항로 272", "인천", 126.4505, 37.4492),
    "송도 센트럴파크": ("인천 연수구 송도동", "인천", 126.6390, 37.3925),
    "수원 화성행궁": ("경기 수원시 팔달구 정조로 825", "경기", 127.0137, 37.2819),
}


def place(name: str) -> dict:
    addr, region, x, y = P[name]
    return Place(name=name, address=addr, region=region, x=x, y=y,
                 tags=airport_tags(name, x, y, AIRPORTS)).model_dump()


# (id, 출발, 도착, 가정 거리 m, 가정 소요 s, 통행료, 실제 탑승 분, 승차 시각, 시나리오)
# 시나리오: ("normal", 비율) / ("check", 비율) / ("clear", 비율) / 규칙 위반 코드 등
SPECS = [
    # ---- 정상 12 ----
    ("N01", "명동역", "홍대입구역", 6800, 1500, 0, 24, "10:20", ("normal", 0.62)),
    ("N02", "경복궁", "강남역", 12400, 2400, 0, 38, "13:05", ("normal", 0.71)),
    ("N03", "서울역", "동대문디자인플라자", 4300, 1080, 0, 17, "09:40", ("normal", 0.80)),
    ("N04", "이태원역", "잠실 롯데월드", 13900, 2340, 0, 36, "15:30", ("normal", 0.88)),
    ("N05", "명동역", "김포국제공항", 20000, 2100, 0, 40, "11:10", ("normal", 0.66)),
    ("N06", "인사동", "여의도 한강공원", 9200, 1920, 0, 31, "17:45", ("normal", 0.93)),
    ("N07", "홍대입구역", "인천국제공항 제1여객터미널", 52000, 3300, 6600, 58, "06:30", ("normal", 0.75)),
    ("N08", "강남역", "명동역", 9600, 2280, 0, 41, "22:30", ("normal", 0.84)),
    ("N09", "동대문디자인플라자", "이태원역", 6100, 1500, 0, 26, "23:40", ("normal", 0.90)),
    ("N10", "잠실 롯데월드", "서울역", 15700, 2700, 0, 46, "02:20", ("normal", 0.70)),
    ("N11", "명동역", "수원 화성행궁", 38000, 3600, 0, 62, "12:00", ("normal", 0.78)),
    ("N12", "서울역", "송도 센트럴파크", 41000, 3500, 0, 57, "14:15", ("normal", 0.95)),
    # ---- 확인 필요 8 ----
    ("C01", "명동역", "홍대입구역", 6800, 1500, 0, 24, "16:00", ("check", 1.15)),
    ("C02", "경복궁", "강남역", 12400, 2400, 0, 40, "12:30", ("check", 1.32)),
    ("C03", "이태원역", "잠실 롯데월드", 13900, 2340, 0, 37, "23:20", ("check", 1.22)),
    ("C04", "서울역", "인천국제공항 제1여객터미널", 56000, 3500, 6600, 60, "08:00", ("check", 1.38)),
    ("C05", "강남역", "김포국제공항", 25000, 2700, 0, 45, "10:00", ("toll", 0.80)),
    ("C06", "홍대입구역", "인천국제공항 제1여객터미널", 52000, 3300, 6600, 55, "13:40", ("toll", 0.85)),
    ("C07", "인사동", "명동역", 2600, 720, 0, 12, "19:10", ("meter_off", 0.90)),
    ("C08", "동대문디자인플라자", "서울역", 4300, 1080, 0, 18, "21:00", ("meter_off", 0.70)),
    # ---- 명백한 초과: 비율 5 ----
    ("X01", "명동역", "김포국제공항", 20000, 2100, 0, 40, "14:00", ("clear", 15.95)),
    ("X02", "잠실 롯데월드", "인천국제공항 제1여객터미널", 70000, 4200, 6600, 75, "07:30", ("clear", 4.80)),
    ("X03", "명동역", "동대문디자인플라자", 3200, 900, 0, 15, "18:20", ("clear", 3.10)),
    ("X04", "홍대입구역", "경복궁", 6500, 1500, 0, 25, "11:45", ("clear", 2.05)),
    ("X05", "서울역", "강남역", 9800, 2100, 0, 35, "00:30", ("clear", 6.40)),
    # ---- 명백한 초과: 할증 규칙 위반 5 ----
    ("R01", "명동역", "강남역", 9600, 2280, 0, 38, "14:20", ("night_day", 0.95)),
    ("R02", "경복궁", "잠실 롯데월드", 17000, 2700, 0, 44, "10:10", ("night_day", 0.92)),
    ("R03", "인사동", "김포국제공항", 21000, 2400, 0, 42, "15:00", ("out_seoul", 0.90)),
    ("R04", "이태원역", "홍대입구역", 8200, 1800, 0, 29, "20:30", ("out_seoul", 0.93)),
    ("R05", "명동역", "여의도 한강공원", 8900, 1800, 0, 30, "12:40", ("night_day", 0.85)),
]

EXPECT = {
    "normal": ("NORMAL", ["WITHIN_UPPER_BOUND"]),
    "check": ("CHECK_NEEDED", ["OVER_UPPER_BOUND"]),
    "toll": ("CHECK_NEEDED", ["TOLL_MISMATCH"]),
    "meter_off": ("CHECK_NEEDED", ["METER_NOT_USED"]),
    "clear": ("CLEAR_EXCESS", ["FAR_OVER_UPPER_BOUND"]),
    "night_day": ("CLEAR_EXCESS", ["NIGHT_SURCHARGE_IN_DAYTIME"]),
    "out_seoul": ("CLEAR_EXCESS", ["OUT_OF_CITY_INSIDE_SEOUL"]),
}


def fake_plate(rng: random.Random) -> str:
    # 형식만 맞춘 가짜 번호. 사업용 택시 형식(서울 NN 바/사/아/자 NNNN).
    return f"서울{rng.randint(10, 99)}{rng.choice('바사아자')}{rng.randint(1000, 9999)}"


def build_case(spec, rng: random.Random, routes: dict) -> dict:
    cid, o, d, dist, dur, toll, minutes, start_hm, (kind, ratio) = spec
    route = routes.get(cid) or Route(distance_m=dist, duration_s=dur, toll_won=toll)
    h, m = map(int, start_hm.split(":"))
    day = datetime(2026, 11, 3 + (int(cid[1:]) % 20), tzinfo=KST)
    start = day.replace(hour=h, minute=m)
    end = start + timedelta(minutes=minutes)
    origin, dest = place(o), place(d)

    receipt_toll = route.toll_won
    if kind == "toll":
        receipt_toll = route.toll_won + rng.choice([5000, 6600, 8000])
    base = ReceiptFields(ride_start=start, ride_end=end, taxi_type="medium")
    calc = compute_upper_bound(route, base, Place(**origin), Place(**dest), RULES)
    meter = int(round(calc["meter_upper"] * ratio / 100.0) * 100)
    night = calc["night_rate"] > 0
    out_city = calc["out_of_city_rate"] > 0
    if kind == "night_day":
        night = True
    if kind == "out_seoul":
        out_city = True

    grade, codes = EXPECT[kind]
    receipt = {
        "total_fare": meter + receipt_toll, "meter_fare": meter, "toll_fee": receipt_toll,
        "ride_start": start.isoformat(), "ride_end": end.isoformat(),
        "night_surcharge": night, "out_of_city_surcharge": out_city,
        "distance_km": None, "vehicle_no": fake_plate(rng), "taxi_type": "medium",
    }
    return {
        "id": cid,
        "description": f"{o} → {d}, {start_hm} 승차, 시나리오 {kind}",
        "image": f"{cid}.png",
        "receipt": receipt,
        "origin": origin, "destination": dest,
        "meter_used": kind != "meter_off", "hailed_via_app": False,
        "route": route.model_dump(),
        "route_source": "kakao" if cid in routes else "assumed",
        "expected_grade": grade, "expected_reasons": codes,
        "generated_ratio_target": ratio,
    }


# ---------- 합성 영수증 이미지 ----------

def render_receipt(case: dict, path: Path, rng: random.Random) -> None:
    r = case["receipt"]
    W, H = 560, 900
    img = Image.new("RGB", (W, H), (252, 251, 247))
    dr = ImageDraw.Draw(img)
    f = lambda s: ImageFont.truetype(FONT, s, index=1)  # noqa: E731  (index 1 = KR)
    big, mid, small = f(30), f(22), f(18)
    y = 30

    def line(left, right="", font=mid, gap=10):
        nonlocal y
        dr.text((36, y), left, fill=(20, 20, 20), font=font)
        if right:
            w = dr.textlength(right, font=font)
            dr.text((W - 36 - w, y), right, fill=(20, 20, 20), font=font)
        y += font.size + gap

    def rule():
        nonlocal y
        dr.line([(36, y + 4), (W - 36, y + 4)], fill=(120, 120, 120), width=1)
        y += 16

    st = datetime.fromisoformat(r["ride_start"]); en = datetime.fromisoformat(r["ride_end"])
    dr.text(((W - dr.textlength("택시 영수증 / TAXI RECEIPT", font=big)) / 2, y),
            "택시 영수증 / TAXI RECEIPT", fill=(0, 0, 0), font=big)
    y += 56
    line("(샘플) 가상운수(주) · SAMPLE ONLY", font=small)
    line("차량번호 / Vehicle No.", r["vehicle_no"])
    line("일자 / Date", st.strftime("%Y-%m-%d"))
    rule()
    line("승차 / Start", st.strftime("%H:%M"))
    line("하차 / End", en.strftime("%H:%M"))
    rule()
    line("할증 / Surcharge", font=mid)
    line("  심야할증 / Night", "적용 Y" if r["night_surcharge"] else "미적용 N")
    line("  시계외할증 / Out-of-city", "적용 Y" if r["out_of_city_surcharge"] else "미적용 N")
    rule()
    line("미터요금 / Meter fare", f"{r['meter_fare']:,}원")
    line("통행료 / Toll", f"{r['toll_fee']:,}원")
    rule()
    line("합계 / TOTAL", f"{r['total_fare']:,}원", font=big, gap=18)
    line("카드 / Card  ****-****-****-" + str(rng.randint(1000, 9999)), font=small)
    line("감사합니다 · Thank you", font=small)
    # 사진처럼 약간 기울이고 노이즈를 준다
    img = img.rotate(rng.uniform(-2.5, 2.5), expand=True, fillcolor=(90, 90, 95))
    img.save(path, "PNG")


def refresh_routes() -> dict:
    from app.config import env
    from app.geo import KakaoGeo
    g = KakaoGeo(env("KAKAO_REST_API_KEY"), AIRPORTS)
    out = {}
    for s in SPECS:
        r = g.get_route(Place(**place(s[1])), Place(**place(s[2])))
        if r is None:
            raise SystemExit(f"route lookup failed for {s[0]}")
        out[s[0]] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-routes", action="store_true")
    args = ap.parse_args()
    routes = refresh_routes() if args.refresh_routes else {}
    CASES.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261118)
    for spec in SPECS:
        case = build_case(spec, rng, routes)
        (CASES / f"{case['id']}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        render_receipt(case, CASES / case["image"], rng)
    print(f"wrote {len(SPECS)} cases to {CASES}")


if __name__ == "__main__":
    main()
