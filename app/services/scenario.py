"""Generate only from an evidenced, reviewed plan; never return an unrelated template."""
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.api_errors import AiApiError
from app.config import settings
from app.guard.scenario_guard import mentioned_organizations, validate_and_normalize
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError, generate_json, generation_budget
from app.schemas.context_graph import ContextGraph, GeneratedScenario, ScenarioPlan
from app.services import scenario_planner
from app.services.scenario_classification import classify_story
from app.services.scenario_retrieval import retrieve_patterns, prompt_patterns, validate_uses, generation_evidence
from app.services.context_graph import ContextIntegrityError, check_claims, check_graph, check_plan, fingerprint, project_facts

logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "scenario.md"
MAX_ATTEMPTS = 2


def _prepare_context(facts, context, plan, context_hash, discovery_evidence=None, discovery_ready=False, readiness_evidence=None, discovery_topics=None, discovery_requirements=None, basic_trace=None):
    if discovery_evidence is not None:
        from app.services.fast_discovery import load_evidence, load_topics
        from app.schemas.fast_discovery import DetailEvidence, IntakeRequirements
        if discovery_ready is not True:
            raise AiApiError(422, "INVALID_REQUEST", "상황 파악을 먼저 마쳐 주세요.")
        try:
            requirements = IntakeRequirements.model_validate(discovery_requirements or {})
            messages = load_evidence(discovery_evidence)
            discovery_topics = load_topics(discovery_topics or {}, messages)
            if readiness_evidence is not None:
                detail = DetailEvidence.model_validate(readiness_evidence)
                source = next((item["text"] for item in messages if item["turn"] == detail.turn), "")
                if " ".join(detail.quote.split()) not in " ".join(source.split()):
                    raise ValueError("Readiness evidence does not match the user source")
                readiness_evidence = detail.model_dump()
        except (ValueError, ValidationError):
            raise AiApiError(422, "INVALID_REQUEST", "상황 기록 형식을 확인해 주세요.") from None
        return scenario_planner.choose_generation_plan(messages, readiness_evidence=readiness_evidence, confirmed_topics=discovery_topics, personalization_target=requirements.target)
    if context is not None:
        graph = ContextGraph.model_validate(scenario_planner.safe_tree(context))
        # Validate stored quotations against the evidence already retained by the server.
        check_graph(graph, [], graph)
        # Accept existing stored plans, but never carry their old labels into generation.
        plan_body = {key: value for key, value in plan.items() if key not in {"fraudSubtype", "caseIds"}} if isinstance(plan, dict) else plan
        selected = ScenarioPlan.model_validate(plan_body) if plan_body is not None else None
        if selected is None or context_hash != fingerprint(graph):
            raise ContextIntegrityError("missing_or_stale_plan")
        check_plan(selected, graph)
        selected._retrieval_trace = basic_trace if basic_trace is not None else retrieve_patterns(" ".join(item.quote for item in graph.evidence))
        validate_uses(selected.referenceUses, selected._retrieval_trace)
        return graph, selected
    # Backwards compatibility for clients providing legacy facts, with fresh extraction and review.
    details = [fact.get("detail", "") for fact in facts if isinstance(fact, dict) and isinstance(fact.get("detail"), str)]
    if not details or not any(detail.strip() for detail in details):
        raise AiApiError(422, "INVALID_REQUEST", "훈련에 사용할 상황을 먼저 알려 주세요.")
    messages = [{"turn": index + 1, "text": scenario_planner.mask_input(detail)} for index, detail in enumerate(details)]
    return scenario_planner.choose_generation_plan(messages, readiness_evidence=readiness_evidence, confirmed_topics=discovery_topics)


def run_scenario(session_id, facts, *, context=None, plan=None, context_hash=None, discovery_evidence=None, discovery_ready=False, readiness_evidence=None, discovery_topics=None, discovery_requirements=None, basic_trace=None):
    with generation_budget(settings.scenario_total_timeout_seconds):
        return _run_scenario(session_id, facts, context=context, plan=plan, context_hash=context_hash,
            discovery_evidence=discovery_evidence, discovery_ready=discovery_ready,
            readiness_evidence=readiness_evidence, discovery_topics=discovery_topics,
            discovery_requirements=discovery_requirements, basic_trace=basic_trace)


def _run_scenario(session_id, facts, *, context=None, plan=None, context_hash=None, discovery_evidence=None, discovery_ready=False, readiness_evidence=None, discovery_topics=None, discovery_requirements=None, basic_trace=None):
    try:
        graph, selected = _prepare_context(facts, context, plan, context_hash, discovery_evidence, discovery_ready, readiness_evidence, discovery_topics, discovery_requirements, basic_trace)
    except (ValidationError, ContextIntegrityError) as error:
        reason = str(error) if isinstance(error, ContextIntegrityError) else ";".join(
            ".".join(map(str, item["loc"])) + ":" + item["type"] for item in error.errors())
        logger.warning("Scenario preparation failed: type=%s check=%s", type(error).__name__, reason)
        raise AiApiError(422, "INVALID_REQUEST", "상황이 변경되었거나 검증된 계획이 없어요. 상황 파악부터 다시 진행해 주세요.") from None
    except LLMError:
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "시나리오 계획을 확인하지 못했어요.") from None
    name_fact = (discovery_topics or {}).get("PERSON_NAME", {})
    confirmed_name = name_fact.get("name") if name_fact.get("status") == "KNOWN" else None
    named_actor = next((entity for entity in graph.entities if entity.id == selected.impersonatedActorId
                        and entity.kind == "PERSON" and entity.label == confirmed_name), None)
    confirmed_name = named_actor.label if named_actor else None
    source_facts = project_facts(graph)
    allowed = mentioned_organizations(source_facts)
    # Reuse evidenced organizations beyond the small legacy safety-name catalogue.
    evidence_text = " ".join(item.quote for item in graph.evidence)
    verified_organizations = [entity.label for entity in graph.entities
                              if entity.kind == "ORGANIZATION" and entity.label in evidence_text]
    allowed = list(dict.fromkeys([*allowed, *verified_organizations]))
    service_fact = (discovery_topics or {}).get("SERVICE", {})
    service_quote = service_fact.get("quote", "") if service_fact.get("status") == "KNOWN" else ""
    service_names = [name for name in verified_organizations if name in service_quote]

    failures = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            prompt = PROMPT_PATH.read_text(encoding="utf-8")
            from app.services.basic_training import BASIC_INPUT_SCOPE, SCOPE_INSTRUCTION
            if BASIC_INPUT_SCOPE.get():
                prompt += SCOPE_INSTRUCTION
            prompt += "\n출력 JSON 스키마:\n" + json.dumps(GeneratedScenario.model_json_schema(), ensure_ascii=False)
            payload = {"context": graph.model_dump(), "plan": selected.model_dump(),
                       "allowedOrganizations": allowed, "confirmedPersonName": confirmed_name, "confirmedServiceNames": service_names, "retryIssues": failures,
                       "retrievedPatterns": prompt_patterns(selected._retrieval_trace) if selected._retrieval_trace else []}
            generated = GeneratedScenario.model_validate(generate_json(prompt, json.dumps(payload, ensure_ascii=False),
                timeout=settings.scenario_timeout_seconds, max_tokens=4000, attempts=1,
                reasoning_effort=settings.pipeline_reasoning_effort or None))
            # The selected actor's verified display name is server-owned context,
            # not creative text for the model to rename on a body-repair attempt.
            if confirmed_name:
                generated.scenario.persona.name = confirmed_name
            check_claims(generated.preservedClaims, graph)
            if {claim.eventId for claim in generated.preservedClaims} != {claim.eventId for claim in selected.anchorClaims}:
                raise ContextIntegrityError("missing_preserved_claim")
            normalized, errors = validate_and_normalize({"analysis": generated.analysis.model_dump(), "scenario": generated.scenario.model_dump()}, allowed, classified=False)
            if normalized is None:
                failures = ["FORMAT_OR_SAFETY_CHECK_FAILED", *errors[:8]]
                continue
            content = normalized["scenario"]
            if confirmed_name and (content["persona"]["name"] != confirmed_name or confirmed_name not in content["openingMessage"]):
                failures = ["CONFIRMED_PERSON_NAME_NOT_USED"]
                continue
            visible_contact = " ".join([*content["persona"].values(), content["background"], content["openingMessage"]])
            if service_names and not any(name in visible_contact for name in service_names):
                failures = ["CONFIRMED_SERVICE_NAME_NOT_USED"]
                continue
            if content["channel"] != selected.channel:
                failures = ["PLAN_CHANNEL_CHANGED"]
                continue
            if selected.requestedActions and not set(selected.requestedActions).issubset({scene["request"] for scene in content["keyScenes"]}):
                failures = ["PLAN_REQUESTED_ACTIONS_CHANGED"]
                continue
            verdict = scenario_planner.review(graph, selected, generated={"analysis": normalized["analysis"], "scenario": content})
            if not verdict.accepts() or (selected.referenceUses and not verdict.referencesSupported):
                logger.warning("Scenario semantic review rejected: attempt=%d codes=%s", attempt + 1, [issue.code for issue in verdict.issues])
                failures = [issue.code + ": " + scenario_planner.mask_input(issue.detail) for issue in verdict.issues] or ["REFERENCE_OR_SEMANTIC_CHECK_FAILED"]
                continue
            accepted = normalized
            break
        except (LLMError, ValidationError, ContextIntegrityError) as error:
            if isinstance(error, ValidationError):
                failures = ["SCHEMA: " + ".".join(map(str, item["loc"])) + ": " + item["type"] for item in error.errors()][:8]
            else:
                failures = [str(error) if isinstance(error, ContextIntegrityError) else type(error).__name__]
    else:
        logger.warning("Scenario pipeline rejected: checks=%s", failures)
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "상황과 일치하는 시나리오를 검증하지 못했어요. 다시 시도해 주세요.")

    # Classification cannot rewrite or re-generate an already accepted story.
    try:
        metadata = classify_story(accepted["scenario"])
    except (LLMError, ValidationError, ContextIntegrityError) as error:
        logger.warning("Scenario metadata classification failed: type=%s", type(error).__name__)
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "생성한 시나리오의 분류 정보를 확인하지 못했어요. 다시 시도해 주세요.") from None
    accepted["scenario"].update(metadata)
    accepted["analysis"].update(riskType=metadata["riskType"], riskTypeReason=metadata["classificationReason"])
    accepted["scenario"]["generationEvidence"] = generation_evidence(selected)
    accepted["scenario"]["sourceFacts"] = source_facts
    accepted["scenario"]["maxTurns"] = settings.training_max_turns
    accepted["fallback_used"] = False
    accepted["review"] = verdict.model_dump()
    accepted["contextGraph"] = graph.model_dump()
    logger.info("Scenario pipeline accepted: attempt=%d", attempt + 1)
    return accepted
