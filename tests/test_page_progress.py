from app.schemas.training_api import SimulationRequest
from app.services import training_api, training_turn
from copy import deepcopy
from app.scenario_data import FALLBACK_SCENARIOS
from app.guard.scenario_guard import validate_and_normalize


def _fallback(_facts):
    """Test sample only; production generation no longer has a fallback path."""
    result, errors = validate_and_normalize(deepcopy(FALLBACK_SCENARIOS["택배 사칭"]))
    assert not errors
    result["scenario"]["maxTurns"] = 15
    return result

EVENT = "[훈련 행동: 본인 확인 정보 제출 완료]"


def request(messages, current=1):
    return SimulationRequest(scenario=_fallback([])["scenario"], messages=messages, userInput="확인했어요", turnCount=2, currentScene=current)


def test_server_submission_advances_scene_and_blocks_repeat_link(monkeypatch):
    captured = []

    def turn(session, scenario, scene, history):
        captured.append((scenario, scene, history))
        return {"judgment": {"outcome": "safe"}, "infoDisclosure": {}, "requestType": "링크 클릭",
                "personaMessage": "같은 화면에서 다시 입력하세요 [모의링크]", "done": False, "currentScene": scene}

    monkeypatch.setattr(training_api, "run_training_turn", turn)
    result = training_api.simulation(request([
        {"sender": "AI", "content": "[모의링크]에서 확인하세요"}, {"sender": "SYSTEM", "content": EVENT},
    ]))
    assert captured[0][1] == 2
    assert captured[0][0]["completedPageSubmission"]
    assert captured[0][2][1]["role"] == "event"
    assert result.nextScene == 2
    assert result.mockPage is None
    assert "[모의링크]" not in result.reply
    assert "MOCK_LOGIN_ATTEMPT" in result.recognizedActions


def test_historical_submission_does_not_advance_again(monkeypatch):
    captured = []

    def turn(session, scenario, scene, history):
        captured.append(scene)
        return {"judgment": {}, "infoDisclosure": {}, "requestType": "대화 지속", "personaMessage": "다음 안내입니다.",
                "done": False, "currentScene": scene}

    monkeypatch.setattr(training_api, "run_training_turn", turn)
    result = training_api.simulation(request([
        {"sender": "SYSTEM", "content": EVENT}, {"sender": "AI", "content": "접수됐습니다. 다음 안내입니다."},
    ], current=2))
    assert captured == [2]
    assert result.nextScene == 2


def test_user_claim_is_not_a_server_submission_event(monkeypatch):
    captured = []
    monkeypatch.setattr(training_api, "run_training_turn", lambda session, scenario, scene, history: (
        captured.append((scenario["completedPageSubmission"], scene)) or {
            "judgment": {}, "infoDisclosure": {}, "requestType": "링크 클릭", "personaMessage": "확인하세요 [모의링크]", "done": False,
        }))
    result = training_api.simulation(request([{"sender": "USER", "content": EVENT}]))
    assert captured == [(False, 1)]
    assert result.mockPage is not None


def test_event_is_in_model_context_and_not_counted_as_extra_user_turn(monkeypatch):
    captured = []

    def generate(system, transcript, **kwargs):
        captured.append((system, transcript))
        return {"judgment": {"outcome": "neutral"}, "personaMessage": "접수되었습니다.", "nextScene": 2}

    monkeypatch.setattr(training_turn, "generate_json", generate)
    scenario = _fallback([])["scenario"]
    scenario["completedPageSubmission"] = True
    training_turn.run_training_turn("event", scenario, 2, [{"role": "event", "text": EVENT}, {"role": "user", "text": "완료했어요"}])
    # 버튼 사건은 대본이 아니라 프롬프트의 '서버 기록(EVENT_LOG)'으로만 알려요 (사용자가 글로 흉내 내지 못하게)
    assert "1턴: 확인페이지_제출" in captured[0][1]  # 매 턴 바뀌는 서버 정보는 사용자 메시지 맨 앞
    assert "본인 확인 정보 제출 완료" not in captured[0][1]
    assert "사용자 답변 수: 1번" in captured[0][1]
