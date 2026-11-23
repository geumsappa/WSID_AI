"""Attach reporting metadata after an immutable story has passed semantic review."""
from copy import deepcopy

from app.case_db import load_cases, public_ref
from app.guard.scenario_guard import find_unsafe_text
from app.schemas.context_graph import ScenarioClassification
from app.services.context_graph import ContextIntegrityError
from app.services.scenario_planner import ask

RISK_BY_CODE = {
    "SECONDHAND_TRADE": "기타", "JOB_SCAM": "기타", "SMISHING": "스미싱",
    "MESSENGER_PHISHING": "메신저피싱", "LOAN_FRAUD": "대출빙자",
    "INSTITUTION_IMPERSONATION": "기관사칭", "INVESTMENT_FRAUD": "투자·가상자산",
}


CODE_DEFINITIONS = {
    "SECONDHAND_TRADE": "중고 물품의 판매·구매·거래를 빙자한 사기",
    "SMISHING": "문자로 보낸 거짓 안내·링크·답장 또는 정보 입력 요구를 통한 피싱",
    "MESSENGER_PHISHING": "메신저에서 지인·가족·기존 관계자를 사칭해 정보나 금전을 요구",
    "LOAN_FRAUD": "대출·대환·신용 개선을 빙자해 금전이나 정보를 요구",
    "INSTITUTION_IMPERSONATION": "공공·수사·금융기관의 공적 권위나 법적 절차를 사칭; 일반 민간 서비스·판매자 사칭을 뜻하지 않음",
    "INVESTMENT_FRAUD": "투자·수익·자산 운용 기회를 빙자한 사기",
    "JOB_SCAM": "취업·채용·아르바이트 기회나 지원 절차를 빙자한 사기",
}


def classify_story(story):
    cases = load_cases()
    def validate(result):
        text = " ".join([result.fraudSubtype, result.reason, *result.tags])
        if find_unsafe_text(text, story.get("allowedOrganizations", [])):
            raise ContextIntegrityError("unsafe_classification_metadata")
        seen = set()
        for reference in result.references:
            case = cases.get(reference.caseId)
            if case is None or reference.caseId in seen:
                raise ContextIntegrityError("invalid_classification_reference")
            mechanics = [*case.get("tactics", []), *case.get("requestedActions", [])]
            if reference.technique not in mechanics:
                raise ContextIntegrityError("unsupported_reference_technique")
            seen.add(reference.caseId)
    references = [{key: case.get(key) for key in ("id", "summary", "tactics", "requestedActions", "requestedInfo")} for case in cases.values()]
    result = ask("scenario_classify", {"story": deepcopy(story), "compatibilityCodes": RISK_BY_CODE, "codeDefinitions": CODE_DEFINITIONS,
                 "referenceTechniques": references}, ScenarioClassification, max_tokens=1200, validate=validate)
    metadata = {"fraudSubtype": result.fraudSubtype, "fraudTypeCode": result.fraudTypeCode,
                "riskType": RISK_BY_CODE[result.fraudTypeCode], "classificationFit": result.classificationFit,
                "classificationReason": result.reason, "tags": result.tags,
                "basedOnCases": [public_ref(cases[reference.caseId]) for reference in result.references],
                "referenceStatus": "MATCHED" if result.references else "NOT_MATCHED"}
    return metadata
