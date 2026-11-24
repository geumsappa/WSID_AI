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


def test_grounded_anchor_skips_optional_question_then_verifies_plan(grounded_model):
    grounded_model[1]["discovery_brief"] = decision()
    result = ask()
    assert result.canGenerateScenario
    assert "ACTIVITY_DETAIL" not in result.facts["intakeRequirements"]["topics"]
    assert result.facts["readinessEvidence"]["quote"] == "직원에게 배송 일정을 물었어요"
    assert result.facts["questionHistory"] == []
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "generation_plan", "semantic_review"]


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


@pytest.mark.parametrize("text", ["택배나 배송 이야기를 하고 싶어요.", "중고거래 이야기를 하고 싶어요.", "보험에 대해 이야기하고 싶어요."])
def test_topic_selection_gets_an_opening_question_without_plan_justification(grounded_model, text):
    grounded_model[1]["discovery_brief"] = {"updates": [], "contextSufficient": False, "nextTopic": None,
        "requiredTopics": [], "personalizationTarget": "UNDECIDED"}
    result = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=text))
    assert not result.canGenerateScenario
    assert not result.facts["basicScenarioOffered"] and not result.facts["intakePaused"]
    assert result.nextQuestion == "그 주제와 관련해 최근에 어떤 일이 있었나요?"
    assert result.facts["questionHistory"][-1]["topic"] == "ACTIVITY"
    assert result.facts["intakeRequirements"]["topics"] == ["ACTIVITY"]
    assert "ACTIVITY" not in result.facts["knownTopics"]
    assert len(grounded_model[0]) == 2  # null proposal is corrected once before the safe opening fallback
    assert grounded_model[0][1][1]["questionRequired"]


def test_opening_can_use_a_model_question_without_detailed_assessment(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": "ACTIVITY", "nextQuestion": "어떤 일이 있으셨나요?",
        "questionAssessments": [{"topic": "ACTIVITY", "status": "MISSING", "reason": "주제 선택"}]}
    result = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="배송 이야기를 하고 싶어요"))
    assert result.nextQuestion == "어떤 일이 있으셨나요?"
    assert not result.facts["basicScenarioOffered"]
    assert len(grounded_model[0]) == 1


def test_explicit_stop_is_respected_without_a_forced_opening(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": "ACTIVITY", "stopRequested": True}
    result = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="지금은 더 설명하고 싶지 않아요"))
    assert result.facts["basicScenarioOffered"]
    assert result.facts["questionHistory"] == []
    assert not result.canGenerateScenario
    assert len(grounded_model[0]) == 1


def test_topic_entry_can_continue_into_real_experience(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": None}
    first = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="배송 이야기를 하고 싶어요"))
    grounded_model[1]["discovery_brief"] = {"updates": [
        {"topic": "ACTIVITY", "turn": 2, "quote": "물건을 주문했어요"},
        {"topic": "ACCESS", "turn": 2, "quote": "온라인 상점에서"}],
        "personalizationTarget": "OTHER", "contextSufficient": True}
    second = discover(DiscoveryRequest(conversation=[], knownFacts=first.facts, currentInput="온라인 상점에서 물건을 주문했어요"))
    assert second.canGenerateScenario
    assert second.facts["knownTopics"]["ACTIVITY"]["turn"] == 2
    assert not second.facts["basicScenarioOffered"]


def test_opening_is_not_repeated_when_no_experience_can_be_supplied(grounded_model):
    grounded_model[1]["discovery_brief"] = {"updates": [], "nextTopic": None}
    first = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="여행 이야기를 하고 싶어요"))
    second = discover(DiscoveryRequest(conversation=[], knownFacts=first.facts, currentInput="더 생각나는 건 없어요"))
    assert first.facts["questionHistory"][-1]["topic"] == "ACTIVITY"
    assert second.nextQuestion != first.nextQuestion
    assert second.facts["basicScenarioOffered"]
    assert len(second.facts["questionHistory"]) == 1


def test_sufficient_flag_without_contact_basis_keeps_asking(grounded_model):
    text = "집에서 고기랑 채소를 구워 먹었어요"
    grounded_model[1]["discovery_brief"] = {
        "updates": [{"topic": "ACTIVITY", "turn": 1, "quote": "집에서 고기랑 채소를 구워 먹었어요"},
                    {"topic": "ACTIVITY_DETAIL", "turn": 1, "quote": "고기랑 채소"}],
        "personalizationTarget": "OTHER", "contextSufficient": True, "readinessBasis": None,
        "requiredTopics": ["INTERACTION"], "nextTopic": "INTERACTION",
        "nextQuestion": "그 식사와 관련해 누군가와 정하거나 부탁받은 일이 있었나요?"}
    result = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=text))
    assert not result.canGenerateScenario
    assert not result.facts["discoveryReady"]
    assert result.nextQuestion == "그 식사와 관련해 누군가와 정하거나 부탁받은 일이 있었나요?"
    assert not result.facts["basicScenarioOffered"]
    assert len(grounded_model[0]) == 1


def test_contact_evidence_must_support_the_claimed_context_topic(grounded_model):
    value = decision()
    # A real quotation about a purchase cannot justify the separate shipping interaction.
    value["readinessBasis"]["topics"] = ["INTERACTION"]
    value["readinessBasis"]["evidence"] = [{"turn": 1, "quote": "책을 샀고"}]
    value["contextSufficient"] = True
    grounded_model[1]["discovery_brief"] = value
    assert not ask().canGenerateScenario


@pytest.mark.parametrize("text", ["카페 갔어요", "집에서 밥 먹었어요", "친구랑 만났어요"])
def test_first_experience_does_not_offer_basic_when_model_omits_question(grounded_model, text):
    grounded_model[1]["discovery_brief"] = {"updates": [{"topic": "ACTIVITY", "turn": 1, "quote": text}],
        "personalizationTarget": "UNDECIDED", "contextSufficient": False, "readinessBasis": None,
        "requiredTopics": [], "nextTopic": None}
    result = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput=text))
    assert not result.canGenerateScenario
    assert not result.facts["basicScenarioOffered"]
    assert not result.facts["intakePaused"]
    assert result.nextQuestion.endswith("?")
    assert result.facts["questionSelectionRecovered"]
    assert result.facts["knownTopics"].keys() == {"ACTIVITY"}
    assert grounded_model[0][0][1]["basicOfferAllowed"] is False


def test_basic_offer_requires_followup_difficulty_or_explicit_stop():
    from app.services.fast_discovery import _can_offer_basic
    topics = {"ACTIVITY": {"status": "KNOWN"}, "ACTIVITY_DETAIL": {"status": "PARTIAL"}}
    assert not _can_offer_basic(topics, [])
    assert not _can_offer_basic(topics, [{"topic": "ACTIVITY_DETAIL"}])
    assert _can_offer_basic(topics, [{"topic": "ACTIVITY_DETAIL"}, {"topic": "ACTIVITY_DETAIL"}])
    assert _can_offer_basic({}, [], stop_requested=True)
