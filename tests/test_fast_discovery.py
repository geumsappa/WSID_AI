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


def test_ready_is_advertised_only_after_plan_review(grounded_model, graph_data):
    result = ask(graph_data["evidence"][0]["quote"])
    assert result.canGenerateScenario
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "generation_plan", "semantic_review"]
    assert result.facts["preparedScenario"]
    assert result.facts["discoveryReady"] is True
    assert "scenarioPlan" not in result.facts and "contextGraph" not in result.facts


def test_generation_reuses_plan_verified_during_discovery(grounded_model, graph_data):
    result = ask(graph_data["evidence"][0]["quote"])
    generated = training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    assert generated.scenario["fraudTypeCode"] == "MESSENGER_PHISHING"
    assert generated.scenario["sourceFacts"][0]["actorId"] == "actor_peer"
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "generation_plan", "semantic_review", "scenario_generate", "draft_review", "scenario_classify"]


def test_fast_ready_does_not_bypass_later_semantic_rejection(grounded_model, graph_data, review_data):
    result = ask(graph_data["evidence"][0]["quote"])
    review_data["checks"]["actorsPreserved"] = False
    review_data["approved"] = False
    review_data["issues"] = [{"code": "ACTOR_CHANGED", "detail": "행동 주체를 바꿈"}]
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 503
    assert any(task == "scenario_generate" for task, _ in grounded_model[0])
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


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


def test_exhausted_provider_failure_is_not_replaced_with_a_fake_success(grounded_model):
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
    payload = next(payload for task, payload in grounded_model[0] if task == "generation_plan")
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


def test_generation_repairs_a_missing_plan_but_joint_review_can_still_veto(grounded_model, graph_data, plan_data, review_data):
    from copy import deepcopy
    from app.services import scenario_planner
    response = scenario_planner.prepare_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    plan = response[1].model_dump()
    grounded_model[0].clear()
    count = []
    def proposal(payload):
        count.append(payload)
        return {"context": deepcopy(graph_data), "selectedPlan": None if len(count) == 1 else plan}
    grounded_model[1]["generation_plan"] = proposal
    result = ask(graph_data["evidence"][0]["quote"])
    review_data["approved"] = False
    review_data["checks"]["recipientPlausible"] = False
    review_data["issues"] = [{"code": "RECIPIENT_MISMATCH", "detail": "요청을 받을 근거가 부족함"}]
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 503
    assert len(count) == 2  # Plan schema repair happened before readiness; generation cannot replan.
    assert count[1]["schemaErrors"][0]["path"] == "selectedPlan"
    verdicts = [payload for task, payload in grounded_model[0] if task == "semantic_review"]
    assert verdicts and all(payload["reviewScope"] == "CONTEXT_AND_PLAN" for payload in verdicts)
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


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


def test_discovery_retries_transient_failure_within_shared_budget(monkeypatch):
    from app.llm import llm_client as llm
    from app.services import fast_discovery
    from types import SimpleNamespace
    import json
    now = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(fast_discovery, "settings", SimpleNamespace(discovery_timeout_seconds=60,
        discovery_total_timeout_seconds=110, discovery_reasoning_effort=""))
    calls = []
    def provider(system, content, max_tokens, timeout, **kwargs):
        calls.append(timeout)
        if len(calls) == 1:
            now[0] += 60
            raise TimeoutError()
        return json.dumps({"updates": [{"topic": "ACTIVITY", "status": "KNOWN", "turn": 1, "quote": "코딩했어요"}],
            "personalizationTarget": "UNDECIDED", "contextSufficient": False,
            "requiredTopics": ["ACTIVITY_DETAIL"], "nextTopic": "ACTIVITY_DETAIL", "nextQuestion": "어떤 작업이었나요?",
            "questionAssessments": [{"topic": "ACTIVITY_DETAIL", "status": "MISSING", "missingDetail": "작업 대상",
                "scenarioImpact": "작업과 연결된 접근 명분", "reason": "활동 이름만 제공", "evidence": []}]})
    monkeypatch.setattr(llm, "_call_provider", provider)
    result = fast_discovery.run_discovery_turn("test", [{"role": "user", "text": "코딩했어요"}], {})
    assert calls == [60, 50]
    assert result["next_question"] == "어떤 작업이었나요?"
    assert not result["done"]
    assert llm._generation_deadline.get() is None


def test_confirmed_interaction_skips_question_repair_but_keeps_plan_review(grounded_model):
    text = "별빛서점 직원에게 배송 일정을 물었어요"
    grounded_model[1]["discovery_brief"] = {
        "updates": [{"topic": topic, "turn": 1, "quote": text} for topic in ("ACTIVITY", "SERVICE", "INTERACTION")],
        "personalizationTarget": "SERVICE", "requiredTopics": ["ACCESS"], "contextSufficient": False,
        "readinessBasis": None, "nextTopic": None, "nextQuestion": ""}
    result = ask(text)
    assert result.canGenerateScenario and result.facts["preparedScenario"]
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "generation_plan", "semantic_review"]
    assert result.facts["readinessBasis"]["evidence"] == [{"turn": 1, "quote": text}]


def test_discovery_timing_logs_counts_without_user_text(grounded_model, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="app.services.fast_discovery")
    grounded_model[1]["discovery_brief"] = {
        "updates": [{"topic": "ACTIVITY", "turn": 1, "quote": "빵 먹었어요"}],
        "personalizationTarget": "OTHER", "requiredTopics": ["SETTING"], "nextTopic": "SETTING"}
    ask("빵 먹었어요")
    records = [record for record in caplog.records if record.message.startswith("Discovery timing:")]
    assert len(records) == 1
    assert "decision_calls=1" in records[0].message
    assert "question_review_calls=0" in records[0].message and "plan_ms=0" in records[0].message
    assert "빵 먹었어요" not in caplog.text
