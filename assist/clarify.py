"""근거 기반 추가 질문의 검색어·정의와 결정적 검증기.

질문 문장은 고정하지 않는다. 모델이 검색된 근거 문단과 사용자 질문을 바탕으로 만들고,
아래 검증기가 '근거에 묶여 있는지·대상 사실을 묻는지·유도/값 요구가 없는지'를 코드로 확인한다.
검증을 통과하지 못한 질문은 내보내지 않고, 묻지 않은 채 조건부 답변 경로로 넘어간다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rag.guard import has_forbidden

from .contracts import Condition, FactKey, condition_id

# 모델에 주는 사실 정의. 사용자에게 보이는 문장이 아니라 무엇을 물어야 하는지의 기준이다.
FACT_DEFINITIONS: dict[str, str] = {
    "transferredMoney": "사기 의심 상대에게 돈을 보내거나 건넸는가",
    "installedApp": "상대가 시킨 앱을 설치했거나 링크를 눌러 앱이 깔렸는가",
    "sharedInformation": "신분증·계좌번호·비밀번호·인증번호 같은 개인·금융정보를 넘겼는가",
    "contactContinues": "그 사기 의심 상대와 지금도 연락이 이어지고 있는가",
}
# 추가 질문 근거를 찾는 검색어. 의도 검색어와 합쳐 한 번의 검색으로 두 용도를 함께 쓴다.
FACT_QUERIES: dict[str, str] = {
    "transferredMoney": "사기범에게 송금 이체 입금한 경우 지급정지 피해구제 절차",
    "installedApp": "사기범이 설치를 유도한 악성 앱 원격제어 앱 설치 여부 확인과 조치",
    "sharedInformation": "비밀번호 인증번호 신분증 계좌번호 등 개인정보 유출 시 조치",
    "contactContinues": "사기 의심 상대와 연락 중단 차단 사칭 확인",
}
# 질문이 대상 사실을 묻고 있다는 표지. 질문과 인용 근거 모두에 하나 이상 있어야 한다.
FACT_ANCHORS: dict[str, tuple[str, ...]] = {
    "transferredMoney": ("돈", "송금", "이체", "입금", "결제", "보내", "보냈", "건네", "건넸"),
    "installedApp": ("앱", "어플", "애플리케이션", "설치", "원격"),
    "sharedInformation": ("비밀번호", "인증번호", "OTP", "신분증", "계좌번호", "카드번호",
                          "주민등록", "개인정보", "금융정보", "보안카드"),
    "contactContinues": ("연락", "통화", "문자", "메시지", "답장", "대화"),
}
# 구체 항목. 질문에 쓰였다면 인용 근거나 사용자 질문에 실제로 나와야 한다(지어낸 구체성 차단).
SPECIFIC_TERMS: tuple[str, ...] = (
    "비밀번호", "인증번호", "OTP", "신분증", "계좌번호", "카드번호", "주민등록", "보안카드",
    "원격", "링크", "QR", "상품권", "가상자산", "코인", "대출", "선입금", "수수료", "택배",
    "검찰", "경찰", "금융감독원", "금감원", "은행", "카드사", "가족", "지인", "자녀",
)
# 유도·단정·재촉, 실제 값 요구, 부정 유도 질문.
BANNED_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p) for p in (
    r"죠\s*\?$", r"맞(으시|으세|나|지)", r"분명", r"반드시", r"당장", r"바로", r"서둘", r"빨리",
    r"알려\s*주(세요|십시오|시겠)", r"말씀해\s*주", r"입력해", r"적어\s*주", r"보내\s*주(세요|십시오)",
    r"(무엇|뭐|어떤 번호|몇 번)", r"않(으셨|았|으신)", r"안\s*하셨", r"\d{2,}",
    r"제게|저에게|여기에", r"클릭", r"누르세요",
))
MIN_LEN, MAX_LEN = 8, 120


@dataclass(frozen=True)
class ClarificationCheck:
    ok: bool
    reason: str | None = None


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", _norm(text)).strip()


# 조건 문장과 질문이 같은 내용을 다루는지 볼 때 무시하는 흔한 말.
STOP_STEMS = {"상대", "사용", "경우", "있다", "있는", "했다", "하는", "하였", "그리", "또는", "이나", "에게",
              "에서", "으로", "통해", "대해", "관련", "혹시", "지금", "이미", "해당"}


def _stems(text: str) -> set[str]:
    return {t[:2] for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", _norm(text))} - STOP_STEMS


@dataclass(frozen=True)
class ConditionSeed:
    """검증된 인용 조건(문단 코드 + 원문 인용). 질문 문장이 검증을 통과하면 Condition이 된다."""
    referenceCode: str
    quote: str

    def to_condition(self, question: str) -> Condition:
        # 조건 문장은 모델이 따로 쓰지 않고, 검증을 통과한 질문 문장을 그대로 쓴다.
        statement = question.strip()
        return Condition(conditionId=condition_id(self.referenceCode, statement, self.quote),
                         statement=statement, referenceCode=self.referenceCode, quote=self.quote)


def propose_condition(*, reference_code: str | None, quote: str | None, references: list[dict],
                      confirmed: list) -> tuple[ConditionSeed | None, str | None]:
    """근거 평가 모델이 고른 '네 사실 밖의 조건'을 검증한다.
    조건은 반드시 검색된 문단의 원문을 글자 그대로 인용해야 한다(지어낸 조건 차단)."""
    if not (reference_code and quote):
        return None, "INCOMPLETE"
    ref = {r["code"]: r for r in references}.get(reference_code)
    if ref is None:
        return None, "UNGROUNDED_CITATION"
    quote = _flat(quote)
    if len(quote) < 8 or quote not in _flat(ref["excerpt"]):
        return None, "QUOTE_NOT_IN_EVIDENCE"
    if has_forbidden(quote, strict=True):
        return None, "FORBIDDEN"
    if any(c.referenceCode == reference_code and _flat(c.quote) == quote for c in confirmed):
        return None, "ALREADY_CONFIRMED"
    return ConditionSeed(referenceCode=reference_code, quote=quote), None


def validate_clarification(*, key: FactKey | None, content: str | None, cited_codes: list[str],
                           references: list[dict], question: str | None,
                           condition: ConditionSeed | None = None) -> ClarificationCheck:
    """모델이 만든 추가 질문이 내보내도 되는 질문인지 결정적으로 확인한다.
    key(네 사실) 또는 condition(근거 문단 인용 조건) 중 하나를 대상으로 한다."""
    if not content:
        return ClarificationCheck(False, "EMPTY")
    text = _norm(content).strip()
    if not MIN_LEN <= len(text) <= MAX_LEN:
        return ClarificationCheck(False, "LENGTH")
    if has_forbidden(text, strict=True):
        return ClarificationCheck(False, "FORBIDDEN")
    if any(p.search(text) for p in BANNED_PATTERNS):
        return ClarificationCheck(False, "LEADING_OR_VALUE_REQUEST")
    if "\n" in text or text.count("?") != 1 or not re.search(r"(요|까)\?$", text):
        return ClarificationCheck(False, "NOT_SINGLE_POLITE_QUESTION")
    if re.search(r"[.!]", text):
        return ClarificationCheck(False, "MULTIPLE_SENTENCES")
    by_code = {ref["code"]: ref for ref in references}
    if not cited_codes or len(set(cited_codes)) != len(cited_codes) or not set(cited_codes) <= by_code.keys():
        return ClarificationCheck(False, "UNGROUNDED_CITATION")
    evidence = _norm(" ".join(by_code[c].get("title", "") + " " + by_code[c]["excerpt"] for c in cited_codes))
    if condition is not None:
        if condition.referenceCode not in cited_codes:
            return ClarificationCheck(False, "UNGROUNDED_CITATION")
        # 질문이 인용된 조건의 내용을 실제로 묻는지 확인한다.
        if not _stems(text) & _stems(condition.quote):
            return ClarificationCheck(False, "OFF_TARGET")
    else:
        anchors = FACT_ANCHORS[key]
        if not any(a in text for a in anchors):
            return ClarificationCheck(False, "OFF_TARGET")
        if not any(a in evidence for a in anchors):
            return ClarificationCheck(False, "EVIDENCE_OFF_TARGET")
    support = evidence + " " + _norm(question or "")
    if any(term in text and term not in support for term in SPECIFIC_TERMS):
        return ClarificationCheck(False, "UNSUPPORTED_SPECIFIC")
    return ClarificationCheck(True)
