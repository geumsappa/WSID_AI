"""General facts/roles regression checks replacing activity-specific completion rules."""
from copy import deepcopy

import pytest

from app.schemas.context_graph import ContextGraph, ScenarioPlan
from app.schemas.discovery import DiscoveryRequest
from app.services import discovery, scenario_planner
from app.services.context_graph import ContextIntegrityError, check_graph, check_plan


def test_duplicate_or_missing_references_are_rejected(graph_data):
    graph = ContextGraph.model_validate(graph_data)
    graph.entities.append(deepcopy(graph.entities[0]))
    with pytest.raises(ContextIntegrityError):
        check_graph(graph, [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    graph.entities.pop()
    graph.events[0].actorId = "invented_person"
    with pytest.raises(ContextIntegrityError):
        check_graph(graph, [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])


def test_uncertain_or_negated_events_cannot_become_established_plan_facts(graph_data, plan_data):
    for certainty in ("UNCERTAIN", "NEGATED"):
        graph = ContextGraph.model_validate(graph_data)
        graph.events[0].certainty = certainty
        with pytest.raises(ContextIntegrityError):
            check_plan(ScenarioPlan.model_validate(plan_data), graph)


def test_unknown_actor_cannot_be_filled_with_user(graph_data, plan_data):
    graph_data["events"][0]["actorId"] = None
    plan_data["anchorClaims"][0]["actorId"] = "actor_user"
    with pytest.raises(ContextIntegrityError, match="changed_actor"):
        check_plan(ScenarioPlan.model_validate(plan_data), ContextGraph.model_validate(graph_data))


def test_actor_and_activity_category_are_not_the_plan_selection_switch(pipeline_model, graph_data):
    graph_data["events"][0]["category"] = "아직 분류하지 않은 새로운 활동"
    result = discovery.discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=graph_data["evidence"][0]["quote"]))
    assert result.canGenerateScenario
    assert result.facts["scenarioPlan"]["anchorClaims"][0]["actorId"] == "actor_peer"


def test_empty_context_cannot_complete_at_five_turns(pipeline_model, graph_data, review_data):
    graph_data.update(events=[], evidence=[], focusEventIds=[], summary="추가 정보 필요")
    pipeline_model[1]["intake_plan"] = {"context": graph_data, "realConcern": False, "selectedPlan": None, "alternativeApproaches": [], "followupQuestion": "기억나는 활동을 한 가지 말씀해 주시겠어요?"}
    review_data["approved"] = False
    review_data["checks"]["sufficientContext"] = False
    result = discovery.discover(DiscoveryRequest(conversation=[], knownFacts={"intakeTurn": 5}, currentInput="모르겠어요"))
    assert not result.canGenerateScenario and result.facts["extractedFacts"] == []


def test_invalid_source_turn_is_rejected(graph_data):
    graph_data["evidence"][0]["sourceTurn"] = 99
    with pytest.raises(ContextIntegrityError, match="unknown_source_turn"):
        check_graph(ContextGraph.model_validate(graph_data), [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])


def test_planner_sees_questions_separately_from_user_evidence(pipeline_model, graph_data):
    question = "함께 이용한 분이 누구였나요?"
    discovery.discover(DiscoveryRequest(conversation=[{"sender": "AI", "content": question}], knownFacts={}, currentInput=graph_data["evidence"][0]["quote"]))
    extract = next(payload for task, payload in pipeline_model[0] if task == "intake_plan")
    assert all(question != message["text"] for message in extract["messages"])
    assert question in extract["previousQuestions"]
