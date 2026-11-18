"""장소 검색·경로 조회. 카카오 API는 GeoProvider 인터페이스 뒤에 숨긴다. docs/api.md §2"""
from __future__ import annotations

import math
import time
from typing import Protocol

import httpx

from app.models import Place, Route


class GeoError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class GeoProvider(Protocol):
    def search_place(self, query: str, size: int = 5) -> list[Place]: ...
    def get_route(self, origin: Place, destination: Place) -> Route | None: ...


# ---------- 공항 판별 ----------

def haversine_m(x1: float, y1: float, x2: float, y2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(y1), math.radians(y2)
    dp, dl = p2 - p1, math.radians(x2 - x1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def airport_tags(name: str, x: float, y: float, airports_cfg: dict) -> list[str]:
    radius = airports_cfg.get("radius_m", 3000)
    tags = []
    for tag, spec in airports_cfg.items():
        if not isinstance(spec, dict):
            continue
        by_name = any(k.lower() in name.lower() for k in spec.get("names", []))
        by_coord = any(haversine_m(x, y, px, py) <= radius for px, py in spec.get("points", []))
        if by_name or by_coord:
            tags.append(tag)
    return tags


def region_of(address: str) -> str:
    return address.split()[0] if address.strip() else ""


# ---------- 카카오 구현 ----------

class KakaoGeo:
    LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
    NAVI_URL = "https://apis-navi.kakaomobility.com/v1/directions"

    def __init__(self, api_key: str | None, airports_cfg: dict, cache_ttl_s: int = 3600,
                 client: httpx.Client | None = None):
        self._key = api_key
        self._airports = airports_cfg
        self._ttl = cache_ttl_s
        self._cache: dict[tuple, tuple[float, Route | None]] = {}
        self._client = client or httpx.Client(timeout=5.0)

    def _headers(self) -> dict:
        if not self._key:
            raise GeoError("KAKAO_KEY_MISSING", "KAKAO_REST_API_KEY is not set")
        return {"Authorization": f"KakaoAK {self._key}"}

    def search_place(self, query: str, size: int = 5) -> list[Place]:
        try:
            resp = self._client.get(self.LOCAL_URL, params={"query": query, "size": size},
                                    headers=self._headers())
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise GeoError("PLACE_SEARCH_FAILED", f"place search failed: {type(e).__name__}") from e
        return [self.place_from_doc(d) for d in resp.json().get("documents", [])[:size]]

    def place_from_doc(self, d: dict) -> Place:
        x, y = float(d["x"]), float(d["y"])
        address = d.get("road_address_name") or d.get("address_name", "")
        name = d.get("place_name", "")
        return Place(name=name, address=address, region=region_of(d.get("address_name", address)),
                     x=x, y=y, tags=airport_tags(name, x, y, self._airports))

    def get_route(self, origin: Place, destination: Place) -> Route | None:
        key = (round(origin.x, 6), round(origin.y, 6), round(destination.x, 6), round(destination.y, 6))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        try:
            resp = self._client.get(self.NAVI_URL, headers=self._headers(), params={
                "origin": f"{origin.x},{origin.y}", "destination": f"{destination.x},{destination.y}",
            })
            resp.raise_for_status()
        except httpx.HTTPError:
            return None  # 판정 쪽에서 ROUTE_UNAVAILABLE
        route = self.route_from_response(resp.json())
        if route is not None:
            self._cache[key] = (time.monotonic(), route)
        return route

    @staticmethod
    def route_from_response(data: dict) -> Route | None:
        routes = data.get("routes") or []
        if not routes or routes[0].get("result_code", 0) != 0:
            return None
        s = routes[0].get("summary") or {}
        fare = s.get("fare") or {}
        if "distance" not in s or "duration" not in s:
            return None
        return Route(distance_m=int(s["distance"]), duration_s=int(s["duration"]),
                     toll_won=int(fare.get("toll") or 0),
                     kakao_taxi_estimate_won=fare.get("taxi"))


# ---------- 테스트·평가용 가짜 구현 ----------

class FixtureGeo:
    """고정 경로/장소를 돌려준다. 평가 재현성을 위해 실시간 조회를 하지 않는다."""

    def __init__(self, routes: dict[tuple[str, str], Route] | None = None,
                 places: dict[str, list[Place]] | None = None):
        self.routes = routes or {}
        self.places = places or {}

    def search_place(self, query: str, size: int = 5) -> list[Place]:
        return self.places.get(query, [])[:size]

    def get_route(self, origin: Place, destination: Place) -> Route | None:
        return self.routes.get((origin.name, destination.name))
