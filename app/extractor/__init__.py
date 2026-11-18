"""영수증 이미지 → ReceiptFields. docs/llm.md §1"""
from __future__ import annotations

from datetime import timedelta

from pydantic import ValidationError

from app.llm import LLMError, VisionLLM
from app.models import ReceiptFields


class ExtractError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def postprocess(r: ReceiptFields) -> ReceiptFields:
    """docs/llm.md §1 후처리 (코드)."""
    conf = dict(r.confidence)
    if r.meter_fare is not None and r.total_fare is not None:
        if abs((r.meter_fare + (r.toll_fee or 0)) - r.total_fare) > 100:
            for f in ("meter_fare", "toll_fee", "total_fare"):
                conf[f] = min(conf.get(f, 1.0), 0.5)
    end = r.ride_end
    if r.ride_start is not None and end is not None and end < r.ride_start:
        end = end + timedelta(days=1)
        conf["ride_end"] = min(conf.get("ride_end", 1.0), 0.6)
    return r.model_copy(update={"confidence": conf, "ride_end": end})


def low_confidence_fields(r: ReceiptFields, threshold: float) -> list[str]:
    """임계값 미만 필드. null 필드도 사용자 확인이 필요하므로 confidence가 없으면 낮은 것으로 본다."""
    out = []
    for f in ReceiptFields.model_fields:
        if f == "confidence":
            continue
        val = getattr(r, f)
        c = r.confidence.get(f)
        if val is None or val == "unknown":
            continue
        if c is None or c < threshold:
            out.append(f)
    return out


def extract(llm: VisionLLM | None, image_bytes: bytes, mime: str) -> ReceiptFields:
    if llm is None:
        raise ExtractError("LLM_UNAVAILABLE", "vision LLM is not configured")
    last: Exception | None = None
    for _ in range(2):  # 검증 실패 시 1회 재시도
        try:
            raw = llm.extract_receipt(image_bytes, mime)
        except LLMError as e:
            raise ExtractError("LLM_UNAVAILABLE", str(e)) from e
        try:
            raw = {k: v for k, v in raw.items() if k in ReceiptFields.model_fields}
            raw["confidence"] = {k: v for k, v in (raw.get("confidence") or {}).items()
                                 if raw.get(k) is not None}
            return postprocess(ReceiptFields(**raw))
        except (ValidationError, TypeError, AttributeError) as e:
            last = e
    raise ExtractError("IMAGE_UNREADABLE", "could not read receipt fields") from last
