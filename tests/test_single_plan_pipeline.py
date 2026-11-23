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
    assert [task for task, _ in calls[1:]] == ["generation_plan", "scenario_generate", "semantic_review", "scenario_classify"]
    review = next(payload for task, payload in calls if task == "semantic_review")
    assert review["reviewScope"] == "CONTEXT_PLAN_AND_DRAFT"
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
    assert error.value.status == 422
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
