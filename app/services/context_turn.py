"""Extract grounded event structure, plan candidates, then independently verify meaning."""
import logging

from pydantic import ValidationError

from app import fallback
from app.api_errors import AiApiError
from app.config import settings
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError
from app.services import scenario_planner
from app.services.context_graph import ContextIntegrityError, fingerprint, project_facts

logger = logging.getLogger(__name__)


def run_context_turn(session_id, history, known_facts=None):
    known_facts = known_facts or {}
    if not history:
        return _result(fallback.FIRST_QUESTION, {})
    if history[-1]["role"] != "user":
        raise ValueError("Last message must be a user answer")
    previous_turn = known_facts.get("intakeTurn", 0)
    previous_turn = previous_turn if type(previous_turn) is int and 0 <= previous_turn < 1000000 else 0
    users = [message for message in history if message["role"] == "user"]
    turn = max(previous_turn + 1, len(users))
    messages = [{"turn": turn - len(users) + index + 1, "text": scenario_planner.mask_input(message["text"])} for index, message in enumerate(users)]
    _, sensitive = mask_sensitive(history[-1]["text"])
    masked_last = scenario_planner.mask_input(history[-1]["text"])
    try:
        previous = scenario_planner.load_previous(known_facts)
        questions = [scenario_planner.mask_input(message["text"]) for message in history if message["role"] == "ai"][-10:]
        extraction, selected = scenario_planner.intake(messages, previous, known_facts.get("scenarioPlan"), settings.max_context_turns, questions)
        graph = extraction.context
        verdict = scenario_planner.review(graph, selected, messages=messages, previous=previous)
        accepted_context = verdict.accepts_context()
        stored = graph if accepted_context else previous
        ready = accepted_context and selected is not None and verdict.accepts()
        state = {"pipelineVersion": 1, "intakeTurn": turn,
                 "contextGraph": stored.model_dump() if stored else None,
                 "extractedFacts": project_facts(stored) if stored else [],
                 "verification": verdict.checks.model_dump(),
                 "verificationIssues": [issue.code for issue in verdict.issues]}
        if ready:
            state["scenarioPlan"] = selected.model_dump()
            state["planContextHash"] = fingerprint(graph)
        question = fallback.DONE_MESSAGE if ready else scenario_planner.safe_question(verdict.followupQuestion or extraction.followupQuestion)
        return _result(question, state, done=ready, masked_text=masked_last, sensitive_types=sensitive,
                       real_concern=extraction.realConcern, review=verdict.model_dump())
    except (LLMError, ValidationError, ContextIntegrityError) as error:
        reason = str(error) if isinstance(error, ContextIntegrityError) else type(error).__name__
        logger.warning("Context pipeline failed: type=%s check=%s", type(error).__name__, reason)
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "상황을 검증하지 못했어요. 잠시 후 다시 시도해 주세요.") from None


def _result(question, state, done=False, masked_text=None, sensitive_types=None, real_concern=False, review=None):
    return {"next_question": question, "extracted_facts": state.get("extractedFacts", []),
            "sensitive_detected": bool(sensitive_types), "sensitive_types": sensitive_types or [],
            "masked_text": masked_text, "done": done, "real_concern": real_concern,
            "fallback_used": False, "state": state, "review": review}
