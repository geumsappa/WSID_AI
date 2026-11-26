"""Repairs edit only narrative fields and must pass the original validators again."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.schemas.context_graph import GeneratedScenario
from app.schemas.scenario_revision import ScenarioRevision
from app.services.scenario_revision import apply_revision
from app.services.context_graph import ContextIntegrityError
from app.services import scenario as service, scenario_planner
from app.api_errors import AiApiError


def test_opening_repair_preserves_verified_claims_and_unedited_story(generation_data):
    draft = GeneratedScenario.model_validate(generation_data)
    before = draft.model_dump()
    fixed = apply_revision(draft, ScenarioRevision.model_validate({"scenario": {"openingMessage": "안녕하세요. 거래 내역에 관해 문의드려요."}}))
    expected = deepcopy(before)
    expected["scenario"]["openingMessage"] = "안녕하세요. 거래 내역에 관해 문의드려요."
    assert fixed.model_dump() == expected
    assert draft.model_dump() == before


def test_repair_cannot_change_context_or_preserved_actor_claims():
    with pytest.raises(ValidationError):
        ScenarioRevision.model_validate({"scenario": {"title": "수정"}, "preservedClaims": [{"eventId": "other", "actorId": "other"}]})
    with pytest.raises(ValidationError):
        ScenarioRevision.model_validate({"scenario": {"title": "수정", "contextGraph": {}}})


def test_partial_persona_and_explicit_page_clear_preserve_other_fields(generation_data):
    draft = GeneratedScenario.model_validate(generation_data)
    draft.scenario.mockPage = "금융"
    fixed = apply_revision(draft, ScenarioRevision.model_validate({"scenario": {"persona": {"tone": "차분함"}, "clearMockPage": True}}))
    assert fixed.scenario.persona.name == draft.scenario.persona.name
    assert fixed.scenario.persona.role == draft.scenario.persona.role
    assert fixed.scenario.persona.tone == "차분함"
    assert fixed.scenario.mockPage is None


def test_empty_invalid_and_noop_repair_are_rejected(generation_data):
    draft = GeneratedScenario.model_validate(generation_data)
    with pytest.raises(ValidationError): ScenarioRevision.model_validate({"scenario": {}})
    with pytest.raises(ValidationError): apply_revision(draft, ScenarioRevision.model_validate({"scenario": {"title": ""}}))
    with pytest.raises(ContextIntegrityError):
        apply_revision(draft, ScenarioRevision.model_validate({"scenario": {"title": draft.scenario.title}}))


def test_pipeline_supplies_previous_draft_and_only_merges_requested_changes(grounded_model, graph_data, review_data, generation_data):
    reviews = []
    def review(payload):
        result = deepcopy(review_data)
        reviews.append(payload)
        if len(reviews) == 1:
            result.update(approved=False, issues=[{"code": "ACTOR_CHANGED", "detail": "첫 대사의 호칭을 수정"}])
            result["checks"]["actorsPreserved"] = False
        return result
    def write(payload):
        if payload["mode"] == "REPAIR":
            assert payload["previousDraft"]["scenario"]["title"] == generation_data["scenario"]["title"]
            assert payload["retryIssues"]
            return {"scenario": {"openingMessage": "안녕. 이용요금 정산에 관해 잠깐 얘기할까?"}}
        return deepcopy(generation_data)
    grounded_model[1].update(semantic_review=review, scenario_generate=write)
    result = service.run_scenario("test", [{"detail": graph_data["evidence"][0]["quote"]}])
    assert result["scenario"]["title"] == generation_data["scenario"]["title"]
    assert result["scenario"]["openingMessage"] == "안녕. 이용요금 정산에 관해 잠깐 얘기할까?"
    assert len(reviews) == 2
    payloads = [payload for task, payload in grounded_model[0] if task == "scenario_generate"]
    assert [p["mode"] for p in payloads] == ["GENERATE", "REPAIR"]
    assert payloads[0]["context"] == payloads[1]["context"]
    assert payloads[0]["plan"] == payloads[1]["plan"]
    assert payloads[0]["userMessages"] == payloads[1]["userMessages"]
    assert payloads[0]["userMessages"] == [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]
    assert all(review["userMessages"] == payloads[0]["userMessages"] for review in reviews)
    assert payloads[0]["sourceScope"] == payloads[1]["sourceScope"] == "LEGACY_FACTS"


def test_repaired_unsafe_output_never_reaches_classification(grounded_model, graph_data, review_data, generation_data):
    bad_review = deepcopy(review_data)
    bad_review.update(approved=False, issues=[{"code": "OTHER", "detail": "도입 수정 필요"}])
    grounded_model[1]["semantic_review"] = bad_review
    grounded_model[1]["scenario_generate"] = lambda payload: ({"scenario": {"openingMessage": "https://unsafe.example.com"}}
        if payload["mode"] == "REPAIR" else deepcopy(generation_data))
    with pytest.raises(AiApiError): service.run_scenario("test", [{"detail": graph_data["evidence"][0]["quote"]}])
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


def test_semantically_rejected_candidates_get_one_feedback_repair(grounded_model, graph_data, plan_data, review_data):
    seen = []
    def comparison(payload):
        seen.append(payload)
        verdict = deepcopy(review_data)
        if len(seen) == 1:
            verdict.update(approved=False, issues=[{"code": "EVENT_CHANGED", "detail": "완료된 활동을 되돌림"}])
            verdict["checks"]["factsSupported"] = False
        return {"alternativesDistinct": True, "distinctionReason": "다른 접근",
            "assessments": [{"candidateId": item["id"], "review": deepcopy(verdict), "grounding": 2,
                "plausibility": 2, "originality": 1, "reason": "근거 검사"} for item in payload["candidates"]]}
    grounded_model[1]["candidate_compare"] = comparison
    graph, selected = scenario_planner.choose_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    proposals = [payload for task, payload in grounded_model[0] if task == "candidate_plan"]
    assert len(proposals) == 2 and len(seen) == 2
    assert proposals[1]["previousProposal"] and proposals[1]["revisionFeedback"]
    assert proposals[1]["messages"] == proposals[0]["messages"]
    assert graph.userActorId == graph_data["userActorId"]
