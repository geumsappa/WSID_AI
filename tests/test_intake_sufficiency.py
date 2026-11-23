"""A supported contact anchor stops optional detail collection, never identity checks."""
from copy import deepcopy
import pytest
from app.api_errors import AiApiError
from app.schemas.discovery import DiscoveryRequest
from app.services.discovery import discover

TEXT = "어제 별빛서점에서 책을 샀고 직원에게 배송 일정을 물었어요"


def decision():
    return {"updates": [
        {"topic": "ACTIVITY", "turn": 1, "quote": "책을 샀고"},
        {"topic": "SERVICE", "turn": 1, "quote": "별빛서점"},
        {"topic": "INTERACTION", "turn": 1, "quote": "직원에게 배송 일정을 물었어요"}],
        "personalizationTarget": "SERVICE", "contextSufficient": False,
        "requiredTopics": ["ACTIVITY_DETAIL"], "nextTopic": "ACTIVITY_DETAIL", "nextQuestion": "무슨 책인가요?",
        "readinessBasis": {"topics": ["SERVICE", "INTERACTION"],
            "evidence": [{"turn": 1, "quote": "직원에게 배송 일정을 물었어요"}],
            "contactReason": "이미 문의한 배송 일정을 언급하는 서점 직원 사칭 연락"},
        "questionAssessments": [{"topic": "ACTIVITY_DETAIL", "status": "MISSING", "necessity": "OPTIONAL",
            "missingDetail": "구입한 책 제목", "scenarioImpact": "배송 문의 명분에는 책 제목이 필요하지 않음",
            "reason": "이미 배송 문의라는 구체적 상호작용이 있음", "evidence": []}]}


def ask():
    return discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=TEXT))


def test_grounded_anchor_finishes_without_optional_question_or_extra_call(grounded_model):
    grounded_model[1]["discovery_brief"] = decision()
    result = ask()
    assert result.canGenerateScenario
    assert "ACTIVITY_DETAIL" not in result.facts["intakeRequirements"]["topics"]
    assert result.facts["readinessEvidence"]["quote"] == "직원에게 배송 일정을 물었어요"
    assert result.facts["questionHistory"] == []
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief"]


def test_anchor_does_not_skip_a_real_blocking_gap(grounded_model):
    value = decision()
    value["questionAssessments"][0]["necessity"] = "REQUIRED"
    grounded_model[1]["discovery_brief"] = value
    result = ask()
    assert not result.canGenerateScenario
    assert result.nextQuestion == "무슨 책인가요?"


def test_readiness_basis_cannot_invent_a_user_statement(grounded_model):
    value = decision()
    value["readinessBasis"]["evidence"][0]["quote"] = "직원이 제 번호로 연락했어요"
    grounded_model[1]["discovery_brief"] = value
    with pytest.raises(AiApiError):
        ask()


def test_contact_basis_cannot_make_an_unknown_topic_known(grounded_model):
    value = decision()
    value["readinessBasis"]["topics"] = ["ACCESS"]
    value["nextTopic"] = None
    grounded_model[1]["discovery_brief"] = value
    assert not ask().canGenerateScenario


def test_explicit_name_gap_is_kept_in_person_target(grounded_model):
    text = "친구가 제 책 배송을 대신 신청했어요"
    value = {"updates": [{"topic": "ACTIVITY", "turn": 1, "quote": "배송을 대신 신청했어요"},
        {"topic": "PEOPLE", "turn": 1, "quote": "친구가"},
        {"topic": "TRANSACTION", "turn": 1, "quote": text}],
        "personalizationTarget": "UNDECIDED", "contextSufficient": True,
        "requiredTopics": ["PERSON_NAME"], "nextTopic": "PERSON_NAME",
        "questionAssessments": [{"topic": "PERSON_NAME", "status": "MISSING", "necessity": "OPTIONAL",
            "missingDetail": "친구의 이름이나 별명", "scenarioImpact": "상대를 부를 이름", "reason": "아직 모름"}]}
    grounded_model[1]["discovery_brief"] = value
    result = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=text))
    assert not result.canGenerateScenario
    assert result.facts["intakeRequirements"]["target"] == "PERSON"
    assert result.facts["questionHistory"][-1]["topic"] == "PERSON_NAME"
