"""ANSWER 결과를 내보내기 전에, 사기 유형에 맞지 않는 절차를 권하는지 코드로 검사해요.

프롬프트 규칙(model.CONTEXT_RULES)은 모델이 어길 수 있어서, 같은 규칙을 여기서 한 번 더 확인해요.
- SECONDHAND_TRADE: 통신사기피해환급법 제2조제2호 단서(재화·용역 거래를 가장한 사기 제외)라서
  지급정지·피해구제 신청·환급을 할 일로 권하지 않아요. "대상이 아니에요"처럼 부정하는 문장은 괜찮아요.
- INVESTMENT_FRAUD: 지급정지를 권하려면 '받아들여지지 않을 수 있다'와 '있는 그대로 투자 사기라고 알린다'를 함께 말해요.
- 모든 유형: 환급·피해금 회수를 장담하지 않아요.
검사는 문장 단위예요. 위반이면 graph가 한 번 다시 쓰게 하고, 그래도 위반이면 repair로 고쳐서 내보내요.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import AnswerResponse

REFUND_PROCEDURE = re.compile(r"지급\s?정지|피해\s?구제|환급|채권\s?소멸")
PAYMENT_STOP = re.compile(r"지급\s?정지")
NEGATED = re.compile(r"않|안\s?되|안\s?돼|못\s?하|아니|어렵|제외|불가|거절|해당하지|수\s?없|없어요")
PROMISE = re.compile(r"(전액|반드시|꼭|무조건|틀림없이|100%)[^.!?\n]{0,15}(환급|돌려받|되찾)")
INVESTMENT_CAVEAT = re.compile(r"받아들여지지\s?않|거절될\s?수|거절당할\s?수|안\s?될\s?수|되지\s?않을\s?수|어려울\s?수")
INVESTMENT_HONEST = re.compile(r"사실대로|있는\s?그대로|투자\s?사기라고")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
LIST_ITEM = re.compile(r"^\d+\.\s*")

CAVEAT_TEXT = "투자 사기는 지급정지가 받아들여지지 않을 수 있어요."
HONEST_TEXT = "금융회사와 경찰에는 있는 그대로 투자 사기라고 알리세요."

FEEDBACK = {
    "SECONDHAND_REFUND": "fraudType이 SECONDHAND_TRADE라 지급정지·피해구제 신청·환급은 해당하지 않는다. "
                         "이를 할 일로 권하지 말고 더 보내지 않기, 증거 보존, 경찰 신고, 배상명령·지급명령·소액소송 같은 민사 절차를 안내한다.",
    "INVESTMENT_CAVEAT": "fraudType이 INVESTMENT_FRAUD인데 지급정지를 권하면서 "
                         "투자 사기는 받아들여지지 않을 수 있다는 점을 말하지 않았다. 함께 말한다.",
    "INVESTMENT_HONEST": "fraudType이 INVESTMENT_FRAUD인데 지급정지를 권하면서 "
                         "금융회사·경찰에 있는 그대로 투자 사기라고 알리라는 점을 말하지 않았다. 함께 말한다.",
    "PROMISE": "환급이나 피해금 회수를 장담했다. 가능성으로만 말한다.",
}


@dataclass(frozen=True)
class Violation:
    kind: str
    sentence: str | None = None


def sentences(text: str) -> list[str]:
    return [s.strip() for line in text.splitlines() for s in SENTENCE_END.split(line) if s.strip()]


def _bad_sentence(sentence: str, fraud_type: str | None) -> str | None:
    if PROMISE.search(sentence):
        return "PROMISE"
    if fraud_type == "SECONDHAND_TRADE" and REFUND_PROCEDURE.search(sentence) and not NEGATED.search(sentence):
        return "SECONDHAND_REFUND"
    return None


def check(text: str | None, fraud_type: str | None) -> list[Violation]:
    if not text:
        return []
    found = [Violation(kind, s) for s in sentences(text) if (kind := _bad_sentence(s, fraud_type))]
    if fraud_type == "INVESTMENT_FRAUD" and any(
            PAYMENT_STOP.search(s) and not NEGATED.search(s) for s in sentences(text)):
        if not INVESTMENT_CAVEAT.search(text):
            found.append(Violation("INVESTMENT_CAVEAT"))
        if not INVESTMENT_HONEST.search(text):
            found.append(Violation("INVESTMENT_HONEST"))
    return found


def violations(result: AnswerResponse, fraud_type: str | None) -> list[Violation]:
    return check(result.answer, fraud_type) + check(result.guidance, fraud_type)


def feedback(found: list[Violation]) -> str:
    """모델에 돌려줄 이유. 답변 원문은 담지 않아요."""
    return " ".join(dict.fromkeys(FEEDBACK[v.kind] for v in found))


def _strip_bad(text: str, fraud_type: str | None) -> str:
    lines, number = [], 0
    for line in text.splitlines():
        kept = [s for s in SENTENCE_END.split(line) if s.strip() and not _bad_sentence(s.strip(), fraud_type)]
        line = " ".join(s.strip() for s in kept)
        if not line or LIST_ITEM.fullmatch(line):
            continue
        if LIST_ITEM.match(line):  # 지운 항목 때문에 번호가 비지 않게 다시 매겨요
            number += 1
            line = LIST_ITEM.sub(f"{number}. ", line, count=1)
        lines.append(line)
    return "\n".join(lines)


def repair(result: AnswerResponse, fraud_type: str | None) -> AnswerResponse:
    """다시 써도 위반이면: 위반 문장은 빼고, 투자 사기 단서는 붙이고, 남는 답이 없으면 근거 부족으로 돌려요."""
    if result.status != "ANSWERED":
        if result.guidance and check(result.guidance, fraud_type):
            return result.model_copy(update={"guidance": None})
        return result
    text = _strip_bad(result.answer or "", fraud_type)
    kinds = {v.kind for v in check(text, fraud_type)}
    extra = [t for k, t in (("INVESTMENT_CAVEAT", CAVEAT_TEXT), ("INVESTMENT_HONEST", HONEST_TEXT)) if k in kinds]
    if extra:
        text = (text + "\n" + " ".join(extra)).strip()
    if not text or len(text) > 2000 or check(text, fraud_type):
        return AnswerResponse(status="UNABLE_TO_ANSWER", answer=None, referenceCodes=[], reasonCode="NO_EVIDENCE")
    return AnswerResponse.model_validate({**result.model_dump(), "answer": text})
