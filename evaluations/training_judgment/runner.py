"""AI 시뮬레이션 판정 정확도 평가.

사기범 대사 하나와 사용자 답 하나를 실제 훈련 처리(run_training_turn: 가림 → LLM 판정 → 서버 보정)에 넣고,
판정(safe/risky/neutral)이 정답과 맞는지 셉니다.

실행 (WSID_AI 폴더, venv 켠 상태):
    python -m evaluations.training_judgment.runner --list           # 문항 목록만
    python -m evaluations.training_judgment.runner --dry            # 연결 확인 (mock, 비용 없음, 점수 의미 없음)
    python -m evaluations.training_judgment.runner                  # 실제 LLM으로 전체 (비용 발생)
    python -m evaluations.training_judgment.runner --case risky-mixed --case safe-stop
    python -m evaluations.training_judgment.runner --repeats 3      # 같은 문항을 3번씩 (판정이 흔들리는지)
    python -m evaluations.training_judgment.runner --hard --list    # 어려운 문항(정답 초안·반대 의견) 보기
    python -m evaluations.training_judgment.runner --hard           # 어려운 문항으로 평가
"""
import argparse
import copy
import json
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app.llm import llm_client
from app.scenario_data import FALLBACK_SCENARIOS
from app.services import training_turn

HERE = Path(__file__).parent
LABELS = ("safe", "risky", "neutral")
KO = {"safe": "안전", "risky": "위험", "neutral": "중립"}


def load_cases(selected: list[str] | None, hard: bool = False) -> list[dict]:
    cases = json.loads((HERE / ("cases_hard.json" if hard else "cases.json")).read_text(encoding="utf-8"))["cases"]
    if selected:
        cases = [c for c in cases if c["id"] in selected]
        missing = set(selected) - {c["id"] for c in cases}
        if missing:
            raise SystemExit(f"없는 문항: {', '.join(sorted(missing))}")
    return cases


def judge(case: dict) -> dict:
    scenario = copy.deepcopy(FALLBACK_SCENARIOS[case["scenario"]]["scenario"])
    history = [{"role": "persona", "text": case["persona"]}, {"role": "user", "text": case["user"]}]
    started = time.monotonic()
    result = training_turn.run_training_turn("eval", scenario, case["scene"], history)
    judgment = result.get("judgment") or {}
    return {"outcome": judgment.get("outcome"), "reason": judgment.get("reason", ""),
            "provided": result.get("infoDisclosure", {}).get("provided", []),
            "fallback": result.get("fallbackUsed", False), "seconds": round(time.monotonic() - started, 1)}


def main():
    parser = argparse.ArgumentParser(description="AI 시뮬레이션 판정 정확도 평가")
    parser.add_argument("--list", action="store_true", help="문항 목록만 보기")
    parser.add_argument("--dry", action="store_true", help="mock LLM 으로 연결만 확인 (비용 없음)")
    parser.add_argument("--case", action="append", help="특정 문항만 (여러 번 쓸 수 있음)")
    parser.add_argument("--repeats", type=int, default=1, help="문항마다 반복 횟수")
    parser.add_argument("--hard", action="store_true", help="판정이 갈릴 수 있는 어려운 문항(cases_hard.json)으로")
    args = parser.parse_args()
    cases = load_cases(args.case, args.hard)

    if args.list:
        for c in cases:
            print(f"{c['id']:24} 정답={KO[c['expected']]:2}  [{c['scenario']} {c['scene']}장면] {c['user']}")
            if c.get("debate"):
                print(f"{'':24} 이유: {c['why']} / 반대 의견: {c['debate']}")
        print(f"\n총 {len(cases)}문항: " + ", ".join(f"{KO[k]} {v}" for k, v in Counter(c['expected'] for c in cases).items()))
        return

    if args.dry:
        llm_client.settings = replace(llm_client.settings, llm_provider="mock")
    provider = llm_client.settings.llm_provider
    print(f"LLM: {provider} / {llm_client.settings.llm_model or '-'}"
          + ("  (mock 이라 점수는 의미 없어요)" if provider == "mock" else f"  (호출 약 {len(cases) * args.repeats}번, 비용 발생)"))

    rows = []
    for case in cases:
        for attempt in range(args.repeats):
            got = judge(case)
            ok = got["outcome"] == case["expected"]
            rows.append({**case, "attempt": attempt + 1, **{f"got_{k}": v for k, v in got.items()}, "ok": ok})
            mark = "O" if ok else "X"
            print(f"[{mark}] {case['id']:24} 정답={KO[case['expected']]} 판정={KO.get(got['outcome'], got['outcome'])}"
                  f"  {got['seconds']}초" + ("  (기본값)" if got["fallback"] else ""))

    # 요약: 전체 정확도, 정답별 맞힌 비율, 헷갈린 표, 틀린 문항
    total = len(rows)
    correct = sum(r["ok"] for r in rows)
    matrix = defaultdict(Counter)
    for r in rows:
        matrix[r["expected"]][r["got_outcome"]] += 1
    lines = [f"# 판정 정확도 평가{' (어려운 문항)' if args.hard else ''} ({datetime.now().strftime('%Y-%m-%d %H:%M')})", "",
             f"- LLM: {provider} / {llm_client.settings.llm_model or '-'}",
             f"- 전체 정확도: **{correct}/{total} = {correct / total:.0%}**", ""]
    lines += ["| 정답 \\ 판정 | 안전 | 위험 | 중립 | 맞힌 비율 |", "|---|---|---|---|---|"]
    for label in LABELS:
        row = matrix[label]
        n = sum(row.values())
        if n:
            lines.append(f"| {KO[label]} | {row['safe']} | {row['risky']} | {row['neutral']} | {row[label] / n:.0%} |")
    misses = [r for r in rows if not r["ok"]]
    if misses:
        lines += ["", "## 틀린 문항", ""]
        for r in misses:
            lines.append(f"- `{r['id']}` 정답 {KO[r['expected']]} → 판정 {KO.get(r['got_outcome'], r['got_outcome'])}: "
                         f"\"{r['user']}\" (이유: {r['got_reason'] or '-'})")
    if args.repeats > 1:
        unstable = [cid for cid, outs in _by_case(rows).items() if len(set(outs)) > 1]
        lines += ["", f"- 반복할 때 판정이 바뀐 문항: {', '.join(unstable) if unstable else '없음'}"]
    summary = "\n".join(lines)
    print("\n" + summary)

    out = Path("evaluation-results") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-training-judgment")
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(summary + "\n", encoding="utf-8")
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out}")


def _by_case(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["id"]].append(r["got_outcome"])
    return grouped


if __name__ == "__main__":
    main()
