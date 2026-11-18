"""피해 대응(긴급대응) AI 로직. 실제 검색·생성은 rag/ 모듈이 하고, 여기서는 API 규칙을 맞춰요.

- AI-06 run_extract : 상황 설명 → 사실 4개 + 확인 안 된 항목 (+ 추정 사기 유형)
- AI-07 run_explain : 사실 + 검토된 근거 → 대응이 필요한 이유 설명
- 질문 답변 run_answer : 사실 + 질문 → 근거 안에서 답 / 꼭 필요한 사실을 모르면 되묻기 / 범위 밖이면 답변 불가

Gemini 실패(과부하·시간 초과)는 AiUnavailable 로 올려서 라우터가 503 을 돌려줘요.
Spring 은 503 이면 AI 설명 없이 대응 카드만 보여 주면 돼요.
"""
from __future__ import annotations

import re

from app.guard.sensitive import mask_sensitive
from rag.explain import ExplainError, explain
from rag.extract import extract_facts
from rag.guard import has_forbidden
from rag.qa import answer as rag_answer
from rag.retrieve import find_by_question, find_references


class AiUnavailable(Exception):
    """AI(Gemini) 호출 실패 또는 근거 인덱스 없음 → 503"""


# 금융 상품 추천 등 피해 대응 범위 밖 질문 (답변 불가)
OUT_OF_SCOPE = re.compile(r"(주식|코인|종목|가상자산|비트코인).{0,8}(추천|사도|살까|매수|오를)|투자.{0,6}(추천|어디)|대출.{0,6}(추천|받고 싶|어디서)|로또|도박")

# 질문이 어떤 사실에 달려 있는지. 그 사실이 UNKNOWN 이면 답하기 전에 먼저 되물어요.
TOPIC = [
    ("transferredMoney", re.compile(r"돌려받|환급|환불|지급정지|피해구제|돈을? ?(찾|회수)")),
    ("sharedInformation", re.compile(r"개인정보|비밀번호|인증번호|신분증|OTP|명의|계좌번호.{0,4}(알려|줬|넘)", re.I)),
    ("installedApp", re.compile(r"앱|어플|apk|원격|악성|휴대폰.{0,4}(초기화|점검)", re.I)),
]
CLARIFY = {
    "transferredMoney": "답을 드리기 전에 하나만 확인할게요. 상대에게 이미 돈을 보내셨나요?",
    "sharedInformation": "답을 드리기 전에 하나만 확인할게요. 신분증, 비밀번호, 인증번호 같은 정보를 상대에게 알려 주셨나요?",
    "installedApp": "답을 드리기 전에 하나만 확인할게요. 상대가 알려 준 앱을 설치하셨나요?",
}


def _facts_dict(facts) -> dict:
    return facts.model_dump(by_alias=True) if hasattr(facts, "model_dump") else dict(facts)


def run_extract(description: str) -> dict:
    masked, _ = mask_sensitive(description)
    try:
        return extract_facts(masked)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001  Gemini 503·429·시간 초과 등
        raise AiUnavailable(f"사실 추출 실패: {type(e).__name__}") from e


def run_explain(facts, verified_references: list[dict]) -> dict:
    try:
        return explain(_facts_dict(facts), verified_references)
    except ExplainError as e:
        raise AiUnavailable(str(e)) from e


def _references(facts: dict, question: str, fraud_type: str | None) -> list[dict]:
    # 사실로 찾은 근거(상황에 맞는 대응)와 질문으로 찾은 근거(묻는 내용: 신고 후 절차, 배상명령 등)를 섞어요.
    try:
        by_facts = find_references(facts, fraud_type, k=4)
        by_question = find_by_question(question, fraud_type, k=4)
        refs, seen = [], set()
        for r in by_question[:3] + by_facts + by_question[3:]:
            if r["code"] not in seen:
                seen.add(r["code"])
                refs.append(r)
        return refs[:6]
    except SystemExit as e:  # rag.index.load: 인덱스 없음·임베딩 설정 불일치
        raise AiUnavailable(str(e)) from e
    except Exception as e:  # noqa: BLE001  임베딩 호출 실패
        raise AiUnavailable(f"근거 검색 실패: {type(e).__name__}") from e


def run_answer(facts, question: str, fraud_type: str | None = None,
               verified_references: list[dict] | None = None) -> dict:
    facts = _facts_dict(facts)
    q, _ = mask_sensitive(question.strip())
    unable = {"status": "UNABLE_TO_ANSWER", "answer": None, "referenceCodes": [], "references": [],
              "clarifyFactKey": None, "clarifyQuestion": None}

    if OUT_OF_SCOPE.search(q):
        return unable

    for key, pattern in TOPIC:
        if pattern.search(q) and facts.get(key) == "UNKNOWN":
            return {**unable, "status": "NEED_USER_INPUT", "clarifyFactKey": key, "clarifyQuestion": CLARIFY[key]}

    refs = verified_references if verified_references is not None else _references(facts, q, fraud_type)
    if not refs:
        return unable
    try:
        res = rag_answer(q, facts, refs, fraud_type)
    except ExplainError as e:
        raise AiUnavailable(str(e)) from e
    if has_forbidden(res["answer"]):  # rag_answer 가 이미 검사하지만 한 번 더
        raise AiUnavailable("답변에 연락처·링크 등 금지 표현이 있어요.")
    return {"status": "ANSWERED", "answer": res["answer"], "referenceCodes": res["referenceCodes"],
            "references": refs, "clarifyFactKey": None, "clarifyQuestion": None}
