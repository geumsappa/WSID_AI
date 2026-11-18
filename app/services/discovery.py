import json

from app.api_errors import AiApiError
from app.guard.sensitive import mask_sensitive
from app.schemas.discovery import DiscoveryRequest, DiscoveryResult
from app.services.context_turn import run_context_turn


def discover(request: DiscoveryRequest) -> DiscoveryResult:
    # 공개 훈련 API에서 사용하는 sender/content를 기존 AI 로직의 role/text로 변환한다.
    if not request.currentInput.strip() or len(json.dumps(request.knownFacts, ensure_ascii=False)) > 16000:
        raise AiApiError(422, "INVALID_REQUEST", "상황 파악 요청 형식을 확인해 주세요.")
    history = [
        {"role": "user" if message.sender == "USER" else "ai", "text": message.content}
        for message in request.conversation
    ]
    history.append({"role": "user", "text": request.currentInput})
    result = run_context_turn("discovery", history, known_facts=request.knownFacts)
    facts = dict(request.knownFacts)
    facts.pop("canGenerateScenario", None)
    # 기존 누적 사실을 유지하고 새 사실을 중복 없이 합친다.
    accumulated = facts.get("extractedFacts", [])
    if not isinstance(accumulated, list):
        accumulated = []
    for fact in result["extracted_facts"]:
        if fact not in accumulated:
            accumulated.append(fact)
    facts["extractedFacts"] = accumulated[-30:]
    safe_json, _ = mask_sensitive(json.dumps(facts, ensure_ascii=False))
    safe_question, _ = mask_sensitive(result["next_question"])
    return DiscoveryResult(
        facts=json.loads(safe_json),
        nextQuestion=safe_question,
        canGenerateScenario=result["done"],
    )
