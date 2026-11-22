from fastapi.testclient import TestClient

from app.main import app
from app.services import discovery


INTERNAL_HEADERS = {"X-Internal-Api-Key": "test-internal-key"}  # tests/conftest.py와 같은 키

def test_spring_contract_reuses_context_logic(monkeypatch):
    captured = {}

    def run(session_id, history, known_facts=None):
        captured.update(history=history, facts=known_facts)
        return {
            "state": {"extractedFacts": [{"category": "거래(중고·쇼핑)", "detail": "물건을 판매함"}]},
            "next_question": "물건을 사는 쪽이었나요?",
            "done": False,
        }

    monkeypatch.setattr(discovery, "run_context_turn", run)
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        response = client.post("/internal/ai/discovery/next", json={
            "conversation": [{"sender": "AI", "content": "최근 어떤 상황을 겪으셨나요?", "phase": "DISCOVERY"}],
            "knownFacts": {"activity": "중고거래"},
            "currentInput": "물건을 팔았어요.",
        })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["facts"]["extractedFacts"][0]["detail"] == "물건을 판매함"
    assert data["data"]["nextQuestion"] == "물건을 사는 쪽이었나요?"
    assert data["data"]["canGenerateScenario"] is False
    assert captured["history"][-1] == {"role": "user", "text": "물건을 팔았어요."}
    assert captured["facts"] == {"activity": "중고거래"}


def test_invalid_request_does_not_echo_input():
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        response = client.post("/internal/ai/discovery/next", json={
            "conversation": [{"sender": "INVALID", "content": "private-input"}],
            "knownFacts": {},
            "currentInput": "current-private-input",
        })
    assert response.status_code == 422
    assert "private-input" not in response.text


def test_mock_model_accepts_spring_request():
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        response = client.post("/internal/ai/discovery/next", json={
            "conversation": [], "knownFacts": {}, "currentInput": "최근 중고거래를 했어요.",
        })
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["facts"], dict)
    assert data["nextQuestion"]
    assert isinstance(data["canGenerateScenario"], bool)
