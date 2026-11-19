"""LLM planning and independent review with shared, domain-independent invariants."""
import json
import re
from pathlib import Path
from pydantic import ValidationError

from app.case_db import cases_for_prompt
from app.config import settings
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import generate_json
from app.schemas.context_graph import ContextGraph, IntakePlan, GenerationIntakePlan, SemanticReview
from app.services.context_graph import ContextIntegrityError, check_graph, check_plan

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def mask_input(text):
    text = mask_sensitive(text)[0]
    return re.sub(r"https?://\S+|www\.\S+|(?<!\w)[\w-]+\.(?:com|net|org|kr|co|io|me|ly)(?:/\S*)?", "[링크]", text, flags=re.IGNORECASE)


def safe_tree(value, key=""):
    references = {"id", "actorId", "userActorId", "eventId", "evidenceIds", "participantIds", "focusEventIds", "recipientActorId", "impersonatedActorId"}
    if isinstance(value, dict):
        return {name: safe_tree(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [safe_tree(item, key) for item in value]
    if isinstance(value, str) and key not in references:
        return mask_input(value)
    return value


def ask(task, payload, model, *, timeout=None, max_tokens=2500, validate=None):
    prompt = (PROMPTS / f"{task}.md").read_text(encoding="utf-8")
    prompt += "\n출력 JSON 스키마:\n" + json.dumps(model.model_json_schema(), ensure_ascii=False)
    content = dict(payload)
    for attempt in range(2):
        raw = generate_json(prompt, json.dumps(content, ensure_ascii=False), timeout=timeout or settings.planning_timeout_seconds,
                            max_tokens=max_tokens, attempts=1, reasoning_effort=settings.pipeline_reasoning_effort or None)
        try:
            result = model.model_validate(safe_tree(raw))
            if validate:
                validate(result)
            return result
        except (ValidationError, ContextIntegrityError) as error:
            if attempt:
                raise
            content["schemaErrors"] = ([{"path": ".".join(map(str, item["loc"])), "type": item["type"]} for item in error.errors()]
                                       if isinstance(error, ValidationError) else [{"path": task, "type": str(error)}])
            content["formatCorrection"] = "오류가 있는 필드를 출력 스키마에 맞춰 다시 생성하세요. 설명은 설명 필드에, 분류는 enum의 정확한 값으로 작성하세요. 사실을 바꾸지 마세요."


def intake(messages, previous=None, previous_plan=None, question_budget=5, asked_questions=None, *, generation_requested=False, readiness_evidence=None):
    from app.scenario_data import ALLOWED_CHANNELS, SUBTYPE_TO_RISK_TYPE
    from app.case_db import load_cases
    references = {subtype: [cid for cid, case in load_cases().items() if case["riskType"] == risk] for subtype, risk in SUBTYPE_TO_RISK_TYPE.items()}

    def validate(result):
        # Grounding is checked before any generated plan can be used.
        check_graph(result.context, messages, previous)
        if result.selectedPlan is not None:
            check_plan(result.selectedPlan, result.context)

    result = ask("intake_plan", {"messages": messages, "previousContext": previous.model_dump() if previous else None,
                               "previousPlan": previous_plan, "questionBudget": question_budget,
                               "generationRequested": generation_requested, "readinessEvidence": readiness_evidence,
                               "previousQuestions": asked_questions or [], "classifications": SUBTYPE_TO_RISK_TYPE,
                               "channels": ALLOWED_CHANNELS, "allowedReferencesBySubtype": references,
                               "referenceTechniques": cases_for_prompt()},
                 GenerationIntakePlan if generation_requested else IntakePlan, max_tokens=3500, validate=validate)
    selected = result.selectedPlan
    if selected is not None and selected.missingInformation:
        selected = None
    return result, selected


def review(graph, plan, *, messages=None, previous=None, generated=None):
    from app.case_db import get_case
    references = []
    for case_id in plan.caseIds if plan else []:
        case = get_case(case_id)
        if case:
            references.append({key: case.get(key) for key in
                               ("id", "riskType", "subtype", "summary", "tactics", "requestedActions", "requestedInfo")})
    payload = {"reviewStage": "GENERATED" if generated is not None else "PLAN",
               "reviewScope": "DRAFT_AGAINST_ACCEPTED_PLAN" if generated is not None else "CONTEXT_AND_PLAN",
               "referenceTechniques": references,
               "context": graph.model_dump(), "plan": plan.model_dump() if plan else None,
               "userMessages": messages or [], "previousContext": previous.model_dump() if previous else None,
               "generated": generated}
    return ask("semantic_review", payload, SemanticReview, timeout=settings.verification_timeout_seconds, max_tokens=1500)


def load_previous(facts):
    raw = facts.get("contextGraph")
    return ContextGraph.model_validate(safe_tree(raw)) if raw is not None else None


def safe_question(text):
    from app.guard.scenario_guard import PHONE_PATTERN, URL_PATTERN
    question = mask_sensitive(" ".join(text.split()))[0][:300]
    if URL_PATTERN.search(question) or PHONE_PATTERN.search(question):
        question = ""
    if question.count("?") > 1:
        question = question.split("?", 1)[0] + "?"
    return question or "말씀하신 상황에서 실제로 어떤 일이 있었는지 조금 더 알려주시겠어요?"
