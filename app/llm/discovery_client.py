"""Strict model output at the discovery boundary; internal state remains compatible."""
import json
from app.config import settings
from app.llm.llm_client import generate_json as _generate_json, LLMError
from app.schemas.discovery_output import DiscoveryOutput, Stop, OfferBasic, output_schema


def generate_json(system_prompt, user_content, **kwargs):
    payload = json.loads(user_content)
    allow_basic = payload.get("basicOfferAllowed") is True
    raw = _generate_json(system_prompt, user_content,
        output_schema=output_schema(allow_basic=allow_basic), **kwargs)
    if settings.llm_provider == "mock":
        return raw  # The existing mock tests dialogue state, not provider decoding.
    result = DiscoveryOutput.model_validate(raw)
    if isinstance(result.action, Stop):
        messages = payload.get("messages", [])
        latest = max(messages, key=lambda row: row["turn"]) if messages else None
        quote = result.action.evidence
        if not latest or quote.turn != latest["turn"] or quote.quote not in latest["text"]:
            raise LLMError("invalid_stop_evidence")
    if isinstance(result.action, OfferBasic) and not allow_basic:
        raise LLMError("basic_offer_not_allowed")
    return result.as_decision()
