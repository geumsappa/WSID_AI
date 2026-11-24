"""
'어떡하지' 모의 훈련 LLM 출력 검증기 (LLM 응답을 사용자에게 보내기 직전에 사용)

흐름
    raw = call_llm(prompt)
    errors, data = validate(raw, state)
    if errors:  # 1회만 재시도: 오류 목록을 붙여 다시 요청
        raw = call_llm(prompt + retry_note(errors)); errors, data = validate(raw, state)
    if errors:  # 그래도 실패하면 안전한 대체 대사
        data = fallback(state)
    data = apply_server_overrides(data, state, mask_types)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.guard import scam_media

MOVES = {"접근", "신뢰", "답변후진행", "요구", "압박", "확전", "물러남", "마무리", "중단"}
OUTCOMES = {"safe", "risky", "neutral"}
PROPS = {"[모의링크]": "링크 클릭", "[모의앱]": "앱 설치", "[모의계좌]": "송금"}
PROP_LIMIT = 2
SPEAKER_LIMIT = 2

# '예방'·'조심하세요'·'주의하세요'는 뺐어요: 기관 사칭범은 "피해 예방 차원에서", "요즘 사칭이 많으니 주의하세요"처럼
# 오히려 이 말로 믿게 만들어요. 역할을 깨는 진짜 조언("실제 정보는 보내지 마세요")은 아래 말과 training_turn 의 ROLE_BREAK 가 잡아요.
_FORBIDDEN = [
    "훈련", "가상", "시뮬레이션", "인공지능", "AI", "챗봇",
    "실제 정보", "공식 경로", "공식 홈페이지에서 확인",
]
_URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|net|kr|co|io|me|ly|xyz|app|link)\b", re.I)
_PHONE_LIKE = re.compile(r"(?<!\d)0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)|(?<!\d)1[5-9]\d{2}-?\d{4}(?!\d)")
_LONG_NUM = re.compile(r"(?<!\d)\d(?:[-\s]?\d){4,}(?!\d)(?!\s*원)")
# 날짜("2026-11-24", "2026.11.24")는 계좌번호가 아니에요 → 숫자 검사 전에 빼요
_DATE = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}(?:\s*일)?(?!\d)")
_BRACKET = re.compile(r"\[[^\]]+\]")


@dataclass
class TurnState:
    current_scene: int
    total_scenes: int
    scene_request_type: str          # 현재 장면 request
    next_scene_request_type: str | None
    user_turns: int
    min_turns: int
    max_turns: int
    prop_counts: dict = field(default_factory=lambda: {"[모의링크]": 0, "[모의앱]": 0, "[모의계좌]": 0})
    speaker_changes: int = 0
    allowed_requests: tuple = ()
    media_counts: dict = field(default_factory=dict)  # 사진 소품 종류별 사용 횟수 (scam_media.count_used)


def _check_message(msg: str, data: dict, st: TurnState) -> list[str]:
    errs: list[str] = []
    move = data.get("move")
    if move == "중단":
        return [] if msg == "" else ["move=중단이면 personaMessage 는 빈 문자열이어야 한다"]
    if not msg.strip():
        errs.append("personaMessage 가 비어 있다")
    if len(msg) > 350:
        errs.append("personaMessage 가 너무 길다 (350자 이하)")

    stripped = msg
    for p in PROPS:
        stripped = stripped.replace(p, "")
    for w in _FORBIDDEN:
        if w in stripped:
            errs.append(f"금지어 사용: {w}")
    if "모의" in stripped:
        errs.append("소품 표시 밖에서 '모의'를 썼다")
    if "[가림:" in msg:
        errs.append("[가림:...] 표시를 대사에 옮겨 썼다")
    if _URL.search(msg):
        errs.append("URL 처럼 보이는 문자열이 있다")
    if _PHONE_LIKE.search(msg):
        errs.append("가리지 않은 전화번호가 있다 (X로 가려야 함)")
    if _LONG_NUM.search(_DATE.sub("", msg)):
        errs.append("계좌·카드번호처럼 보이는 숫자가 있다")

    # 사진 소품([사진:상품|...] 등)은 서버(scam_media.clean)가 모양·한도를 고쳐요. 여기선 개수만 알려 줘요
    media = scam_media.find(msg)
    if len(media) > 1:
        errs.append("사진 소품이 한 메시지에 2개 이상이다")
    plain = scam_media.strip(msg)
    # 소품 검사
    brackets = _BRACKET.findall(plain)
    unknown = [b for b in brackets if b not in PROPS]
    if unknown:
        errs.append(f"알 수 없는 대괄호 표시: {unknown}")
    used = [b for b in brackets if b in PROPS]
    if len(used) > 1:
        errs.append("한 메시지에 소품이 2개 이상이다")
    if used:
        prop = used[0]
        if not plain.rstrip().endswith(prop):
            errs.append("소품은 메시지 맨 끝에 와야 한다")
        if len(plain.replace(prop, "").strip()) < 15:
            errs.append("명분 없이 소품만 보냈다")
        scene_type = st.scene_request_type if data.get("nextScene") == st.current_scene else st.next_scene_request_type
        if PROPS[prop] != scene_type:
            errs.append(f"{prop} 는 '{scene_type}' 장면에서 쓸 수 없다")
        if st.prop_counts.get(prop, 0) >= PROP_LIMIT:
            errs.append(f"{prop} 한도({PROP_LIMIT}회)를 넘었다")
        if move == "압박":
            errs.append("압박 대사에는 소품을 새로 붙이지 않는다")
        if st.user_turns == 0:
            errs.append("첫 메시지에는 소품을 붙이지 않는다")
    return errs


def validate(raw: str, st: TurnState) -> tuple[list[str], dict | None]:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ["JSON 하나만 출력해야 한다"], None

    errs: list[str] = []
    j = data.get("judgment") or {}
    if j.get("outcome") not in OUTCOMES:
        errs.append("judgment.outcome 값이 잘못됐다")
    for k in ("achievedActionIds", "violatedActionIds", "unmaskedInfoTypes"):
        if not isinstance(j.get(k, []), list):
            errs.append(f"judgment.{k} 는 배열이어야 한다")
    if data.get("move") not in MOVES:
        errs.append("move 값이 잘못됐다")
    if data.get("nextScene") not in (st.current_scene, min(st.current_scene + 1, st.total_scenes)):
        errs.append("nextScene 은 현재 장면이거나 +1 이어야 한다")
    if st.allowed_requests and data.get("requestType") not in st.allowed_requests:
        errs.append("requestType 이 허용 목록에 없다")
    if data.get("speaker") and st.speaker_changes >= SPEAKER_LIMIT:
        errs.append("화자 변경 한도를 넘었다")
    for f in data.get("newFacts", []) or []:
        if "[가림:" in f or _LONG_NUM.search(f) or _PHONE_LIKE.search(f):
            errs.append("newFacts 에 개인정보나 실제 번호처럼 보이는 값이 있다")

    errs += _check_message(str(data.get("personaMessage", "")), data, st)
    return errs, data


def retry_note(errors: list[str]) -> str:
    lines = "\n".join(f"- {e}" for e in errors)
    return f"\n\n# 직전 출력이 규칙을 어겼다. 아래를 고쳐서 JSON 하나만 다시 출력한다.\n{lines}\n"


def fallback(st: TurnState) -> dict:
    """재시도까지 실패했을 때: 진행을 멈추지 않는 무난한 대사 (요구·소품 없음)"""
    return {
        "judgment": {"outcome": "neutral", "userAction": "판정 실패(대체 응답)", "achievedActionIds": [],
                     "violatedActionIds": [], "reason": "LLM 출력 검증 실패", "unmaskedInfoTypes": []},
        "move": "신뢰", "speaker": None,
        "personaMessage": "네, 말씀하신 부분 확인하고 있어요. 조금만 더 여쭤봐도 될까요?",
        "requestMade": False, "requestType": st.scene_request_type, "nextScene": st.current_scene,
        "newFacts": [], "scenarioComplete": False, "realConcern": False, "_fallback": True,
    }


def apply_server_overrides(data: dict, st: TurnState, mask_types: list[str], events_this_turn: list[str]) -> dict:
    """LLM 이 아니라 서버가 정답을 아는 값은 서버 값으로 덮어쓴다."""
    j = data["judgment"]
    j["disclosedInfo"] = list(mask_types)                     # 가림 결과가 정답
    risky_events = {"앱설치링크_누름", "송금_누름", "확인페이지_제출", "신분증사진_제공", "통장사진_제공", "사진_제공", "상품권사진_제공"}
    if mask_types or risky_events & set(events_this_turn):
        j["outcome"] = "risky"                                 # 실제 입력·사건이 있으면 무조건 risky
    if data.get("realConcern"):
        data["personaMessage"] = ""
        data["move"] = "중단"
    turns_left = st.max_turns - st.user_turns
    if turns_left <= 0:
        data["scenarioComplete"] = True
    elif data.get("scenarioComplete") and st.user_turns < st.min_turns and data.get("move") != "마무리":
        data["scenarioComplete"] = False                       # 최소 턴 전 조기 종료 방지
    return data
