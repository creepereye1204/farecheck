"""평가 실행 (docs/eval.md §2~3).

  python eval/run_eval.py --cases eval/cases --out eval/report.md

- (a) 엔진 단독: 정답 추출 필드로 판정.
- (b) 전체 파이프라인: 영수증 이미지 → 추출(LLM) → 판정. LLM_API_KEY가 없으면 건너뛰고 표에 '미실행'으로 남긴다.
- 경로는 케이스 json의 fixture만 쓴다 (실시간 조회 안 함).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import extractor, report  # noqa: E402
from app.config import fare_rules  # noqa: E402
from app.fare_engine import judge  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.models import Place, ReceiptFields, Route  # noqa: E402

KST = timezone(timedelta(hours=9))


def load_cases(d: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))]


def run_judge(case: dict, receipt: ReceiptFields, llm=None):
    origin, dest = Place(**case["origin"]), Place(**case["destination"])
    route = Route(**case["route"]) if case.get("route") else None
    t0 = time.perf_counter()
    v = judge(receipt, route, origin, dest, fare_rules(),
              meter_used=case["meter_used"], hailed_via_app=case["hailed_via_app"])
    report.build_ko(v, receipt, origin, dest)
    report.summarize(llm, v, route, origin, dest, "en")
    return v, time.perf_counter() - t0


def classify(case: dict, v, extracted: ReceiptFields | None) -> str:
    if extracted is not None:
        truth = case["receipt"]
        for f in ("total_fare", "meter_fare", "toll_fee", "night_surcharge", "out_of_city_surcharge"):
            if getattr(extracted, f) != truth[f]:
                return "추출 오류"
    if v.grade == "UNSUPPORTED" and v.reasons[0].code == "ROUTE_UNAVAILABLE":
        return "경로 오류"
    return "규칙 오류"


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    normals = [r for r in rows if r["expected"] == "NORMAL"]
    clears = [r for r in rows if r["expected"] == "CLEAR_EXCESS"]
    return {
        "n": n,
        "false_accusation": sum(r["actual"] == "CLEAR_EXCESS" for r in normals) / max(1, len(normals)),
        "accuracy": sum(r["expected"] == r["actual"] for r in rows) / max(1, n),
        "clear_recall": sum(r["actual"] == "CLEAR_EXCESS" for r in clears) / max(1, len(clears)),
    }


def p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else float("nan")


def same_minute(a, b: str | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    a = a.astimezone(KST) if a.tzinfo else a.replace(tzinfo=KST)
    return a.strftime("%Y-%m-%d %H:%M") == datetime.fromisoformat(b).astimezone(KST).strftime("%Y-%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "eval" / "cases"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "report.md"))
    ap.add_argument("--skip-pipeline", action="store_true")
    args = ap.parse_args()
    case_dir = Path(args.cases)
    cases = load_cases(case_dir)
    rules = fare_rules()

    # (a) 엔진 단독
    engine_rows = []
    for c in cases:
        v, dt = run_judge(c, ReceiptFields(**c["receipt"]))
        engine_rows.append({"id": c["id"], "expected": c["expected_grade"], "actual": v.grade,
                            "ratio": v.ratio, "codes": [r.code for r in v.reasons], "secs": dt,
                            "cause": None if v.grade == c["expected_grade"] else classify(c, v, None),
                            "reasons_ok": set(c["expected_reasons"]) <= {r.code for r in v.reasons}})

    # (b) 전체 파이프라인
    llm = None if args.skip_pipeline else make_llm()
    pipe_rows, amount_ok, time_ok = [], 0, 0
    if llm is not None:
        for c in cases:
            img = (case_dir / c["image"]).read_bytes()
            t0 = time.perf_counter()
            try:
                ext = extractor.extract(llm, img, "image/png")
            except extractor.ExtractError as e:
                pipe_rows.append({"id": c["id"], "expected": c["expected_grade"], "actual": "ERROR",
                                  "cause": f"추출 오류 ({e.code})", "secs": time.perf_counter() - t0})
                continue
            amount_ok += ext.total_fare == c["receipt"]["total_fare"]
            time_ok += (same_minute(ext.ride_start, c["receipt"]["ride_start"])
                        and same_minute(ext.ride_end, c["receipt"]["ride_end"]))
            v, dt = run_judge(c, ext, llm)
            pipe_rows.append({"id": c["id"], "expected": c["expected_grade"], "actual": v.grade,
                              "secs": time.perf_counter() - t0,
                              "cause": None if v.grade == c["expected_grade"] else classify(c, v, ext)})

    em = metrics(engine_rows)
    pm = metrics(pipe_rows) if pipe_rows else None
    fmt = lambda x: f"{x * 100:.1f}%"  # noqa: E731
    na = "미실행 (LLM_API_KEY 없음)"
    assumed = sum(c.get("route_source") == "assumed" for c in cases)

    pipe_p95 = f"{p95([r['secs'] for r in pipe_rows]):.2f}s (추출+설명 포함)" if pm else na

    def ok(val, cond):
        return f"{val} {'✅' if cond else '❌'}"

    lines = [
        "# eval/report.md — 평가 결과",
        "",
        f"- 실행 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- rule_version: `{rules.version}`",
        f"- 케이스: {len(cases)}건 (정상 {sum(c['expected_grade']=='NORMAL' for c in cases)} / "
        f"확인 필요 {sum(c['expected_grade']=='CHECK_NEEDED' for c in cases)} / "
        f"명백한 초과 {sum(c['expected_grade']=='CLEAR_EXCESS' for c in cases)})",
        f"- 경로 fixture: 카카오 실측 {len(cases) - assumed}건, **가정값 {assumed}건**"
        + (" — 서류 제출용 숫자는 `python eval/make_cases.py --refresh-routes`로 실측 경로로 바꾼 뒤 다시 낼 것"
           if assumed else ""),
        "",
        "## 지표",
        "",
        "| 지표 | 합격선 | (a) 엔진 단독 | (b) 전체 파이프라인 |",
        "|---|---|---|---|",
        f"| 오고발률 (NORMAL→CLEAR_EXCESS) | 0% | {ok(fmt(em['false_accusation']), em['false_accusation'] == 0)} | "
        f"{ok(fmt(pm['false_accusation']), pm['false_accusation'] == 0) if pm else na} |",
        f"| 등급 정확도 | ≥ 90% | {ok(fmt(em['accuracy']), em['accuracy'] >= .9)} | "
        f"{ok(fmt(pm['accuracy']), pm['accuracy'] >= .9) if pm else na} |",
        f"| 명백한 초과 재현율 | ≥ 90% | {ok(fmt(em['clear_recall']), em['clear_recall'] >= .9)} | "
        f"{ok(fmt(pm['clear_recall']), pm['clear_recall'] >= .9) if pm else na} |",
        f"| 금액 추출 정확도 | ≥ 95% | – | "
        f"{ok(fmt(amount_ok / len(cases)), amount_ok / len(cases) >= .95) if pm else na} |",
        f"| 시각 추출 정확도 | ≥ 90% | – | "
        f"{ok(fmt(time_ok / len(cases)), time_ok / len(cases) >= .9) if pm else na} |",
        f"| `/api/judge` 응답 p95 | ≤ 10초 | {p95([r['secs'] for r in engine_rows]) * 1000:.1f}ms "
        f"(LLM 설명 제외) | "
        f"{pipe_p95} |",
        "",
        "## 틀린 케이스",
        "",
    ]
    wrong = [("a", r) for r in engine_rows if r["expected"] != r["actual"]] + \
            [("b", r) for r in pipe_rows if r["expected"] != r["actual"]]
    if wrong:
        lines += ["| 모드 | 케이스 | 기대 | 실제 | 원인 |", "|---|---|---|---|---|"]
        lines += [f"| {m} | {r['id']} | {r['expected']} | {r['actual']} | {r['cause']} |" for m, r in wrong]
    else:
        lines.append("없음.")
    missing_reason = [r["id"] for r in engine_rows if not r["reasons_ok"]]
    lines += ["", f"기대 근거 코드 누락(엔진 단독): {', '.join(missing_reason) if missing_reason else '없음'}", "",
              "## 케이스별 결과 (엔진 단독)", "", "| 케이스 | 기대 | 실제 | ratio | 근거 |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['expected']} | {r['actual']} | {r['ratio']} | {', '.join(r['codes'])} |"
              for r in engine_rows]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:22]))


if __name__ == "__main__":
    main()
