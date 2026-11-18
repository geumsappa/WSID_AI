"""시나리오 가드레일 (SIM-006).

LLM 이 만든 시나리오가
  1) 우리가 정한 모양(필드, 허용 목록)을 지키는지
  2) 실제 사기에 그대로 쓸 수 있는 내용(실존 기관명, 진짜 URL, 진짜 전화번호)이 없는지
  3) 근거 사례(caseId)가 사례 DB 에 실제로 있고 위험 유형이 맞는지
  4) 모방범죄 우려(범행 운영 요령, 단계별 절차 안내, 지나치게 구체적인 대본)가 없는지
  5) 사기범이 개인정보를 직접 요구하는 장면이 있는지 (훈련의 핵심)
검사한다. 문제가 있으면 errors 에 이유를 담아 돌려주고, 서비스가 재시도 또는 기본 시나리오로 대체한다.
"""
import re

from app.case_db import get_case, public_ref
from app.scenario_data import (
    ALLOWED_CHANNELS,
    ALLOWED_MOCK_PAGES,
    ALLOWED_REQUESTS,
    ALLOWED_SUBTYPES,
    RISK_TYPES,
    SUBTYPE_TO_RISK_TYPE,
)

# 시나리오에 나오면 안 되는 실존 기관·회사·서비스 이름 (필요하면 계속 추가)
REAL_NAMES = [
    # 은행·금융
    "국민은행", "KB", "신한", "우리은행", "하나은행", "농협", "NH", "기업은행", "IBK",
    "카카오뱅크", "케이뱅크", "토스", "새마을금고", "우체국", "삼성카드", "현대카드", "롯데카드",
    # 택배·쇼핑·플랫폼
    "CJ대한통운", "대한통운", "한진택배", "롯데택배", "로젠", "쿠팡", "당근", "번개장터", "중고나라",
    "네이버", "카카오", "11번가", "G마켓", "알바몬", "알바천국", "잡코리아", "사람인",
    # 공공기관
    "금융감독원", "금감원", "금융위원회", "경찰청", "검찰", "국세청", "건강보험", "국민연금",
    "법원", "정부24", "행정안전부", "질병관리청",
]

URL_PATTERN = re.compile(r"https?://|www\.|\b[\w-]+\.(com|net|org|kr|co|io|me|ly)\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\d)(0\d{1,2}[\s-]?\d{3,4}[\s-]?\d{4}|1[5-9]\d{2}[\s-]?\d{4})(?!\d)")
VOICE_WORDS = ["전화를 걸", "통화로 안내", "보이스피싱", "전화 연결"]

# ---- 모방범죄 검사 (SIM-006): 교육용 수준을 넘어 '범행 설명서'가 되는 내용 ----
COPYCAT_WORDS = [
    # 범행 운영·회피 요령
    "대포통장", "수거책", "인출책", "자금세탁", "돈세탁", "환치기", "추적을 피", "추적이 안",
    "신고를 피", "경찰에 걸리지", "이체 한도를 올", "한도를 높이",
    # 실제 원격제어·메신저 도구 이름
    "팀뷰어", "teamviewer", "애니데스크", "anydesk", "퀵서포트", "quicksupport", "텔레그램", "telegram",
]
# "1단계 ... 2단계", "① ... ②", "입력한 뒤 ... 이체" 처럼 절차를 단계별로 안내하는 표현
STEP_PATTERNS = [
    re.compile(r"1\s*단계.*2\s*단계", re.DOTALL),
    re.compile(r"①.*②", re.DOTALL),
    re.compile(r"(?:^|\s)1[.)]\s.+?\s2[.)]\s", re.DOTALL),
    re.compile(r"(첫째|먼저).{0,60}(둘째|그다음|그 다음).{0,60}(셋째|마지막으로)", re.DOTALL),
    re.compile(r"(입력|선택|클릭|로그인)(하신|한|하고 난)?\s*(뒤|후|다음)에?.{0,40}(입력|선택|클릭|이체|송금)"),
]
MAX_OPENING_CHARS = 250   # 첫 메시지가 이보다 길면 '완성된 사기 대본' 수준으로 봄
MAX_SCENE_TEXT_CHARS = 200


def find_copycat_risk(text: str) -> list[str]:
    """실제 범행에 그대로 쓸 수 있을 만큼 구체적인지 검사."""
    problems = []
    lowered = text.lower()
    for word in COPYCAT_WORDS:
        if word in lowered:
            problems.append(f"모방범죄 우려 표현: {word}")
    if any(p.search(text) for p in STEP_PATTERNS):
        problems.append("모방범죄 우려: 절차를 단계별로 안내")
    return problems


def mentioned_organizations(facts: list[dict]) -> list[str]:
    """Only names present in input facts may be reused in the fictional role-play."""
    text = " ".join(str(fact.get(key) or "") for fact in facts for key in ("detail", "impersonatedIdentity"))
    return [name for name in REAL_NAMES if (re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", text)
            if name.isascii() else name in text)]


def find_unsafe_text(text: str, allowed_organizations: list[str] | None = None) -> list[str]:
    """실제 사기에 악용될 수 있는 내용을 찾는다."""
    problems = []
    organization_text = text
    for allowed in sorted(set(allowed_organizations or []) & set(REAL_NAMES), key=len, reverse=True):
        organization_text = organization_text.replace(allowed, "")
    for name in REAL_NAMES:
        if name in organization_text:
            problems.append(f"실존 이름 사용: {name}")
    if URL_PATTERN.search(text):
        problems.append("실제 URL 형태 포함 ([모의링크]만 허용)")
    if PHONE_PATTERN.search(text):
        problems.append("전화번호 형태 포함")
    return problems


def _all_texts(obj) -> list[str]:
    """dict/list 안의 모든 글자를 꺼낸다 (검사용)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [t for v in obj.values() for t in _all_texts(v)]
    if isinstance(obj, list):
        return [t for v in obj for t in _all_texts(v)]
    return []


def _s(value, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def validate_and_normalize(data: dict, allowed_organizations: list[str] | None = None) -> tuple[dict | None, list[str]]:
    """LLM 결과를 검사하고, 통과하면 깔끔하게 정리한 dict 를 돌려준다.

    반환: (정리된 결과 또는 None, 문제 목록)
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, ["JSON 객체가 아님"]

    raw_a = data.get("analysis") or {}
    raw_s = data.get("scenario") or {}
    if not isinstance(raw_a, dict) or not isinstance(raw_s, dict):
        return None, ["analysis/scenario 형식 오류"]

    # ---- 분석 ----
    judgment_points = []
    for i, jp in enumerate(raw_a.get("judgmentPoints") or [], start=1):
        if isinstance(jp, dict) and _s(jp.get("description")):
            judgment_points.append({"id": _s(jp.get("id"), 10) or f"J{i}",
                                    "description": _s(jp.get("description"))})
    judgment_points = judgment_points[:4]
    if not judgment_points:
        errors.append("판단 지점(judgmentPoints)이 없음")

    # ---- SIM-003 위험 유형 분류 ----
    # 세부 유형이 정해지면 대분류는 표에서 자동으로 정함 (LLM 이 대분류를 잘못 적어도 바로잡음)
    subtype = _s(raw_s.get("fraudSubtype"))
    if subtype not in ALLOWED_SUBTYPES:
        errors.append(f"허용되지 않은 세부 유형: {subtype}")
    risk_type = SUBTYPE_TO_RISK_TYPE.get(subtype, "기타")
    llm_risk_type = _s(raw_a.get("riskType")) or _s(raw_s.get("riskType"))
    if llm_risk_type and llm_risk_type not in RISK_TYPES:
        errors.append(f"허용되지 않은 위험 유형: {llm_risk_type}")

    analysis = {
        "riskType": risk_type,
        "riskTypeReason": _s(raw_a.get("riskTypeReason"), 200),
        "usedFacts": [_s(f, 200) for f in (raw_a.get("usedFacts") or []) if _s(f)][:5],
        "riskSignals": [
            {"signal": _s(r.get("signal"), 100), "reason": _s(r.get("reason"), 200)}
            for r in (raw_a.get("riskSignals") or []) if isinstance(r, dict) and _s(r.get("signal"))
        ][:5],
        "judgmentPoints": judgment_points,
    }
    jp_ids = {jp["id"] for jp in judgment_points}
    first_jp = judgment_points[0]["id"] if judgment_points else "J1"

    # ---- 시나리오 ----
    channel = _s(raw_s.get("channel"))
    if channel not in ALLOWED_CHANNELS:
        errors.append(f"허용되지 않은 채널: {channel}")

    persona_raw = raw_s.get("persona") or {}
    persona = {k: _s(persona_raw.get(k), 50) for k in ("name", "role", "organization", "tone")} \
        if isinstance(persona_raw, dict) else {}
    if not persona.get("name"):
        errors.append("가상 인물 이름 없음")

    key_scenes = []
    for i, sc in enumerate(raw_s.get("keyScenes") or [], start=1):
        if not isinstance(sc, dict):
            continue
        request = _s(sc.get("request"))
        if request not in ALLOWED_REQUESTS:
            errors.append(f"장면 {i}: 허용되지 않은 요구 행동 {request}")
        jp_id = _s(sc.get("judgmentPointId"), 10)
        key_scenes.append({
            "order": i,
            "judgmentPointId": jp_id if jp_id in jp_ids else first_jp,
            "goal": _s(sc.get("goal")),
            "tactic": _s(sc.get("tactic")),
            "request": request,
            "requestedInfo": [_s(x, 30) for x in (sc.get("requestedInfo") or []) if _s(x)][:5],
        })
    if not 1 <= len(key_scenes) <= 4:
        errors.append(f"핵심 장면 수가 1~4개가 아님: {len(key_scenes)}")
    # 훈련의 핵심: 사기범이 개인정보를 '직접 요구'하는 장면이 반드시 있어야 함
    if not any(sc["request"] == "개인정보 제공" and sc["requestedInfo"] for sc in key_scenes):
        errors.append("개인정보를 요구하는 장면(request=개인정보 제공, requestedInfo 있음)이 없음")
    # 대본이 지나치게 길고 구체적인지 (자르기 전 원본 길이로 확인)
    if len(str(raw_s.get("openingMessage") or "")) > MAX_OPENING_CHARS:
        errors.append("모방범죄 우려: 첫 메시지가 지나치게 길고 구체적")
    for sc in raw_s.get("keyScenes") or []:
        if isinstance(sc, dict) and max(len(str(sc.get("goal") or "")), len(str(sc.get("tactic") or ""))) > MAX_SCENE_TEXT_CHARS:
            errors.append("모방범죄 우려: 장면 설명이 지나치게 구체적")
            break

    # ---- SIM-005/006 근거 사례: DB 에 실제로 있고, 위험 유형이 맞아야 함 ----
    case_ids = [str(c).strip() for c in (raw_s.get("basedOnCaseIds") or []) if str(c).strip()][:3]
    based_on = []
    if not case_ids:
        errors.append("근거 사례(basedOnCaseIds)가 없음")
    for cid in case_ids:
        case = get_case(cid)
        if case is None:
            errors.append(f"사례 DB 에 없는 caseId: {cid}")
        elif case["riskType"] != risk_type:
            errors.append(f"근거 사례 {cid} 의 유형({case['riskType']})이 시나리오 유형({risk_type})과 다름")
        else:
            based_on.append(public_ref(case))

    expected_actions = []
    for i, ea in enumerate(raw_s.get("expectedActions") or [], start=1):
        if isinstance(ea, dict) and _s(ea.get("description")):
            jp_id = _s(ea.get("judgmentPointId"), 10)
            expected_actions.append({
                "id": f"A{i}",
                "judgmentPointId": jp_id if jp_id in jp_ids else first_jp,
                "description": _s(ea.get("description")),
            })
    expected_actions = expected_actions[:5]
    if not expected_actions:
        errors.append("평가할 대응 행동(expectedActions)이 없음")

    mock_page = raw_s.get("mockPage")
    if mock_page not in ALLOWED_MOCK_PAGES:
        mock_page = None

    scenario = {
        "riskType": risk_type,
        "fraudSubtype": subtype,
        "title": _s(raw_s.get("title"), 60),
        "channel": channel,
        "persona": persona,
        "background": _s(raw_s.get("background")),
        "openingMessage": _s(raw_s.get("openingMessage")),
        "keyScenes": key_scenes,
        "expectedActions": expected_actions,
        "mockPage": mock_page,
        "basedOnCases": based_on,
    }
    if not scenario["openingMessage"]:
        errors.append("첫 메시지(openingMessage) 없음")

    # ---- 악용 가능 내용 검사 (시나리오 전체 글자 대상) ----
    all_text = " ".join(_all_texts({k: v for k, v in scenario.items() if k != "basedOnCases"}))
    errors += find_unsafe_text(all_text, allowed_organizations)
    errors += find_copycat_risk(all_text)
    if any(w in all_text for w in VOICE_WORDS):
        errors.append("보이스피싱(전화) 요소 포함")

    if errors:
        return None, errors
    scenario["allowedOrganizations"] = [name for name in (allowed_organizations or []) if name in REAL_NAMES]
    return {"analysis": analysis, "scenario": scenario}, []
