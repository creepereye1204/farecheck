# fare-rules.md — 요금 규칙과 판정 알고리즘

이 문서가 `fare_engine`의 명세다. 모든 예시는 그대로 단위 테스트가 된다.

## 1. 설계 원칙: "확실한 것만 잡는다"

- 엔진은 **실제 요금을 추정하지 않는다.** 대신 "이 경로에서 정상 미터기로 나올 수 있는 **최대** 요금(상한)"을 계산한다.
- 상한은 일부러 넉넉하게 잡는다. 우회 허용, 시간요금과 거리요금 동시 합산, 불확실한 규칙은 상한이 커지는 쪽 값을 쓴다.
- 대가: 상한 이하로 살짝 더 받은 바가지는 못 잡는다. **의도된 선택이다.** 이 서비스의 목표는 잘못된 신고를 만들지 않으면서, 검증이 어려워 묻히던 명백한 사례를 "검증 가능한 신고"로 만드는 것이다.

## 2. 서울 중형택시 요금표 (출처: 서울시 교통분야 누리집 '서울 택시요금', 2023.2.1 04시 시행분)

| 시간대 | 기본요금 (1.6km까지) | 거리요금 | 시간요금 |
|---|---|---|---|
| 주간 04:00~22:00 | 4,800원 | 131m당 100원 | 30초당 100원 |
| 심야 22:00~23:00, 02:00~04:00 (20%) | 5,800원 | 131m당 120원 | 30초당 120원 |
| 심야 23:00~02:00 (40%) | 6,700원 | 131m당 140원 | 30초당 140원 |

- 시계외할증: 20%
- 심야·시계외 중복할증 상한: 원문 페이지에 40%와 60% 표기가 섞여 있다 → **설정값, 기본 60%** (상한이 커지는 쪽). `open-questions Q3`
- 최종 요금은 십원 단위 반올림
- 시간·거리 병산의 기준 속도(15.10 또는 15.72km/h)는 이 알고리즘에 영향이 없다. 상한 계산에서 시간요금과 거리요금을 둘 다 전 구간에 더하기 때문이다.

## 3. 입력

- `route.distance_m`, `route.duration_s`, `route.toll_won` (카카오모빌리티)
- `receipt` (사용자 확인 완료된 `ReceiptFields`)
- `origin.region`, `destination.region` (카카오 로컬 주소의 시·도. 예: "서울특별시", "인천광역시")
- `destination.tags` (예: `ICN_AIRPORT`, `GMP_AIRPORT`). 공항 판별은 좌표 반경 또는 장소명 매칭, 설정값.
- `meter_used`, `hailed_via_app`

## 4. 상한 계산

```
d_max = route.distance_m × DETOUR_TOLERANCE                 # 기본 1.20

T = (ride_end - ride_start)  if 둘 다 있음
    else route.duration_s × MISSING_TIME_FACTOR             # 기본 2.0

day_meter = 4800
          + ceil(max(0, d_max - 1600) / 131) × 100
          + ceil(T / 30) × 100

night_rate = 승차~하차 구간과 겹치는 시간대 중 가장 높은 할증률 (0, 0.2, 0.4)
             승차·하차 시각이 없으면 0.4로 가정 (상한이 커지는 쪽)

out_rate   = 0.2  if 도착지가 서울 밖이고 시계외할증 예외 목록에 없음
             0    otherwise

multiplier = 1 + min(night_rate + out_rate, COMBINED_CAP)   # 기본 0.6

meter_upper = round10(day_meter × multiplier + ROUNDING_SLACK)   # 기본 200원
```

`ROUNDING_SLACK`은 심야 기본요금이 정확히 1.2배·1.4배가 아닌 차이(5,760원 vs 5,800원 등)를 흡수한다.

## 5. 비교 대상 금액

```
charged_meter = receipt.meter_fare                          if 있음
              = receipt.total_fare - (receipt.toll_fee or 0) otherwise
ratio = charged_meter / meter_upper
```

## 6. 등급 판정

등급은 **(a) 금액 비율**과 **(b) 규칙 위반 근거** 중 더 높은 쪽으로 정한다.

### (a) 금액 비율
| 조건 | 근거 코드 | severity |
|---|---|---|
| ratio ≤ 1.0 | `WITHIN_UPPER_BOUND` | info |
| 1.0 < ratio ≤ CLEAR_RATIO (기본 1.5) | `OVER_UPPER_BOUND` | check |
| ratio > CLEAR_RATIO | `FAR_OVER_UPPER_BOUND` | clear |

### (b) 규칙 근거
| 근거 코드 | 조건 | severity |
|---|---|---|
| `NIGHT_SURCHARGE_IN_DAYTIME` | 영수증에 심야할증 표시 & 승차~하차가 전부 04:05~21:55 안 | clear |
| `OUT_OF_CITY_INSIDE_SEOUL` | 시계외할증 표시 & 출발·도착 모두 서울특별시 | clear |
| `OUT_OF_CITY_TO_EXEMPT` | 시계외할증 표시 & 도착지가 예외 목록(기본: 인천공항) | check (Q4 확인 후 clear로 올릴 수 있음) |
| `TOLL_MISMATCH` | receipt.toll_fee > route.toll_won + TOLL_SLACK (기본 3,000원) | check |
| `METER_NOT_USED` | 사용자가 "미터기를 켜지 않았다" 체크 | check |
| `APP_CALL_FEE_NOT_MODELED` | 앱 호출 탑승 | info (호출료를 상한에 넣지 않았다는 고지) |

### 최종 등급
```
severity 최댓값이 clear  → CLEAR_EXCESS
                 check  → CHECK_NEEDED
                 info   → NORMAL
taxi_type ≠ medium      → UNSUPPORTED (모범·대형·고급 플랫폼은 탄력요금제라 POC 범위 밖)
경로 조회 실패           → UNSUPPORTED (ROUTE_UNAVAILABLE)
금액 없음                → UNSUPPORTED (AMOUNT_MISSING)
```

## 7. 설정 파일 `config/fare_rules.yaml`

```yaml
version: "seoul-medium-2023-02"
base_fare: 4800
base_distance_m: 1600
distance_unit_m: 131
time_unit_s: 30
unit_price: 100
night_tiers:            # [시작, 끝, 할증률]
  - ["22:00", "23:00", 0.2]
  - ["23:00", "02:00", 0.4]
  - ["02:00", "04:00", 0.2]
out_of_city_rate: 0.2
combined_cap: 0.6       # Q3
detour_tolerance: 1.20
missing_time_factor: 2.0
rounding_slack_won: 200
clear_ratio: 1.5
toll_slack_won: 3000
daytime_guard_minutes: 5
out_of_city_exempt_tags: ["ICN_AIRPORT"]   # Q4
out_of_city_exempt_severity: "check"       # Q4
low_confidence_threshold: 0.8
```

## 8. 테스트 예시 (그대로 단위 테스트로 옮길 것)

공통: 경로 거리 10.0km, 실제 소요 30분, 도착지 서울, 통행료 0, 앱 호출 아님.
→ d_max = 12,000m, 거리요금 ceil(10,400/131)=80 → 8,000원, 시간요금 ceil(1,800/30)=60 → 6,000원, day_meter = 18,800원

| # | 승차 | 영수증 | 기대 meter_upper | 기대 ratio | 기대 등급 | 기대 근거 |
|---|---|---|---|---|---|---|
| E1 | 14:00 | 17,000원 | 19,000 | 0.895 | NORMAL | WITHIN_UPPER_BOUND |
| E2 | 14:00 | 24,000원 | 19,000 | 1.263 | CHECK_NEEDED | OVER_UPPER_BOUND |
| E3 | 14:00 | 35,000원 | 19,000 | 1.842 | CLEAR_EXCESS | FAR_OVER_UPPER_BOUND |
| E4 | 23:30 | 30,000원 | 26,520 | 1.131 | CHECK_NEEDED | OVER_UPPER_BOUND |
| E5 | 14:00 | 15,000원, 심야할증 표시 | 19,000 | 0.789 | CLEAR_EXCESS | NIGHT_SURCHARGE_IN_DAYTIME |
| E6 | 14:00 | 17,000원, 시계외할증 표시, 출발·도착 서울 | 19,000 | 0.895 | CLEAR_EXCESS | OUT_OF_CITY_INSIDE_SEOUL |
| E7 | 14:00 | 17,000원, 미터기 미사용 체크 | 19,000 | 0.895 | CHECK_NEEDED | METER_NOT_USED |

E4 계산: 18,800 × 1.4 = 26,320, + 200 = 26,520

추가 시나리오 (시연용, 경로 수치는 가정):
| # | 내용 | 계산 | 기대 등급 |
|---|---|---|---|
| E8 | 명동→김포공항(서울 강서구), 주간, 경로 20km·실제 40분 가정, 480,000원 청구 | d_max 24,000 → 171×100=17,100 / 시간 80×100=8,000 / day_meter 29,900 / upper 30,100 / ratio 15.9 | CLEAR_EXCESS |

E8은 언론에 보도된 일본인 관광객 사례(명동→김포공항 48만원, 평소의 약 16배)를 재현한 것이다. 실제 시연 전에는 카카오 경로 응답으로 수치를 교체한다.

## 9. 판정 근거 노출

`Verdict.reasons`의 `params`에는 사람이 검산할 수 있는 숫자를 모두 넣는다:
`d_max`, `T`, `day_meter`, `multiplier`, `meter_upper`, `charged_meter`, `ratio`, `rule_version`.
신고서에 이 숫자가 그대로 들어가서, 담당 공무원이 계산 과정을 확인할 수 있어야 한다.
