import copy
import json

from fastapi.testclient import TestClient

from app.main import app
from app.scenario_data import FALLBACK_SCENARIOS
from app.services import training_api, training_turn
from app.guard.scenario_guard import validate_and_normalize


def _fallback(_facts):
    """Test sample only; production generation no longer has a fallback path."""
    result, errors = validate_and_normalize(copy.deepcopy(FALLBACK_SCENARIOS["택배 사칭"]))
    assert not errors
    result["scenario"]["maxTurns"] = 15
    return result


INTERNAL_HEADERS = {"X-Internal-Api-Key": "test-internal-key"}  # tests/conftest.py와 같은 키

def test_virtual_photo_submission_is_risky_without_any_image_in_model_input(monkeypatch):
    captured = []

    def answer(system, content, **kwargs):
        captured.append(system + "\n" + content)
        return {"judgment": {"outcome": "safe"}, "personaMessage": "확인했습니다.", "nextScene": 1}

    monkeypatch.setattr(training_turn, "generate_json", answer)
    scenario = _fallback([])["scenario"]
    scenario["keyScenes"][0]["request"] = "개인정보 제공"
    scenario["keyScenes"][0]["requestedInfo"] = ["신분증 사진"]
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        response = client.post("/internal/ai/simulation/reply", json={
            "scenario": scenario, "messages": [], "userInput": "[가상 신분증 사진 제공]", "turnCount": 1,
        })
    assert response.status_code == 200, response.text
    assert "SENSITIVE_INFO_SHARED" in response.json()["data"]["recognizedActions"]
    assert "RISKY_RESPONSE" in response.json()["data"]["recognizedActions"]
    assert "1턴: 신분증사진_제공" in captured[0]  # 사진 제공은 서버 사건 기록(EVENT_LOG)으로 전달
    assert "base64" not in captured[0]
    assert "data:image" not in captured[0]


def test_promising_a_photo_is_not_recorded_as_actual_photo_provision(monkeypatch):
    monkeypatch.setattr(training_turn, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "risky"}, "personaMessage": "신분증 사진을 보내 주세요.", "nextScene": 1,
    })
    scenario = copy.deepcopy(FALLBACK_SCENARIOS["중고거래 사기"]["scenario"])
    scenario["keyScenes"][0]["request"] = "개인정보 제공"
    scenario["keyScenes"][0]["requestedInfo"] = ["신분증 사진"]
    result = training_turn.run_training_turn("photo", scenario, 1, [{"role": "user", "text": "네 보내드릴게요"}])
    assert not result["infoDisclosure"]["provided"]
    assert "여기에 적어" not in result["personaMessage"]
    assert "신분증 사진" in result["personaMessage"]
    assert "보내" in result["personaMessage"]


def test_bankbook_and_generic_photo_markers_are_disclosure(monkeypatch):
    monkeypatch.setattr(training_turn, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "neutral"}, "personaMessage": "확인했습니다.", "nextScene": 1,
    })
    for marker, subject in [("[가상 통장 사진 제공]", "통장 사진"), ("[가상 사진 제공]", "사진")]:
        result = training_turn.run_training_turn("photo", _fallback([])["scenario"], 1,
                                                 [{"role": "user", "text": marker}])
        assert subject in result["infoDisclosure"]["provided"]
        assert result["judgment"]["outcome"] == "risky"


def test_evaluation_receives_event_and_always_records_photo_risk(monkeypatch):
    captured = []

    def report(system, content, **kwargs):
        captured.append(json.loads(content))
        return {"overallFeedback": "훈련을 마쳤어요.", "strengths": [], "improvements": [], "recommendations": []}

    monkeypatch.setattr(training_api, "generate_json", report)
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        response = client.post("/internal/ai/training/evaluate", json={
            "scenario": _fallback([])["scenario"], "conversation": [],
            "actions": [{"actionCode": "SEND_MOCK_ID_CARD"}],
        })
    assert response.status_code == 200, response.text
    assert captured[0]["actions"] == [{"actionCode": "SEND_MOCK_ID_CARD"}]
    assert any("신분증 사진 제공 요청에 응했어요" in text for text in response.json()["data"]["improvements"])


def test_image_payload_is_rejected_before_model_call(monkeypatch):
    called = []
    monkeypatch.setattr(training_api, "run_training_turn", lambda *a, **k: called.append(True))
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        response = client.post("/internal/ai/simulation/reply", json={
            "scenario": {}, "messages": [], "userInput": "사진", "turnCount": 1,
            "image": "data:image/png;base64,do-not-echo",
        })
    assert response.status_code == 422
    assert not called
    assert "do-not-echo" not in response.text
