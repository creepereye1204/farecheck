# ARCHITECTURE.md

## 전체 흐름

```
[관광객 모바일 웹]
   │ 1. 영수증 사진 업로드
   ▼
POST /api/extract ──► extractor (비전 LLM) ──► ReceiptFields (+ 필드별 confidence)
   │ 2. 추출 결과를 화면에 보여주고 사용자가 확인·수정
   │    + 출발지/도착지 입력 (장소 검색: GET /api/places)
   ▼
POST /api/judge
   ├─► geo.search_place()  출발지·도착지 좌표 확정 (카카오 로컬)
   ├─► geo.get_route()     도로 거리·소요시간·통행료 (카카오모빌리티)
   ├─► fare_engine.judge() 상한 계산 + 할증 규칙 검사 → Verdict   ← 순수 함수
   ├─► report.build_ko()   한국어 신고서 (템플릿, LLM 미사용)
   ├─► report.summarize()  사용자 언어 설명 (LLM, 번역·풀어쓰기만)
   └─► store.save_case()
   ▼
[결과 화면] 판정 등급 + 근거 + 신고서 복사 버튼 + 신고 경로 안내
```

## 모듈 책임

| 모듈 | 책임 | 외부 호출 | 테스트 방식 |
|---|---|---|---|
| `extractor` | 영수증 이미지 → `ReceiptFields` | 비전 LLM | 고정 응답 가짜 LLM |
| `geo` | 장소 검색, 경로 조회 | 카카오 API | 녹화된 응답(json fixture) |
| `fare_engine` | 요금 상한, 할증 검사, 등급 판정 | **없음** | 순수 단위 테스트 |
| `report` | 신고서 템플릿 채우기, 사용자 언어 요약 | 요약만 LLM | 템플릿은 스냅샷 테스트 |
| `store` | 케이스 저장/조회 | SQLite | 임시 DB |
| `api` | 입력 검증, 모듈 조합, 에러 매핑 | 위 모듈들 | TestClient |

`fare_engine`은 `geo`, `extractor`를 import하지 않는다. 입력은 전부 데이터로 받는다.

## 핵심 데이터 모델 (Pydantic)

```python
class ReceiptFields(BaseModel):
    total_fare: int | None          # 최종 결제 금액(원)
    meter_fare: int | None          # 미터기 요금(원). 영수증에 따로 있으면
    toll_fee: int | None            # 통행료(원)
    ride_start: datetime | None     # 승차 시각
    ride_end: datetime | None       # 하차 시각
    night_surcharge: bool | None    # 심야할증 표시 여부
    out_of_city_surcharge: bool | None  # 시계외할증 표시 여부
    distance_km: float | None       # 영수증에 주행거리가 찍혀 있으면
    vehicle_no: str | None          # 차량번호 (신고서용)
    taxi_type: Literal["medium", "deluxe", "platform", "unknown"]
    confidence: dict[str, float]    # 필드별 0~1

class TripInput(BaseModel):
    receipt: ReceiptFields          # 사용자 확인을 거친 값
    origin: Place                   # 이름 + 좌표
    destination: Place
    meter_used: bool = True         # "미터기를 켜지 않았다" 체크박스
    user_lang: str = "en"           # BCP-47

class Route(BaseModel):
    distance_m: int
    duration_s: int
    toll_won: int
    kakao_taxi_estimate_won: int | None   # 참고용. 판정에 직접 쓰지 않음

class Verdict(BaseModel):
    grade: Literal["NORMAL", "CHECK_NEEDED", "CLEAR_EXCESS", "UNSUPPORTED"]
    upper_bound_won: int            # 이 경로에서 나올 수 있는 최대 정상 요금
    charged_won: int                # 비교 대상 금액
    ratio: float                    # charged / upper_bound
    reasons: list[Reason]           # 등급을 만든 근거들 (코드, 설명용 파라미터)
    rule_version: str               # config/fare_rules.yaml의 version

class Reason(BaseModel):
    code: str                       # 예: "OVER_UPPER_BOUND", "NIGHT_SURCHARGE_IN_DAYTIME"
    severity: Literal["info", "check", "clear"]
    params: dict                    # 템플릿에 들어갈 숫자들
```

등급 이름은 UI 문구와 분리한다. 화면 문구는 `report`에서 언어별로 매핑한다.

## 판정 결과 저장 (POC)

`cases` 테이블 하나. 컬럼: `id`, `created_at`, `origin_name`, `dest_name`, `grade`, `ratio`, `reason_codes`(json), `rule_version`, `user_lang`.
**영수증 이미지와 차량번호는 저장하지 않는다.** 차량번호는 신고서 텍스트에만 들어가고 서버에 남기지 않는다. 본선에서 대시보드를 붙일 때 이 테이블을 집계한다.

## 에러 처리 원칙
- 외부 API 실패 시 판정을 추측으로 채우지 않는다. `UNSUPPORTED` 등급과 이유 코드(`ROUTE_UNAVAILABLE` 등)를 돌려준다.
- 추출 confidence가 낮은 필드는 화면에서 노란색으로 표시하고 사용자 확인을 요구한다 (임계값 0.8, 설정값).
