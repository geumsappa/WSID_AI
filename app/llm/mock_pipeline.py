"""Schema-only fake provider. It does NOT evaluate real-world meaning or model quality."""
from copy import deepcopy


def respond(task, payload):
    if task == "discovery_brief":
        return {"ready": False, "question": "어떤 일이 있었는지 조금 더 알려주시겠어요?", "detail": None}
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
        from app.case_db import case_ids_for_subtype
        graph = payload["context"]
        events = {event["id"]: event for event in graph["events"]}
        if not graph["focusEventIds"]:
            return {"candidates": [], "selectedPlanId": None, "followupQuestion": "어떤 상황을 겪으셨나요?"}
        plan = {"id": "mock_plan", "anchorClaims": [{"eventId": key, "actorId": events[key]["actorId"]} for key in graph["focusEventIds"]],
                "recipientActorId": graph["userActorId"], "impersonatedActorId": None, "impersonatedRole": "mock 훈련 상대",
                "pretext": "mock 형식 확인", "recipientReason": "mock: 의미 품질을 평가하지 않는 형식 확인용",
                "personalizationBasis": [{"eventId": graph["focusEventIds"][0], "evidenceId": events[graph["focusEventIds"][0]]["evidenceIds"][0], "detail": "mock 구조 검사용", "scenarioImpact": "mock은 실제 맥락 충분성을 평가하지 않음"}],
                "fictionalEvents": ["mock 요청 메시지가 도착함"], "fraudSubtype": "중고거래 사기", "channel": "중고거래 앱 채팅",
                "caseIds": case_ids_for_subtype("중고거래 사기")[:1], "missingInformation": []}
        return {"candidates": [plan], "selectedPlanId": plan["id"], "followupQuestion": ""}
    if task == "semantic_review":
        enough = payload.get("plan") is not None
        return {"approved": enough, "checks": {"factsSupported": True, "actorsPreserved": True, "recipientPlausible": enough,
                "focusPreserved": True, "fictionSeparated": True, "sufficientContext": enough}, "issues": [],
                "followupQuestion": "" if enough else "어떤 상황을 겪으셨나요?"}
    if task == "scenario_generate":
        from app.scenario_data import FALLBACK_SCENARIOS
        plan = payload["plan"]
        content = deepcopy(FALLBACK_SCENARIOS[plan["fraudSubtype"]])
        content["scenario"]["title"] = "(mock) " + content["scenario"]["title"]
        content["scenario"]["channel"] = plan["channel"]
        content["scenario"]["basedOnCaseIds"] = plan["caseIds"]
        content["preservedClaims"] = plan["anchorClaims"]
        return content
    raise ValueError("Unknown pipeline task")
