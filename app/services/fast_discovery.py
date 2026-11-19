"""One short model call per conversational turn; heavy verification happens at generation."""
import json
import logging
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app import fallback
from app.api_errors import AiApiError
from app.config import settings
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError, generate_json
from app.schemas.fast_discovery import DiscoveryDecision, EvidenceMessage
from app.services.scenario_planner import mask_input, safe_question

logger = logging.getLogger(__name__)
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "discovery_brief.md"
MAX_MESSAGES = 20
MAX_EVIDENCE_CHARS = 9000


def load_evidence(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_MESSAGES:
        raise ValueError("Invalid discovery evidence")
    messages = TypeAdapter(list[EvidenceMessage]).validate_python(raw)
    if len({message.turn for message in messages}) != len(messages):
        raise ValueError("Duplicate evidence turn")
    result = [{"turn": message.turn, "text": mask_input(message.text)} for message in messages]
    if len(json.dumps(result, ensure_ascii=False)) > MAX_EVIDENCE_CHARS:
        raise ValueError("Discovery evidence too large")
    return sorted(result, key=lambda message: message["turn"])


def _journal(history, known):
    users = [message for message in history if message["role"] == "user"]
    previous = known.get("intakeTurn", 0)
    previous = previous if type(previous) is int and 0 <= previous < 1000000 else 0
    turn = max(previous + 1, len(users))
    prior = load_evidence(known["discoveryEvidence"]) if known.get("discoveryEvidence") else []
    # Migration: retain previously verified quotations when starting from an older state.
    if not prior and isinstance(known.get("contextGraph"), dict):
        prior = [{"turn": item["sourceTurn"], "text": mask_input(item["quote"])}
                 for item in known["contextGraph"].get("evidence", [])]
    merged = {item["turn"]: item for item in prior}
    for index, message in enumerate(users):
        number = turn - len(users) + index + 1
        merged[number] = {"turn": number, "text": mask_input(message["text"])}
    messages = sorted(merged.values(), key=lambda item: item["turn"])[-MAX_MESSAGES:]
    while len(messages) > 1 and len(json.dumps(messages, ensure_ascii=False)) > MAX_EVIDENCE_CHARS:
        messages.pop(0)
    return load_evidence(messages), turn


def run_discovery_turn(session_id, history, known_facts=None):
    if not history:
        return {"state": {}, "next_question": fallback.FIRST_QUESTION, "done": False, "fallback_used": False}
    if history[-1]["role"] != "user":
        raise ValueError("Last message must be a user answer")
    try:
        messages, turn = _journal(history, known_facts or {})
        questions = [mask_input(message["text"]) for message in history if message["role"] == "ai"][-5:]
        raw = generate_json(PROMPT.read_text(encoding="utf-8"),
                            json.dumps({"messages": messages, "previousQuestions": questions}, ensure_ascii=False),
                            max_tokens=200, timeout=settings.discovery_timeout_seconds, attempts=1,
                            reasoning_effort=settings.discovery_reasoning_effort or None, reuse_connection=True)
        decision = DiscoveryDecision.model_validate(raw)
        if decision.ready:
            source = next((message["text"] for message in messages if message["turn"] == decision.detail.turn), "")
            if " ".join(decision.detail.quote.split()) not in " ".join(source.split()):
                raise ValueError("Unstated contextual detail")
        state = {"pipelineVersion": 2, "intakeTurn": turn, "discoveryEvidence": messages,
                 "discoveryReady": decision.ready, "extractedFacts": []}
        if decision.ready:
            state["readinessEvidence"] = {"turn": decision.detail.turn, "quote": mask_input(decision.detail.quote)}
        return {"state": state, "next_question": fallback.DONE_MESSAGE if decision.ready else safe_question(decision.question),
                "done": decision.ready, "fallback_used": False,
                "masked_text": mask_input(history[-1]["text"]),
                "sensitive_types": mask_sensitive(history[-1]["text"])[1]}
    except (LLMError, ValidationError, ValueError, KeyError, TypeError) as error:
        logger.warning("Brief discovery failed: type=%s", type(error).__name__)
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "상황을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.") from None
