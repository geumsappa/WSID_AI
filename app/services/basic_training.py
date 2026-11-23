"""User-selected basic training; fictional seed stays separate from user facts."""
import re
from contextvars import ContextVar

OFFER = "지금 내용만으로는 맞춤 훈련을 만들 정보가 부족해요. 상황을 더 자세히 설명하시거나, 준비된 사례로 기본 훈련을 진행하시겠어요?"
REMINDER = "정보가 더 떠오르지 않으면 ‘기본 훈련 시작’을 선택해 주세요. 지금까지 말씀해 주신 내용은 유지됩니다."
MORE = "좋아요. 장소나 서비스 이름, 언제 있었는지, 직접 하신 일 중 기억나는 내용을 더 알려 주세요. 기본 훈련을 원하시면 ‘기본 훈련 시작’이라고 말씀해 주세요."
READY = "사례 기반 기본 훈련을 준비할 수 있어요. 실제 경험과 구분된 가상 상황으로 진행합니다."
BASIC_INPUT_SCOPE = ContextVar("basic_input_scope", default=False)
SCOPE_INSTRUCTION = "\n현재는 사용자가 선택한 사례 기반 기본 훈련이다. messages/context는 서비스가 만든 가상 연습 설정이며 실제 사용자 사실이 아니다. 이 가상 설정 안의 사실 보존을 검증하되 사용자 경험으로 설명하지 마라. 지정한 retrievedPatterns의 수법을 최소 하나 referenceUses로 사용하고, 다른 사건이나 개인 식별 정보는 만들지 마라. title/background는 훈련 속 사건 도입만 자연스럽게 작성한다. 입력의 ‘역할을 연습’, ‘가상 훈련 설정’ 같은 설명이나 검증상의 미확인 사항을 화면 문장으로 옮기지 마라. 가상 여부와 출처 구분은 메타데이터와 별도 화면 안내가 담당한다."


def choice(text, *, allow_affirmative=True):
    value = re.sub(r"[\s.!?~。！？]+", "", text).lower()
    if not allow_affirmative and not value.startswith("기본") and value != "네기본훈련으로할게요":
        return "MORE" if value in {"더이야기하기", "더설명하기"} else "DECLINE" if value in {"아니", "아니요", "싫어", "아뇨"} else None
    if value in {"네", "넵", "넹", "예", "응", "ㅇㅇ", "좋아요", "좋아", "진행해줘", "진행해주세요", "기본훈련시작", "기본훈련", "기본시나리오", "기본훈련으로진행", "네기본훈련으로할게요"}:
        return "BASIC"
    if value in {"더이야기하기", "더설명하기"}:
        return "MORE"
    if value in {"아니", "아니요", "싫어", "아뇨"}:
        return "DECLINE"
    return None


def generate_basic(request):
    from app.api_errors import AiApiError
    from app.llm.llm_client import generation_budget, LLMError
    from app.services.context_graph import ContextIntegrityError
    from pydantic import ValidationError
    from app.config import settings
    from app.services.fast_discovery import load_evidence
    from app.services.scenario_retrieval import basic_pattern_selection
    from app.services.context_graph import fingerprint
    from app.services.scenario import run_scenario
    from app.services import scenario_planner
    facts = request.facts
    selection = facts.get("basicScenarioSelection")
    if (not isinstance(selection, dict) or not isinstance(selection.get("caseId"), str)
            or facts.get("discoveryReady") is not True):
        raise AiApiError(422, "INVALID_REQUEST", "기본 훈련을 먼저 선택해 주세요.")
    try:
        messages = load_evidence(facts.get("discoveryEvidence"))
    except ValueError:
        raise AiApiError(422, "INVALID_REQUEST", "상황 기록을 확인해 주세요.") from None
    consent = next((row for row in messages if row["turn"] == selection.get("consentTurn")), None)
    if not consent or choice(consent["text"]) != "BASIC":
        raise AiApiError(422, "INVALID_REQUEST", "기본 훈련 선택 기록을 확인해 주세요.")
    try:
        trace = basic_pattern_selection("", selection.get("caseId"))
    except ValueError:
        raise AiApiError(422, "INVALID_REQUEST", "선택한 기본 사례를 확인해 주세요.") from None
    row = trace["patterns"][0]
    seed = (f"가상 훈련 설정: 나는 {row['targetRole']}입니다. {row['context']} 상황에서 "
            f"스마트폰으로 {row['claimedRoles'][0]}의 메시지를 확인하는 역할을 연습합니다. "
            "실제 사용자 경험이 아니라 사례 기반 연습용 가상 설정입니다.")
    token = BASIC_INPUT_SCOPE.set(True)
    try:
        with generation_budget(settings.scenario_total_timeout_seconds):
            graph, plan = scenario_planner.choose_generation_plan(
                [{"turn": 1, "text": seed}], reference_trace=trace)
            result = run_scenario("basic", [], context=graph.model_dump(), plan=plan.model_dump(),
                                  context_hash=fingerprint(graph), basic_trace=trace)
    except (LLMError, ContextIntegrityError, ValidationError):
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "기본 훈련 시나리오를 검증하지 못했어요. 다시 시도해 주세요.") from None
    finally:
        BASIC_INPUT_SCOPE.reset(token)
    story = result["scenario"]
    story["generationMode"] = "BASIC"
    story["basicCaseId"] = row["id"]
    story["generationEvidence"]["basicSelectionReason"] = selection.get("selectionReason")
    story["fictionalContext"] = seed
    story["sourceFacts"] = []
    return result
