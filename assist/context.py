"""assist v1.1 선택 필드(history·fraudType)를 검색·판단에 쓰는 도우미.

- history: "둘 다 알려줘", "그거요"처럼 앞 대화를 가리키는 짧은 질문이나, 확인 질문 뒤 원문이
  사라진 후속 PLAN(question=null)에서 무엇을 묻는지 잡는 데 써요. 사실의 근거로는 쓰지 않아요.
- fraudType: 중고거래(재화·용역 사기)면 지급정지·환급 전용 문단을 빼고, 유형이 맞는 문단을 조금 올려요.
- TOPIC_REFS: 임베딩 검색만으로는 '중고거래' 같은 유형 단어에 끌려가 정작 묻는 내용(민사 절차,
  신고 이후 등)의 근거를 놓치는 경우가 있어서, 질문 주제로 꼭 넣을 근거 code를 먼저 정해요.
  (예전 /answer 가안의 app/services/solution.py 규칙을 assist로 옮긴 것)
"""
from __future__ import annotations

import re

from .contracts import AnswerRequest, PlanRequest

SHORT_QUESTION = 40
HISTORY_FOR_MODEL = 6
HISTORY_CHARS = 600

TOPIC_REFS = [
    (re.compile(r"(더|또|추가로?) ?(돈|입금|송금|보내)|수수료|보증금|선입금|환불.{0,6}(하려면|조건|먼저)"),
     ["REF_FSS_2024_28_ALERT_V1_P1"]),
    (re.compile(r"고소|형사|어디(에|로|서)? ?(신고|접수|가)|경찰서|접수 ?(방법|절차)"),
     ["REF_EASYLAW_COMPLAINT_V1_P1", "REF_EASYLAW_SECONDHAND_REPORT_V1_P1"]),
    (re.compile(r"민사|소송|소장|전자소송|인지|송달료|비용|소액|지급명령|손해배상"),
     ["REF_EASYLAW_CIVIL_FILING_V1_P1", "REF_EASYLAW_SECONDHAND_CIVIL_V1_P1",
      "REF_EASYLAW_SMALL_CLAIMS_V1_P1", "REF_EASYLAW_PAYMENT_ORDER_V1_P1"]),
    (re.compile(r"배상|합의|돌려받|되찾|회수|피해 ?회복"),
     ["REF_EASYLAW_VICTIM_RECOVERY_V1_P1", "REF_EASYLAW_COMPENSATION_ORDER_V1_P1",
      "REF_EASYLAW_SECONDHAND_CIVIL_V1_P1"]),
    (re.compile(r"신고 ?(했|하고|한 ?뒤|후|다음|이후)|접수 ?(했|후)|수사|진행 ?상황|결과"),
     ["REF_EASYLAW_VICTIM_INFO_V1_P1", "REF_EASYLAW_VICTIM_RECOVERY_V1_P1"]),
]
# 중고거래인데 환급·지급정지를 묻지 않았으면 '환급 대상 아님' 문단은 빼요 (매 답변마다 같은 말을 반복하지 않게)
REFUND_TOPIC = re.compile(r"환급|지급정지|피해구제|돌려받|되찾|환불")
REFUND_NOTICE_CODES = {"REF_LAW_REFUND_PROCEDURE_V1_P2", "REF_FSS_2026_07_NONFACE_V1_P1"}


# 확인 질문에 대한 짧은 대답("네", "아니요, 안 보냈어요")은 '무엇을 물었는지'가 아니라서 건너뛰어요
SHORT_REPLY = re.compile(r"^\s*(예|네|응|아니|아뇨|모름|모르|잘 ?모르|송금|정보 ?제공|앱 ?설치|연락 ?지속)")


def previous_user(request: PlanRequest | AnswerRequest) -> str:
    """history에서 가장 최근의 '질문다운' 사용자 말. history에는 이번 질문 앞의 대화만 넣는다는 전제라,
    확인 질문 뒤 후속 PLAN·ANSWER(question=null)에서는 이게 원래 질문이에요."""
    for turn in reversed(request.history or []):
        if turn.role == "USER" and not (len(turn.content) <= 20 and SHORT_REPLY.match(turn.content)):
            return turn.content
    return ""


# "둘 다", "그거"처럼 앞 답변에 나온 것을 가리키는 말. 이때는 직전 도우미 답변도 주제에 넣어요
# (예: 답변에 '배상명령·민사소송'이 나왔고 "둘 다 알려줘" → 두 주제의 근거를 모두 찾기)
REFERENTIAL = re.compile(r"둘 ?다|셋 ?다|두 ?(개|가지)|그거|그것|그중|그 중|그럼|아까|방금|위에|각각")


def previous_ai(request: PlanRequest | AnswerRequest) -> str:
    for turn in reversed(request.history or []):
        if turn.role == "AI":
            return turn.content
    return ""


def topic_text(request: PlanRequest | AnswerRequest) -> str:
    """주제를 잡을 글: 질문이 없거나 짧으면 직전 사용자 말을, 앞 답변을 가리키면 직전 도우미 답변도 붙여요."""
    question = request.question or ""
    parts = []
    prev = previous_user(request)
    if prev and len(question) < SHORT_QUESTION:
        parts.append(prev)
    if question and REFERENTIAL.search(question):
        parts.append(previous_ai(request)[:600])
    parts.append(question)
    return " ".join(p for p in parts if p).strip()


def topic_codes(text: str) -> list[str]:
    codes: list[str] = []
    for pattern, refs in TOPIC_REFS:
        if pattern.search(text):
            codes += [c for c in refs if c not in codes]
    return codes


def drop_refund_notice(references: list[dict], fraud_type: str | None, text: str) -> list[dict]:
    if fraud_type != "SECONDHAND_TRADE" or REFUND_TOPIC.search(text):
        return references
    return [r for r in references if r["code"] not in REFUND_NOTICE_CODES]


def history_for_model(request: PlanRequest | AnswerRequest) -> list[dict] | None:
    """모델에 넘길 최근 대화 (최근 6턴, 턴당 600자). 없으면 None."""
    if not request.history:
        return None
    return [{"role": t.role, "content": t.content[:HISTORY_CHARS]} for t in request.history[-HISTORY_FOR_MODEL:]]
