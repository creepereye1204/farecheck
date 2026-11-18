"""한국어 신고서(템플릿, LLM 미사용)와 사용자 언어 설명(LLM, 실패 시 고정 템플릿). docs/llm.md §2~3"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.fare_engine import calc_params
from app.llm import LLMError, VisionLLM
from app.models import Place, ReceiptFields, Route, Verdict

TEMPLATE_DIR = Path(__file__).parent / "templates"
KST = timezone(timedelta(hours=9))

GRADE_KO = {
    "NORMAL": "정상 범위",
    "CHECK_NEEDED": "확인 필요",
    "CLEAR_EXCESS": "명백한 초과",
    "UNSUPPORTED": "자동 판정 불가",
}
GRADE_EN = {
    "NORMAL": "Within the normal range",
    "CHECK_NEEDED": "Needs checking",
    "CLEAR_EXCESS": "Clearly above the normal range",
    "UNSUPPORTED": "Cannot be checked automatically",
}
# AGENTS.md 절대 규칙 2. 이 단어가 나오면 LLM 설명을 버리고 고정 템플릿을 쓴다.
FORBIDDEN = re.compile(r"fraud|scam|crim|illegal|사기|범죄|불법|詐欺|诈骗|犯罪|違法|违法", re.IGNORECASE)


def _won(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.1f}"
    return f"{int(v):,}"


@lru_cache
def _env() -> Environment:
    e = Environment(loader=FileSystemLoader(TEMPLATE_DIR), undefined=StrictUndefined,
                    keep_trailing_newline=True, trim_blocks=False)
    e.filters["won"] = _won
    return e


@lru_cache
def _reason_templates() -> dict[str, str]:
    with open(TEMPLATE_DIR / "reasons_ko.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def reason_text_ko(code: str, params: dict) -> str:
    tpl = _reason_templates().get(code)
    if tpl is None:
        return code
    e = _env()
    return e.from_string(tpl).render(**params)


def _fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "확인 불가"
    dt = dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)
    return dt.strftime("%Y-%m-%d %H:%M")


def build_ko(verdict: Verdict, receipt: ReceiptFields, origin: Place, destination: Place) -> str:
    calc = calc_params(verdict)
    return _env().get_template("report_ko.j2").render(
        ride_start=_fmt_time(receipt.ride_start),
        ride_end=_fmt_time(receipt.ride_end),
        origin_name=origin.name, origin_address=origin.address or "-",
        dest_name=destination.name, dest_address=destination.address or "-",
        vehicle_no=receipt.vehicle_no,
        total_fare=receipt.total_fare, meter_fare=receipt.meter_fare, toll_fee=receipt.toll_fee,
        rule_version=verdict.rule_version,
        calc=calc or None,
        grade_ko=GRADE_KO[verdict.grade],
        reasons=[reason_text_ko(r.code, r.params) for r in verdict.reasons],
    )


# ---------- 사용자 언어 설명 ----------

_EN_REASON = {
    "WITHIN_UPPER_BOUND": "the charged meter fare is within the highest normal fare for this route",
    "OVER_UPPER_BOUND": "the charged meter fare is {ratio}x the highest normal fare for this route",
    "FAR_OVER_UPPER_BOUND": "the charged meter fare is {ratio}x the highest normal fare for this route",
    "NIGHT_SURCHARGE_IN_DAYTIME": "the receipt shows a night surcharge, but the ride was during daytime hours",
    "OUT_OF_CITY_INSIDE_SEOUL": "the receipt shows an out-of-city surcharge, but the whole ride was inside Seoul",
    "OUT_OF_CITY_TO_EXEMPT": "the receipt shows an out-of-city surcharge on a route that is normally exempt",
    "TOLL_MISMATCH": "the toll on the receipt is higher than the toll on the route",
    "METER_NOT_USED": "you reported that the meter was not used",
    "TAXI_TYPE_UNSUPPORTED": "this tool only checks regular (medium) taxis",
    "ROUTE_UNAVAILABLE": "the road route could not be looked up",
    "AMOUNT_MISSING": "the paid amount could not be confirmed",
}
_SEV = {"info": 0, "check": 1, "clear": 2}


def fallback_explanation(verdict: Verdict) -> str:
    """LLM 실패 시 쓰는 고정 영어 템플릿."""
    main = max(verdict.reasons, key=lambda r: _SEV[r.severity])
    why = _EN_REASON.get(main.code, main.code).format(ratio=verdict.ratio)
    parts = [f"Result: {GRADE_EN[verdict.grade]}."]
    if verdict.upper_bound_won:
        parts.append(f"The highest normal meter fare for this route is about {verdict.upper_bound_won:,} KRW, "
                     f"and {why}.")
    else:
        parts.append(f"This is because {why}.")
    if verdict.grade in ("CHECK_NEEDED", "CLEAR_EXCESS"):
        parts.append("You can copy the Korean report below and submit it through an official reporting channel.")
    parts.append("A city officer makes the final decision.")
    return " ".join(parts)


def summarize(llm: VisionLLM | None, verdict: Verdict, route: Route | None,
              origin: Place, destination: Place, lang: str) -> dict:
    payload = {
        "verdict": verdict.model_dump(),
        "route": route.model_dump() if route else None,
        "origin": origin.name, "destination": destination.name,
    }
    if llm is not None:
        try:
            text = llm.explain(payload, lang)
            if text and not FORBIDDEN.search(text):
                return {"lang": lang, "text": text, "source": "llm"}
        except LLMError:
            pass
    return {"lang": "en", "text": fallback_explanation(verdict), "source": "template"}
