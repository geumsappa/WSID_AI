"""User facts only: normalisation, stable references and readiness checks."""
import hashlib
import json
import re
from copy import deepcopy

from app.guard.sensitive import mask_sensitive

FACT_CATEGORIES = ["거래(중고·쇼핑)", "구직·아르바이트", "택배·배송", "금융·대출", "투자",
                   "가족·지인 연락", "공공기관·민원", "여행·이동", "기타"]
VAGUE = re.compile(r"^(모르겠.*|기억.*안.*|없(?:음|어요|습니다)?|네|아니요|응|그냥|글쎄.*|답변\d*)[.!? ]*$")


def text_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()


def infer_category(detail: str) -> str:
    for category, words in [
        ("구직·아르바이트", ("채용", "역량검사", "역검", "면접", "입사지원", "아르바이트", "구직")),
        ("택배·배송", ("택배", "배송", "반송")), ("거래(중고·쇼핑)", ("중고거래", "중고 거래", "물건을 팔", "자전거를 팔")),
        ("투자", ("투자", "리딩방", "가상자산")), ("금융·대출", ("대출", "은행 업무")),
        ("가족·지인 연락", ("가족", "지인", "자녀")), ("공공기관·민원", ("민원", "공공기관")),
    ]:
        if any(word in detail for word in words): return category
    return "기타"


def meaningful(fact: dict) -> bool:
    detail = str(fact.get("detail") or "").strip()
    no_context = any(phrase in detail for phrase in ("모르겠", "기억나지", "기억이 안", "기억 못", "별일 없", "아무것도 안", "상황이 없", "정보가 없"))
    return len(text_key(detail)) >= 4 and not VAGUE.fullmatch(detail) and not detail.startswith("[") and not no_context


def merge_facts(previous: list, incoming: list, retracted: list[str], turn: int) -> list[dict]:
    """Retractions reference existing IDs; repeated details enrich rather than duplicate a fact."""
    merged = []
    keys = {}
    for raw in [*deepcopy(previous), *deepcopy(incoming)]:
        if not isinstance(raw, dict) or not isinstance(raw.get("detail"), str):
            continue
        detail = mask_sensitive(" ".join(raw["detail"].split()))[0][:200]
        if not detail or raw.get("factId") in retracted:
            continue
        key = text_key(detail)
        category = raw.get("category") if raw.get("category") in FACT_CATEGORIES and raw.get("category") != "기타" else infer_category(detail)
        fact = {"category": category, "detail": detail}
        for field in ("channel", "impersonatedIdentity", "requestedAction"):
            value = raw.get(field)
            fact[field] = mask_sensitive(value.strip())[0][:100] if isinstance(value, str) and value.strip() else None
        risk = raw.get("riskFactors", [])
        fact["riskFactors"] = list(dict.fromkeys(mask_sensitive(item)[0][:50] for item in risk if isinstance(item, str) and item.strip()))[:5] if isinstance(risk, list) else []
        fact["factId"] = "fact-" + hashlib.sha256(key.encode()).hexdigest()[:12].translate(str.maketrans("0123456789", "ghijklmnop"))
        source = raw.get("sourceTurn")
        fact["sourceTurn"] = source if isinstance(source, int) and not isinstance(source, bool) and source > 0 else turn
        if key in keys:
            old = merged[keys[key]]
            for field in ("channel", "impersonatedIdentity", "requestedAction"):
                if fact[field] is not None: old[field] = fact[field]
            if category != "기타": old["category"] = category
            old["riskFactors"] = list(dict.fromkeys([*old["riskFactors"], *fact["riskFactors"]]))[:5]
        else:
            keys[key] = len(merged)
            merged.append(fact)
    merged = merged[-30:]
    while merged and len(json.dumps(merged, ensure_ascii=False)) > 12000:
        merged.pop(0)
    return merged


def sufficient_context(facts: list[dict]) -> bool:
    facts = [fact for fact in facts if meaningful(fact)]
    if not facts:
        return False
    # Knowing the sender's institution does not make a normal notice a scam request.
    contact = any(fact.get("requestedAction") in ("링크 클릭", "개인정보 제공", "송금", "앱 설치")
                  or fact.get("riskFactors") for fact in facts)
    if contact:
        return all(any(fact.get(field) for fact in facts) for field in ("channel", "impersonatedIdentity", "requestedAction"))
    return any(fact.get("category") != "기타" for fact in facts) and any(fact.get("channel") for fact in facts)


def followup(facts: list[dict], asked: list[str]) -> str:
    """Ask about missing information without inventing an incident or replaying a question."""
    facts = [fact for fact in facts if meaningful(fact)]
    seen = {text_key(question) for question in asked}
    if not facts:
        starters = ["최근에 하신 일이나 받은 연락 중 기억나는 상황을 한 가지만 말씀해 주시겠어요?",
                    "오늘이나 최근에 이용한 서비스가 있었나요?", "최근에 받은 문자나 메시지 중 기억나는 것이 있나요?"]
        return next((question for question in starters if text_key(question) not in seen),
                    "지금 떠오르는 상황이 없다면 나중에 다시 시작하셔도 괜찮아요. 기억나는 일이 생기면 말씀해 주세요.")
    received = any(fact.get("requestedAction") or fact.get("impersonatedIdentity") or any(word in fact["detail"] for word in ("문자", "연락", "메시지", "알림")) for fact in facts)
    candidates = []
    if received:
        if not any(fact.get("channel") for fact in facts): candidates.append("그 연락은 어떤 경로로 받으셨나요?")
        if not any(fact.get("impersonatedIdentity") for fact in facts): candidates.append("상대방은 자신을 누구라고 소개했나요?")
        if not any(fact.get("requestedAction") for fact in facts): candidates.append("상대방은 무엇을 해 달라고 했나요?")
    candidates += ["그 일과 관련해서 이후에 받은 안내나 연락이 있었나요?", "그 상황에서 직접 하신 일을 조금 더 알려주시겠어요?", "훈련에 반영하고 싶은 상황을 한 가지만 더 알려주시겠어요?"]
    return next((question for question in candidates if text_key(question) not in seen),
                "지금까지 알려주신 내용에서 정정하거나 추가할 부분이 있나요?")
