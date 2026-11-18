"""HTTP 라우터. docs/api.md §1"""
from __future__ import annotations

import io

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app import extractor, report
from app.config import env
from app.extractor import ExtractError
from app.fare_engine import judge as engine_judge
from app.geo import GeoError
from app.models import TripInput

router = APIRouter(prefix="/api")

MAX_IMAGE_BYTES = 10 * 1024 * 1024
DIRECT_MIME = {"image/jpeg", "image/png", "image/webp"}
HEIC_MIME = {"image/heic", "image/heif"}


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _heic_to_jpeg(data: bytes) -> bytes:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


@router.get("/health")
def health(request: Request):
    s = request.app.state
    body = {"status": "ok", "kakao": bool(env("KAKAO_REST_API_KEY")) or s.geo_is_fake,
            "llm": s.llm is not None}
    if getattr(s, "demo", False):
        body["demo"] = True
    return body


@router.post("/extract")
async def extract(request: Request, image: UploadFile = File(...)):
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        return error(413, "IMAGE_TOO_LARGE", "image must be 10MB or smaller")
    mime = (image.content_type or "").lower()
    name = (image.filename or "").lower()
    if mime in HEIC_MIME or name.endswith((".heic", ".heif")):
        try:
            data, mime = _heic_to_jpeg(data), "image/jpeg"
        except Exception:
            return error(422, "IMAGE_UNREADABLE", "could not decode HEIC image")
    elif mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in DIRECT_MIME:
        return error(415, "UNSUPPORTED_IMAGE_TYPE", "use jpg, png or heic")

    try:
        r = extractor.extract(request.app.state.llm, data, mime)
    except ExtractError as e:
        return error(503 if e.code == "LLM_UNAVAILABLE" else 422, e.code, str(e))
    threshold = request.app.state.rules.low_confidence_threshold
    return {"receipt": r.model_dump(mode="json"),
            "low_confidence_fields": extractor.low_confidence_fields(r, threshold)}


@router.get("/places")
def places(request: Request, q: str = ""):
    if not q.strip():
        return error(400, "QUERY_REQUIRED", "q is required")
    try:
        found = request.app.state.geo.search_place(q.strip(), size=5)
    except GeoError as e:
        return error(503, e.code, str(e))
    return {"places": [p.model_dump() for p in found]}


@router.post("/judge")
def judge(request: Request, trip: TripInput):
    s = request.app.state
    try:
        route = s.geo.get_route(trip.origin, trip.destination)
    except GeoError:
        route = None  # 추측하지 않는다 → UNSUPPORTED(ROUTE_UNAVAILABLE)

    verdict = engine_judge(trip.receipt, route, trip.origin, trip.destination, s.rules,
                           meter_used=trip.meter_used, hailed_via_app=trip.hailed_via_app)
    report_ko = report.build_ko(verdict, trip.receipt, trip.origin, trip.destination)
    explanation = report.summarize(s.llm, verdict, route, trip.origin, trip.destination, trip.user_lang)
    case_id = s.store.save_case(trip.origin.name, trip.destination.name, verdict, trip.user_lang)

    body = {
        "case_id": case_id,
        "verdict": verdict.model_dump(),
        "route": route.model_dump() if route else None,
        "report_ko": report_ko,
        "explanation": explanation,
    }
    channels = [c for c in (s.app_config.get("report_channels") or []) if c.get("url")]
    if channels:
        body["report_channels"] = channels
    return body


@router.get("/cases/{case_id}")
def get_case(request: Request, case_id: str):
    c = request.app.state.store.get_case(case_id)
    if c is None:
        return error(404, "CASE_NOT_FOUND", "no such case")
    return c
