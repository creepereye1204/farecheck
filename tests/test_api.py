"""API 통합 테스트 (TestClient + 가짜 geo/LLM)."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.geo import FixtureGeo
from app.llm import FakeLLM
from app.main import create_app
from app.models import Place, Route
from app.store import CaseStore
from tests.test_modules import GMP, GOOD, ORIGIN

ROUTES = {("명동역", "김포국제공항"): Route(distance_m=20000, duration_s=2100, toll_won=0,
                                         kakao_taxi_estimate_won=27000)}


@pytest.fixture
def client(tmp_path):
    geo = FixtureGeo(routes=ROUTES, places={"김포공항": [GMP]})
    app = create_app(geo=geo, llm=FakeLLM(GOOD, explain_text="Clearly above the normal range."),
                     store=CaseStore(str(tmp_path / "a.db")))
    return TestClient(app)


def png() -> bytes:
    b = io.BytesIO(); Image.new("RGB", (4, 4)).save(b, "PNG"); return b.getvalue()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "kakao": True, "llm": True}


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Taxi Fare Check" in r.text


def test_extract(client):
    r = client.post("/api/extract", files={"image": ("r.png", png(), "image/png")})
    assert r.status_code == 200
    assert r.json()["receipt"]["total_fare"] == 480000
    assert r.json()["low_confidence_fields"] == ["vehicle_no"]


def test_extract_errors(client, tmp_path):
    r = client.post("/api/extract", files={"image": ("r.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_IMAGE_TYPE"
    r = client.post("/api/extract", files={"image": ("r.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")})
    assert r.status_code == 413
    no_llm = TestClient(create_app(geo=FixtureGeo(), llm=None, store=CaseStore(str(tmp_path / "b.db"))))
    r = no_llm.post("/api/extract", files={"image": ("r.png", png(), "image/png")})
    assert r.status_code == 503 and r.json()["error"]["code"] == "LLM_UNAVAILABLE"


def test_places(client):
    r = client.get("/api/places", params={"q": "김포공항"})
    assert r.json()["places"][0]["tags"] == ["GMP_AIRPORT"]
    assert client.get("/api/places").status_code == 400


def _trip(**kw):
    return {"receipt": GOOD, "origin": ORIGIN.model_dump(), "destination": GMP.model_dump(),
            "meter_used": True, "hailed_via_app": False, "user_lang": "ja", **kw}


def test_judge_e8_and_case_lookup(client):
    r = client.post("/api/judge", json=_trip())
    assert r.status_code == 200
    d = r.json()
    assert d["verdict"]["grade"] == "CLEAR_EXCESS"
    assert d["verdict"]["upper_bound_won"] == 30100
    assert d["route"]["kakao_taxi_estimate_won"] == 27000
    assert "명백한 초과" in d["report_ko"]
    assert d["explanation"] == {"lang": "ja", "text": "Clearly above the normal range.", "source": "llm"}
    assert "report_channels" not in d  # Q6 미확정: 비어 있으면 생략
    c = client.get(f"/api/cases/{d['case_id']}").json()
    assert c["grade"] == "CLEAR_EXCESS" and "vehicle_no" not in c
    assert client.get("/api/cases/c_none").status_code == 404


def test_judge_route_unavailable(client):
    other = Place(name="어딘가", region="서울특별시", x=127.0, y=37.5).model_dump()
    d = client.post("/api/judge", json=_trip(destination=other)).json()
    assert d["verdict"]["grade"] == "UNSUPPORTED"
    assert d["verdict"]["reasons"][0]["code"] == "ROUTE_UNAVAILABLE"


def test_judge_invalid_body(client):
    r = client.post("/api/judge", json={"receipt": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REQUEST"
