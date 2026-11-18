"""시연용 E8 영수증(명동→김포공항 48만원, docs/fare-rules.md §8) 생성. 합성 이미지, 가짜 차량번호."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from make_cases import render_receipt  # noqa: E402

E8 = {
    "id": "E8",
    "description": "보도 사례 재현: 명동→김포공항 48만원 (경로 20km·40분은 docs/fare-rules.md E8 가정값)",
    "image": "e8_receipt.png",
    "origin": "명동역",
    "destination": "김포국제공항",
    "route": {"distance_m": 20000, "duration_s": 2100, "toll_won": 0, "kakao_taxi_estimate_won": None},
    "receipt": {
        "total_fare": 480000, "meter_fare": 480000, "toll_fee": 0,
        "ride_start": "2026-11-18T14:02:00+09:00", "ride_end": "2026-11-18T14:42:00+09:00",
        "night_surcharge": False, "out_of_city_surcharge": False, "distance_km": None,
        "vehicle_no": "서울31바1234", "taxi_type": "medium",
    },
}

if __name__ == "__main__":
    out = ROOT / "demo"
    (out / "e8.json").write_text(json.dumps(E8, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    render_receipt(E8, out / E8["image"], random.Random(8))
    print("wrote demo/e8.json, demo/e8_receipt.png")
