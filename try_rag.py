#!/usr/bin/env python3
"""RAG 한 번 돌려보기 (서버 없이). AI-07 이 받을 요청과 돌려줄 응답을 그대로 출력합니다.

예)
  python try_rag.py --money YES --app UNKNOWN
  python try_rag.py --info YES --type INVESTMENT_FRAUD
  python try_rag.py --money YES --type SECONDHAND_TRADE --debug
키 없이 흐름만 보려면 앞에 RAG_FAKE=1 을 붙이세요 (Windows PowerShell: $env:RAG_FAKE=1).
"""
import argparse
import json

from rag.explain import ExplainError, explain
from rag.retrieve import find_references

ap = argparse.ArgumentParser()
ap.add_argument("--money", choices=["YES", "NO", "UNKNOWN"], help="transferredMoney: 돈을 보냈나")
ap.add_argument("--contact", choices=["YES", "NO", "UNKNOWN"], help="contactContinues: 연락이 이어지나")
ap.add_argument("--info", choices=["YES", "NO", "UNKNOWN"], help="sharedInformation: 정보를 넘겼나")
ap.add_argument("--app", choices=["YES", "NO", "UNKNOWN"], help="installedApp: 앱을 설치했나")
ap.add_argument("--type", help="사기 유형 코드 (예: SECONDHAND_TRADE)")
ap.add_argument("-k", type=int, default=4, help="근거 문단 수 (최대 10)")
ap.add_argument("--debug", action="store_true", help="검색 점수·검토 여부도 표시")
a = ap.parse_args()

facts = {k: v for k, v in {
    "transferredMoney": a.money, "contactContinues": a.contact,
    "sharedInformation": a.info, "installedApp": a.app}.items() if v}
if not facts:
    ap.error("사실을 하나 이상 넣어 주세요. 예: --money YES")

refs = find_references(facts, a.type, k=a.k, debug=a.debug)
request = {"facts": facts, "verifiedReferences": [{k: r[k] for k in ("code", "title", "excerpt")} for r in refs]}
print("=== AI-07 요청 (Spring → AI) ===")
print(json.dumps(request if not a.debug else {**request, "_debug": [
    {"code": r["code"], "score": r["_score"], "reviewed": r["_reviewed"]} for r in refs]}, ensure_ascii=False, indent=2))

if not refs:
    print("\n확정된 피해 사실이 없거나 맞는 근거가 없어요. Spring 정책상 이 경우 AI-07 을 부르지 않고 추가 질문을 해요.")
    raise SystemExit(0)
try:
    result = explain(facts, request["verifiedReferences"])
    print("\n=== AI-07 응답 (AI → Spring) ===")
    print(json.dumps({"success": True, "data": result}, ensure_ascii=False, indent=2))
except ExplainError as e:
    print(f"\n=== AI-07 실패 ({e}) → Spring 은 설명 없이 대응 카드만 보여줌 ===")
    print(json.dumps({"success": False}, ensure_ascii=False))
