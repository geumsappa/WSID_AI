from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api_errors import AiApiError
from app.main import app
from app.schemas.context_graph import ContextGraph, ScenarioPlan, SemanticReview
from app.schemas.discovery import DiscoveryRequest
from app.schemas.training_api import ScenarioRequest
from app.services import context_turn, discovery, scenario, scenario_planner, training_api
from app.services.context_graph import ContextIntegrityError, check_claims, check_graph, check_plan, fingerprint


def discover_fixture(graph_data):
    return discovery.discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=graph_data["evidence"][0]["quote"]))


def test_legacy_detailed_flow_extracts_plans_reviews_generates_and_reviews_again(pipeline_model, graph_data):
    calls, _ = pipeline_model
    result = discover_fixture(graph_data)
    assert result.canGenerateScenario
    assert result.facts["contextGraph"]["events"][0]["actorId"] == "actor_peer"
    generated = training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SECONDHAND_TRADE"))
    assert generated.scenario["fraudTypeCode"] == "MESSENGER_PHISHING"
    assert "동료가 비용을 지불" in generated.scenario["background"]
    assert generated.scenario["sourceFacts"][0]["actorId"] == "actor_peer"
    assert [task for task, _ in calls] == ["intake_plan", "semantic_review", "scenario_generate", "semantic_review"]


@pytest.mark.parametrize("action", ["비용을 지불함", "예약을 취소함", "서류를 제출함", "물건을 수령함", "일정을 변경함"])
def test_claim_actor_integrity_is_independent_of_activity_keywords(graph_data, action):
    graph = ContextGraph.model_validate(graph_data)
    graph.events[0].action = action
    from app.schemas.context_graph import ClaimBinding
    with pytest.raises(ContextIntegrityError, match="changed_actor"):
        check_claims([ClaimBinding(eventId="event_a", actorId="actor_user")], graph)


def test_fake_quote_is_rejected_before_planning(pipeline_model, graph_data):
    graph_data["evidence"][0]["quote"] = "사용자가 직접 카드로 결제함"
    with pytest.raises(AiApiError) as error:
        discovery.discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="동료가 비용을 냈어요"))
    assert error.value.status == 503
    assert [task for task, _ in pipeline_model[0]] == ["intake_plan", "intake_plan"]


def test_assistant_question_cannot_be_cited_as_user_fact(pipeline_model, graph_data):
    graph_data["evidence"][0]["quote"] = "직접 결제하셨나요?"
    with pytest.raises(AiApiError):
        discovery.discover(DiscoveryRequest(conversation=[{"sender": "AI", "content": "직접 결제하셨나요?"}], knownFacts={}, currentInput="아니요 동료가 했어요"))


def test_plan_cannot_change_actor_or_recipient(pipeline_model, graph_data, plan_data):
    plan_data["anchorClaims"][0]["actorId"] = "actor_user"
    with pytest.raises(AiApiError):
        discover_fixture(graph_data)
    assert not any(task == "scenario_generate" for task, _ in pipeline_model[0])


def test_recipient_reason_requires_semantic_approval(pipeline_model, graph_data, review_data):
    review_data["checks"]["recipientPlausible"] = False
    review_data["issues"] = [{"code": "RECIPIENT_MISMATCH", "detail": "요청이 사용자에게 올 근거가 부족함"}]
    review_data["followupQuestion"] = "함께 이용한 비용은 어떻게 나누기로 하셨나요?"
    result = discover_fixture(graph_data)
    assert not result.canGenerateScenario
    assert "scenarioPlan" not in result.facts
    assert result.facts["contextGraph"]["events"][0]["actorId"] == "actor_peer"
    assert "비용" in result.nextQuestion


def test_bad_fact_extraction_is_not_persisted_even_if_plan_looks_plausible(pipeline_model, graph_data, review_data):
    review_data["checks"]["actorsPreserved"] = False
    review_data["issues"] = [{"code": "ACTOR_CHANGED", "detail": "원래 행동 주체를 바꿈"}]
    result = discover_fixture(graph_data)
    assert not result.canGenerateScenario and result.facts["contextGraph"] is None
    assert result.facts["extractedFacts"] == []


def test_five_turns_and_classifier_labels_do_not_force_completion(pipeline_model, graph_data, plan_data, review_data):
    plan_data["missingInformation"] = ["요청을 받을 이유 확인"]
    review_data["checks"]["sufficientContext"] = False
    review_data["approved"] = False
    graph_data["evidence"][0]["sourceTurn"] = 6
    result = discovery.discover(DiscoveryRequest(conversation=[], knownFacts={"intakeTurn": 5}, currentInput=graph_data["evidence"][0]["quote"]))
    assert not result.canGenerateScenario and "fraudTypeCode" not in result.facts


def test_stage_approval_flag_alone_is_insufficient(review_data):
    review_data["checks"]["actorsPreserved"] = False
    assert not SemanticReview.model_validate(review_data).accepts()
    review_data["checks"]["actorsPreserved"] = "true"
    with pytest.raises(ValidationError):
        SemanticReview.model_validate(review_data)


def test_stale_plan_is_rejected_without_generation(pipeline_model, graph_data):
    result = discover_fixture(graph_data)
    result.facts["contextGraph"]["events"][0]["object"] = "수정된 대상"
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="MESSENGER_PHISHING"))
    assert error.value.status == 422
    assert not any(task == "scenario_generate" for task, _ in pipeline_model[0])


def test_generation_cannot_swap_preserved_claim_actor(pipeline_model, graph_data, generation_data):
    result = discover_fixture(graph_data)
    generation_data["preservedClaims"][0]["actorId"] = "actor_user"
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="MESSENGER_PHISHING"))
    assert error.value.status == 503
    assert sum(task == "scenario_generate" for task, _ in pipeline_model[0]) == 2


def test_matching_ids_do_not_bypass_semantic_review_of_visible_body(pipeline_model, graph_data, generation_data, review_data):
    result = discover_fixture(graph_data)
    generation_data["scenario"]["background"] = "사용자가 직접 비용을 결제했습니다."
    def verify(payload):
        verdict = deepcopy(review_data)
        if payload["generated"]:
            verdict["checks"]["actorsPreserved"] = False
            verdict["approved"] = False
            verdict["issues"] = [{"code": "ACTOR_CHANGED", "detail": "본문이 행동 주체와 모순됨"}]
        return verdict
    pipeline_model[1]["semantic_review"] = verify
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="MESSENGER_PHISHING"))
    assert error.value.status == 503
    assert sum(task == "scenario_generate" for task, _ in pipeline_model[0]) == 2
    assert sum(task == "semantic_review" for task, _ in pipeline_model[0]) == 3


def test_rejected_generation_can_be_repaired_once(pipeline_model, graph_data, review_data):
    result = discover_fixture(graph_data)
    count = []
    def verify(payload):
        verdict = deepcopy(review_data)
        if payload["generated"]:
            count.append(True)
            if len(count) == 1:
                verdict["approved"] = False
                verdict["checks"]["recipientPlausible"] = False
                verdict["issues"] = [{"code": "RECIPIENT_MISMATCH", "detail": "연결을 수정해야 함"}]
        return verdict
    pipeline_model[1]["semantic_review"] = verify
    generated = training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="MESSENGER_PHISHING"))
    assert generated.scenario["fraudSubtype"] == "지인·가족 사칭"
    generations = [payload for task, payload in pipeline_model[0] if task == "scenario_generate"]
    assert len(generations) == 2 and generations[1]["retryIssues"] == ["RECIPIENT_MISMATCH: 연결을 수정해야 함"]


def test_model_failure_never_returns_a_template(pipeline_model, graph_data):
    from app.llm.llm_client import LLMError
    result = discover_fixture(graph_data)
    def offline(payload):
        raise LLMError("private-provider-detail")
    pipeline_model[1]["scenario_generate"] = offline
    with TestClient(app) as client:
        response = client.post("/internal/ai/scenarios/generate", json={"facts": result.facts, "fraudTypeCode": "MESSENGER_PHISHING"})
    assert response.status_code == 503 and "data" not in response.json()
    assert "private-provider-detail" not in response.text


def test_sensitive_input_is_masked_before_all_stages(pipeline_model, graph_data):
    quote = "동료가 이용요금을 냈고 연락처는 [전화번호] 입니다"
    graph_data["evidence"][0]["quote"] = quote
    discovery.discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="동료가 이용요금을 냈고 연락처는 010-1234-5678 입니다"))
    assert "010-1234-5678" not in json.dumps(pipeline_model[0], ensure_ascii=False)


def test_compacted_history_retains_previously_verified_evidence(pipeline_model, graph_data):
    previous = ContextGraph.model_validate(graph_data)
    graph_data["evidence"].append({"id": "quote_b", "sourceTurn": 12, "quote": "추가로 말씀드려요"})
    check_graph(ContextGraph.model_validate(graph_data), [{"turn": 12, "text": "추가로 말씀드려요"}], previous)


def test_raw_links_are_not_sent_to_models_or_stored_as_fact_text(pipeline_model, graph_data):
    graph_data["evidence"][0]["quote"] = "동료와 함께 이용했고 동료가 요금을 냈어요 [링크]"
    result = discovery.discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="동료와 함께 이용했고 동료가 요금을 냈어요 https://example.com/private"))
    assert "example.com" not in json.dumps(pipeline_model[0])
    assert "example.com" not in json.dumps(result.facts)


def test_explicitly_corrected_actor_is_allowed_with_new_evidence(pipeline_model, graph_data):
    previous = deepcopy(graph_data)
    previous["events"][0]["actorId"] = "actor_user"
    previous["evidence"][0]["quote"] = "제가 이용요금을 냈어요"
    graph_data["evidence"][0].update(sourceTurn=2, quote="제가 아니라 동료가 이용요금을 냈어요")
    result = discovery.discover(DiscoveryRequest(conversation=[], knownFacts={"intakeTurn": 1, "contextGraph": previous}, currentInput=graph_data["evidence"][0]["quote"]))
    assert result.canGenerateScenario
    assert result.facts["contextGraph"]["events"][0]["actorId"] == "actor_peer"


def test_new_unseen_domain_uses_same_graph_checks(graph_data, plan_data):
    graph_data["entities"][1]["label"] = "연구소 직원"
    graph_data["events"][0].update({"action": "보관함을 잠금", "object": "연구 장비"})
    graph_data["evidence"][0]["quote"] = "연구소 직원이 연구 장비 보관함을 잠갔어요"
    graph = ContextGraph.model_validate(graph_data)
    check_graph(graph, [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    check_plan(ScenarioPlan.model_validate(plan_data), graph)


def test_routing_label_is_an_enum_not_a_free_form_story_title(plan_data):
    plan_data["fraudSubtype"] = "누군가를 사칭한 새로운 이야기 제목"
    with pytest.raises(ValidationError):
        ScenarioPlan.model_validate(plan_data)


def test_invalid_output_format_is_repaired_without_changing_user_evidence(pipeline_model, graph_data, plan_data):
    outputs = []
    def proposal(payload):
        candidate = deepcopy(plan_data)
        outputs.append(deepcopy(payload))
        if len(outputs) == 1:
            candidate["fraudSubtype"] = "자유롭게 쓴 이야기 제목"
        return {"context": graph_data, "realConcern": False, "selectedPlan": candidate, "alternativeApproaches": [], "followupQuestion": ""}
    pipeline_model[1]["intake_plan"] = proposal
    result = discover_fixture(graph_data)
    assert result.canGenerateScenario and len(outputs) == 2
    assert "schemaErrors" in outputs[1]
    assert outputs[0]["messages"] == outputs[1]["messages"]


def test_reference_mismatch_gets_bounded_structural_repair(pipeline_model, graph_data, plan_data):
    seen = []
    def proposal(payload):
        candidate = deepcopy(plan_data)
        seen.append(deepcopy(payload))
        if len(seen) == 1:
            candidate["caseIds"] = ["NOT_A_CASE"]
        return {"context": graph_data, "realConcern": False, "selectedPlan": candidate, "alternativeApproaches": [], "followupQuestion": ""}
    pipeline_model[1]["intake_plan"] = proposal
    assert discover_fixture(graph_data).canGenerateScenario
    assert len(seen) == 2 and seen[1]["schemaErrors"][0]["type"] == "unrelated_reference"
    assert seen[0]["messages"] == seen[1]["messages"]


def test_review_explicitly_distinguishes_plan_from_generated_text(pipeline_model, graph_data):
    result = discover_fixture(graph_data)
    training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="MESSENGER_PHISHING"))
    reviews = [payload for task, payload in pipeline_model[0] if task == "semantic_review"]
    assert reviews[0]["reviewStage"] == "PLAN" and reviews[0]["generated"] is None
    assert reviews[1]["reviewStage"] == "GENERATED" and reviews[1]["generated"] is not None


def test_selected_intake_plan_cannot_mix_blockers_with_ready_status(graph_data, plan_data):
    from app.schemas.context_graph import IntakePlan
    plan_data["missingInformation"] = ["필수 사용자 사실 미확인"]
    with pytest.raises(ValidationError):
        IntakePlan.model_validate({"context": graph_data, "selectedPlan": plan_data})
    assert IntakePlan.model_validate({"context": graph_data, "selectedPlan": None,
                                      "followupQuestion": "관련 경험을 조금 더 알려주시겠어요?"}).selectedPlan is None


def test_plan_needs_a_grounded_detail_beyond_an_activity_label(graph_data, plan_data):
    plan_data["personalizationBasis"] = []
    with pytest.raises(ContextIntegrityError, match="missing_personalization_basis"):
        check_plan(ScenarioPlan.model_validate(plan_data), ContextGraph.model_validate(graph_data))


def test_context_detail_must_reference_its_actual_event_evidence(graph_data, plan_data):
    plan_data["personalizationBasis"][0]["evidenceId"] = "invented_quote"
    with pytest.raises(ContextIntegrityError, match="unsupported_personalization_basis"):
        check_plan(ScenarioPlan.model_validate(plan_data), ContextGraph.model_validate(graph_data))


@pytest.mark.parametrize("activity", ["음료를 사옴", "구직 중임", "산책을 함"])
def test_plausible_story_alone_cannot_complete_intake(pipeline_model, graph_data, plan_data, review_data, activity):
    graph_data["events"][0].update(action=activity, actorId="actor_user", object=None)
    graph_data["evidence"][0]["quote"] = activity
    plan_data["anchorClaims"][0]["actorId"] = "actor_user"
    plan_data["personalizationBasis"][0].update(detail=activity, scenarioImpact="활동명을 제목에 사용")
    review_data["approved"] = False
    review_data["checks"]["sufficientContext"] = False
    review_data["issues"] = [{"code": "INSUFFICIENT_CONTEXT", "detail": "활동명 외에 훈련을 달라지게 하는 맥락이 없음"}]
    review_data["followupQuestion"] = "그때 어떤 일이 있었는지 조금 더 말씀해 주시겠어요?"
    result = discover_fixture(graph_data)
    assert not result.canGenerateScenario
    assert result.facts["contextGraph"] is not None
    assert "scenarioPlan" not in result.facts
    assert result.nextQuestion == review_data["followupQuestion"]
    assert not any(task == "scenario_generate" for task, _ in pipeline_model[0])


def test_new_context_detail_must_also_be_preserved_as_a_claim(graph_data, plan_data):
    event = deepcopy(graph_data["events"][0])
    event["id"] = "event_b"
    graph_data["events"].append(event)
    plan_data["personalizationBasis"][0]["eventId"] = "event_b"
    with pytest.raises(ContextIntegrityError, match="personalization_event_missing_from_anchor_claims"):
        check_plan(ScenarioPlan.model_validate(plan_data), ContextGraph.model_validate(graph_data))
    plan_data["anchorClaims"].append({"eventId": "event_b", "actorId": event["actorId"]})
    check_plan(ScenarioPlan.model_validate(plan_data), ContextGraph.model_validate(graph_data))


def test_review_has_actual_reference_mechanics_and_explicit_draft_scope(pipeline_model, graph_data, plan_data):
    from app.case_db import get_case
    result = discover_fixture(graph_data)
    training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="MESSENGER_PHISHING"))
    reviews = [payload for task, payload in pipeline_model[0] if task == "semantic_review"]
    assert reviews[0]["reviewScope"] == "CONTEXT_AND_PLAN"
    assert reviews[1]["reviewScope"] == "DRAFT_AGAINST_ACCEPTED_PLAN"
    case = get_case(plan_data["caseIds"][0])
    assert reviews[1]["referenceTechniques"][0]["tactics"] == case["tactics"]
    assert reviews[1]["referenceTechniques"][0]["requestedActions"] == case["requestedActions"]
    assert "sourceUrl" not in reviews[1]["referenceTechniques"][0]
