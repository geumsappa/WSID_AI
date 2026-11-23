"""Explicit opt-in, evidence provenance, and reviewed-case training regression tests."""
from copy import deepcopy
import pytest
from app.api_errors import AiApiError
from app.schemas.training_api import ScenarioRequest
from app.services import basic_training, fast_discovery, scenario_planner, training_api
from app.services.scenario_retrieval import basic_pattern_selection
from test_fast_discovery import ask


def offer(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": None, "stopRequested": True}
    result = ask("없어")
    assert not result.canGenerateScenario
    assert result.facts["basicScenarioOffered"]
    assert result.nextQuestion == basic_training.OFFER
    return result


def test_bare_yes_is_consent_only_after_offer(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": None}
    first = ask("네")
    assert not first.canGenerateScenario
    assert first.facts.get("basicScenarioSelection") is None
    assert not first.facts["basicScenarioOffered"]
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": None, "stopRequested": True}
    paused = ask("더 생각나는 게 없어요", first.facts)
    assert paused.facts["basicScenarioOffered"]
    calls = len(grounded_model[0])
    accepted = ask("네", paused.facts)
    assert accepted.canGenerateScenario
    assert accepted.facts["scenarioMode"] == "BASIC"
    assert accepted.facts["basicScenarioSelection"]["consentTurn"] == 3
    assert accepted.facts["discoveryEvidence"] == [{"turn": 1, "text": "네"}, {"turn": 2, "text": "더 생각나는 게 없어요"}, {"turn": 3, "text": "네"}]
    assert len(grounded_model[0]) == calls  # choice needs no LLM


def test_more_and_decline_never_opt_in(grounded_model):
    paused = offer(grounded_model)
    for answer in ("더 이야기하기", "아니요"):
        result = ask(answer, paused.facts)
        assert not result.canGenerateScenario
        assert result.facts.get("basicScenarioSelection") is None
        assert not result.facts["basicConsentPending"]
        assert basic_training.choice("네", allow_affirmative=False) is None
    result = ask("기본 훈련 시작", result.facts)
    assert result.canGenerateScenario


def test_real_detail_resumes_personalized_path(grounded_model, graph_data):
    paused = offer(grounded_model)
    text = graph_data["evidence"][0]["quote"]
    grounded_model[1]["discovery_brief"] = {"updates": [{"topic": "ACTIVITY", "turn": 2, "quote": text[:180]},
        {"topic": "INTERACTION", "turn": 2, "quote": text[:180]}], "contextSufficient": True,
        "personalizationTarget": "OTHER", "requiredTopics": [], "nextTopic": None}
    resumed = ask(graph_data["evidence"][0]["quote"], paused.facts)
    assert resumed.canGenerateScenario
    assert resumed.facts["scenarioMode"] == "PERSONALIZED"
    assert resumed.facts["basicScenarioSelection"] is None


def test_basic_catalogue_does_not_invent_search_score():
    selected = basic_pattern_selection("zxqvunknown")
    assert selected["status"] == "DEFAULT_CATALOG_SELECTION"
    assert selected["patterns"][0]["score"] is None
    row = selected["patterns"][0]
    assert row["review"]["status"] == "LICENSE_AND_FACTS_CHECKED"
    assert row["source"]["license"]["code"] == "KOGL-1"
    assert basic_pattern_selection("", row["id"])["status"] == "EXPLICIT_CATALOG_SELECTION"
    with pytest.raises(ValueError): basic_pattern_selection("", "missing-case")


def test_basic_generation_requires_actual_choice_record(grounded_model):
    facts = {"scenarioMode": "BASIC", "discoveryReady": True,
             "discoveryEvidence": [{"turn": 1, "text": "없어"}],
             "basicScenarioSelection": {"caseId": "RC-001", "consentTurn": 1}}
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 422
    assert not grounded_model[0]


def test_basic_full_pipeline_keeps_user_facts_separate_and_sources(grounded_model, graph_data, plan_data, review_data):
    accepted = ask("기본 훈련 시작", offer(grounded_model).facts)
    case_id = accepted.facts["basicScenarioSelection"]["caseId"]
    row = basic_pattern_selection("", case_id)["patterns"][0]
    use = {"caseId": case_id, "technique": row["mechanisms"][0], "adaptationReason": "관계 신뢰를 이용하는 수법을 가상 연락에 반영"}
    from app.schemas.context_graph import Approach
    def candidate(payload):
        graph = deepcopy(graph_data)
        graph["evidence"][0]["quote"] = payload["messages"][0]["text"][:180]
        graph["evidence"][0]["sourceTurn"] = 1
        first = {key: deepcopy(value) for key, value in plan_data.items() if key in Approach.model_fields}
        first.update(referenceUses=[use], desiredOutcome="정보 요구", escalation="후속 요구")
        second = {**first, "id": "plan_b", "pretext": "다른 가상 확인 요청", "requestedActions": ["송금"]}
        return {"context": graph, "realConcern": False, "anchorClaims": plan_data["anchorClaims"],
                "personalizationBasis": plan_data["personalizationBasis"], "candidates": [first, second]}
    grounded_model[1]["candidate_plan"] = candidate
    review_data["referencesSupported"] = True
    result = training_api.scenario(ScenarioRequest(facts=accepted.facts, fraudTypeCode="SMISHING"))
    story = result.scenario
    assert story["generationMode"] == "BASIC"
    assert story["sourceFacts"] == []
    assert story["basicCaseId"] == case_id
    assert "[사례 기반 기본 훈련" not in story["background"]
    assert story["fictionalContext"]
    assert story["generationEvidence"]["usedForGeneration"][0]["caseId"] == case_id
    assert story["generationEvidence"]["sourceAttributions"]
    payload = next(p for task, p in grounded_model[0] if task == "candidate_plan")
    assert "가상 훈련 설정" in payload["messages"][0]["text"]
    assert "없어" not in payload["messages"][0]["text"]
    assert basic_training.BASIC_INPUT_SCOPE.get() is False
