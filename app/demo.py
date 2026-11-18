"""시연 모드 (FARECHECK_DEMO=1). 카카오·LLM 키 없이 화면 흐름을 보여주기 위한 고정 응답.

- 장소·경로: eval/cases의 fixture
- 영수증 추출: 등록된 이미지(sha256 일치)에 대해 정답 필드를 돌려준다. 그 외 이미지는 IMAGE_UNREADABLE.
- 설명: LLM을 쓰지 않고 고정 영어 템플릿 (report.fallback_explanation)
판정·신고서는 실제 fare_engine/report 코드가 만든다. 화면에는 DEMO 배지가 뜬다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.geo import FixtureGeo
from app.llm import LLMError
from app.models import Place, Route

ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = ROOT / "eval" / "cases"
DEMO_DIR = ROOT / "demo"

ALIASES = {
    "명동": "명동역", "myeongdong": "명동역", "김포": "김포국제공항", "gimpo": "김포국제공항",
    "인천공항": "인천국제공항 제1여객터미널", "incheon": "인천국제공항 제1여객터미널",
    "홍대": "홍대입구역", "hongdae": "홍대입구역", "강남": "강남역", "gangnam": "강남역",
    "경복궁": "경복궁", "gyeongbok": "경복궁", "서울역": "서울역", "seoul station": "서울역",
}


class DemoGeo(FixtureGeo):
    def search_place(self, query: str, size: int = 5) -> list[Place]:
        q = query.strip().lower()
        hits = [p for name, p in self._by_name.items() if q in name.lower()]
        for alias, name in ALIASES.items():
            if (alias in q or q in alias) and name in self._by_name and self._by_name[name] not in hits:
                hits.append(self._by_name[name])
        return hits[:size]

    def get_route(self, origin: Place, destination: Place) -> Route | None:
        return self.routes.get((origin.name, destination.name))


class DemoLLM:
    def __init__(self, receipts_by_hash: dict[str, dict]):
        self._by_hash = receipts_by_hash

    def extract_receipt(self, image_bytes: bytes, mime: str) -> dict:
        r = self._by_hash.get(hashlib.sha256(image_bytes).hexdigest())
        if r is None:
            return {"total_fare": "unreadable"}  # 검증 실패 → IMAGE_UNREADABLE
        conf = {k: 0.95 for k, v in r.items() if v is not None}
        conf["vehicle_no"] = 0.62
        return {**r, "confidence": conf}

    def explain(self, verdict_payload: dict, lang: str) -> str:
        raise LLMError("demo mode: template explanation")


def build_demo() -> tuple[DemoGeo, DemoLLM]:
    places: dict[str, Place] = {}
    routes: dict[tuple[str, str], Route] = {}
    receipts: dict[str, dict] = {}
    for p in sorted(CASE_DIR.glob("*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        o, d = Place(**c["origin"]), Place(**c["destination"])
        places[o.name], places[d.name] = o, d
        routes.setdefault((o.name, d.name), Route(**c["route"]))
        img = CASE_DIR / c["image"]
        if img.exists():
            receipts[hashlib.sha256(img.read_bytes()).hexdigest()] = c["receipt"]
    for p in sorted(DEMO_DIR.glob("*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        routes[(c["origin"], c["destination"])] = Route(**c["route"])
        img = DEMO_DIR / c["image"]
        if img.exists():
            receipts[hashlib.sha256(img.read_bytes()).hexdigest()] = c["receipt"]
    geo = DemoGeo(routes=routes)
    geo._by_name = places
    return geo, DemoLLM(receipts)
