"""Schema-only fake provider. It does NOT evaluate real-world meaning or model quality."""
from copy import deepcopy


def respond(task, payload):
    if task == "candidate_plan":
        from app.schemas.context_graph import Approach
        original = respond("intake_plan", payload)
        plan = original["selectedPlan"]
        first = {key: value for key, value in plan.items() if key in Approach.model_fields}
        first.update(desiredOutcome="mock 정보 제공", escalation="mock 진행")
        second = {**first, "id": "mock_other", "impersonatedRole": "mock 다른 접근", "requestedActions": ["송금"],
                  "pretext": "mock 다른 구실", "desiredOutcome": "mock 송금", "escalation": "mock 다른 진행"}
        return {"context": original["context"], "realConcern": False, "anchorClaims": plan["anchorClaims"],
                "personalizationBasis": plan["personalizationBasis"], "candidates": [first, second]}
    if task == "candidate_compare":
        return {"alternativesDistinct": True, "distinctionReason": "mock 구조 확인",
                "assessments": [{"candidateId": plan["id"], "review": respond("semantic_review", {"plan": plan}),
                                 "grounding": 2, "plausibility": 2, "originality": 1, "reason": "mock 구조 확인"} for plan in payload["candidates"]]}
    if task == "discovery_question_review":
        return deepcopy(payload["proposedDecision"])
    if task == "discovery_brief":
        return {"updates": [], "nextTopic": "ACTIVITY", "requiredTopics": ["ACTIVITY"],
                "questionAssessments": [{"topic": "ACTIVITY", "status": "MISSING", "missingDetail": "mock activity",
                    "scenarioImpact": "mock context selection", "reason": "mock contract only", "evidence": []}]}
    if task == "intake_plan":
        extraction = respond("context_extract", payload)
        proposal = respond("scenario_plan", {"context": extraction["context"]})
        return {**extraction, "selectedPlan": next(iter(proposal["candidates"]), None),
                "alternativeApproaches": [], "followupQuestion": proposal["followupQuestion"]}
    if task == "context_extract":
        previous = payload.get("previousContext")
        graph = deepcopy(previous) if previous else {"version": 1, "userActorId": "actor_user", "entities": [{"id": "actor_user", "label": "사용자", "kind": "USER"}],
                                                   "events": [], "evidence": [], "focusEventIds": [], "summary": "mock: 형식 확인용"}
        messages = payload["messages"]
        for message in messages:
            if any(item["sourceTurn"] == message["turn"] for item in graph["evidence"]):
                continue
            suffix = str(message["turn"])
            evidence_id, event_id = "quote_" + suffix, "event_" + suffix
            graph["evidence"].append({"id": evidence_id, "sourceTurn": message["turn"], "quote": message["text"][:180]})
            graph["events"].append({"id": event_id, "actorId": None, "action": "mock 제공 상황", "object": message["text"][:120],
                                    "participantIds": ["actor_user"], "certainty": "CONFIRMED", "evidenceIds": [evidence_id], "category": "기타"})
        graph["events"] = graph["events"][-8:]
        used = {key for event in graph["events"] for key in event["evidenceIds"]}
        graph["evidence"] = [quote for quote in graph["evidence"] if quote["id"] in used]
        graph["focusEventIds"] = [event["id"] for event in graph["events"][-2:]]
        return {"context": graph, "realConcern": False}
    if task == "scenario_plan":
        graph = payload["context"]
        events = {event["id"]: event for event in graph["events"]}
        if not graph["focusEventIds"]:
            return {"candidates": [], "selectedPlanId": None, "followupQuestion": "어떤 상황을 겪으셨나요?"}
        plan = {"id": "mock_plan", "anchorClaims": [{"eventId": key, "actorId": events[key]["actorId"]} for key in graph["focusEventIds"]],
                "recipientActorId": graph["userActorId"], "impersonatedActorId": None, "impersonatedRole": "mock 훈련 상대",
                "pretext": "mock 형식 확인", "recipientReason": "mock: 의미 품질을 평가하지 않는 형식 확인용",
                "personalizationBasis": [{"eventId": graph["focusEventIds"][0], "evidenceId": events[graph["focusEventIds"][0]]["evidenceIds"][0], "detail": "mock 구조 검사용", "scenarioImpact": "mock은 실제 맥락 충분성을 평가하지 않음"}],
                "fictionalEvents": ["mock 요청 메시지가 도착함"], "requestedActions": ["개인정보 제공"], "channel": "중고거래 앱 채팅", "missingInformation": []}
        return {"candidates": [plan], "selectedPlanId": plan["id"], "followupQuestion": ""}
    if task == "semantic_review":
        enough = payload.get("plan") is not None
        return {"approved": enough, "checks": {"factsSupported": True, "actorsPreserved": True, "recipientPlausible": enough,
                "focusPreserved": True, "fictionSeparated": True, "sufficientContext": enough, "contextDrivesPlot": True, "nonGenericProgression": True}, "issues": [],
                "followupQuestion": "" if enough else "어떤 상황을 겪으셨나요?"}
    if task == "scenario_generate":
        from app.scenario_data import FALLBACK_SCENARIOS
        plan = payload["plan"]
        content = deepcopy(FALLBACK_SCENARIOS["중고거래 사기"])
        content["scenario"]["title"] = "(mock) " + content["scenario"]["title"]
        content["scenario"]["channel"] = plan["channel"]
        for field in ("fraudSubtype", "riskType", "basedOnCaseIds"):
            content["scenario"].pop(field, None)
        content["analysis"].pop("riskType", None)
        content["analysis"].pop("riskTypeReason", None)
        content["preservedClaims"] = plan["anchorClaims"]
        if payload.get("mode") == "REPAIR":
            return {"scenario": content["scenario"], "analysis": content["analysis"]}
        return content
    if task == "scenario_classify":
        from app.case_db import case_ids_for_subtype, get_case
        case_id = case_ids_for_subtype("중고거래 사기")[0]
        case = get_case(case_id)
        return {"fraudSubtype": "중고거래 사기", "fraudTypeCode": "SECONDHAND_TRADE", "classificationFit": "EXACT",
                "reason": "mock 분류 인터페이스 점검", "tags": ["mock"], "references": [{"caseId": case_id, "technique": case["requestedActions"][0]}]}
    raise ValueError("Unknown pipeline task")
