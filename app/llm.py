"""비전 LLM 인터페이스와 구현. docs/llm.md, open-questions Q1.

LLM은 (a) 영수증 필드 추출, (b) 판정 결과 풀어 쓰기만 한다. 판정에 쓰지 않는다.
"""
from __future__ import annotations

import base64
import json
from typing import Protocol

from app.config import env


class LLMError(Exception):
    pass


class VisionLLM(Protocol):
    def extract_receipt(self, image_bytes: bytes, mime: str) -> dict: ...
    def explain(self, verdict_payload: dict, lang: str) -> str: ...


EXTRACT_SYSTEM = """You read Korean taxi receipts and return JSON only.
Rules:
- Return exactly the schema given. No prose.
- Never guess. If a field is not printed or not legible, return null and set its confidence <= 0.3.
- Amounts are integers in KRW without separators.
- Times are ISO 8601 with +09:00. If only time is printed, use the date printed elsewhere on the receipt; if no date, return null.
- night_surcharge / out_of_city_surcharge: true only if the receipt explicitly marks it (e.g. 심야할증, 시계외할증, Night surcharge, Out-of-city). false if the receipt has a surcharge section showing none. null if the receipt has no such section.
- taxi_type: "medium" for regular taxis, "deluxe" for 모범/대형, "platform" for premium app rides (e.g. black/luxury), else "unknown".
- confidence: your confidence 0..1 for each non-null field, keyed by field name."""

EXPLAIN_SYSTEM = """Explain a taxi fare check result to a foreign tourist in {lang}.
- Use only the numbers in the JSON. Do not add facts, laws, or amounts.
- Never use words like "fraud", "scam", "crime", "illegal". The highest grade is "clearly above the normal range".
- 4 sentences max: result, why (main reason with numbers), what to do next, a note that a city officer makes the final decision.
- Plain, calm tone. Output only the explanation text."""

_FIELDS = ["total_fare", "meter_fare", "toll_fee", "ride_start", "ride_end", "night_surcharge",
           "out_of_city_surcharge", "distance_km", "vehicle_no", "taxi_type"]


def _nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "total_fare": _nullable("integer"),
        "meter_fare": _nullable("integer"),
        "toll_fee": _nullable("integer"),
        "ride_start": _nullable("string"),
        "ride_end": _nullable("string"),
        "night_surcharge": _nullable("boolean"),
        "out_of_city_surcharge": _nullable("boolean"),
        "distance_km": _nullable("number"),
        "vehicle_no": _nullable("string"),
        "taxi_type": {"type": "string", "enum": ["medium", "deluxe", "platform", "unknown"]},
        "confidence": {
            "type": "object",
            "properties": {f: {"type": "number"} for f in _FIELDS},
            "required": _FIELDS,
            "additionalProperties": False,
        },
    },
    "required": _FIELDS + ["confidence"],
    "additionalProperties": False,
}


class AnthropicVisionLLM:
    def __init__(self, api_key: str | None, model: str):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._model = model

    def _create(self, **kw):
        a = self._anthropic
        try:
            resp = self._client.beta.messages.create(
                model=self._model,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kw,
            )
        except (a.APIConnectionError, a.RateLimitError, a.APIStatusError) as e:
            raise LLMError(f"LLM call failed: {type(e).__name__}") from e
        if resp.stop_reason == "refusal":
            raise LLMError("LLM declined the request")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text:
            raise LLMError("LLM returned no text")
        return text

    def extract_receipt(self, image_bytes: bytes, mime: str) -> dict:
        text = self._create(
            max_tokens=4000,
            system=EXTRACT_SYSTEM,
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": RECEIPT_SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.standard_b64encode(image_bytes).decode()}},
                {"type": "text", "text": "Extract the receipt fields."},
            ]}],
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("LLM returned invalid JSON") from e

    def explain(self, verdict_payload: dict, lang: str) -> str:
        return self._create(
            max_tokens=2000,
            system=EXPLAIN_SYSTEM.format(lang=lang),
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": json.dumps(verdict_payload, ensure_ascii=False)}],
        ).strip()


def make_llm() -> VisionLLM | None:
    """환경 변수로 프로바이더를 고른다. 키가 없으면 None (호출 시 LLM_UNAVAILABLE)."""
    provider = (env("LLM_PROVIDER", "anthropic") or "").lower()
    key = env("LLM_API_KEY")
    if not key:
        return None
    if provider == "anthropic":
        return AnthropicVisionLLM(api_key=key, model=env("LLM_MODEL", "claude-opus-5-5"))
    # Q1: openai 구현은 아직 없다. 인터페이스(VisionLLM)만 맞추면 추가할 수 있다.
    raise LLMError(f"LLM_PROVIDER '{provider}' is not implemented")


class FakeLLM:
    """테스트·평가용 고정 응답 LLM."""

    def __init__(self, extract_response: dict | list[dict] | None = None, explain_text: str = "ok",
                 fail_explain: bool = False):
        self._extract = extract_response
        self.explain_text = explain_text
        self.fail_explain = fail_explain
        self.calls = 0

    def extract_receipt(self, image_bytes: bytes, mime: str) -> dict:
        self.calls += 1
        r = self._extract
        if isinstance(r, list):
            r = r[min(self.calls - 1, len(r) - 1)]
        if r is None:
            raise LLMError("fake LLM: no response")
        return r

    def explain(self, verdict_payload: dict, lang: str) -> str:
        if self.fail_explain:
            raise LLMError("fake LLM explain failure")
        return self.explain_text
