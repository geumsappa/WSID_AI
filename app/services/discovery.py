"""Compatibility adapter: public discovery JSON stays unchanged."""
import json

from app.api_errors import AiApiError
from app.schemas.discovery import DiscoveryRequest, DiscoveryResult
from app.services.fast_discovery import run_discovery_turn as run_context_turn


def discover(request: DiscoveryRequest) -> DiscoveryResult:
    if not request.currentInput.strip() or len(json.dumps(request.knownFacts, ensure_ascii=False)) > 16000:
        raise AiApiError(422, "INVALID_REQUEST", "상황 파악 요청 형식을 확인해 주세요.")
    history = [{"role": "user" if message.sender == "USER" else "ai", "text": message.content}
               for message in request.conversation if message.sender != "SYSTEM" and message.phase in (None, "DISCOVERY")]
    history.append({"role": "user", "text": request.currentInput})
    result = run_context_turn("discovery", history, request.knownFacts)
    state = result["state"]
    if len(json.dumps(state, ensure_ascii=False)) > 16000:
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "상황 기록을 정리하지 못했어요. 다시 시도해 주세요.")
    return DiscoveryResult(facts=state, nextQuestion=result["next_question"], canGenerateScenario=result["done"])
