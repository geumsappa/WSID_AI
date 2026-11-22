"""Public conversational API uses one call; generation still passes the full grounding pipeline."""
import pytest
from fastapi.testclient import TestClient

from app.api_errors import AiApiError
from app.main import app
from app.schemas.discovery import DiscoveryRequest
from app.schemas.training_api import ScenarioRequest
from app.services import discovery, training_api


def ask(text, facts=None, conversation=None):
    return discovery.discover(DiscoveryRequest(conversation=conversation or [], knownFacts=facts or {}, currentInput=text))


def test_public_discovery_calls_only_the_small_decision(grounded_model, graph_data):
    result = ask(graph_data["evidence"][0]["quote"])
    assert result.canGenerateScenario
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief"]
    assert result.facts["discoveryReady"] is True
    assert "scenarioPlan" not in result.facts and "contextGraph" not in result.facts


def test_full_verification_moves_to_generation(grounded_model, graph_data):
    result = ask(graph_data["evidence"][0]["quote"])
    generated = training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    assert generated.scenario["fraudTypeCode"] == "MESSENGER_PHISHING"
    assert generated.scenario["sourceFacts"][0]["actorId"] == "actor_peer"
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "candidate_plan", "candidate_compare", "scenario_generate", "semantic_review", "scenario_classify"]


def test_fast_ready_does_not_bypass_later_semantic_rejection(grounded_model, graph_data, review_data):
    result = ask(graph_data["evidence"][0]["quote"])
    review_data["checks"]["actorsPreserved"] = False
    review_data["approved"] = False
    review_data["issues"] = [{"code": "ACTOR_CHANGED", "detail": "행동 주체를 바꿈"}]
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 422
    assert not any(task == "scenario_generate" for task, _ in grounded_model[0])


def test_one_question_for_incomplete_context_without_planning(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [{"topic": "ACTIVITY", "turn": 1, "quote": "아아 사왔어"}], "nextTopic": "ACCESS", "requiredTopics": ["ACCESS"]}
    result = ask("아까 카페가서 아아 사왔어")
    assert not result.canGenerateScenario
    assert result.nextQuestion == "그 서비스는 어떤 경로로 이용하셨어요?"
    assert len(grounded_model[0]) == 1
    assert result.facts["discoveryEvidence"][0]["text"] == "아까 카페가서 아아 사왔어"


def test_unknown_readiness_quote_is_not_accepted(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [{"topic": "ACTIVITY", "turn": 1, "quote": "직원이 내 돈을 돌려줬어요"}], "nextTopic": None}
    with pytest.raises(AiApiError):
        ask("카페에 갔어요")
    assert len(grounded_model[0]) == 2


def test_history_compaction_keeps_user_evidence_and_current_turn(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": "ACTIVITY"}
    first = ask("제가 주문했어요")
    second = ask("수령은 동료가 했어요", first.facts)
    assert second.facts["discoveryEvidence"] == [{"turn": 1, "text": "제가 주문했어요"}, {"turn": 2, "text": "수령은 동료가 했어요"}]
    assert grounded_model[0][1][1]["messages"] == second.facts["discoveryEvidence"]


def test_sensitive_details_are_masked_in_quick_input_and_stored_evidence(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": "ACTIVITY"}
    result = ask("010-1234-5678로 연락했고 https://example.com/secret 도 봤어요")
    assert "010-1234-5678" not in str(grounded_model[0]) + str(result.facts)
    assert "example.com" not in str(grounded_model[0]) + str(result.facts)


def test_new_answer_does_not_reuse_a_stale_approved_plan(grounded_model, graph_data):
    known = {"intakeTurn": 8, "scenarioPlan": {"id": "stale"}, "planContextHash": "old"}
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": "ACTIVITY"}
    result = ask("정정할게요", known)
    assert not result.canGenerateScenario
    assert "scenarioPlan" not in result.facts and "planContextHash" not in result.facts


def test_provider_failure_is_not_retried_or_replaced_with_a_fake_success(grounded_model):
    from app.llm.llm_client import LLMError
    def offline(payload):
        raise LLMError("private-provider-message")
    grounded_model[1]["discovery_brief"] = offline
    with TestClient(app) as client:
        response = client.post("/internal/ai/discovery/next", headers={"X-Internal-Api-Key": "test-internal-key"}, json={"conversation": [], "knownFacts": {}, "currentInput": "테스트"})
    assert response.status_code == 503
    assert "private-provider-message" not in response.text
    assert len(grounded_model[0]) == 1


def test_generation_requires_ready_and_valid_original_evidence(grounded_model):
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts={"discoveryEvidence": [{"turn": 1, "text": "카페에 갔어요"}], "discoveryReady": False}, fraudTypeCode="SMISHING"))
    assert error.value.status == 422
    assert grounded_model[0] == []


def test_generation_preserves_all_masked_user_sources_for_fact_extraction(grounded_model, graph_data):
    result = ask(graph_data["evidence"][0]["quote"])
    training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    payload = next(payload for task, payload in grounded_model[0] if task == "candidate_plan")
    assert payload["messages"] == result.facts["discoveryEvidence"]
    assert payload["readinessEvidence"] == result.facts["readinessEvidence"]
    assert payload["confirmedTopics"] == result.facts["knownTopics"]


def test_generation_rejects_a_readiness_hint_not_in_user_sources(grounded_model):
    facts = {"discoveryReady": True, "discoveryEvidence": [{"turn": 1, "text": "카페에 갔어요"}],
             "readinessEvidence": {"turn": 1, "quote": "친구가 대신 주문함"}}
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 422
    assert grounded_model[0] == []


def test_generation_repairs_a_missing_candidate_but_review_can_still_veto(grounded_model, graph_data, plan_data, review_data):
    from app.schemas.context_graph import Approach
    first = {key: value for key, value in plan_data.items() if key in Approach.model_fields}
    first.update(desiredOutcome="정보 요구", escalation="안내 뒤 요청 확대")
    second = {**first, "id": "plan_b", "pretext": "이용권 안내", "requestedActions": ["송금"],
              "desiredOutcome": "예약금 요구", "escalation": "기한을 내세워 송금을 재촉"}
    count = []
    def candidate(payload):
        count.append(payload)
        return {"context": graph_data, "realConcern": False, "anchorClaims": plan_data["anchorClaims"],
                "personalizationBasis": plan_data["personalizationBasis"],
                "candidates": [first] if len(count) == 1 else [first, second]}
    grounded_model[1]["candidate_plan"] = candidate
    result = ask(graph_data["evidence"][0]["quote"])
    review_data["approved"] = False
    review_data["checks"]["recipientPlausible"] = False
    review_data["issues"] = [{"code": "RECIPIENT_MISMATCH", "detail": "요청을 받을 근거가 부족함"}]
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 422
    assert len(count) == 2
    assert count[1]["schemaErrors"][0]["path"] == "candidates"
    assert not any(task == "scenario_generate" for task, _ in grounded_model[0])


def test_generation_uses_writer_instructions_with_shared_grounding_rules(monkeypatch, graph_data, plan_data):
    from app.services import scenario_planner
    captured = []
    def model(system, content, **kwargs):
        captured.append(system)
        return {"context": graph_data, "realConcern": False, "selectedPlan": plan_data,
                "alternativeApproaches": [], "followupQuestion": ""}
    monkeypatch.setattr(scenario_planner, "generate_json", model)
    messages = [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]
    scenario_planner.intake(messages, generation_requested=True)
    scenario_planner.intake(messages)
    assert "지금은 대화 질문자가 아니라" in captured[0]
    assert "정보가 부족하면 selectedPlan=null" not in captured[0]
    assert "정보가 부족하면 selectedPlan=null" in captured[1]
    assert all("messages의 사용자 발언만 근거" in prompt for prompt in captured)
    assert all("{{CONTEXT_FACTS}}" not in prompt for prompt in captured)
