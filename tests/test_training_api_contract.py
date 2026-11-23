from fastapi.testclient import TestClient

from app.main import app


INTERNAL_HEADERS = {"X-Internal-Api-Key": "test-internal-key"}  # tests/conftest.py와 같은 키

def test_spring_scenario_reply_evaluation_contract():
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        scenario_response = client.post("/internal/ai/scenarios/generate", json={
            "facts": {"activity": "중고거래"}, "fraudTypeCode": "SECONDHAND_TRADE",
        })
        assert scenario_response.status_code == 200, scenario_response.text
        scenario = scenario_response.json()["data"]["scenario"]
        assert scenario["title"]
        assert scenario["description"]
        assert scenario["openingMessage"]
        assert scenario["keyScenes"]

        conversation = [{"sender": "AI", "phase": "SIMULATION", "content": scenario["openingMessage"]}]
        turn_response = client.post("/internal/ai/simulation/reply", json={
            "scenario": scenario, "messages": conversation,
            "userInput": "거래 앱에서 직접 확인할게요.", "turnCount": 1,
        })
        assert turn_response.status_code == 200, turn_response.text
        turn = turn_response.json()["data"]
        assert isinstance(turn["reply"], str)
        assert isinstance(turn["recognizedActions"], list)
        assert isinstance(turn["shouldFinish"], bool)
        assert "mockPage" in turn

        report_response = client.post("/internal/ai/training/evaluate", json={
            "scenario": scenario, "conversation": conversation,
            "actions": [{"actionCode": "MOCK_PAGE_OPEN"}, {"actionCode": "OFFICIAL_CHANNEL_CHECK"}],
        })
        assert report_response.status_code == 200, report_response.text
        report = report_response.json()["data"]
        assert report["overallFeedback"]
        assert report["turnFeedback"] == []
        assert report["mockPageEvaluation"] == {
            "openedPage": True, "attemptedMockLogin": False, "checkedOfficialChannel": True, "clickedAppLink": False, "attemptedTransfer": False,
        }
        assert "officialContacts" not in report


def test_invalid_scenario_is_rejected_without_input_echo():
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        response = client.post("/internal/ai/simulation/reply", json={
            "scenario": {"private": "do-not-echo"}, "messages": [],
            "userInput": "private-answer", "turnCount": 1,
        })
    assert response.status_code == 422
    assert "private-answer" not in response.text
    assert "do-not-echo" not in response.text


def test_evaluation_model_failure_returns_safe_error(monkeypatch):
    from app.services import training_api
    from app.llm.llm_client import LLMError

    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        scenario = client.post("/internal/ai/scenarios/generate", json={
            "facts": {"activity": "중고거래"}, "fraudTypeCode": "SECONDHAND_TRADE",
        }).json()["data"]["scenario"]

        def unavailable(*args, **kwargs):
            raise LLMError("private-provider-detail")

        monkeypatch.setattr(training_api, "generate_json", unavailable)
        response = client.post("/internal/ai/training/evaluate", json={
            "scenario": scenario, "conversation": [], "actions": [],
        })
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert "private-provider-detail" not in response.text


def test_masked_card_submission_reaches_simulation_and_records_disclosure(monkeypatch):
    from app.services import training_turn

    monkeypatch.setattr(training_turn, "generate_json", lambda *args, **kwargs: {
        "judgment": {"outcome": "safe"}, "personaMessage": "확인했습니다", "nextScene": 1,
    })
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        scenario = client.post("/internal/ai/scenarios/generate", json={
            "facts": {"activity": "중고거래"}, "fraudTypeCode": "SECONDHAND_TRADE",
        }).json()["data"]["scenario"]
        response = client.post("/internal/ai/simulation/reply", json={
            "scenario": scenario,
            "messages": [{"sender": "AI", "phase": "SIMULATION", "content": "카드번호를 알려 주세요"}],
            "userInput": "카드번호는 [카드번호] 입니다", "turnCount": 1,
        })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert "SENSITIVE_INFO_SHARED" in result["recognizedActions"]
    assert "RISKY_RESPONSE" in result["recognizedActions"]
    assert not result["shouldFinish"]


def test_training_routes_require_internal_key():
    with TestClient(app) as client:
        for path in ("discovery/next", "scenarios/generate", "simulation/reply", "training/evaluate"):
            assert client.post(f"/internal/ai/{path}", json={}).status_code == 401
