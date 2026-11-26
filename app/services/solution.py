"""피해 대응(긴급대응) AI 로직. 실제 검색·생성은 rag/ 모듈이 하고, 여기서는 API 규칙을 맞춰요.

- AI-06 run_extract : 상황 설명 → 사실 4개 + 확인 안 된 항목 (+ 추정 사기 유형)
- AI-07 run_explain : 사실 + 검토된 근거 → 대응이 필요한 이유 설명
- 질문 답변 run_answer : 사실 + 질문 → 근거 안에서 답 / 꼭 필요한 사실을 모르면 되묻기 / 범위 밖이면 답변 불가

OpenAI 실패(과부하·시간 초과)는 AiUnavailable 로 올려서 라우터가 503 을 돌려줘요.
Spring 은 503 이면 AI 설명 없이 대응 카드만 보여 주면 돼요.
"""
from __future__ import annotations

import re

from app.guard.sensitive import mask_sensitive
from rag.explain import ExplainError, explain
from rag.extract import extract_facts
from rag.guard import has_forbidden
from rag.qa import answer as rag_answer
from rag.retrieve import find_by_question, find_references, references_by_code


class AiUnavailable(Exception):
    """AI(OpenAI) 호출 실패 또는 근거 인덱스 없음 → 503"""


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
    except Exception as e:  # noqa: BLE001  OpenAI 503·429·시간 초과 등
        raise AiUnavailable(f"사실 추출 실패: {type(e).__name__}") from e


def run_explain(facts, verified_references: list[dict]) -> dict:
    try:
        return explain(_facts_dict(facts), verified_references)
    except ExplainError as e:
        raise AiUnavailable(str(e)) from e


# 질문 주제별로 꼭 넣을 근거. 임베딩 검색만으로는 '중고거래' 같은 유형 단어에 끌려가서
# 정작 묻는 내용(민사 절차, 신고 이후 등)의 근거를 놓치는 경우가 있어서 주제를 먼저 잡아요.
TOPIC_REFS = [
    (re.compile(r"(더|또|추가로?) ?(돈|입금|송금|보내)|수수료|보증금|선입금|환불.{0,6}(하려면|조건|먼저)"),
     ["REF_FSS_2024_28_ALERT_V1_P1"]),
    (re.compile(r"고소|형사|어디(에|로|서)? ?(신고|접수|가)|경찰서|접수 ?(방법|절차)"),
     ["REF_EASYLAW_COMPLAINT_V1_P1", "REF_EASYLAW_SECONDHAND_REPORT_V1_P1"]),
    (re.compile(r"민사|소송|소장|전자소송|인지|송달료|비용|소액|지급명령|손해배상"),
     ["REF_EASYLAW_CIVIL_FILING_V1_P1", "REF_EASYLAW_SECONDHAND_CIVIL_V1_P1",
      "REF_EASYLAW_SMALL_CLAIMS_V1_P1", "REF_EASYLAW_PAYMENT_ORDER_V1_P1"]),
    (re.compile(r"배상|합의|돌려받|되찾|회수|피해 ?회복"),
     ["REF_EASYLAW_VICTIM_RECOVERY_V1_P1", "REF_EASYLAW_COMPENSATION_ORDER_V1_P1", "REF_EASYLAW_SECONDHAND_CIVIL_V1_P1"]),
    (re.compile(r"신고 ?(했|하고|한 ?뒤|후|다음|이후)|접수 ?(했|후)|수사|진행 ?상황|결과"),
     ["REF_EASYLAW_VICTIM_INFO_V1_P1", "REF_EASYLAW_VICTIM_RECOVERY_V1_P1"]),
]
# 중고거래인데 환급·지급정지를 묻지 않았으면 '환급 대상 아님' 문단은 빼요 (매 답변마다 같은 말을 반복하지 않게)
REFUND_TOPIC = re.compile(r"환급|지급정지|피해구제|돌려받|되찾|환불")
REFUND_NOTICE_CODES = {"REF_LAW_REFUND_PROCEDURE_V1_P2", "REF_FSS_2026_07_NONFACE_V1_P1"}


def _references(facts: dict, question: str, fraud_type: str | None) -> list[dict]:
    # 사실로 찾은 근거(상황에 맞는 대응)와 질문으로 찾은 근거(묻는 내용: 신고 후 절차, 배상명령 등)를 섞어요.
    try:
        by_topic = references_by_code([c for pattern, codes in TOPIC_REFS if pattern.search(question) for c in codes])
        by_question = find_by_question(question, fraud_type, k=4)
        by_facts = find_references(facts, fraud_type, k=4)
        refs, seen = [], set()
        for r in by_topic + by_question[:3] + by_facts + by_question[3:]:
            if r["code"] in seen:
                continue
            if fraud_type == "SECONDHAND_TRADE" and r["code"] in REFUND_NOTICE_CODES and not REFUND_TOPIC.search(question):
                continue
            seen.add(r["code"])
            refs.append(r)
        return refs[:7]
    except SystemExit as e:  # rag.index.load: 인덱스 없음·임베딩 설정 불일치
        raise AiUnavailable(str(e)) from e
    except Exception as e:  # noqa: BLE001  임베딩 호출 실패
        raise AiUnavailable(f"근거 검색 실패: {type(e).__name__}") from e


def _clean_history(history: list[dict] | None) -> list[dict]:
    out = []
    for t in (history or [])[-6:]:
        text, _ = mask_sensitive(str(t.get("content", "")).strip())
        if text:
            out.append({"role": "USER" if t.get("role") == "USER" else "AI", "content": text[:600]})
    return out


def run_answer(facts, question: str, fraud_type: str | None = None,
               verified_references: list[dict] | None = None, history: list[dict] | None = None) -> dict:
    facts = _facts_dict(facts)
    q, _ = mask_sensitive(question.strip())
    hist = _clean_history(history)
    # "둘 다", "그거" 처럼 앞 대화를 가리키면 직전 사용자 질문까지 합쳐서 주제를 잡아요
    prev_user = next((t["content"] for t in reversed(hist) if t["role"] == "USER"), "")
    search_text = f"{prev_user} {q}" if prev_user and len(q) < 40 else q
    unable = {"status": "UNABLE_TO_ANSWER", "answer": None, "referenceCodes": [], "references": [],
              "clarifyFactKey": None, "clarifyQuestion": None}

    if OUT_OF_SCOPE.search(q):
        return unable

    for key, pattern in TOPIC:
        if pattern.search(q) and facts.get(key) == "UNKNOWN":
            return {**unable, "status": "NEED_USER_INPUT", "clarifyFactKey": key, "clarifyQuestion": CLARIFY[key]}

    refs = verified_references if verified_references is not None else _references(facts, search_text, fraud_type)
    if not refs:
        return unable
    try:
        res = rag_answer(q, facts, refs, fraud_type, hist)
    except ExplainError as e:
        raise AiUnavailable(str(e)) from e
    if has_forbidden(res["answer"]):  # rag_answer 가 이미 검사하지만 한 번 더
        raise AiUnavailable("답변에 연락처·링크 등 금지 표현이 있어요.")
    return {"status": "ANSWERED", "answer": res["answer"], "referenceCodes": res["referenceCodes"],
            "references": refs, "clarifyFactKey": None, "clarifyQuestion": None}
