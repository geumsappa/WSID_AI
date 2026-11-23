"""API 키 없이 연습할 때 쓰는 가짜 LLM.

진짜 LLM 처럼 똑똑하진 않지만, 진짜와 '같은 모양의 JSON'을 돌려주기 때문에
Spring 연동이나 화면 개발을 LLM 없이도 먼저 해볼 수 있습니다.
"""
import json

def respond(system_prompt: str, user_content: str) -> str:
    task = system_prompt.splitlines()[0].removeprefix("TASK: ")
    if task in ("candidate_plan", "candidate_compare", "discovery_brief", "intake_plan", "semantic_review", "scenario_generate", "scenario_classify"):
        from app.llm.mock_pipeline import respond as pipeline_respond
        return json.dumps(pipeline_respond(task, json.loads(user_content)), ensure_ascii=False)
    if "overallFeedback" in system_prompt and "mockPageEvaluation" in user_content:
        return json.dumps({
            "overallFeedback": "훈련을 완료했어요. 의심스러운 요청은 공식 채널에서 확인하세요.",
            "strengths": [], "improvements": [], "recommendations": ["직접 찾은 공식 채널에서 확인하세요."]
        }, ensure_ascii=False)
    # 프롬프트 첫 줄의 TASK 표시를 보고 어떤 가짜 응답을 줄지 고름
    if system_prompt.startswith("TASK: report"):
        return _respond_report(user_content)
    if system_prompt.startswith("TASK: training"):
        return _respond_training(system_prompt, user_content)
    raise ValueError("Unknown mock LLM task")


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
