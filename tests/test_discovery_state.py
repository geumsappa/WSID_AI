"""Conversation policy is based on supported facts and question purposes, not activity-specific branches."""
from app.schemas.discovery import DiscoveryRequest
from app.services import discovery
from app.services.fast_discovery import QUESTIONS


def ask(text, state=None):
    return discovery.discover(DiscoveryRequest(conversation=[], knownFacts=state or {}, currentInput=text))


def fact(topic, turn, quote, status="KNOWN"):
    return {"topic": topic, "turn": turn, "quote": quote, "status": status}


def model(grounded_model, updates, target=None, changed=False):
    grounded_model[1]["discovery_brief"] = {"updates": updates, "nextTopic": target, "focusChanged": changed,
        "contextSufficient": True, "personalizationTarget": "OTHER", "requiredTopics": [target] if target in QUESTIONS else []}


def test_after_specific_activity_the_same_intent_cannot_be_asked_again(grounded_model):
    model(grounded_model, [fact("SETTING", 1, "취미 공방에 갔어요")], "ACTIVITY")
    first = ask("취미 공방에 갔어요")
    assert first.nextQuestion == QUESTIONS["ACTIVITY"]
    model(grounded_model, [fact("ACTIVITY", 2, "물레 실습을 했어요")], "ACTIVITY")
    second = ask("물레 실습을 했어요", first.facts)
    assert second.facts["intakePaused"]
    assert second.nextQuestion != first.nextQuestion
    assert [item["topic"] for item in second.facts["questionHistory"]] == ["ACTIVITY"]


def test_device_and_service_are_remembered_but_do_not_complete_context(grounded_model):
    model(grounded_model, [fact("SETTING", 1, "집에 있었어요")], "ACTIVITY")
    first = ask("집에 있었어요")
    model(grounded_model, [fact("ACTIVITY", 2, "영상서비스를 봤어요"), fact("SERVICE", 2, "영상서비스")], "ACCESS")
    second = ask("영상서비스를 봤어요", first.facts)
    assert not second.canGenerateScenario
    assert second.nextQuestion == QUESTIONS["ACCESS"]
    model(grounded_model, [fact("ACCESS", 3, "휴대 기기로요", "PARTIAL")], "ACCESS")
    third = ask("휴대 기기로요", second.facts)
    assert not third.canGenerateScenario
    assert "readinessEvidence" not in third.facts
    assert len(third.facts["questionHistory"]) == 3
    assert len(grounded_model[0]) == 3


def test_an_already_named_service_is_not_requested_again(grounded_model):
    model(grounded_model, [fact("ACTIVITY", 1, "영상을 봤어요"), fact("SERVICE", 1, "스트리밍 앱")], "SERVICE")
    result = ask("스트리밍 앱에서 영상을 봤어요")
    assert not result.canGenerateScenario
    assert result.nextQuestion != QUESTIONS["SERVICE"]
    assert result.facts["questionHistory"] == []
    assert result.facts["intakePaused"]


def test_a_single_rich_answer_can_finish_without_a_turn_threshold(grounded_model):
    text = "동료와 함께 이용했고 동료가 비용을 냈어요"
    model(grounded_model, [fact("ACTIVITY", 1, "함께 이용했고"), fact("TRANSACTION", 1, "동료가 비용을 냈어요")])
    result = ask(text)
    assert result.canGenerateScenario
    assert result.facts["questionHistory"] == []


def test_refused_topics_are_not_reworded_and_asked_again(grounded_model):
    model(grounded_model, [fact("ACTIVITY", 1, "정리 작업을 했어요")], "PEOPLE")
    first = ask("정리 작업을 했어요")
    model(grounded_model, [fact("PEOPLE", 2, "말하고 싶지 않아요", "DECLINED")], "PEOPLE")
    second = ask("말하고 싶지 않아요", first.facts)
    assert not second.canGenerateScenario
    assert second.nextQuestion != QUESTIONS["PEOPLE"]
    model(grounded_model, [], "PEOPLE")
    third = ask("생각이 안 나네요", second.facts)
    assert third.nextQuestion not in {first.nextQuestion, second.nextQuestion}


def test_without_an_activity_we_do_not_ask_who_did_it(grounded_model):
    model(grounded_model, [fact("ACTIVITY", 1, "모르겠어요", "DECLINED")], "PEOPLE")
    result = ask("모르겠어요")
    assert not result.canGenerateScenario
    assert result.facts["intakePaused"]
    assert result.facts["questionHistory"] == []


def test_unsupported_question_intents_cannot_generate_procedure_questions(grounded_model):
    model(grounded_model, [fact("ACTIVITY", 1, "공예 실습을 했어요")], "ACTIVITY_METHOD")
    result = ask("공예 실습을 했어요")
    assert result.facts["intakePaused"]
    assert result.facts["questionHistory"] == []


def test_device_corrections_replace_a_fact_without_repeating_device_question(grounded_model):
    model(grounded_model, [fact("ACTIVITY", 1, "자료를 열람했고"), fact("ACCESS", 1, "휴대 기기로", "PARTIAL")])
    first = ask("자료를 열람했고 휴대 기기로 봤어요")
    model(grounded_model, [fact("ACCESS", 2, "컴퓨터였어요", "PARTIAL")], "ACCESS")
    second = ask("정정할게요 컴퓨터였어요", first.facts)
    assert not second.canGenerateScenario
    assert second.facts["knownTopics"]["ACCESS"]["quote"] == "컴퓨터였어요"


def test_explicit_new_activity_resets_the_old_context(grounded_model):
    model(grounded_model, [fact("ACTIVITY", 1, "자료를 봤어요"), fact("PEOPLE", 1, "동료와")])
    first = ask("동료와 자료를 봤어요")
    model(grounded_model, [fact("ACTIVITY", 2, "다른 날에는 전시를 봤어요")], "PEOPLE", changed=True)
    second = ask("다른 날에는 전시를 봤어요", first.facts)
    assert not second.canGenerateScenario
    assert "PEOPLE" not in second.facts["knownTopics"]
    assert len(second.facts["questionHistory"]) == 1


def test_prior_fact_evidence_survives_bounded_history_compaction(grounded_model):
    known = {"intakeTurn": 20, "knownTopics": {"ACTIVITY": {"turn": 1, "quote": "정리 작업", "status": "KNOWN"}},
             "discoveryEvidence": [{"turn": 1, "text": "정리 작업"}] + [{"turn": i, "text": "추가 이야기"} for i in range(2,21)]}
    model(grounded_model, [], "PEOPLE")
    result = ask("계속 설명할게요", known)
    assert len(result.facts["discoveryEvidence"]) == 20
    assert result.facts["knownTopics"]["ACTIVITY"]["turn"] == 1
    assert result.facts["discoveryEvidence"][0]["turn"] == 1


def test_existing_conversation_without_a_ledger_can_recover_known_answers(grounded_model):
    known = {"intakeTurn": 3, "discoveryEvidence": [
        {"turn": 1, "text": "집에 있었어요"}, {"turn": 2, "text": "영상서비스를 봤어요"}, {"turn": 3, "text": "휴대 기기로요"}]}
    model(grounded_model, [fact("ACTIVITY", 2, "영상서비스를 봤어요"), fact("SERVICE", 2, "영상서비스"), fact("ACCESS", 3, "휴대 기기로요", "PARTIAL")], "SERVICE")
    result = ask("앞에서 답했어요", known)
    assert not result.canGenerateScenario
    assert result.facts["questionHistory"] == []
    assert result.facts["intakePaused"]
    assert result.facts["knownTopics"]["ACCESS"]["turn"] == 3


def test_malformed_topic_state_fails_before_a_model_request(grounded_model):
    import pytest
    from app.api_errors import AiApiError
    with pytest.raises(AiApiError):
        ask("추가로 설명할게요", {"knownTopics": {"ACTIVITY": "잘못된 상태"}})
    assert grounded_model[0] == []
