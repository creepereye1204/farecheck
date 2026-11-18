# llm.md — LLM을 쓰는 두 지점

LLM은 **입력을 구조화하고, 결과를 풀어 쓰는 일만** 한다. 금액 계산, 비교, 등급 결정에 LLM 출력을 쓰지 않는다.

## 1. 영수증 추출 (`extractor`)

### 입력
영수증 사진 1장. 대상 형식:
- 2025년 12월 이후 서울 택시 결제기 영수증 (한·영 병기, 최종 요금, 승하차 시각, 심야·시계외 할증 여부 표시)
- 그 이전 한글 전용 영수증
- 앱 결제 화면 캡처 (우버, 카카오T 등)

### 시스템 프롬프트 (요지 — 이 의도를 지키면 문구는 바꿔도 된다)
```
You read Korean taxi receipts and return JSON only.
Rules:
- Return exactly the schema below. No prose.
- Never guess. If a field is not printed or not legible, return null and set its confidence ≤ 0.3.
- Amounts are integers in KRW without separators.
- Times are ISO 8601 with +09:00. If only time is printed, use the date printed elsewhere on the receipt; if no date, return null.
- night_surcharge / out_of_city_surcharge: true only if the receipt explicitly marks it (e.g. 심야할증, 시계외할증, Night surcharge, Out-of-city). false if the receipt has a surcharge section showing none. null if the receipt has no such section.
- taxi_type: "medium" for regular taxis, "deluxe" for 모범/대형, "platform" for premium app rides (e.g. black/luxury), else "unknown".
- confidence: your confidence 0..1 for each non-null field.
```

### 출력 스키마
`ARCHITECTURE.md`의 `ReceiptFields`와 동일. Pydantic으로 검증하고, 검증 실패 시 1회 재시도, 그래도 실패하면 `IMAGE_UNREADABLE`.

### 후처리 (코드)
- `meter_fare + toll_fee`와 `total_fare` 차이가 100원 넘으면 세 필드 confidence를 0.5로 낮춘다.
- `ride_end < ride_start`이면 다음 날로 넘긴 것으로 보정하고 confidence를 0.6으로 낮춘다.

## 2. 사용자 언어 설명 (`report.summarize`)

### 입력
`Verdict` + `Route` + 출발/도착 이름을 JSON으로 그대로 넘긴다.

### 시스템 프롬프트 (요지)
```
Explain a taxi fare check result to a foreign tourist in {lang}.
- Use only the numbers in the JSON. Do not add facts, laws, or amounts.
- Never use words like "fraud", "scam", "crime", "illegal". The highest grade is "clearly above the normal range".
- 4 sentences max: result, why (main reason with numbers), what to do next, a note that a city officer makes the final decision.
- Plain, calm tone.
```

### 실패 처리
LLM 실패 시 언어별 고정 템플릿 문장으로 대체한다 (영어 템플릿은 필수로 둔다). 설명이 없어도 판정과 신고서는 나간다.

## 3. 한국어 신고서 (`report.build_ko`) — LLM 미사용

공무원에게 그대로 전달되는 문서라 **템플릿으로만** 만든다. Jinja2 템플릿 예:

```
[외국인 관광객 택시 요금 확인 요청]

1. 탑승 정보
 - 승차: {{ride_start}} / 하차: {{ride_end}}
 - 출발: {{origin_name}} ({{origin_address}})
 - 도착: {{dest_name}} ({{dest_address}})
 - 차량번호: {{vehicle_no or "확인 불가"}}
 - 결제 금액: {{total_fare}}원 (미터기 {{meter_fare}}원, 통행료 {{toll_fee}}원)

2. 정상 요금 상한 계산 (서울 중형택시 요금표, {{rule_version}})
 - 도로 경로 거리 {{distance_m}}m, 우회 허용 {{detour_tolerance}}배 → {{d_max}}m
 - 실제 소요시간 {{T}}초
 - 기본·거리·시간 요금 합산(주간 기준) {{day_meter}}원 × 할증 {{multiplier}} = 상한 {{meter_upper}}원
 - 시간요금과 거리요금을 전 구간에 모두 더한 값으로, 실제 미터기 요금보다 높게 잡은 보수적 상한입니다.

3. 확인 결과: {{grade_ko}}
 - 청구 미터기 요금 {{charged_meter}}원은 상한의 {{ratio}}배입니다.
{% for r in reasons %} - {{r.text_ko}}
{% endfor %}
4. 비고
 - 본 문서는 자동 계산 결과이며, 위반 여부의 최종 판단은 담당 기관에 있습니다.
 - 영수증 원본은 신고인이 보관하고 있습니다.
```

`grade_ko` 매핑: NORMAL → "정상 범위", CHECK_NEEDED → "확인 필요", CLEAR_EXCESS → "명백한 초과".
각 근거 코드의 `text_ko` 문구는 `report/templates/reasons_ko.yaml`에 둔다.
