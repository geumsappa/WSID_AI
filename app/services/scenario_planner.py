"""LLM planning and independent review with shared, domain-independent invariants."""
import json
import re
import logging
from pathlib import Path
from pydantic import ValidationError

from app.config import settings
from app.guard.sensitive import mask_name_address, mask_sensitive
from app.llm.llm_client import generate_json
from app.schemas.context_graph import ContextGraph, ScenarioPlan, IntakePlan, GenerationIntakePlan, SemanticReview, CandidateProposal, CandidateComparison, SinglePlanProposal
from app.services.context_graph import ContextIntegrityError, check_graph, check_plan
from app.services.scenario_retrieval import retrieve_patterns, prompt_patterns, validate_uses

from app.services.prompt_templates import load_prompt, response_schema

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def mask_input(text):
    text = mask_sensitive(text)[0]
    text = mask_name_address(text)[0]  # 주소·"제 이름은 ○○○"도 외부 AI로 보내기 전에 가려요
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
    prompt_name = ("single_generation_plan" if task == "generation_plan" else "generation_plan" if task == "intake_plan" and payload.get("generationRequested") else task)
    prompt = load_prompt(PROMPTS / f"{prompt_name}.md")
    from app.services.basic_training import BASIC_INPUT_SCOPE, SCOPE_INSTRUCTION
    if BASIC_INPUT_SCOPE.get():
        prompt += SCOPE_INSTRUCTION
    prompt += "\n출력 JSON 스키마:\n" + response_schema(model)
    content = dict(payload)
    for attempt in range(2):
        raw = generate_json(prompt, json.dumps(content, ensure_ascii=False, separators=(",", ":")), timeout=timeout or settings.planning_timeout_seconds,
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


def intake(messages, previous=None, previous_plan=None, question_budget=5, asked_questions=None, *, generation_requested=False, readiness_evidence=None, confirmed_topics=None):
    from app.scenario_data import ALLOWED_CHANNELS, ALLOWED_REQUESTS

    def validate(result):
        # Grounding is checked before any generated plan can be used.
        check_graph(result.context, messages, previous)
        if result.selectedPlan is not None:
            check_plan(result.selectedPlan, result.context)

    previous_plan = {key: value for key, value in previous_plan.items() if key in ScenarioPlan.model_fields} if isinstance(previous_plan, dict) else None
    result = ask("intake_plan", {"messages": messages, "previousContext": previous.model_dump() if previous else None,
                               "previousPlan": previous_plan, "questionBudget": question_budget,
                               "generationRequested": generation_requested, "readinessEvidence": readiness_evidence,
                               "confirmedTopics": confirmed_topics,
                               "previousQuestions": asked_questions or [],
                               "channels": ALLOWED_CHANNELS, "allowedActions": ALLOWED_REQUESTS},
                 GenerationIntakePlan if generation_requested else IntakePlan, max_tokens=3500, validate=validate)
    selected = result.selectedPlan
    if selected is not None and selected.missingInformation:
        selected = None
    return result, selected


def review(graph, plan, *, messages=None, previous=None, generated=None, source_scope=None):
    source_messages = messages if messages is not None else (plan._review_messages if plan else [])
    payload = {"reviewStage": "GENERATED" if generated is not None else "PLAN",
               "sourceScope": source_scope or ("USER_STATEMENTS" if source_messages else "RETAINED_QUOTES"),
               "reviewScope": ("CONTEXT_PLAN_AND_DRAFT" if plan and plan._joint_review_required else "DRAFT_AGAINST_ACCEPTED_PLAN") if generated is not None else "CONTEXT_AND_PLAN",
               "context": graph.model_dump(), "plan": plan.model_dump() if plan else None,
               "userMessages": source_messages, "previousContext": previous.model_dump() if previous else None,
               "generated": generated, "retrievedPatterns": prompt_patterns(plan._retrieval_trace) if plan and plan._retrieval_trace else []}
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



def _approach_plan(proposal, candidate):
    return ScenarioPlan.model_validate({**candidate.model_dump(), "recipientActorId": proposal.context.userActorId,
        "anchorClaims": [claim.model_dump() for claim in proposal.anchorClaims],
        "personalizationBasis": [basis.model_dump() for basis in proposal.personalizationBasis]})


def _check_selected_plan(graph, plan, confirmed_topics=None, personalization_target=None):
    check_plan(plan, graph)
    name_fact = (confirmed_topics or {}).get("PERSON_NAME", {})
    name = name_fact.get("name") if name_fact.get("status") == "KNOWN" else None
    named_actors = {entity.id for entity in graph.entities if entity.kind == "PERSON" and entity.label == name}
    if name and not named_actors:
        raise ContextIntegrityError("confirmed_person_name_missing")
    if (personalization_target == "PERSON" and name_fact.get("status") not in {"KNOWN", "DECLINED"}
            or personalization_target == "OTHER" and name_fact.get("status") == "PARTIAL"):
        raise ContextIntegrityError("person_identity_still_pending")
    people = {entity.id for entity in graph.entities if entity.kind == "PERSON"}
    if plan.impersonatedActorId in people:
        if name_fact.get("status") == "PARTIAL":
            raise ContextIntegrityError("person_identity_still_pending")
        if personalization_target == "SERVICE":
            raise ContextIntegrityError("contact_target_changed_from_service_to_person")
        if name and plan.impersonatedActorId not in named_actors:
            raise ContextIntegrityError("confirmed_person_identity_changed")
    if personalization_target == "PERSON" and name and plan.impersonatedActorId not in named_actors:
        raise ContextIntegrityError("confirmed_person_identity_changed")
    if plan.contactSetup is not None:
        evidence_ids = {item.id for item in graph.evidence}
        if not set(plan.contactSetup.evidenceIds).issubset(evidence_ids):
            raise ContextIntegrityError("unknown_contact_evidence")
        if plan.contactSetup.contactBasis == "FICTIONAL_FIRST_CONTACT" and not plan.contactSetup.fictionalAssumptions:
            raise ContextIntegrityError("missing_fictional_contact_assumption")


def _check_candidates(proposal, messages, confirmed_topics=None, personalization_target=None):
    check_graph(proposal.context, messages)
    ids = [candidate.id for candidate in proposal.candidates]
    if len(set(ids)) != len(ids):
        raise ContextIntegrityError("duplicate_candidate_id")
    signatures = []
    for candidate in proposal.candidates:
        _check_selected_plan(proposal.context, _approach_plan(proposal, candidate), confirmed_topics, personalization_target)
        signatures.append((candidate.impersonatedActorId, candidate.impersonatedRole.strip(), candidate.channel,
                           tuple(sorted(candidate.requestedActions)), candidate.pretext.strip(), candidate.escalation.strip()))
    if signatures[0] == signatures[1]:
        raise ContextIntegrityError("duplicate_candidate_approach")


def prepare_generation_plan(messages, *, readiness_evidence=None, confirmed_topics=None, personalization_target=None):
    """Build one grounded plan; independently review plan and draft together later."""
    from app.scenario_data import ALLOWED_CHANNELS, ALLOWED_REQUESTS
    trace = retrieve_patterns(" ".join(message["text"] for message in messages[-6:]))
    def validate(result):
        check_graph(result.context, messages)
        _check_selected_plan(result.context, result.selectedPlan, confirmed_topics, personalization_target)
        validate_uses(result.selectedPlan.referenceUses, trace)
    result = ask("generation_plan", {"messages": messages, "readinessEvidence": readiness_evidence,
        "confirmedTopics": confirmed_topics, "personalizationTarget": personalization_target,
        "channels": ALLOWED_CHANNELS, "allowedActions": ALLOWED_REQUESTS,
        "retrievedPatterns": prompt_patterns(trace)}, SinglePlanProposal, max_tokens=3000, validate=validate)
    selected = result.selectedPlan
    selected._retrieval_trace = trace
    selected._joint_review_required = True
    selected._review_messages = [dict(message) for message in messages]
    return result.context, selected


def choose_generation_plan(messages, *, readiness_evidence=None, confirmed_topics=None, personalization_target=None, reference_trace=None):
    from app.scenario_data import ALLOWED_CHANNELS, ALLOWED_REQUESTS
    trace = reference_trace if reference_trace is not None else retrieve_patterns(" ".join(message["text"] for message in messages[-6:]))
    def validate_proposal(proposal):
        _check_candidates(proposal, messages, confirmed_topics, personalization_target)
        for candidate in proposal.candidates:
            validate_uses(candidate.referenceUses, trace)
            if reference_trace is not None and not candidate.referenceUses:
                raise ContextIntegrityError("basic_training_requires_case_reference")
    feedback = []
    previous_proposal = None
    for attempt in range(2):
        proposal = ask("candidate_plan", {"messages": messages, "readinessEvidence": readiness_evidence,
            "confirmedTopics": confirmed_topics, "personalizationTarget": personalization_target, "channels": ALLOWED_CHANNELS, "allowedActions": ALLOWED_REQUESTS,
            "revisionFeedback": feedback, "previousProposal": previous_proposal,
            "retrievedPatterns": prompt_patterns(trace)}, CandidateProposal, max_tokens=4000,
            validate=validate_proposal)
        candidates = {candidate.id: _approach_plan(proposal, candidate) for candidate in proposal.candidates}
        def validate_comparison(result):
            ids = [item.candidateId for item in result.assessments]
            if len(set(ids)) != len(ids) or set(ids) != set(candidates):
                raise ContextIntegrityError("candidate_assessment_ids_do_not_match")
        comparison = ask("candidate_compare", {"context": proposal.context.model_dump(), "messages": messages,
            "candidates": [plan.model_dump() for plan in candidates.values()],
            "retrievedPatterns": prompt_patterns(trace)}, CandidateComparison,
            max_tokens=2500, validate=validate_comparison)
        eligible = [item for item in comparison.assessments if item.review.accepts() and item.grounding > 0 and item.plausibility > 0
                    and (not candidates[item.candidateId].referenceUses or item.review.referencesSupported)]
        if comparison.alternativesDistinct and eligible:
            winner = max(eligible, key=lambda item: (item.grounding, item.plausibility, item.originality))
            logger.info("Candidate selected: attempt=%d id=%s", attempt + 1, winner.candidateId)
            selected = candidates[winner.candidateId]
            selected._retrieval_trace = trace
            selected._review_messages = [{"turn": message["turn"], "text": mask_input(message["text"])} for message in messages]
            return proposal.context, selected
        previous_proposal = proposal.model_dump()
        feedback = (["REPETITIVE_PATTERN: " + mask_input(comparison.distinctionReason)] if not comparison.alternativesDistinct else [])
        feedback += [issue.code + ": " + mask_input(issue.detail) for item in comparison.assessments for issue in item.review.issues]
        logger.warning("Candidate comparison rejected: distinct=%s eligible=%d", comparison.alternativesDistinct, len(eligible))
    from app.api_errors import AiApiError
    raise AiApiError(422, "INVALID_REQUEST", "상황에 자연스럽게 연결되는 훈련을 확인하지 못했어요. 상황을 조금 더 알려 주세요.")
