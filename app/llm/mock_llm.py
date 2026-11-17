"""API 키 없이 연습할 때 쓰는 가짜 LLM.

진짜 LLM 처럼 똑똑하진 않지만, 진짜와 '같은 모양의 JSON'을 돌려주기 때문에
Spring 연동이나 화면 개발을 LLM 없이도 먼저 해볼 수 있습니다.
"""
import json

# 사용자 답변에 이런 단어가 있으면 해당 카테고리 사실로 뽑음
KEYWORDS = {
    "거래(중고·쇼핑)": ["중고", "당근", "번개장터", "쇼핑", "샀", "팔았", "구매", "주문"],
    "구직·아르바이트": ["알바", "아르바이트", "면접", "취업", "구직", "채용", "이력서"],
    "택배·배송": ["택배", "배송", "배달"],
    "금융·대출": ["대출", "은행", "카드", "적금", "보험"],
    "투자": ["주식", "코인", "투자", "펀드"],
    "가족·지인 연락": ["엄마", "아빠", "부모님", "가족", "친구", "카톡"],
    "공공기관·민원": ["주민센터", "세금", "민원", "정부", "지원금"],
    "여행·이동": ["여행", "다녀왔", "출장", "비행기", "기차"],
}

FOLLOW_UPS = {
    "거래(중고·쇼핑)": "거래는 주로 어떤 앱이나 사이트로 하세요?",
    "구직·아르바이트": "어떤 일자리를 알아보고 계세요? 공고는 어디서 보셨어요?",
    "택배·배송": "주로 어떤 걸 시키시고, 배송 알림은 문자로 받으세요?",
    "금융·대출": "은행 업무는 주로 앱으로 하세요?",
    "투자": "투자 정보는 주로 어디서 얻으세요?",
    "가족·지인 연락": "가족분들과는 주로 어떤 메신저로 연락하세요?",
    "공공기관·민원": "그런 안내는 보통 문자로 받으세요, 앱으로 받으세요?",
    "여행·이동": "예약은 주로 어떤 앱으로 하셨어요?",
}

# SIM-002 항목 추출용 (mock 은 단어로 대충 추정)
CHANNEL_WORDS = {"문자": "문자", "카톡": "메신저", "메신저": "메신저", "DM": "SNS 메시지",
                 "메일": "이메일", "당근": "중고거래 앱 채팅", "채팅": "중고거래 앱 채팅"}
IDENTITY_WORDS = {"택배": "택배사", "은행": "은행", "검찰": "수사기관", "경찰": "수사기관",
                  "엄마": "가족", "아빠": "가족", "아들": "가족", "딸": "가족", "채용": "채용 담당자",
                  "구매자": "중고거래 구매자"}
ACTION_WORDS = {"링크": "링크 클릭", "눌러": "링크 클릭", "송금": "송금", "입금": "송금", "보내": "송금",
                "앱": "앱 설치", "설치": "앱 설치", "정보": "개인정보 제공", "번호": "개인정보 제공"}
RISK_WORDS = {"링크": "출처 불명 링크", "오늘": "시간 압박", "급": "시간 압박", "반송": "불이익 위협",
              "고수익": "고수익 보장", "엄마": "가족 사칭", "인증": "개인정보 요구"}


def _pick(text: str, table: dict) -> str | None:
    for word, value in table.items():
        if word in text:
            return value
    return None


DEFAULT_QUESTIONS = [
    "요즘 온라인으로 물건을 사거나 팔아보신 적 있으세요?",
    "최근에 택배를 기다리고 계신 게 있나요?",
    "요즘 일자리나 아르바이트를 알아보고 계신가요?",
    "가족이나 지인과는 주로 어떤 메신저로 연락하세요?",
]


def respond(system_prompt: str, user_content: str) -> str:
    # 프롬프트 첫 줄의 TASK 표시를 보고 어떤 가짜 응답을 줄지 고름
    if system_prompt.startswith("TASK: scenario"):
        return _respond_scenario(user_content)
    if system_prompt.startswith("TASK: report"):
        return _respond_report(user_content)
    if system_prompt.startswith("TASK: training"):
        return _respond_training(system_prompt, user_content)
    return _respond_context(user_content)


SAFE_WORDS = ["안 ", "않", "싫", "못", "거절", "확인해", "확인할", "공식", "사기", "의심", "직접", "그만", "차단"]
RISKY_WORDS = ["네", "알려드릴", "보낼", "보냈", "눌러", "눌렀", "설치", "할게요", "드릴게요", "입금"]


def _respond_training(system_prompt: str, user_content: str) -> str:
    """훈련 대화용 가짜 응답: 단어로 대충 판정하고, 매 답변마다 다음 장면으로 넘어감."""
    import re

    from app import fallback

    start = system_prompt.index("## 이번 훈련 시나리오") + len("## 이번 훈련 시나리오")
    end = system_prompt.index("## 현재 진행 상황")
    scenario = json.loads(system_prompt[start:end])
    current, total = map(int, re.search(r"지금 장면: (\d+)번 / 전체 (\d+)개", system_prompt).groups())

    last = [line for line in user_content.splitlines() if line.startswith("사용자:")][-1]
    if "[" in last:  # 가려진 개인정보 표시 = 실제로 입력함
        outcome = "risky"
    elif any(w in last for w in SAFE_WORDS):
        outcome = "safe"
    elif any(w in last for w in RISKY_WORDS):
        outcome = "risky"
    else:
        outcome = "neutral"

    scene = scenario["keyScenes"][current - 1]
    actions = [a["id"] for a in scenario["expectedActions"] if a["judgmentPointId"] == scene["judgmentPointId"]]
    complete = current >= total and outcome != "neutral"
    next_scene = current if outcome == "neutral" else min(current + 1, total)
    nxt = scenario["keyScenes"][next_scene - 1]

    if complete:
        message = fallback.TRAINING_END_LINE
    else:
        line = fallback.TRAINING_PERSONA_LINES[nxt["request"]]
        message = line.replace("{info}", ", ".join(nxt["requestedInfo"]) or "본인 확인 정보")

    result = {
        "judgment": {
            "outcome": outcome,
            "userAction": f"(mock) {last.removeprefix('사용자:').strip()[:30]}",
            "achievedActionIds": actions if outcome == "safe" else [],
            "violatedActionIds": actions if outcome == "risky" else [],
            "reason": f"(mock) 단어 기준 {outcome} 판정",
            "disclosedInfo": [],
        },
        "personaMessage": f"(mock) {message}",
        "nextScene": next_scene,
        "requestType": nxt["request"],
        "choices": fallback.TRAINING_CHOICES[nxt["request"]],
        "scenarioComplete": complete,
        "realConcern": False,
    }
    return json.dumps(result, ensure_ascii=False)


def _respond_scenario(user_content: str) -> str:
    """시나리오 생성용 가짜 응답: 사실 카테고리에 맞는 기본 시나리오를 그대로 돌려줌."""
    import copy

    from app.scenario_data import FALLBACK_SCENARIOS, pick_subtype

    facts = []
    for line in user_content.splitlines():
        # "- [거래(중고·쇼핑)] 자전거 판매" 형태의 줄을 읽음
        if line.startswith("- [") and "]" in line:
            category, detail = line[3:].split("]", 1)
            facts.append({"category": category, "detail": detail.strip()})

    result = copy.deepcopy(FALLBACK_SCENARIOS[pick_subtype(facts)])
    result["analysis"]["usedFacts"] = [f["detail"] for f in facts][:3]
    result["scenario"]["title"] = "(mock) " + result["scenario"]["title"]
    return json.dumps(result, ensure_ascii=False)


def _respond_context(user_content: str) -> str:
    answers = _user_answers(user_content)
    last_answer = answers[-1] if answers else ""
    answer_count = len(answers)

    facts = []
    for category, words in KEYWORDS.items():
        if any(w in last_answer for w in words):
            facts.append({
                "category": category,
                "detail": last_answer[:60],
                "channel": _pick(last_answer, CHANNEL_WORDS),
                "impersonatedIdentity": _pick(last_answer, IDENTITY_WORDS),
                "requestedAction": _pick(last_answer, ACTION_WORDS),
                "riskFactors": [v for k, v in RISK_WORDS.items() if k in last_answer],
            })
            break  # mock 은 한 답변에 사실 1개만

    if facts:
        question = FOLLOW_UPS[facts[0]["category"]]
    else:
        question = DEFAULT_QUESTIONS[answer_count % len(DEFAULT_QUESTIONS)]

    result = {
        "next_question": f"(mock) {question}",
        "extracted_facts": facts,
        "enough_info": answer_count >= 4,
    }
    return json.dumps(result, ensure_ascii=False)


def _user_answers(user_content: str) -> list[str]:
    return [
        line.removeprefix("사용자:").strip()
        for line in user_content.splitlines()
        if line.startswith("사용자:")
    ]


def _respond_report(user_content: str) -> str:
    """리포트용 가짜 응답: 판정 기록 줄을 읽어 짧은 문장을 만듦."""
    import re

    feedback, signals = [], []
    for line in user_content.splitlines():
        m = re.match(r"- (\d+)턴 \(장면 \d+\) 판정 (\w+)", line)
        if not m:
            continue
        turn, outcome = int(m.group(1)), m.group(2)
        given = re.search(r"넘긴 정보: (\[.*?\])", line)
        if given:
            text = f"(mock) {given.group(1)}을 넘겼어요. 메시지로 이런 정보를 보내면 명의도용에 쓰일 수 있어요."
        elif outcome == "safe":
            text = "(mock) 요구에 응하지 않고 잘 대응했어요."
        elif outcome == "risky":
            text = "(mock) 상대의 재촉에 응했어요. 공식 앱에서 먼저 확인했어야 해요."
            signals.append({"signal": "(mock) 재촉과 압박", "explanation": "(mock) 서두르게 만드는 건 대표적인 수법이에요.", "turn": turn})
        else:
            text = "(mock) 상대가 누구인지 먼저 확인해 보세요."
        feedback.append({"turn": turn, "feedback": text})
    return json.dumps({
        "headline": "(mock) 훈련 결과",
        "overview": "(mock) 개인정보 요구에 대한 대응을 중심으로 결과를 정리했어요.",
        "turnFeedback": feedback,
        "missedRiskSignals": signals[:3],
    }, ensure_ascii=False)
