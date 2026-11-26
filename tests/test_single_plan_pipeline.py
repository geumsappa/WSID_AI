"""One-plan latency reduction keeps source checking and independent semantic gates."""
from copy import deepcopy
import pytest
from app.api_errors import AiApiError
from app.schemas.discovery import DiscoveryRequest
from app.schemas.training_api import ScenarioRequest
from app.services import discovery, training_api, scenario_planner, scenario
from app.services.prompt_templates import PROMPTS


def ready(graph_data):
    return discovery.discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=graph_data["evidence"][0]["quote"]))


def test_single_plan_and_joint_review_receive_original_evidence_and_same_criteria(monkeypatch, grounded_model, graph_data):
    prompts = []
    generate = scenario_planner.generate_json
    def capture(system, content, **kwargs):
        prompts.append(system)
        return generate(system, content, **kwargs)
    monkeypatch.setattr(scenario_planner, "generate_json", capture)
    monkeypatch.setattr(scenario, "generate_json", capture)
    facts = ready(graph_data).facts
    training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    calls = grounded_model[0]
    assert [task for task, _ in calls[1:]] == ["generation_plan", "semantic_review", "scenario_generate", "draft_review", "scenario_classify"]
    review = next(payload for task, payload in calls if task == "semantic_review")
    assert review["reviewScope"] == "CONTEXT_AND_PLAN"
    draft_review = next(payload for task, payload in calls if task == "draft_review")
    assert draft_review["reviewScope"] == "DRAFT_AGAINST_ACCEPTED_PLAN"
    assert review["userMessages"] == facts["discoveryEvidence"]
    writer = next(payload for task, payload in calls if task == "scenario_generate")
    assert writer["userMessages"] == facts["discoveryEvidence"]
    assert writer["sourceScope"] == review["sourceScope"] == "USER_STATEMENTS"
    criteria = (PROMPTS / "scenario_quality_criteria.md").read_text(encoding="utf-8")
    assert all(criteria in prompt for prompt in prompts if prompt.startswith(("TASK: generation_plan", "TASK: scenario_generate", "TASK: semantic_review")))


def test_plan_with_changed_actor_fails_before_draft_generation(grounded_model, graph_data):
    graph, plan = scenario_planner.prepare_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    body = plan.model_dump()
    body["anchorClaims"][0]["actorId"] = "actor_user"
    grounded_model[0].clear()
    grounded_model[1]["generation_plan"] = {"context": graph.model_dump(), "selectedPlan": body}
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=ready(graph_data).facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 503
    assert not any(task == "scenario_generate" for task, _ in grounded_model[0])


def test_positive_approval_does_not_override_negative_fact_checks(grounded_model, graph_data, review_data):
    verdict = deepcopy(review_data)
    verdict["approved"] = True
    verdict["checks"]["factsSupported"] = False
    grounded_model[1]["semantic_review"] = verdict
    with pytest.raises(AiApiError):
        training_api.scenario(ScenarioRequest(facts=ready(graph_data).facts, fraudTypeCode="SMISHING"))
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


def test_writer_keeps_nonselected_user_statements_and_negations(grounded_model, graph_data):
    source = [{"turn": 1, "text": graph_data["evidence"][0]["quote"]},
              {"turn": 2, "text": "저는 그분에게 선물하거나 돈을 드린 적은 없어요"}]
    training_api.scenario(ScenarioRequest(facts={"discoveryReady": True, "discoveryEvidence": source}, fraudTypeCode="SMISHING"))
    writer = next(payload for task, payload in grounded_model[0] if task == "scenario_generate")
    reviewer = next(payload for task, payload in grounded_model[0] if task == "semantic_review")
    assert len(writer["context"]["evidence"]) == 1  # The extracted summary does not contain turn two.
    assert writer["userMessages"] == reviewer["userMessages"] == source
    assert "드린 적은 없어요" in writer["userMessages"][1]["text"]


def test_source_forwarding_keeps_sensitive_values_masked(grounded_model, graph_data):
    source = graph_data["evidence"][0]["quote"] + " 연락처 010-1234-5678, https://example.com/private"
    scenario.run_scenario("test", [{"detail": source}])
    writer = next(payload for task, payload in grounded_model[0] if task == "scenario_generate")
    reviewer = next(payload for task, payload in grounded_model[0] if task == "semantic_review")
    assert writer["userMessages"] == reviewer["userMessages"]
    assert writer["userMessages"] and writer["sourceScope"] == "LEGACY_FACTS"
    assert "010-1234-5678" not in str(writer["userMessages"])
    assert "example.com" not in str(writer["userMessages"])


def test_saved_context_quotes_are_not_presented_as_a_full_conversation(grounded_model, graph_data, plan_data):
    from app.schemas.context_graph import ContextGraph
    from app.services.context_graph import fingerprint
    scenario.run_scenario("test", [], context=graph_data, plan=plan_data,
                          context_hash=fingerprint(ContextGraph.model_validate(graph_data)))
    writer = next(payload for task, payload in grounded_model[0] if task == "scenario_generate")
    reviewer = next(payload for task, payload in grounded_model[0] if task == "semantic_review")
    assert writer["sourceScope"] == reviewer["sourceScope"] == "RETAINED_QUOTES"
    assert writer["userMessages"] == [{"turn": item["sourceTurn"], "text": item["quote"]} for item in graph_data["evidence"]]
    assert reviewer["userMessages"] == writer["userMessages"]


def test_readiness_reason_is_shared_by_intake_plan_writer_and_review(grounded_model, graph_data):
    facts = ready(graph_data).facts
    basis = facts["readinessBasis"]
    assert basis and basis["contactReason"]
    training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    for task in ("generation_plan", "scenario_generate", "semantic_review"):
        payload = next(p for name, p in grounded_model[0] if name == task)
        assert payload["readinessBasis"] == basis
    from app.services.prompt_templates import load_prompt
    policy = (PROMPTS / "context_sufficiency.md").read_text(encoding="utf-8")
    for name in ("discovery_brief", "discovery_question_review", "single_generation_plan", "semantic_review"):
        prompt = load_prompt(PROMPTS / (name + ".md"))
        assert policy in prompt
        assert "{{CONTEXT_SUFFICIENCY}}" not in prompt


def test_generation_reuses_the_approved_anchor_instead_of_replanning(grounded_model, graph_data):
    facts = ready(graph_data).facts
    grounded_model[0].clear()
    grounded_model[1]["generation_plan"] = lambda _: pytest.fail("Generation must not replan approved context")
    result = training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    assert result.scenario["sourceFacts"][0]["eventId"] == "event_a"
    assert not any(task == "generation_plan" for task, _ in grounded_model[0])


def test_changed_intake_basis_is_rejected_before_model_call(grounded_model, graph_data):
    facts = ready(graph_data).facts
    facts["readinessBasis"]["evidence"][0]["quote"] = "말한 적 없는 사전 결제 약속"
    grounded_model[0].clear()
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 422
    assert not grounded_model[0]


def test_missing_personalization_anchor_is_completed_before_draft(grounded_model, graph_data, plan_data, generation_data):
    graph_data["evidence"].append({"id": "quote_b", "sourceTurn": 1, "quote": "동료와 함께 이용했고"})
    graph_data["events"].append({"id": "event_b", "actorId": "actor_peer", "action": "함께 이용함",
        "participantIds": ["actor_user"], "certainty": "CONFIRMED", "evidenceIds": ["quote_b"], "category": "기타"})
    plan_data["personalizationBasis"].append({"eventId": "event_b", "evidenceId": "quote_b",
        "detail": "함께 이용한 동료", "scenarioImpact": "함께한 경험을 아는 척하는 첫 연락에 활용"})
    # The model omits event_b from anchorClaims; the writer must receive canonical claims.
    generation_data["preservedClaims"].append({"eventId": "event_b", "actorId": "actor_peer"})
    facts = ready(graph_data).facts
    result = training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    writer = next(p for task, p in grounded_model[0] if task == "scenario_generate")
    assert writer["plan"]["anchorClaims"] == [
        {"eventId": "event_a", "actorId": "actor_peer"}, {"eventId": "event_b", "actorId": "actor_peer"}]
    assert result.scenario["title"]
    assert sum(task == "generation_plan" for task, _ in grounded_model[0]) == 1


def test_reference_completion_never_promotes_uncertain_events(grounded_model, graph_data, plan_data):
    graph_data["evidence"].append({"id": "quote_b", "sourceTurn": 1, "quote": "동료와 함께 이용했고"})
    graph_data["events"].append({"id": "event_b", "actorId": "actor_peer", "action": "함께 이용함",
        "participantIds": ["actor_user"], "certainty": "UNCERTAIN", "evidenceIds": ["quote_b"], "category": "기타"})
    plan_data["personalizationBasis"].append({"eventId": "event_b", "evidenceId": "quote_b",
        "detail": "불확실한 사건", "scenarioImpact": "이 사건을 접근에 이용하려는 잘못된 계획"})
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=ready(graph_data).facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 503
    assert not any(task == "scenario_generate" for task, _ in grounded_model[0])
