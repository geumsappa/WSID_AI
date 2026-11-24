"""Question need is checked against cross-topic user evidence, not topic keywords."""
import pytest
from app.api_errors import AiApiError
from app.schemas.fast_discovery import QuestionAssessment
from app.schemas.discovery import DiscoveryRequest
from app.services.discovery import discover


def ask(text, facts=None):
    return discover(DiscoveryRequest(conversation=[], knownFacts=facts or {}, currentInput=text))


def need(topic, *, status="MISSING", quote=None, turn=1):
    return {"topic": topic, "status": status, "missingDetail": "구체적인 역할", "scenarioImpact": "연락 상대와 요청 이유가 달라짐",
            "reason": "기존 사용자 발언과 질문 목적을 대조", "evidence": [{"turn": turn, "quote": quote}] if quote else []}


def initial(model):
    text = "친구와 자료 발표를 준비했고 제가 자료를 정리하기로 했어요"
    model[1]["discovery_brief"] = {"updates": [
        {"topic": "ACTIVITY", "turn": 1, "quote": "자료 발표를 준비했고"},
        {"topic": "PEOPLE", "turn": 1, "quote": "친구와"},
        {"topic": "ROLE", "turn": 1, "quote": "제가 자료를 정리하기로 했어요"}],
        "personalizationTarget": "PERSON", "requiredTopics": ["PERSON_NAME", "PEOPLE"],
        "contextSufficient": False, "nextTopic": "PERSON_NAME", "questionAssessments": [need("PERSON_NAME")]}
    return ask(text)


def new_question(model):
    model[1]["discovery_brief"] = {"updates": [{"topic": "PERSON_NAME", "turn": 2, "quote": "도토리", "name": "도토리"}],
        "personalizationTarget": "PERSON", "requiredTopics": ["INTERACTION"], "contextSufficient": False,
        "nextTopic": "INTERACTION", "nextQuestion": "무슨 이야기를 나누셨나요?", "questionAssessments": [need("INTERACTION")]}


def test_cross_topic_evidence_resolves_redundant_question_without_inventing_a_new_fact(grounded_model):
    first = initial(grounded_model)
    new_question(grounded_model)
    grounded_model[1]["discovery_question_review"] = {"updates": [], "personalizationTarget": "PERSON",
        "requiredTopics": ["INTERACTION"], "contextSufficient": True, "nextTopic": None,
        "questionAssessments": [need("INTERACTION", status="ANSWERED", quote="제가 자료를 정리하기로 했어요")]}
    result = ask("별명은 도토리", first.facts)
    assert result.canGenerateScenario
    assert result.facts["questionNeedReview"]["performed"]
    assert result.facts["questionAssessments"][0]["status"] == "ANSWERED"
    assert "INTERACTION" not in result.facts["intakeRequirements"]["topics"]
    assert "INTERACTION" not in result.facts["knownTopics"]  # Reuse evidence; do not manufacture a new statement.
    assert result.facts["knownTopics"]["ROLE"]["quote"] == "제가 자료를 정리하기로 했어요"
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "discovery_brief", "discovery_question_review", "generation_plan", "semantic_review"]


def test_a_genuine_new_gap_keeps_one_question_after_review(grounded_model):
    first = initial(grounded_model)
    new_question(grounded_model)
    result = ask("별명은 도토리", first.facts)
    assert not result.canGenerateScenario
    assert result.nextQuestion == "무슨 이야기를 나누셨나요?"
    assert result.facts["questionNeedReview"]["performed"]
    assert sum(task == "discovery_question_review" for task, _ in grounded_model[0]) == 1


def test_existing_need_with_adequate_reason_does_not_add_a_review_call(grounded_model):
    result = initial(grounded_model)
    assert not result.canGenerateScenario
    assert result.facts["questionHistory"][-1]["topic"] == "PERSON_NAME"
    assert not result.facts["questionNeedReview"]["performed"]
    assert len(grounded_model[0]) == 1


def test_opening_exploration_does_not_need_scenario_justification(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "personalizationTarget": "OTHER", "nextTopic": "ACTIVITY",
        "requiredTopics": ["ACTIVITY"], "questionAssessments": []}
    result = ask("모르겠어요")
    assert not result.canGenerateScenario
    assert not result.facts["basicScenarioOffered"]
    assert result.facts["questionHistory"][-1]["topic"] == "ACTIVITY"
    assert result.facts["knownTopics"] == {}


def test_review_cannot_cite_an_unstated_user_fact(grounded_model):
    first = initial(grounded_model)
    new_question(grounded_model)
    grounded_model[1]["discovery_question_review"] = {"updates": [], "personalizationTarget": "PERSON", "contextSufficient": True,
        "questionAssessments": [need("INTERACTION", status="ANSWERED", quote="친구가 송금을 약속했어요")]}
    with pytest.raises(AiApiError) as error:
        ask("별명은 도토리", first.facts)
    assert error.value.status == 503
    assert sum(task == "discovery_question_review" for task, _ in grounded_model[0]) == 2  # one bounded format correction


def test_answered_identity_does_not_replace_an_unconfirmed_name(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [
        {"topic": "ACTIVITY", "turn": 1, "quote": "친구랑 준비했고"},
        {"topic": "ROLE", "turn": 1, "quote": "제가 자료를 만들었어요"},
        {"topic": "PEOPLE", "turn": 1, "quote": "친구랑"}],
        "personalizationTarget": "PERSON", "contextSufficient": True, "requiredTopics": [],
        "questionAssessments": [need("PERSON_NAME", status="ANSWERED", quote="친구랑")]}
    result = ask("친구랑 준비했고 제가 자료를 만들었어요")
    assert not result.canGenerateScenario
    assert result.facts["knownTopics"].get("PERSON_NAME") is None


def test_assessment_requires_real_missing_detail_and_quoted_support():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        QuestionAssessment.model_validate({"topic": "ROLE", "status": "MISSING", "reason": "더 자세한 맥락"})
    with pytest.raises(ValidationError):
        QuestionAssessment.model_validate(need("INTERACTION", status="ANSWERED"))


def test_resolved_need_can_omit_missing_details_but_a_real_question_cannot():
    answered = {"topic": "INTERACTION", "status": "ANSWERED", "reason": "앞서 약속을 설명함",
                "missingDetail": None, "scenarioImpact": None,
                "evidence": [{"turn": 1, "quote": "자료 정리를 맡기로 했어요"}]}
    assert QuestionAssessment.model_validate(answered).status == "ANSWERED"
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        QuestionAssessment.model_validate({**answered, "status": "MISSING"})


def test_reassessment_cannot_bypass_review_of_a_new_question(grounded_model):
    from copy import deepcopy
    first = initial(grounded_model)
    new_question(grounded_model)
    proposed = deepcopy(grounded_model[1]["discovery_brief"])
    def answer(payload):
        result = deepcopy(proposed)
        if payload.get("mode") != "REASSESS":
            result["questionAssessments"] = []  # First attempt cannot justify its question.
        return result
    grounded_model[1]["discovery_brief"] = answer
    grounded_model[1]["discovery_question_review"] = {"updates": [], "personalizationTarget": "PERSON",
        "requiredTopics": [], "contextSufficient": True, "nextTopic": None,
        "questionAssessments": [need("INTERACTION", status="ANSWERED", quote="제가 자료를 정리하기로 했어요")]}
    result = ask("별명은 도토리", first.facts)
    assert result.canGenerateScenario and result.facts["questionNeedReview"]["performed"]
    assert sum(task == "discovery_question_review" for task, _ in grounded_model[0]) == 1


@pytest.mark.parametrize("topic", [None, "ACTIVITY"])
def test_missing_or_already_answered_question_gets_one_internal_correction(grounded_model, topic):
    text = "카페 갔어요"
    def answer(payload):
        if payload.get("questionRequired"):
            assert payload["questionCorrection"]["reason"] == "NEXT_QUESTION_REQUIRED"
            return {"updates": [], "personalizationTarget": "OTHER", "contextSufficient": False,
                "requiredTopics": ["SETTING"], "nextTopic": "SETTING", "nextQuestion": "어느 카페에 가셨나요?",
                "questionAssessments": [{"topic": "SETTING", "status": "MISSING", "reason": "방문 매장 미확인",
                    "missingDetail": "카페 이름", "scenarioImpact": "매장 관련 접근의 발신자 구체화", "evidence": []}]}
        return {"updates": [{"topic": "ACTIVITY", "turn": 1, "quote": text}],
            "personalizationTarget": "OTHER", "contextSufficient": False, "readinessBasis": None,
            "requiredTopics": [], "nextTopic": topic, "nextQuestion": ""}
    grounded_model[1]["discovery_brief"] = answer
    result = ask(text)
    assert result.nextQuestion == "어느 카페에 가셨나요?"
    assert result.facts["knownTopics"]["ACTIVITY"]["quote"] == text
    assert not result.facts["basicScenarioOffered"]
    assert "questionSelectionRecovered" not in result.facts
    assert [name for name, _ in grounded_model[0]] == ["discovery_brief", "discovery_brief"]


def test_bad_question_correction_preserves_facts_and_asks_instead_of_error(grounded_model):
    text = "카페 갔어요"
    def answer(payload):
        if payload.get("questionRequired"):
            return {"nextTopic": "SETTING", "nextQuestion": None, "updates": []}
        return {"updates": [{"topic": "ACTIVITY", "turn": 1, "quote": text}],
            "contextSufficient": False, "readinessBasis": None, "nextTopic": None, "nextQuestion": ""}
    grounded_model[1]["discovery_brief"] = answer
    result = ask(text)
    assert result.nextQuestion.endswith("?")
    assert result.facts["knownTopics"]["ACTIVITY"]["quote"] == text
    assert result.facts["questionSelectionRecovered"]
    assert not result.facts["basicScenarioOffered"]
    assert len(grounded_model[0]) == 2


def test_explicit_stop_allows_null_without_forcing_a_question(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "stopRequested": True,
        "contextSufficient": False, "nextTopic": None, "nextQuestion": ""}
    result = ask("여기까지만 말할게요")
    assert result.facts["basicScenarioOffered"]
    assert len(grounded_model[0]) == 1
    assert "questionCorrection" not in grounded_model[0][0][1]
