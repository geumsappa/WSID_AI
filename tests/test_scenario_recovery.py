"""Rejected drafts recover internally without bypassing source or safety checks."""
from copy import deepcopy

import pytest

from app.api_errors import AiApiError
from app.llm.llm_client import LLMError
from app.schemas.context_graph import ContextGraph
from app.services import scenario, scenario_planner
from app.services.context_graph import fingerprint


def source(graph_data):
    return [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]


def reject_twice(grounded_model, review_data):
    reviews = []
    def review(payload):
        reviews.append(deepcopy(payload))
        verdict = deepcopy(review_data)
        if len(reviews) <= 2:
            verdict.update(approved=False, issues=[{
                "code": "NARRATIVE_MISMATCH",
                "detail": "접근 명분을 실제 이용 관계에 맞게 수정" if len(reviews) == 1 else "앞서 없던 정산 약속을 제거"}])
            verdict["checks"]["recipientPlausible"] = False
        return verdict
    grounded_model[1]["semantic_review"] = review
    return reviews


def test_failed_patch_replans_from_original_and_returns_only_approved_story(grounded_model, graph_data, review_data):
    # Obtain a valid model response once, then make the replanner choose a new plan.
    graph, plan = scenario_planner.prepare_generation_plan(source(graph_data))
    grounded_model[0].clear()
    def proposal(payload):
        body = plan.model_dump()
        if payload.get("mode") == "REPLAN":
            body.update(id="plan_recovered", pretext="실제 이용 경험을 아는 척하는 가상 안내")
        return {"context": graph.model_dump(), "selectedPlan": body}
    grounded_model[1]["generation_plan"] = proposal
    reviews = reject_twice(grounded_model, review_data)
    result = scenario.run_scenario("test", [], discovery_evidence=source(graph_data), discovery_ready=True)
    plans = [payload for task, payload in grounded_model[0] if task == "generation_plan"]
    writers = [payload for task, payload in grounded_model[0] if task == "scenario_generate"]
    assert [payload["mode"] for payload in writers] == ["GENERATE", "REPAIR", "GENERATE"]
    assert len(plans) == 2 and plans[1]["mode"] == "REPLAN"
    assert plans[1]["messages"] == plans[0]["messages"] == source(graph_data)
    assert plans[1]["retrievedPatterns"] == plans[0]["retrievedPatterns"]
    assert plans[1]["previousPlan"]["id"] == "plan_a"
    assert len(plans[1]["recoveryFeedback"]) == 2
    assert writers[2]["plan"]["id"] == "plan_recovered"
    assert "previousDraft" not in writers[2]  # A new plan cannot patch the rejected plan's draft.
    assert writers[2]["retryIssues"] == plans[1]["recoveryFeedback"]
    assert writers[1]["repairHistory"][0]["issues"] == reviews[1]["revisionHistory"][0]["issues"]
    assert all(payload["userMessages"] == source(graph_data) for payload in writers + reviews)
    assert result["review"]["approved"] is True
    assert result["scenario"]["sourceFacts"]
    assert result["contextGraph"] == graph.model_dump()
    assert [task for task, _ in grounded_model[0]].count("scenario_classify") == 1
    assert grounded_model[0][-1][0] == "scenario_classify"


def test_saved_quotes_remain_quotes_after_recovery(grounded_model, graph_data, plan_data, review_data):
    reject_twice(grounded_model, review_data)
    scenario.run_scenario("test", [], context=graph_data, plan=plan_data,
        context_hash=fingerprint(ContextGraph.model_validate(graph_data)))
    writers = [p for task, p in grounded_model[0] if task == "scenario_generate"]
    assert [p["mode"] for p in writers] == ["GENERATE", "REPAIR", "GENERATE"]
    assert all(p["sourceScope"] == "RETAINED_QUOTES" for p in writers)


def test_replanning_cannot_change_confirmed_person(grounded_model, graph_data, review_data):
    graph, plan = scenario_planner.prepare_generation_plan(source(graph_data))
    grounded_model[0].clear()
    reject_twice(grounded_model, review_data)
    def proposal(payload):
        body = plan.model_dump()
        if payload.get("mode") == "REPLAN":
            body.update(impersonatedActorId=None, impersonatedRole="다른 안내 담당자")
        return {"context": graph.model_dump(), "selectedPlan": body}
    grounded_model[1]["generation_plan"] = proposal
    topics = {"PERSON_NAME": {"status": "KNOWN", "turn": 1, "quote": "동료", "name": "동료"}}
    with pytest.raises(AiApiError):
        scenario.run_scenario("test", [], discovery_evidence=source(graph_data), discovery_ready=True,
            discovery_topics=topics, discovery_requirements={"target": "PERSON"})
    assert [p["mode"] for task, p in grounded_model[0] if task == "scenario_generate"] == ["GENERATE", "REPAIR"]
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


def test_provider_outage_does_not_rebuild_a_valid_plan(grounded_model, graph_data):
    def offline(payload):
        raise LLMError("network unavailable")
    grounded_model[1]["scenario_generate"] = offline
    with pytest.raises(AiApiError) as error:
        scenario.run_scenario("test", [], discovery_evidence=source(graph_data), discovery_ready=True)
    assert error.value.status == 503
    assert sum(task == "generation_plan" for task, _ in grounded_model[0]) == 1
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


def test_all_failed_draft_attempts_log_the_stage_without_silent_replanning(grounded_model, graph_data, generation_data, caplog):
    broken = deepcopy(generation_data)
    broken["scenario"]["title"] = "x" * 80
    grounded_model[1]["scenario_generate"] = broken
    with pytest.raises(AiApiError):
        scenario.run_scenario("test", [], discovery_evidence=source(graph_data), discovery_ready=True)
    entries = [record.message for record in caplog.records if "Scenario attempt rejected:" in record.message]
    assert len(entries) == 4
    assert all("stage=DRAFT_SCHEMA" in message and "scenario.title" in message for message in entries)


def test_diagnostic_reasons_redact_names_verbatim_input_and_contact_values(graph_data):
    graph = ContextGraph.model_validate(graph_data)
    graph.entities[1].label = "가상별명"
    messages = [{"turn": 1, "text": "가상 상황에서 함께 물건을 사용했어요"}]
    detail = ("UNSUPPORTED_FACT: 가상별명: 가상 상황에서 함께 물건을 사용했어요 "
              "010-1234-5678 private@example.com https://example.com/private")
    result = " ".join(scenario._diagnostic_failures([detail], graph, messages))
    assert "UNSUPPORTED_FACT" in result
    assert all(secret not in result for secret in (
        "가상별명", messages[0]["text"], "010-1234-5678", "private@example.com", "example.com"))
