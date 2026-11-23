"""One short model call per conversational turn; heavy verification happens at generation."""
import json
import logging
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app import fallback
from app.api_errors import AiApiError
from app.config import settings
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError, generate_json, generation_budget
from app.schemas.fast_discovery import ConversationDecision, FactUpdate, EvidenceMessage
from app.services.scenario_planner import mask_input

logger = logging.getLogger(__name__)
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "discovery_brief.md"
REVIEW_PROMPT = PROMPT.with_name("discovery_question_review.md")
MAX_MESSAGES = 20
MAX_EVIDENCE_CHARS = 9000

# General question purposes, not activity-specific scenario branches.
QUESTIONS = {
    "ACTIVITY_DETAIL": "말씀하신 활동에서 구체적으로 어떤 일을 하고 계셨어요?",
    "TIME": "그 일이 있었던 건 언제쯤인가요? 대략적인 시점이면 괜찮아요.",
    "ACTIVITY": "그때 어떤 일을 하셨어요?",
    "PERSON_NAME": "그분은 뭐라고 부르면 될까요? 이름이나 별명으로 알려 주세요.",
    "PEOPLE": "혼자 하셨나요, 다른 사람과 함께하셨나요?",
    "PURPOSE": "그 활동이나 작업으로 하려던 일은 무엇이었나요?",
    "ACCESS": "그 서비스는 어떤 경로로 이용하셨어요?",
    "INTERACTION": "직원이나 다른 사람과 주고받은 이야기가 있었나요?",
    "TRANSACTION": "결제나 예약은 직접 하셨나요?",
    "SERVICE": "어떤 서비스나 업체를 이용하셨어요?",
    "SETTING": "어느 장소나 상황에서 있었던 일인가요?",
    "ROLE": "그 일에서 본인이나 상대가 맡은 일은 무엇이었나요?",
}
CLARIFICATIONS = {
    "ACTIVITY_DETAIL": "그때 하던 일의 내용이나 목적을 조금 더 알려주실 수 있나요?",
    "TIME": "방문하거나 이용한 시점을 대략 기억하시나요?",
    "ACTIVITY": "그곳에서 실제로 어떤 일을 했는지 알려 주시겠어요?",
    "SETTING": "그 일이 있었던 장소나 상황을 조금 더 알려 주시겠어요?",
    "ROLE": "말씀하신 일에서 직접 맡아 하신 부분이 있었나요?",
    "PEOPLE": "그분과는 어떤 관계인가요?",
    "PERSON_NAME": "그분을 부르는 호칭이나 별명이 있나요?",
    "PURPOSE": "그 일정이나 일을 시작하게 된 계기가 있었나요?",
    "ACCESS": "이용 신청이나 계정 관리는 누가 하셨나요?",
    "INTERACTION": "상대와 정하거나 부탁받은 일이 있었나요?",
    "TRANSACTION": "누가 계산했고, 나중에 정산하기로 한 약속이 있었나요?",
    "SERVICE": "이용한 서비스나 업체 이름은 무엇인가요?",
}
CONTEXT_TOPICS = {"ACTIVITY_DETAIL", "PURPOSE", "ACCESS", "INTERACTION", "TRANSACTION", "ROLE"}


def _supported(update, messages):
    source = next((message["text"] for message in messages if message["turn"] == update.turn), "")
    return " ".join(update.quote.split()) in " ".join(source.split())


def load_topics(raw, messages):
    if not isinstance(raw, dict) or len(raw) > len(QUESTIONS):
        raise ValueError("Invalid known topics")
    result = {}
    for topic, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError("Invalid topic value")
        fact = FactUpdate.model_validate({**value, "topic": topic})
        if _supported(fact, messages):
            result[topic] = fact.model_dump(exclude={"topic"})
    return result



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
    messages = sorted(merged.values(), key=lambda item: item["turn"])
    # Preserve the sources of active facts when the raw conversation is compacted.
    raw_topics = known.get("knownTopics", {})
    protected = ({value.get("turn") for value in raw_topics.values() if isinstance(value, dict)} if isinstance(raw_topics, dict) else set()) | {turn}
    while len(messages) > MAX_MESSAGES or (len(messages) > 1 and len(json.dumps(messages, ensure_ascii=False)) > MAX_EVIDENCE_CHARS):
        victim = next((index for index, item in enumerate(messages) if item["turn"] not in protected), 0)
        messages.pop(victim)
    return load_evidence(messages), turn


def scenario_question(text):
    from app.guard.scenario_guard import PHONE_PATTERN, URL_PATTERN
    text = " ".join(text.split())
    if URL_PATTERN.search(text) or PHONE_PATTERN.search(text) or text.count("?") > 1:
        return ""
    return mask_input(text)


def _available_topics(topics, question_history):
    """One initial question and at most one clarification for a partial answer."""
    available = []
    for topic in QUESTIONS:
        fact = topics.get(topic, {})
        count = sum(item["topic"] == topic for item in question_history)
        if (not fact and count == 0) or (fact.get("status") == "PARTIAL" and count < 2):
            available.append(topic)
    if topics.get("ACTIVITY", {}).get("status") != "KNOWN":
        return ["ACTIVITY"] if "ACTIVITY" in available else []
    # Names are asked only when a person is the chosen source of personalization.
    return available


def _exploration(topics, question_history):
    """Expose open context routes; a declined social route closes only that route."""
    available = _available_topics(topics, question_history)
    routes = {"ACTIVITY_DETAIL", "SETTING", "SERVICE", "TIME", "ACCESS", "PURPOSE", "INTERACTION", "TRANSACTION", "ROLE"}
    return {"humanInteractionRequired": False,
            "activityDetailKnown": topics.get("ACTIVITY_DETAIL", {}).get("status") == "KNOWN",
            "openContextTopics": [topic for topic in available if topic in routes],
            "closedTopics": [topic for topic, value in topics.items()
                             if value.get("status") in {"DECLINED", "NOT_APPLICABLE"}],
            "knownTopics": [topic for topic, value in topics.items() if value.get("status") == "KNOWN"]}


def _requirements(decision, topics=None):
    satisfied = {item.topic for item in decision.questionAssessments if item.status in {"ANSWERED", "UNNECESSARY"}}
    required = [topic for topic in dict.fromkeys(decision.requiredTopics) if topic not in satisfied]
    # An identity already marked incomplete cannot disappear behind OTHER readiness.
    if decision.personalizationTarget == "OTHER" and (topics or {}).get("PERSON_NAME", {}).get("status") == "PARTIAL":
        required = list(dict.fromkeys(["PERSON_NAME", *required]))
    if decision.personalizationTarget == "PERSON":
        required = ["PERSON_NAME", "PEOPLE", *[topic for topic in required if topic not in {"PERSON_NAME", "PEOPLE"}]]
    if decision.personalizationTarget == "SERVICE" and "SERVICE" not in required:
        required.append("SERVICE")
    return required


def _is_resolved(topic, topics):
    status = topics.get(topic, {}).get("status")
    return status == "KNOWN" or topic in {"PERSON_NAME", "SERVICE", "TIME", "ACCESS"} and status == "DECLINED"


def _ready(topics, decision):
    details = [value for topic, value in topics.items() if topic in CONTEXT_TOPICS and value["status"] == "KNOWN"]
    ready = (decision.contextSufficient and decision.personalizationTarget != "UNDECIDED"
             and topics.get("ACTIVITY", {}).get("status") == "KNOWN" and bool(details)
             and all(_is_resolved(topic, topics) for topic in _requirements(decision, topics)))
    return bool(ready), details


def _select_question(topics, question_history, decision, history):
    """Only a model-selected, necessary gap may cause a question."""
    target = decision.nextTopic
    available = _available_topics(topics, question_history)
    # A readiness claim must not skip an explicitly required, still answerable gap.
    if decision.contextSufficient and target is None:
        target = next((topic for topic in _requirements(decision, topics) if topic in available and not _is_resolved(topic, topics)), target)
    if target not in available or target not in _requirements(decision, topics):
        return None
    if (target == "PERSON_NAME" and decision.personalizationTarget != "PERSON"
            and not (decision.personalizationTarget == "OTHER" and topics.get("PERSON_NAME", {}).get("status") == "PARTIAL")):
        return None
    assessment = next((item for item in decision.questionAssessments if item.topic == target), None)
    if assessment is None or assessment.status != "MISSING":
        return None
    previous = {item.get("question") for item in question_history}
    previous.update(message["text"] for message in history if message["role"] == "ai")
    proposed = scenario_question(decision.nextQuestion) if target == decision.nextTopic and decision.nextQuestion.strip() else ""
    question = proposed or (CLARIFICATIONS[target] if topics.get(target, {}).get("status") == "PARTIAL" else QUESTIONS[target])
    if question in previous:
        return None
    return target, question


def _apply_updates(topics, decision, messages):
    for update in decision.updates:
        if not _supported(update, messages):
            raise ValueError("Unstated contextual detail")
        topics[update.topic] = update.model_dump(exclude={"topic"})


def run_discovery_turn(session_id, history, known_facts=None):
    # Leave a margin before Spring's 120s and the browser's 130s limits.
    with generation_budget(settings.discovery_total_timeout_seconds):
        return _run_discovery_turn(session_id, history, known_facts)


def _run_discovery_turn(session_id, history, known_facts=None):
    if not history:
        return {"state": {}, "next_question": fallback.FIRST_QUESTION, "done": False, "fallback_used": False}
    if history[-1]["role"] != "user":
        raise ValueError("Last message must be a user answer")
    try:
        known = known_facts or {}
        messages, turn = _journal(history, known)
        topics = load_topics(known.get("knownTopics", {}), messages)
        question_history = known.get("questionHistory", [])
        if not isinstance(question_history, list):
            raise ValueError("Invalid question history")
        question_history = [item for item in question_history if isinstance(item, dict)
                            and item.get("topic") in QUESTIONS and type(item.get("afterTurn")) is int][-20:]
        from app.services.basic_training import OFFER, REMINDER, MORE, READY, choice
        if known.get("basicScenarioOffered") is True or known.get("intakePaused") is True:
            selected_choice = choice(history[-1]["text"], allow_affirmative=known.get("basicConsentPending") is True)
            if selected_choice in {"BASIC", "MORE", "DECLINE"}:
                state = {key: value for key, value in known.items() if key not in
                         {"canGenerateScenario", "readinessEvidence", "basicScenarioSelection", "scenarioMode"}}
                state.update(intakeTurn=turn, discoveryEvidence=messages, discoveryReady=False,
                             basicScenarioOffered=True, basicConsentPending=False, intakePaused=True,
                             scenarioMode="PERSONALIZED", basicScenarioSelection=None, readinessEvidence=None,
                             questionAssessments=[], questionNeedReview={"performed": False})
                if selected_choice == "BASIC":
                    from app.services.scenario_retrieval import basic_pattern_selection
                    trace = basic_pattern_selection(" ".join(item["text"] for item in messages[:-1]))
                    state.update(discoveryReady=True, scenarioMode="BASIC", basicScenarioOffered=False,
                        intakePaused=False, basicScenarioSelection={"caseId": trace["patterns"][0]["id"],
                            "consentTurn": turn, "selectionReason": trace["status"], "corpusHash": trace["corpusHash"]})
                return {"state": state, "next_question": READY if selected_choice == "BASIC" else MORE,
                        "done": selected_choice == "BASIC", "fallback_used": False}
        targets = _available_topics(topics, question_history)
        previous_topics = json.dumps(topics, sort_keys=True, ensure_ascii=False)
        prompt = PROMPT.read_text(encoding="utf-8")
        payload = {"messages": messages, "knownTopics": topics, "exploration": _exploration(topics, question_history),
                   "questionHistory": question_history, "questionTargets": targets,
                   "previousRequirements": known.get("intakeRequirements", {}),
                   "previousQuestions": [mask_input(message["text"]) for message in history if message["role"] == "ai"][-5:]}

        corrections_left = 1
        def decide(content, *, review=False):
            nonlocal corrections_left
            content = dict(content)
            while True:
                raw = generate_json(REVIEW_PROMPT.read_text(encoding="utf-8") if review else prompt,
                                    json.dumps(content, ensure_ascii=False), max_tokens=1600, timeout=settings.discovery_timeout_seconds, attempts=2,
                                    reasoning_effort=settings.discovery_reasoning_effort or None, reuse_connection=True)
                try:
                    result = ConversationDecision.model_validate(raw)
                    if any(not _supported(update, messages) for update in result.updates):
                        raise ValueError("Unstated contextual detail")
                    assessment_topics = [item.topic for item in result.questionAssessments]
                    if len(assessment_topics) != len(set(assessment_topics)):
                        raise ValueError("Duplicate question assessment")
                    if any(not _supported(quote, messages) for item in result.questionAssessments for quote in item.evidence):
                        raise ValueError("Unstated question justification")
                    return result
                except (ValidationError, ValueError) as error:
                    if not corrections_left:
                        raise
                    corrections_left -= 1
                    content["schemaErrors"] = ([{"path": ".".join(map(str, item["loc"])), "type": item["type"]} for item in error.errors()] if isinstance(error, ValidationError) else [{"path": "questionAssessments" if "question" in str(error).lower() else "updates.quote",
                        "type": "source_or_question_contract_mismatch"}])
                    content["formatCorrection"] = "schemaErrors의 각 오류를 수정하세요. updates는 최대 12개, quote는 180자 이하입니다. 이미 knownTopics에 있는 사실을 반복 출력하지 마세요. 사용자 원문에서 연속된 짧은 부분을 그대로 quote에 복사하고 해당 turn을 사용하세요. 어미·조사·띄어쓰기를 고치거나 인용 안에 생략표시를 넣지 마세요. 이름/호칭도 인용 안의 값이어야 합니다. 사실을 추가하지 마세요. questionAssessments의 MISSING에는 missingDetail과 scenarioImpact가 필요하고 ANSWERED에는 정확한 기존 사용자 인용 evidence가 필요합니다. 중복 topic을 넣지 마세요."


        decision = decide(payload)
        if decision.focusChanged:
            topics, question_history = {}, []
        _apply_updates(topics, decision, messages)
        ready, details = _ready(topics, decision)
        selected = None if ready else _select_question(topics, question_history, decision, history)
        reviewed_question = False
        unchanged_pause = (known.get("intakePaused") is True and not decision.focusChanged
                           and json.dumps(topics, sort_keys=True, ensure_ascii=False) == previous_topics)
        # Reassess the existing facts once rather than walking through unused topics.
        if not ready and selected is None and (details or topics.get("ACTIVITY", {}).get("status") == "KNOWN") and not unchanged_pause:
            reassess_payload = {**payload, "mode": "REASSESS", "knownTopics": topics,
                                "exploration": _exploration(topics, question_history),
                                "questionHistory": question_history,
                                "questionTargets": _available_topics(topics, question_history),
                                "previousRequirements": {"target": decision.personalizationTarget, "topics": _requirements(decision, topics)}}
            reassessed = decide(reassess_payload)
            # This pass may simplify a plan, but cannot erase the user's current facts.
            if reassessed.focusChanged:
                logger.warning("Ignored focus reset during discovery reassessment")
            _apply_updates(topics, reassessed, messages)
            decision = reassessed
            ready, details = _ready(topics, decision)
            selected = None if ready else _select_question(topics, question_history, decision, history)
        previous_required = known.get("intakeRequirements", {}).get("topics", [])
        previous_resolved = bool(previous_required) and all(_is_resolved(topic, topics) for topic in previous_required)
        reviewed_question = bool(selected and previous_resolved and selected[0] not in previous_required and not decision.focusChanged)
        if reviewed_question:
            # A newly introduced requirement must be checked against all existing facts,
            # including facts stored under another topic. At most one independent review.
            reviewed = decide({**payload, "mode": "REVIEW_QUESTION_NEED", "knownTopics": topics,
                "proposedDecision": decision.model_dump(), "proposedQuestion": selected[1],
                "questionTargets": _available_topics(topics, question_history)}, review=True)
            if reviewed.focusChanged:
                raise ValueError("Question review cannot reset the user's focus")
            _apply_updates(topics, reviewed, messages)
            decision = reviewed
            ready, details = _ready(topics, decision)
            selected = None if ready else _select_question(topics, question_history, decision, history)
        state = {"pipelineVersion": 2, "intakeTurn": turn, "discoveryEvidence": messages,
                 "knownTopics": topics, "questionHistory": question_history,
                 "discoveryReady": ready, "extractedFacts": [],
                 "scenarioMode": "PERSONALIZED", "basicScenarioSelection": None,
                 "basicScenarioOffered": False, "basicConsentPending": False, "intakePaused": False, "readinessEvidence": None,
                 "intakeRequirements": {"target": decision.personalizationTarget, "topics": _requirements(decision, topics)},
                 "questionAssessments": [item.model_dump() for item in decision.questionAssessments],
                 "questionNeedReview": {"performed": reviewed_question}}
        if ready:
            detail = max(details, key=lambda item: item["turn"])
            state["readinessEvidence"] = {"turn": detail["turn"], "quote": mask_input(detail["quote"])}
            question = fallback.DONE_MESSAGE
        elif selected is not None:
            target, question = selected
            state["questionHistory"] = [*question_history, {"topic": target, "afterTurn": turn, "question": question}][-20:]
        else:
            state["intakePaused"] = True
            state["basicScenarioOffered"] = True
            state["basicConsentPending"] = True
            # Spring persists every AI reply and rejects blank generated text.
            # Keep waiting without repeating a question or manufacturing readiness.
            question = REMINDER if known.get("basicScenarioOffered") is True else OFFER
        return {"state": state, "next_question": question,
                "done": ready, "fallback_used": False,
                "masked_text": mask_input(history[-1]["text"]),
                "sensitive_types": mask_sensitive(history[-1]["text"])[1]}
    except (LLMError, ValidationError, ValueError, KeyError, TypeError) as error:
        logger.warning("Brief discovery failed: type=%s", type(error).__name__)
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "상황을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.") from None
