"""Classification happens after story acceptance and cannot send instructions back to generation."""
from copy import deepcopy
import json

import pytest

from app.api_errors import AiApiError
from app.schemas.training_api import ScenarioRequest
from app.scenario_data import ALLOWED_SUBTYPES
from app.services import scenario_planner, training_api


def create(graph_data, hint="JOB_SCAM"):
    return training_api.scenario(ScenarioRequest(facts={"discoveryReady": True,
        "discoveryEvidence": [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]}, fraudTypeCode=hint))


def label():
    return {"fraudSubtype": "구독 서비스 계정 복구 사칭", "fraudTypeCode": "SMISHING",
            "classificationFit": "APPROXIMATE", "reason": "완성된 문자와 정보 입력 요청을 기준으로 대응",
            "tags": ["계정 복구 사칭", "개인정보 요구"], "references": []}


def test_free_labels_are_attached_after_story_review_without_rewriting(grounded_model, graph_data, generation_data):
    original = deepcopy(generation_data["scenario"])
    grounded_model[1]["scenario_classify"] = label()
    result = create(graph_data)
    assert result.scenario["fraudSubtype"] not in ALLOWED_SUBTYPES
    assert result.scenario["fraudTypeCode"] == "SMISHING"
    assert result.scenario["classificationFit"] == "APPROXIMATE"
    assert result.scenario["tags"] == label()["tags"]
    for key in ("title", "background", "openingMessage", "persona", "channel"):
        assert result.scenario[key] == original[key]
    stages = [task for task, _ in grounded_model[0]]
    assert stages == ["generation_plan", "scenario_generate", "semantic_review", "scenario_classify"]
    classified_story = grounded_model[0][-1][1]["story"]
    assert "fraudSubtype" not in classified_story and "riskType" not in classified_story
    assert result.scenario["basedOnCases"] == [] and result.scenario["referenceStatus"] == "NOT_MATCHED"


def test_old_type_lists_and_hint_are_not_given_to_writers(grounded_model, graph_data, monkeypatch):
    prompts = []
    original = scenario_planner.generate_json
    def capture(system, content, **kwargs):
        if system.startswith("TASK: generation_plan"):
            prompts.append(system)
        return original(system, content, **kwargs)
    monkeypatch.setattr(scenario_planner, "generate_json", capture)
    create(graph_data, hint="INVESTMENT_FRAUD")
    for task, payload in grounded_model[0]:
        if task in ("generation_plan", "scenario_generate", "semantic_review"):
            assert all(key not in payload for key in ("classifications", "compatibilityCodes", "referenceTechniques", "allowedReferencesBySubtype"))
            assert "fraudSubtype" not in json.dumps(payload, ensure_ascii=False)
    assert prompts
    assert all("fraudSubtype" not in prompt and "caseIds" not in prompt for prompt in prompts)


def test_metadata_repair_does_not_regenerate_the_accepted_story(grounded_model, graph_data):
    attempts = []
    def classify(payload):
        attempts.append(deepcopy(payload))
        result = label()
        if len(attempts) == 1:
            result["fraudTypeCode"] = "NEW_UNSUPPORTED_PUBLIC_CODE"
        return result
    grounded_model[1]["scenario_classify"] = classify
    result = create(graph_data)
    assert result.scenario["fraudTypeCode"] == "SMISHING"
    assert len(attempts) == 2
    assert attempts[0]["story"] == attempts[1]["story"]
    assert sum(task == "scenario_generate" for task, _ in grounded_model[0]) == 1


def test_classifier_cannot_return_a_rewritten_background(grounded_model, graph_data, generation_data):
    original = deepcopy(generation_data)
    grounded_model[1]["scenario_classify"] = {**label(), "background": "다른 이야기로 바꿈"}
    with pytest.raises(AiApiError) as error:
        create(graph_data)
    assert error.value.status == 503
    assert generation_data == original
    assert sum(task == "scenario_generate" for task, _ in grounded_model[0]) == 1
    assert sum(task == "scenario_classify" for task, _ in grounded_model[0]) == 2


def test_rejected_story_is_not_sent_for_classification(grounded_model, graph_data, review_data):
    def reject(payload):
        verdict = deepcopy(review_data)
        if payload["generated"] is not None:
            verdict["approved"] = False
            verdict["checks"]["actorsPreserved"] = False
            verdict["issues"] = [{"code": "ACTOR_CHANGED", "detail": "주체가 바뀜"}]
        return verdict
    grounded_model[1]["semantic_review"] = reject
    with pytest.raises(AiApiError):
        create(graph_data)
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


def test_only_actual_reference_mechanics_can_be_attached(grounded_model, graph_data):
    outputs = []
    def classify(payload):
        outputs.append(payload)
        case = payload["referenceTechniques"][0]
        reference = {"caseId": case["id"], "technique": "자료에 없는 기술" if len(outputs) == 1 else case["requestedActions"][0]}
        return {**label(), "references": [reference]}
    grounded_model[1]["scenario_classify"] = classify
    result = create(graph_data)
    assert len(outputs) == 2
    assert outputs[1]["schemaErrors"][0]["type"] == "unsupported_reference_technique"
    assert result.scenario["basedOnCases"][0]["caseId"] == outputs[1]["referenceTechniques"][0]["id"]
    assert result.scenario["referenceStatus"] == "MATCHED"


def _reference_trace():
    from app.services.scenario_retrieval import retrieve_patterns
    trace = retrieve_patterns("가족 지인 휴대폰 고장 메신저 연락")
    assert trace["patterns"]
    return trace


def test_post_classification_sends_only_rag_shortlist(monkeypatch, grounded_model, generation_data):
    from app.services import scenario_classification as module
    trace = _reference_trace()
    calls = []
    def retrieve(query, *, limit):
        calls.append((query, limit))
        return trace
    monkeypatch.setattr(module, "retrieve_patterns", retrieve)
    story = deepcopy(generation_data["scenario"])
    story["sourceFacts"] = [{"detail": "검색에 섞으면 안 되는 숨은 배경 문장"}]
    story["generationEvidence"] = {"usedForGeneration": [{"caseId": "unrelated-before-id"}]}
    before = deepcopy(story)
    module.classify_story(story)
    payload = grounded_model[0][-1][1]
    assert len(calls) == 1 and calls[0][1] == 3
    assert "숨은 배경" not in calls[0][0] and "unrelated-before-id" not in calls[0][0]
    assert [item["id"] for item in payload["referenceTechniques"]] == [item["id"] for item in trace["patterns"]]
    assert len(payload["referenceTechniques"]) <= 3
    assert payload["referenceRetrieval"]["corpusHash"] == trace["corpusHash"]
    assert "source" not in payload["referenceTechniques"][0]
    assert story == before


def test_no_relevant_match_still_classifies_without_attaching_a_source(monkeypatch, grounded_model, generation_data):
    from app.services import scenario_classification as module
    trace = _reference_trace()
    trace.update(patterns=[], status="NO_RELEVANT_PATTERN")
    monkeypatch.setattr(module, "retrieve_patterns", lambda query, limit: trace)
    result = module.classify_story(generation_data["scenario"])
    assert grounded_model[0][-1][1]["referenceTechniques"] == []
    assert result["basedOnCases"] == [] and result["referenceStatus"] == "NOT_MATCHED"


def test_existing_but_unretrieved_pattern_id_is_rejected(monkeypatch, grounded_model, generation_data):
    from app.services import scenario_classification as module
    from app.services.context_graph import ContextIntegrityError
    from app.services.scenario_retrieval import _index
    trace = _reference_trace()
    trace["patterns"] = trace["patterns"][:1]
    available = {row["id"] for row in trace["patterns"]}
    other = next(row for row in _index()[0]["records"] if row["id"] not in available)
    monkeypatch.setattr(module, "retrieve_patterns", lambda query, limit: trace)
    grounded_model[1]["scenario_classify"] = {**label(), "references": [{"caseId": other["id"], "technique": other["requestedActions"][0]}]}
    with pytest.raises(ContextIntegrityError, match="unretrieved_classification_reference"):
        module.classify_story(generation_data["scenario"])
    assert len(grounded_model[0]) == 2
    assert grounded_model[0][-1][1]["schemaErrors"][0]["type"] == "unretrieved_classification_reference"


def test_post_match_source_comes_from_reviewed_registry(monkeypatch, grounded_model, generation_data):
    from app.services import scenario_classification as module
    from app.schemas.sim import CaseRef
    trace = _reference_trace()
    row = trace["patterns"][0]
    monkeypatch.setattr(module, "retrieve_patterns", lambda query, limit: trace)
    grounded_model[1]["scenario_classify"] = {**label(), "references": [{"caseId": row["id"], "technique": row["mechanisms"][0]}]}
    result = module.classify_story(generation_data["scenario"])
    source = result["basedOnCases"][0]
    CaseRef.model_validate(source)
    assert source["caseId"] == row["id"]
    assert source["sourceUrl"] == row["source"]["url"]
    assert source["issuer"] == row["source"]["publisher"]
    assert source["sourceDate"] == row["source"]["publishedDate"]
    assert "generationEvidence" not in result  # Post-hoc matches never become generation evidence.
