"""Generate only from an evidenced, reviewed plan; never return an unrelated template."""
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.api_errors import AiApiError
from app.config import settings
from app.guard.scenario_guard import mentioned_organizations, validate_and_normalize
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError, generate_json
from app.schemas.context_graph import ContextGraph, GeneratedScenario, ScenarioPlan
from app.services import scenario_planner
from app.services.context_graph import ContextIntegrityError, check_claims, check_graph, check_plan, fingerprint, project_facts

logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "scenario.md"
MAX_ATTEMPTS = 2


def _prepare_context(facts, context, plan, context_hash, discovery_evidence=None, discovery_ready=False, readiness_evidence=None):
    if discovery_evidence is not None:
        from app.services.fast_discovery import load_evidence
        from app.schemas.fast_discovery import DetailEvidence
        if discovery_ready is not True:
            raise AiApiError(422, "INVALID_REQUEST", "상황 파악을 먼저 마쳐 주세요.")
        try:
            messages = load_evidence(discovery_evidence)
            if readiness_evidence is not None:
                detail = DetailEvidence.model_validate(readiness_evidence)
                source = next((item["text"] for item in messages if item["turn"] == detail.turn), "")
                if " ".join(detail.quote.split()) not in " ".join(source.split()):
                    raise ValueError("Readiness evidence does not match the user source")
                readiness_evidence = detail.model_dump()
        except (ValueError, ValidationError):
            raise AiApiError(422, "INVALID_REQUEST", "상황 기록 형식을 확인해 주세요.") from None
        intake, selected = scenario_planner.intake(messages, generation_requested=True, readiness_evidence=readiness_evidence)
        graph = intake.context
        verdict = scenario_planner.review(graph, selected, messages=messages)
        if selected is None or not verdict.accepts():
            raise AiApiError(422, "INVALID_REQUEST", "시나리오를 만들기 위한 상황이 부족해요. 상황 파악에서 조금 더 알려 주세요.")
        return graph, selected
    if context is not None:
        graph = ContextGraph.model_validate(scenario_planner.safe_tree(context))
        # Validate stored quotations against the evidence already retained by the server.
        check_graph(graph, [], graph)
        selected = ScenarioPlan.model_validate(plan) if plan is not None else None
        if selected is None or context_hash != fingerprint(graph):
            raise ContextIntegrityError("missing_or_stale_plan")
        check_plan(selected, graph)
        return graph, selected
    # Backwards compatibility for clients providing legacy facts, with fresh extraction and review.
    details = [fact.get("detail", "") for fact in facts if isinstance(fact, dict) and isinstance(fact.get("detail"), str)]
    if not details or not any(detail.strip() for detail in details):
        raise AiApiError(422, "INVALID_REQUEST", "훈련에 사용할 상황을 먼저 알려 주세요.")
    messages = [{"turn": index + 1, "text": scenario_planner.mask_input(detail)} for index, detail in enumerate(details)]
    intake, selected = scenario_planner.intake(messages, generation_requested=True, readiness_evidence=readiness_evidence)
    graph = intake.context
    verdict = scenario_planner.review(graph, selected, messages=messages)
    if selected is None or not verdict.accepts():
        raise AiApiError(422, "INVALID_REQUEST", "시나리오에 필요한 상황을 먼저 확인해 주세요.")
    return graph, selected


def run_scenario(session_id, facts, *, context=None, plan=None, context_hash=None, discovery_evidence=None, discovery_ready=False, readiness_evidence=None):
    try:
        graph, selected = _prepare_context(facts, context, plan, context_hash, discovery_evidence, discovery_ready, readiness_evidence)
    except (ValidationError, ContextIntegrityError) as error:
        reason = str(error) if isinstance(error, ContextIntegrityError) else ";".join(
            ".".join(map(str, item["loc"])) + ":" + item["type"] for item in error.errors())
        logger.warning("Scenario preparation failed: type=%s check=%s", type(error).__name__, reason)
        raise AiApiError(422, "INVALID_REQUEST", "상황이 변경되었거나 검증된 계획이 없어요. 상황 파악부터 다시 진행해 주세요.") from None
    except LLMError:
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "시나리오 계획을 확인하지 못했어요.") from None
    source_facts = project_facts(graph)
    allowed = mentioned_organizations(source_facts)
    failures = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            prompt = PROMPT_PATH.read_text(encoding="utf-8")
            prompt += "\n출력 JSON 스키마:\n" + json.dumps(GeneratedScenario.model_json_schema(), ensure_ascii=False)
            payload = {"context": graph.model_dump(), "plan": selected.model_dump(),
                       "allowedOrganizations": allowed, "retryIssues": failures}
            generated = GeneratedScenario.model_validate(generate_json(prompt, json.dumps(payload, ensure_ascii=False),
                timeout=settings.scenario_timeout_seconds, max_tokens=4000, attempts=1,
                reasoning_effort=settings.pipeline_reasoning_effort or None))
            check_claims(generated.preservedClaims, graph)
            if {claim.eventId for claim in generated.preservedClaims} != {claim.eventId for claim in selected.anchorClaims}:
                raise ContextIntegrityError("missing_preserved_claim")
            normalized, errors = validate_and_normalize({"analysis": generated.analysis.model_dump(), "scenario": generated.scenario.model_dump()}, allowed)
            if normalized is None:
                failures = ["FORMAT_OR_SAFETY_CHECK_FAILED", *errors[:8]]
                continue
            content = normalized["scenario"]
            if content["fraudSubtype"] != selected.fraudSubtype or content["channel"] != selected.channel:
                failures = ["PLAN_CLASSIFICATION_OR_CHANNEL_CHANGED"]
                continue
            if {case["caseId"] for case in content["basedOnCases"]} != set(selected.caseIds):
                failures = ["PLAN_REFERENCES_CHANGED"]
                continue
            verdict = scenario_planner.review(graph, selected, generated={"analysis": normalized["analysis"], "scenario": content})
            if not verdict.accepts():
                logger.warning("Scenario semantic review rejected: attempt=%d codes=%s", attempt + 1, [issue.code for issue in verdict.issues])
                failures = [issue.code + ": " + scenario_planner.mask_input(issue.detail) for issue in verdict.issues] or ["SEMANTIC_CHECK_FAILED"]
                continue
            content["sourceFacts"] = source_facts
            content["maxTurns"] = settings.training_max_turns
            normalized["fallback_used"] = False
            normalized["review"] = verdict.model_dump()
            normalized["contextGraph"] = graph.model_dump()
            logger.info("Scenario pipeline accepted: attempt=%d", attempt + 1)
            return normalized
        except (LLMError, ValidationError, ContextIntegrityError) as error:
            if isinstance(error, ValidationError):
                failures = ["SCHEMA: " + ".".join(map(str, item["loc"])) + ": " + item["type"] for item in error.errors()][:8]
            else:
                failures = [str(error) if isinstance(error, ContextIntegrityError) else type(error).__name__]
    logger.warning("Scenario pipeline rejected: checks=%s", failures)
    raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "상황과 일치하는 시나리오를 검증하지 못했어요. 다시 시도해 주세요.")
