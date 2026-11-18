# open-questions.md — 아직 정해지지 않은 것

구현 중 막히면 여기에 추가하고 멈춘다. 결정되면 항목을 지우지 말고 `결정:` 줄을 붙인다.

| # | 질문 | 영향 범위 | 임시 처리 |
|---|---|---|---|
| Q1 | 비전 LLM 프로바이더·모델 | extractor, summarize | `LLM_PROVIDER`/`LLM_MODEL` 환경변수. 인터페이스 2개 구현 가능하게. 현재 anthropic만 구현, openai는 `VisionLLM` 구현만 추가하면 됨 |
| Q2 | 공모전 규정상 사전 개발 코드 사용 가능 여부 (본선 24시간 규칙) | 전체 | POC는 서류용 증거로만 쓰고, 금지라면 본선에서 문서로 재구현 |
| Q3 | 서울 중형택시 심야·시계외 중복할증 상한이 40%인지 60%인지 (서울시 요금 페이지에 두 표기 혼재) | fare_engine 상한 | `combined_cap: 0.6` (상한이 커지는 쪽) |
| Q4 | 서울→인천공항 운행에 시계외할증이 붙지 않는다는 규칙이 지금도 유효한지 (근거: 2022년 서울시 단속 보도자료) | `OUT_OF_CITY_TO_EXEMPT` 등급 | severity를 check로 두고, 확인되면 clear로 상향 |
| Q5 | 카카오모빌리티 길찾기 `fare.taxi`에 심야·시계외 할증이 반영되는지 | 교차참고 표시 | 판정에 쓰지 않음, 참고값으로만 노출 |
| Q6 | 외국인이 쓸 공식 신고 경로 링크 (서울시 택시 QR 신고, 120다산콜재단 외국어 상담) | report_channels | 설정값 비워두고 확인 후 입력. 링크를 지어내지 않는다 |
| Q7 | `detour_tolerance 1.2`, `clear_ratio 1.5` 적정성 | 등급 | 평가셋 결과로 보정. 오고발률 0%를 지키는 범위에서만 낮춘다 |
| Q8 | 앱 호출(우버·카카오T) 탑승의 호출료 범위 | 상한 | POC는 상한에 미반영 + `APP_CALL_FEE_NOT_MODELED` 고지 |
| Q9 | 평가셋 경로 fixture가 실측이 아님 (카카오 키 없음). 전체 파이프라인(LLM 추출) 평가도 미실행 | eval/report.md 숫자 | `route_source: "assumed"`로 표시. 키 확보 후 `python eval/make_cases.py --refresh-routes` → `run_eval.py` 재실행 |
