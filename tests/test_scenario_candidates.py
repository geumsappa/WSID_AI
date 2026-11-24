"""Candidate diversity is gated by grounding, and non-PII objectives remain valid training goals."""
from copy import deepcopy

import pytest

from app.api_errors import AiApiError
from app.schemas.context_graph import CandidateProposal
from app.schemas.training_api import ScenarioRequest
from app.services import scenario_planner, training_api


def proposal(graph, plan):
    from app.schemas.context_graph import Approach
    first = {key: value for key, value in plan.items() if key in Approach.model_fields}
    first.update(desiredOutcome="정보 제공 요구", escalation="아는 관계를 내세운 부탁에서 재촉으로 진행")
    other = {**first, "id": "plan_b", "impersonatedActorId": None, "impersonatedRole": "가상 안내 직원",
             "pretext": "이용권 예약 안내", "channel": "이메일", "requestedActions": ["송금"],
             "desiredOutcome": "가상 예약금 요구", "escalation": "안내에서 예약 기한을 강조하는 흐름"}
    return {"context": graph, "anchorClaims": plan["anchorClaims"], "personalizationBasis": plan["personalizationBasis"],
            "candidates": [first, other]}


def comparison(bundle, verdict):
    return {"alternativesDistinct": True, "distinctionReason": "다른 인물과 요구 행동",
            "assessments": [{"candidateId": item["id"], "review": deepcopy(verdict), "grounding": 2,
                             "plausibility": 2, "originality": index, "reason": "근거와 전개 비교"}
                            for index, item in enumerate(bundle["candidates"])]}


def test_two_candidates_share_evidenced_facts_but_differ_in_approach(grounded_model, graph_data, plan_data, review_data):
    bundle = proposal(graph_data, plan_data)
    judgments = comparison(bundle, review_data)
    judgments["assessments"][0]["grounding"] = 2
    judgments["assessments"][1]["grounding"] = 1
    grounded_model[1].update(candidate_plan=bundle, candidate_compare=judgments)
    graph, winner = scenario_planner.choose_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    assert winner.id == plan_data["id"]
    assert graph.events[0].actorId == "actor_peer"
    assert len([task for task, _ in grounded_model[0] if task == "candidate_plan"]) == 1
    assert len([task for task, _ in grounded_model[0] if task == "candidate_compare"]) == 1


def test_high_originality_cannot_rescue_a_factually_rejected_candidate(grounded_model, graph_data, plan_data, review_data):
    bundle = proposal(graph_data, plan_data)
    judgments = comparison(bundle, review_data)
    judgments["assessments"][1]["originality"] = 2
    judgments["assessments"][1]["review"]["approved"] = False
    judgments["assessments"][1]["review"]["checks"]["actorsPreserved"] = False
    judgments["assessments"][1]["review"]["issues"] = [{"code": "ACTOR_CHANGED", "detail": "주체 변경"}]
    grounded_model[1].update(candidate_plan=bundle, candidate_compare=judgments)
    _, winner = scenario_planner.choose_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    assert winner.id == bundle["candidates"][0]["id"]


def test_identical_candidates_are_rejected_structurally(grounded_model, graph_data, plan_data):
    from app.services.context_graph import ContextIntegrityError
    bundle = proposal(graph_data, plan_data)
    bundle["candidates"][1] = {**bundle["candidates"][0], "id": "other_id"}
    with pytest.raises(ContextIntegrityError, match="duplicate_candidate_approach"):
        scenario_planner._check_candidates(CandidateProposal.model_validate(bundle), [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])


def test_comparison_cannot_evaluate_an_unknown_candidate(grounded_model, graph_data, plan_data, review_data):
    from app.services.context_graph import ContextIntegrityError
    bundle = proposal(graph_data, plan_data)
    judgments = comparison(bundle, review_data)
    judgments["assessments"][1]["candidateId"] = "not_proposed"
    grounded_model[1].update(candidate_plan=bundle, candidate_compare=judgments)
    with pytest.raises(ContextIntegrityError, match="candidate_assessment_ids_do_not_match"):
        scenario_planner.choose_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])


def test_semantic_near_duplicates_trigger_one_candidate_revision(grounded_model, graph_data, plan_data, review_data):
    bundle = proposal(graph_data, plan_data)
    seen = []
    def evaluate(payload):
        seen.append(payload)
        result = comparison(bundle, review_data)
        if len(seen) == 1:
            result.update(alternativesDistinct=False, distinctionReason="표현만 다른 접근")
        return result
    grounded_model[1].update(candidate_plan=bundle, candidate_compare=evaluate)
    scenario_planner.choose_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    proposals = [payload for task, payload in grounded_model[0] if task == "candidate_plan"]
    assert len(proposals) == 2 and proposals[1]["revisionFeedback"]
    assert proposals[0]["messages"] == proposals[1]["messages"]


def test_money_only_goal_reaches_the_public_scenario_contract(grounded_model, graph_data, plan_data, review_data, generation_data):
    plan_data.update(requestedActions=["송금"], desiredOutcome="정산금 요구")
    for scene in generation_data["scenario"]["keyScenes"]:
        scene["request"] = "송금"
        scene["requestedInfo"] = []
    result = training_api.scenario(ScenarioRequest(facts={"discoveryReady": True,
        "discoveryEvidence": [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]}, fraudTypeCode="SMISHING"))
    assert all(scene["request"] == "송금" for scene in result.scenario["keyScenes"])
    assert not any(scene["requestedInfo"] for scene in result.scenario["keyScenes"])


def test_generic_draft_can_be_rejected_even_when_ids_match(grounded_model, graph_data, review_data):
    def verify(payload):
        result = deepcopy(review_data)
        result["approved"] = False
        result["checks"]["nonGenericProgression"] = False
        result["issues"] = [{"code": "REPETITIVE_PATTERN", "detail": "맥락과 무관한 동일 절차"}]
        return result
    grounded_model[1]["semantic_review"] = verify
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts={"discoveryReady": True,
            "discoveryEvidence": [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]}, fraudTypeCode="SMISHING"))
    assert error.value.status == 503
    assert sum(task == "scenario_generate" for task, _ in grounded_model[0]) == 4
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


@pytest.mark.parametrize("bad", [False, True])
def test_contact_setup_preserves_its_evidenced_and_fictional_boundary(graph_data, plan_data, bad):
    from app.services.context_graph import ContextIntegrityError
    bundle = proposal(graph_data, plan_data)
    for item in bundle["candidates"]:
        item["contactSetup"] = {"contactBasis": "FICTIONAL_FIRST_CONTACT", "senderRole": item["impersonatedRole"],
            "contactPremise": "훈련에서 가정한 첫 연락; 실제 연락처 취득은 미확인",
            "knowledgePremise": "실제 사건을 안다고 주장하는 가상 상대",
            "evidenceIds": ["not_a_source"] if bad else [graph_data["evidence"][0]["id"]],
            "fictionalAssumptions": ["이후 도착한다고 가정한 사칭 연락"]}
    parsed = CandidateProposal.model_validate(bundle)
    messages = [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]
    if bad:
        with pytest.raises(ContextIntegrityError, match="unknown_contact_evidence"):
            scenario_planner._check_candidates(parsed, messages)
    else:
        scenario_planner._check_candidates(parsed, messages)
        plan = scenario_planner._approach_plan(parsed, parsed.candidates[0])
        assert plan.contactSetup.contactBasis == "FICTIONAL_FIRST_CONTACT"
        assert plan.contactSetup.knowledgePremise == bundle["candidates"][0]["contactSetup"]["knowledgePremise"]


def test_fictional_contact_requires_an_explicit_assumption(graph_data, plan_data):
    from app.services.context_graph import ContextIntegrityError
    bundle = proposal(graph_data, plan_data)
    bundle["candidates"][0]["contactSetup"] = {"contactBasis": "FICTIONAL_FIRST_CONTACT", "senderRole": "가상 담당자",
        "contactPremise": "가상 연락", "knowledgePremise": "안다고 주장함", "fictionalAssumptions": []}
    with pytest.raises(ContextIntegrityError, match="missing_fictional_contact_assumption"):
        scenario_planner._check_candidates(CandidateProposal.model_validate(bundle), [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
