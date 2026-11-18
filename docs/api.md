# api.md — HTTP 계약과 외부 API

## 1. 내부 API

모든 응답은 JSON. 에러는 `{"error": {"code": "...", "message": "..."}}` + 적절한 HTTP 상태코드.

### `POST /api/extract`
영수증 이미지 → 추출 필드. 저장하지 않는다.

- 요청: `multipart/form-data`, `image` (jpg/png/heic, 최대 10MB)
- 응답 200:
```json
{
  "receipt": {
    "total_fare": 35000, "meter_fare": 35000, "toll_fee": 0,
    "ride_start": "2026-11-18T14:02:00+09:00",
    "ride_end": "2026-11-18T14:31:00+09:00",
    "night_surcharge": false, "out_of_city_surcharge": false,
    "distance_km": null, "vehicle_no": "서울00가0000",
    "taxi_type": "medium",
    "confidence": {"total_fare": 0.98, "ride_start": 0.91, "vehicle_no": 0.62}
  },
  "low_confidence_fields": ["vehicle_no"]
}
```
- 에러: `IMAGE_UNREADABLE`(422), `LLM_UNAVAILABLE`(503)

### `GET /api/places?q=명동`
장소 검색 프록시. 상위 5개.
```json
{"places": [{"name": "명동역 4호선", "address": "서울 중구 ...", "region": "서울특별시",
             "x": 126.98, "y": 37.56, "tags": []}]}
```
`tags`에는 공항 판별 결과(`ICN_AIRPORT`, `GMP_AIRPORT`)를 넣는다. 판별 규칙: 장소명에 "인천국제공항"/"Incheon International Airport" 포함 또는 공항 기준 좌표 반경 3km 이내 (설정값).

### `POST /api/judge`
- 요청:
```json
{
  "receipt": { ...사용자가 확인·수정한 ReceiptFields... },
  "origin": {"name": "명동역", "x": 126.98, "y": 37.56, "region": "서울특별시", "tags": []},
  "destination": {"name": "김포국제공항", "x": 126.80, "y": 37.56, "region": "서울특별시", "tags": ["GMP_AIRPORT"]},
  "meter_used": true,
  "hailed_via_app": false,
  "user_lang": "en"
}
```
- 응답 200:
```json
{
  "case_id": "c_01J...",
  "verdict": {
    "grade": "CLEAR_EXCESS",
    "upper_bound_won": 30100,
    "charged_won": 480000,
    "ratio": 15.95,
    "reasons": [{"code": "FAR_OVER_UPPER_BOUND", "severity": "clear",
                 "params": {"d_max": 24000, "T": 2400, "day_meter": 29900,
                            "multiplier": 1.0, "meter_upper": 30100,
                            "charged_meter": 480000, "ratio": 15.95}}],
    "rule_version": "seoul-medium-2023-02"
  },
  "route": {"distance_m": 20000, "duration_s": 2100, "toll_won": 0, "kakao_taxi_estimate_won": 27000},
  "report_ko": "…한국어 신고서 전문…",
  "explanation": {"lang": "en", "text": "…"},
  "report_channels": [{"label": "...", "url": "..."}]
}
```
- `report_channels`의 실제 링크는 `open-questions Q6` 확정 전까지 설정 파일에서 읽고, 비어 있으면 생략한다. 링크를 지어내지 않는다.

### `GET /api/cases/{case_id}`
저장된 판정 요약 조회 (이미지·차량번호 없음).

### `GET /api/health`
`{"status": "ok", "kakao": true, "llm": true}` — 키 존재 여부만 확인.

## 2. 외부 API (구현 전에 공식 문서로 필드명을 확인할 것)

### 카카오 로컬 — 키워드로 장소 검색
- `GET https://dapi.kakao.com/v2/local/search/keyword.json?query={q}`
- 헤더: `Authorization: KakaoAK {KAKAO_REST_API_KEY}`
- 사용 필드: `documents[].place_name`, `x`, `y`, `address_name`
- `region`은 `address_name`의 첫 토큰(시·도)으로 만든다. 부족하면 좌표→행정구역 변환 API를 쓴다.

### 카카오모빌리티 — 자동차 길찾기
- `GET https://apis-navi.kakaomobility.com/v1/directions?origin={x},{y}&destination={x},{y}`
- 헤더: `Authorization: KakaoAK {KAKAO_REST_API_KEY}`
- 사용 필드: `routes[0].summary.distance`(m), `duration`(s), `fare.taxi`(원), `fare.toll`(원)
- `routes[0].result_code != 0`이면 `ROUTE_UNAVAILABLE`
- `fare.taxi`는 **참고용으로만** 응답에 싣는다. 심야·시계외 할증 반영 여부가 확인되지 않았기 때문이다 (`open-questions Q5`). 판정은 `fare-rules.md`의 상한으로만 한다.
- 무료 쿼터 안에서는 별도 제휴 없이 쓸 수 있다. 같은 출발·도착 쌍은 메모리 캐시(TTL 1시간).

### 비전 LLM
`docs/llm.md` 참고. 프로바이더는 인터페이스 뒤에 숨긴다:
```python
class VisionLLM(Protocol):
    def extract_receipt(self, image_bytes: bytes, mime: str) -> dict: ...
    def explain(self, verdict_payload: dict, lang: str) -> str: ...
```
