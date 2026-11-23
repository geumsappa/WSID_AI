from copy import deepcopy
import json

import pytest


@pytest.fixture
def graph_data():
    return {"version": 1, "userActorId": "actor_user", "entities": [
        {"id": "actor_user", "label": "사용자", "kind": "USER"},
        {"id": "actor_peer", "label": "동료", "kind": "PERSON"}],
        "events": [{"id": "event_a", "actorId": "actor_peer", "action": "비용을 지불함", "object": "이용요금",
                    "participantIds": ["actor_user"], "certainty": "CONFIRMED", "evidenceIds": ["quote_a"], "category": "기타"}],
        "evidence": [{"id": "quote_a", "sourceTurn": 1, "quote": "동료와 함께 이용했고 동료가 이용요금을 냈어요"}],
        "focusEventIds": ["event_a"], "summary": "함께 이용한 동료가 비용을 지불함"}


@pytest.fixture
def plan_data():
    return {"id": "plan_a", "anchorClaims": [{"eventId": "event_a", "actorId": "actor_peer"}],
            "recipientActorId": "actor_user", "impersonatedActorId": "actor_peer", "impersonatedRole": "함께 이용한 동료",
            "personalizationBasis": [{"eventId": "event_a", "evidenceId": "quote_a", "detail": "함께 이용한 동료가 사용자의 이용요금까지 지불함", "scenarioImpact": "동료를 사칭한 정산 요청으로 접근 인물과 구실을 연결함"}],
            "pretext": "이용요금 정산 요청", "recipientReason": "함께 이용한 동료를 사칭해 사용자에게 정산을 요청함",
            "fictionalEvents": ["동료를 사칭한 정산 메시지가 도착함"], "requestedActions": ["개인정보 제공"], "channel": "메신저", "missingInformation": []}


@pytest.fixture
def review_data():
    return {"approved": True, "checks": {"factsSupported": True, "actorsPreserved": True, "recipientPlausible": True,
            "focusPreserved": True, "fictionSeparated": True, "sufficientContext": True, "contextDrivesPlot": True, "nonGenericProgression": True}, "issues": [], "followupQuestion": ""}


@pytest.fixture
def generation_data(plan_data):
    from app.scenario_data import FALLBACK_SCENARIOS
    result = deepcopy(FALLBACK_SCENARIOS["지인·가족 사칭"])
    result["scenario"].update({"title": "이용요금 정산을 요청하는 메시지", "channel": plan_data["channel"],
        "persona": {"name": "동료", "role": "함께 이용한 동료", "organization": "없음", "tone": "친근한 말투"},
        "background": "함께 이용한 동료가 비용을 지불했습니다. 이후 이용요금 정산을 요청하는 메시지가 도착합니다.",
        "openingMessage": "오늘 이용요금 정산 때문에 연락했어. 정산 내역을 확인해 줘."})
    for field in ("fraudSubtype", "riskType", "basedOnCaseIds"):
        result["scenario"].pop(field, None)
    result["analysis"].pop("riskType", None)
    result["analysis"].pop("riskTypeReason", None)
    result["preservedClaims"] = deepcopy(plan_data["anchorClaims"])
    return result


def discovery_fixture_contract(value, payload=None):
    """Add question reasons to legacy scripted fixtures; new policy tests provide explicit assessments."""
    value = deepcopy(value)
    if "questionAssessments" not in value:
        topics = [*value.get("requiredTopics", [])]
        if value.get("nextTopic"):
            topics.append(value["nextTopic"])
        if value.get("personalizationTarget") == "PERSON":
            topics.extend(["PERSON_NAME", "PEOPLE"])
        if value.get("personalizationTarget") == "SERVICE":
            topics.append("SERVICE")
        name = (payload or {}).get("knownTopics", {}).get("PERSON_NAME", {})
        if value.get("personalizationTarget") != "SERVICE" and (name.get("status") == "PARTIAL" or any(
                update.get("topic") == "PERSON_NAME" and update.get("status") == "PARTIAL" for update in value.get("updates", []))):
            topics.append("PERSON_NAME")
        from typing import get_args
        from app.schemas.fast_discovery import Topic
        value["questionAssessments"] = [{"topic": topic, "status": "MISSING",
            "missingDetail": "scripted missing " + topic, "scenarioImpact": "scripted scenario dependency " + topic,
            "reason": "legacy fixture declares this requirement", "evidence": []} for topic in dict.fromkeys(topics) if topic in get_args(Topic)]
    return value


@pytest.fixture
def grounded_model(monkeypatch, graph_data, plan_data, review_data, generation_data):
    from app.services import scenario, scenario_planner, fast_discovery
    calls = []
    overrides = {}
    def generate(system, content, **kwargs):
        task = system.splitlines()[0].removeprefix("TASK: ")
        payload = json.loads(content)
        calls.append((task, payload))
        if task in overrides:
            value = overrides[task]
            value = deepcopy(value(payload) if callable(value) else value)
            if task == "scenario_generate" and payload.get("mode") == "REPAIR" and "preservedClaims" in value:
                value = {"scenario": value["scenario"], "analysis": value["analysis"]}
            return discovery_fixture_contract(value, payload) if task in {"discovery_brief", "discovery_question_review"} else value
        if task == "discovery_question_review":
            return deepcopy(payload["proposedDecision"])
        from app.schemas.context_graph import Approach
        first = {key: value for key, value in plan_data.items() if key in Approach.model_fields}
        first.update(desiredOutcome="정산을 구실로 정보를 요구", escalation="알던 관계를 내세운 뒤 정산 확인을 재촉")
        second = {**first, "id": "plan_b", "impersonatedActorId": None, "impersonatedRole": "가상 이용 안내 담당자",
                  "channel": "이메일", "requestedActions": ["송금"], "pretext": "이용권 예약 안내", "desiredOutcome": "예약을 구실로 송금 요구",
                  "escalation": "이용 안내로 시작해 예약 기한을 강조"}
        candidate_set = {"context": graph_data, "realConcern": False, "anchorClaims": plan_data["anchorClaims"],
                         "personalizationBasis": plan_data["personalizationBasis"], "candidates": [first, second]}
        comparison = {"alternativesDistinct": True, "distinctionReason": "접근 인물과 요구 행동이 다름",
                      "assessments": [{"candidateId": first["id"], "review": review_data, "grounding": 2, "plausibility": 2, "originality": 2, "reason": "기존 관계와 실제 지불 주체를 사용"},
                                      {"candidateId": second["id"], "review": review_data, "grounding": 1, "plausibility": 1, "originality": 1, "reason": "다른 접근 비교"}]}
        result = deepcopy({"candidate_plan": candidate_set, "candidate_compare": comparison, "intake_plan": {"context": graph_data, "realConcern": False, "selectedPlan": None if plan_data.get("missingInformation") else plan_data, "alternativeApproaches": [], "followupQuestion": ""},
                         "semantic_review": review_data, "scenario_generate": generation_data,
                         "scenario_classify": {"fraudSubtype": "지인·가족 사칭", "fraudTypeCode": "MESSENGER_PHISHING", "classificationFit": "EXACT", "reason": "동료를 사칭해 정산을 요청함", "tags": ["지인 사칭", "개인정보 요구"], "references": []},
                         "discovery_brief": {"updates": [{"topic": topic, "status": "KNOWN", "turn": 1,
                            "quote": (graph_data["evidence"] or [{"quote": ""}])[0]["quote"]}
                            for topic in ("ACTIVITY", "TRANSACTION")], "nextTopic": None,
                            "contextSufficient": True, "personalizationTarget": "OTHER"}}[task])
        if task == "scenario_generate" and payload.get("mode") == "REPAIR":
            return {"scenario": result["scenario"], "analysis": result["analysis"]}
        return result
    monkeypatch.setattr(scenario_planner, "generate_json", generate)
    monkeypatch.setattr(scenario, "generate_json", generate)
    monkeypatch.setattr(fast_discovery, "generate_json", generate)
    return calls, overrides


@pytest.fixture
def pipeline_model(grounded_model, monkeypatch):
    """Retain coverage of the existing detailed/legacy grounding contract.

    New public discovery timing and deferred verification use grounded_model directly.
    """
    from app.services import context_turn, discovery
    monkeypatch.setattr(discovery, "run_context_turn", context_turn.run_context_turn)
    return grounded_model

"""모든 내부 API가 X-Internal-Api-Key를 요구하므로 테스트용 키를 공통으로 설정합니다."""
from types import SimpleNamespace

import pytest

INTERNAL_KEY = "test-internal-key"
INTERNAL_HEADERS = {"X-Internal-Api-Key": INTERNAL_KEY}


@pytest.fixture(autouse=True)
def internal_api_key(monkeypatch):
    from app import dependencies
    monkeypatch.setattr(dependencies, "settings", SimpleNamespace(internal_api_key=INTERNAL_KEY))
