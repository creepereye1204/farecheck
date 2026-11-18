# AGENTS.md — 바가지 판독기 (codename: farecheck)

이 저장소는 **하네스 엔지니어링** 방식으로 진행한다. 이 디렉터리의 문서가 유일한 진실의 원천이고, 코드는 이 문서를 읽은 에이전트가 작성한다.

## 한 줄 정의
외국인 관광객이 택시 영수증과 승하차 정보를 올리면, **결정론적 요금 엔진**이 정상 요금 상한을 계산해 "정상 / 확인 필요 / 명백한 초과"로 판정하고, 담당 공무원이 바로 검토할 수 있는 **한국어 신고서**를 만들어 준다.

## 읽는 순서
1. `docs/product.md` — 왜 만드는가, 누가 쓰는가, POC 범위
2. `ARCHITECTURE.md` — 모듈 구조와 데이터 흐름
3. `docs/fare-rules.md` — 요금 규칙과 판정 알고리즘 (가장 중요)
4. `docs/api.md` — HTTP 계약
5. `docs/llm.md` — LLM을 쓰는 두 지점의 스펙
6. `docs/eval.md` — 완료 기준과 평가셋
7. `docs/open-questions.md` — 아직 정해지지 않은 것

## 절대 규칙
1. **판정은 코드가 한다. LLM은 판정하지 않는다.** LLM의 역할은 (a) 영수증 이미지에서 필드 추출, (b) 판정 결과를 사용자 언어로 풀어 쓰기, 두 가지뿐이다. 요금 계산, 임계값 비교, 할증 규칙 검사는 전부 `fare_engine`의 순수 함수로 구현한다.
2. **"사기", "fraud", "scam", "범죄" 같은 단정 표현을 출력하지 않는다.** 최고 등급도 "명백한 초과(Clear excess)"까지만 쓴다. 최종 판단은 담당 공무원 몫이다.
3. **추출 결과는 반드시 사용자 확인을 거친다.** OCR 오류가 기사에 대한 잘못된 신고로 이어지면 안 된다. `/api/extract` → 사용자 확인/수정 → `/api/judge` 2단계를 지킨다.
4. **POC 범위 밖 기능을 만들지 않는다.** 쇼핑·숙박 바가지, 재단 대시보드, 회원가입, 실제 신고 자동 접수는 범위 밖이다 (`docs/product.md` 참고).
5. **불확실한 규칙은 하드코딩하지 말고 설정값으로 뺀다.** `docs/open-questions.md`에 있는 항목은 `config/fare_rules.yaml`에서 바꿀 수 있어야 한다.
6. 문서와 다르게 구현해야 할 이유가 생기면, 임의로 바꾸지 말고 `docs/open-questions.md`에 항목을 추가하고 멈춘다.

## 기술 스택
- Python 3.11+, FastAPI, Pydantic v2, httpx
- 저장소: SQLite (SQLModel 또는 sqlite3 직접 사용)
- 프론트: FastAPI가 서빙하는 단일 모바일 웹 페이지 (바닐라 HTML/JS, 빌드 도구 없음)
- 테스트: pytest
- 외부 API: 카카오 로컬(장소 검색), 카카오모빌리티 자동차 길찾기, 비전 LLM 1종

## 디렉터리 구조
```
farecheck/
├── AGENTS.md
├── ARCHITECTURE.md
├── docs/
├── config/
│   └── fare_rules.yaml        # 요금표·임계값·예외 규칙
├── app/
│   ├── main.py                # FastAPI 앱, 라우터 등록, 정적 파일 서빙
│   ├── api/                   # 라우터 (extract, judge, cases)
│   ├── fare_engine/           # 순수 함수만. 외부 호출 금지
│   ├── extractor/             # 영수증 → ReceiptFields (LLM)
│   ├── geo/                   # 장소 검색, 경로 조회 (카카오)
│   ├── report/                # 한국어 신고서(템플릿), 사용자 언어 요약(LLM)
│   ├── store/                 # SQLite 저장
│   └── static/                # index.html, app.js, style.css
├── eval/
│   ├── cases/                 # 평가 케이스 (json + 이미지)
│   └── run_eval.py
└── tests/
```

## 환경 변수
```
KAKAO_REST_API_KEY=        # 카카오 로컬 + 카카오모빌리티 공용
LLM_PROVIDER=              # anthropic | openai  (open-questions Q1)
LLM_MODEL=                 # 비전 지원 모델 ID
LLM_API_KEY=
DB_PATH=./farecheck.db
```
키가 없으면 앱은 뜨되, 해당 기능 호출 시 명확한 에러를 낸다. 키를 코드나 로그에 남기지 않는다.

## 작업 단위와 완료 기준
- 각 모듈은 단위 테스트와 함께 커밋한다. `fare_engine`은 `docs/fare-rules.md`의 모든 예시를 테스트 케이스로 옮긴다.
- 외부 API는 인터페이스 뒤에 숨기고, 테스트에서는 가짜 구현을 쓴다.
- POC 완료 기준은 `docs/eval.md`의 합격선이다.
