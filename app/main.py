"""FastAPI 앱. 실행: uvicorn app.main:app"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import app_config, env, fare_rules
from app.geo import GeoProvider, KakaoGeo
from app.llm import LLMError, VisionLLM, make_llm
from app.store import CaseStore

STATIC_DIR = Path(__file__).parent / "static"


def create_app(geo: GeoProvider | None = None, llm: VisionLLM | None = "env",  # type: ignore[assignment]
               store: CaseStore | None = None) -> FastAPI:
    app = FastAPI(title="farecheck")
    cfg = app_config()
    app.state.demo = geo is None and env("FARECHECK_DEMO") == "1"
    if app.state.demo:
        from app.demo import build_demo
        geo, llm = build_demo()
    app.state.rules = fare_rules()
    app.state.app_config = cfg
    app.state.geo_is_fake = geo is not None
    app.state.geo = geo or KakaoGeo(env("KAKAO_REST_API_KEY"), cfg.get("airports", {}),
                                    cfg.get("route_cache_ttl_s", 3600))
    if llm == "env":
        try:
            llm = make_llm()
        except LLMError:
            llm = None
    app.state.llm = llm
    app.state.store = store or CaseStore(env("DB_PATH", "./farecheck.db"))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "INVALID_REQUEST", "message": "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])}})

    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
