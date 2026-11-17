#!/usr/bin/env python3
"""질문하고 답 받기 (데모, 터미널 대화형)

  python ask.py
  > 상황을 알려주세요: 중고거래로 노트북 산다고 50만원 보냈는데 판매자가 연락이 안 돼요
  > 궁금한 점: 돈 돌려받을 수 있나요?

흐름: 상황 문장 → [AI-06] 사실 추출 → [긴급 카드] → [검색] 근거 문단 → [답변] 근거 안에서만
같은 상황에서 질문을 여러 번 할 수 있고, 빈 줄을 입력하면 새 상황으로 넘어갑니다. 끝내려면 Ctrl+C.
키 없이 흐름만 보려면 RAG_FAKE=1.
"""
import json

from rag.explain import ExplainError
from rag.extract import extract_facts
from rag.index import load
from rag.qa import answer
from rag.retrieve import find_references

LABEL = {"transferredMoney": "돈을 보냄", "contactContinues": "연락 이어짐",
         "sharedInformation": "정보 넘김", "installedApp": "앱 설치"}
# 데모용 긴급 카드. 실제로는 Spring response-cards YAML(검토된 연락처)에서 가져와요.
URGENT = {
    "transferredMoney": "돈을 보낸 금융회사 고객센터 또는 112에 바로 지급정지를 요청하세요.",
    "installedApp": "휴대폰 전원을 끄거나 비행기 모드로 바꾸고, 다른 전화로 신고하세요.",
    "sharedInformation": "금융거래 비밀번호를 바꾸고 개인정보노출자 등록·안심차단을 신청하세요.",
}


def main():
    _, all_refs, _ = load()
    meta = {r["code"]: r for r in all_refs}
    print("금사빠 RAG 데모 · 끝내려면 Ctrl+C\n")
    while True:
        desc = input("상황을 알려주세요: ").strip()
        if not desc:
            continue
        try:
            ex = extract_facts(desc)
        except Exception as e:
            print(f"  ! 사실 추출 실패: {e}\n")
            continue
        facts, ft = ex["facts"], ex["fraudType"]
        print("\n[AI-06 사실 추출]", ", ".join(f"{LABEL[k]}={v}" for k, v in facts.items()), f"| 유형 추정={ft}")

        urgent = [URGENT[k] for k, v in facts.items() if v == "YES" and k in URGENT]
        if ft == "SECONDHAND_TRADE" and facts["transferredMoney"] == "YES":
            # 중고거래는 환급법 대상이 아니라 법정 지급정지가 보장되지 않음
            urgent[urgent.index(URGENT["transferredMoney"])] = (
                "송금한 금융회사에 사기 피해 사실을 알리고, 경찰 사이버범죄 신고를 하세요. 대화·입금 내역은 지우지 말고 보관하세요.")
        if urgent:
            print("\n[긴급 대응 카드 - AI 답변보다 먼저 표시]")
            for u in urgent:
                print("  ⚠", u)

        refs = find_references(facts, ft, k=5)
        if not refs:
            print("\n확정된 피해 사실이 없어요. 실제 서비스에서는 추가 질문(AI-01)을 먼저 해요.\n")
            continue
        print("\n[찾은 근거]")
        for r in refs:
            print("  -", r["title"])

        while True:
            q = input("\n궁금한 점 (빈 줄이면 새 상황): ").strip()
            if not q:
                print()
                break
            try:
                res = answer(q, facts, refs, ft)
            except (ExplainError, ValueError) as e:
                print(f"  ! 답변 실패({e}). 실제 서비스에서는 대응 카드만 보여줘요.")
                continue
            print("\n[답변]\n" + res["answer"])
            if res["referenceCodes"]:
                print("\n[출처]")
                for c in res["referenceCodes"]:
                    m = meta.get(c, {})
                    print(f"  - {m.get('title', c)} ({m.get('authority') or '기관 미상'}, {m.get('published_at') or '날짜 미상'})")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n종료")
