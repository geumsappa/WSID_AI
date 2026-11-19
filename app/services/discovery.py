import json
from copy import deepcopy

from app.api_errors import AiApiError
from app.guard.sensitive import mask_sensitive
from app.schemas.discovery import DiscoveryRequest, DiscoveryResult
from app.services.context_turn import run_context_turn
from app.services.context_facts import meaningful, merge_facts
from app.scenario_data import pick_subtype, SUBTYPE_TO_FRAUD_CODE


def discover(request: DiscoveryRequest) -> DiscoveryResult:
    # 공개 훈련 API에서 사용하는 sender/content를 기존 AI 로직의 role/text로 변환한다.
    if not request.currentInput.strip() or len(json.dumps(request.knownFacts, ensure_ascii=False)) > 16000:
        raise AiApiError(422, "INVALID_REQUEST", "상황 파악 요청 형식을 확인해 주세요.")
    history = [
        {"role": "user" if message.sender == "USER" else "ai", "text": message.content}
        for message in request.conversation if message.sender != "SYSTEM" and message.phase in (None, "DISCOVERY")
    ]
    history.append({"role": "user", "text": request.currentInput})
    facts = dict(request.knownFacts)
    previous_turn = facts.get("intakeTurn", 0)
    previous_turn = previous_turn if isinstance(previous_turn, int) and not isinstance(previous_turn, bool) and 0 <= previous_turn < 1000000 else 0
    turn = max(sum(message["role"] == "user" for message in history), previous_turn + 1)
    if "extractedFacts" in facts:
        facts["extractedFacts"] = merge_facts(facts["extractedFacts"] if isinstance(facts["extractedFacts"], list) else [], [], [], max(1, turn - 1))
    result = run_context_turn("discovery", history, known_facts=deepcopy(facts))
    facts.pop("canGenerateScenario", None)
    facts["intakeTurn"] = turn
    accumulated = merge_facts(facts.get("extractedFacts", []), result["extracted_facts"], result.get("retracted_fact_ids", []), turn)
    facts["extractedFacts"] = accumulated
    if any(meaningful(fact) for fact in accumulated):
        facts["fraudTypeCode"] = SUBTYPE_TO_FRAUD_CODE[pick_subtype(accumulated)]
    else:
        facts.pop("fraudTypeCode", None)
    safe_json, _ = mask_sensitive(json.dumps(facts, ensure_ascii=False))
    safe_question, _ = mask_sensitive(result["next_question"])
    return DiscoveryResult(
        facts=json.loads(safe_json),
        nextQuestion=safe_question,
        canGenerateScenario=result["done"] and any(meaningful(fact) for fact in accumulated),
    )
