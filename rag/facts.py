"""AI-06 사실(4개 키) ↔ 근거 문단 태그(situation_codes) 연결."""
from .config import FACT_KEYS, FACT_VALUES

# 사실이 YES 일 때 찾아야 할 피해 상황 코드
FACT_TO_SITUATIONS = {
    "transferredMoney": ["money_sent"],
    "sharedInformation": ["personal_info_leak", "id_leak", "account_leak"],
    "installedApp": ["malicious_app"],
    "contactContinues": ["suspicious_contact"],
}
# 검색 질의문을 만들 때 쓰는 한국어 표현 (사용자 원문은 AI-07 에 오지 않으므로 사실로만 질의를 만든다)
FACT_PHRASES = {
    ("transferredMoney", "YES"): "사기범에게 돈을 송금했다. 지급정지와 피해구제 신청, 환급 절차",
    ("sharedInformation", "YES"): "신분증이나 계좌번호 같은 개인정보를 넘겼다. 명의도용 방지와 노출자 등록, 안심차단",
    ("installedApp", "YES"): "링크를 눌러 앱을 설치했다. 악성앱 원격조종 대응과 휴대폰 점검",
    ("contactContinues", "YES"): "사기 의심 상대와 아직 연락 중이다. 연락을 끊고 공식 번호로 확인",
}


def validate_facts(facts: dict) -> dict:
    if not isinstance(facts, dict) or not facts:
        raise ValueError("facts 는 비어 있지 않은 객체여야 해요.")
    for k, v in facts.items():
        if k not in FACT_KEYS:
            raise ValueError(f"허용되지 않은 사실 키: {k}")
        if v not in FACT_VALUES:
            raise ValueError(f"사실 값은 YES/NO/UNKNOWN 중 하나여야 해요: {k}={v}")
    return dict(facts)


def situations_for(facts: dict) -> list[str]:
    out: list[str] = []
    for k, v in facts.items():
        if v == "YES":
            for s in FACT_TO_SITUATIONS[k]:
                if s not in out:
                    out.append(s)
    return out


def query_text(facts: dict, fraud_type: str | None = None) -> str:
    parts = [FACT_PHRASES[(k, v)] for k, v in facts.items() if (k, v) in FACT_PHRASES]
    unknown = [k for k, v in facts.items() if v == "UNKNOWN"]
    if unknown:
        parts.append("아직 확인되지 않은 사항이 있다")
    if fraud_type:
        parts.append(f"사기 유형 {fraud_type}")
    return ". ".join(parts) or "금융사기 피해 대응 방법"
