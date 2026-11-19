from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import context_turn, discovery, scenario as scenario_service, training_api
from app.services.context_facts import merge_facts
from app.scenario_data import FALLBACK_SCENARIOS
from app.llm.llm_client import LLMError


def fact(category="구직·아르바이트", detail="기업은행 역량검사를 봤어요", **kwargs):
    return {"category": category, "detail": detail, **kwargs}


def test_same_fact_is_enriched_without_duplicate_or_mutating_previous():
    previous = merge_facts([], [fact()], [], 1)
    original = deepcopy(previous)
    result = merge_facts(previous, [fact(channel="이메일")], [], 2)
    assert len(result) == 1
    assert result[0]["factId"] == previous[0]["factId"]
    assert result[0]["channel"] == "이메일"
    assert previous == original


def test_explicit_correction_replaces_referenced_fact_and_drops_old_institution(monkeypatch):
    previous = merge_facts([], [fact()], [], 1)
    old_id = previous[0]["factId"]
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {
        "next_question": "어떤 전형이었나요?", "extracted_facts": [fact(detail="신한은행 면접을 봤어요")],
        "retracted_fact_ids": [old_id], "enough_info": True,
    })
    with TestClient(app) as client:
        response = client.post("/internal/ai/discovery/next", json={
            "conversation": [], "knownFacts": {"extractedFacts": previous}, "currentInput": "기업은행이 아니고 신한은행 면접이었어요.",
        })
    assert response.status_code == 200
    result = response.json()["data"]
    assert len(result["facts"]["extractedFacts"]) == 1
    assert "신한은행" in result["facts"]["extractedFacts"][0]["detail"]
    assert "기업은행" not in str(result["facts"])
    assert result["facts"]["fraudTypeCode"] == "JOB_SCAM"


def test_ordinary_topic_change_does_not_allow_arbitrary_fact_deletion(monkeypatch):
    previous = merge_facts([], [fact()], [], 1)
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {
        "next_question": "추가로 알려주세요", "extracted_facts": [], "retracted_fact_ids": [previous[0]["factId"]],
    })
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "오늘은 산책도 했어요"}], {"extractedFacts": previous})
    assert result["retracted_fact_ids"] == []


def test_no_facts_cannot_become_ready_even_if_model_says_enough(monkeypatch):
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {"enough_info": True, "next_question": "완료"})
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "모르겠어요"}])
    assert not result["done"]


def test_string_false_is_not_treated_as_boolean_true(monkeypatch):
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {"enough_info": "false", "next_question": "질문"})
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "모르겠어요"}])
    assert not result["done"] and result["fallback_used"]


def test_repeated_question_is_replaced_with_missing_information_question(monkeypatch):
    repeated = "어떤 경로로 연락을 받으셨나요?"
    previous = merge_facts([], [fact("택배·배송", "택배 반송 문자에서 링크를 누르라고 했어요", channel="문자", requestedAction="링크 클릭")], [], 1)
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {"next_question": repeated, "extracted_facts": [], "enough_info": False})
    result = context_turn.run_context_turn("s", [{"role": "ai", "text": repeated}, {"role": "user", "text": "문자였어요"}], {"extractedFacts": previous})
    assert result["next_question"] != repeated
    assert "누구" in result["next_question"]


def test_scenario_preserves_recruitment_category_despite_old_trade_hint(monkeypatch):
    captured = []
    def generate(session, facts):
        captured.extend(facts)
        return scenario_service._fallback(facts)
    monkeypatch.setattr(training_api, "run_scenario", generate)
    with TestClient(app) as client:
        response = client.post("/internal/ai/scenarios/generate", json={"facts": {"extractedFacts": [fact()]}, "fraudTypeCode": "SECONDHAND_TRADE"})
    assert response.status_code == 200, response.text
    scenario = response.json()["data"]["scenario"]
    assert captured[0]["category"] == "구직·아르바이트"
    assert scenario["fraudTypeCode"] == "JOB_SCAM"
    assert scenario["fraudSubtype"] == "채용·아르바이트 사기"
    assert scenario["sourceFacts"][0]["detail"] == "기업은행 역량검사를 봤어요"
    assert scenario["persona"]["organization"] == "기업은행"
    assert "가상" in scenario["background"]


def test_empty_situation_is_rejected_without_calling_model(monkeypatch):
    called = []
    monkeypatch.setattr(training_api, "run_scenario", lambda *a: called.append(True))
    with TestClient(app) as client:
        response = client.post("/internal/ai/scenarios/generate", json={"facts": {}, "fraudTypeCode": "JOB_SCAM"})
    assert response.status_code == 422
    assert not called


@pytest.mark.parametrize("category, detail, code", [
    ("거래(중고·쇼핑)", "자전거를 판매했어요", "SECONDHAND_TRADE"),
    ("택배·배송", "주소가 틀렸다는 문자를 받았어요", "SMISHING"),
    ("구직·아르바이트", "기업은행 역량검사를 봤어요", "JOB_SCAM"),
])
def test_fallback_keeps_user_context_and_matching_type(monkeypatch, category, detail, code):
    monkeypatch.setattr(scenario_service, "generate_json", lambda *a, **k: (_ for _ in ()).throw(LLMError("offline")))
    result = scenario_service.run_scenario("s", [fact(category, detail)])
    assert result["fallback_used"]
    assert detail in result["scenario"]["background"]
    assert result["scenario"]["sourceFacts"][0]["detail"] == detail


def test_model_cannot_invent_an_institution_the_user_never_mentioned(monkeypatch):
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {
        "next_question": "생성할게요", "enough_info": True,
        "extracted_facts": [fact(detail="기업은행 역량검사를 봤어요")],
    })
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "모르겠어요"}])
    assert not result["extracted_facts"]
    assert not result["done"]
    assert result["next_question"].endswith("?")


def test_prompt_only_contains_reviewed_cases_for_relevant_activity():
    from app.case_db import cases_for_prompt, case_ids_for_subtype
    filtered = cases_for_prompt({"채용·아르바이트 사기"})
    assert len(filtered) < len(cases_for_prompt())
    assert all(case_id in filtered for case_id in case_ids_for_subtype("채용·아르바이트 사기"))
    assert all(case_id not in filtered for case_id in case_ids_for_subtype("중고거래 사기"))


def test_model_failure_preserves_explicit_activity_without_inventing_contact(monkeypatch):
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: (_ for _ in ()).throw(LLMError("offline")))
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "기업은행 역량검사를 봤어요"}])
    assert result["fallback_used"]
    assert result["extracted_facts"][0]["category"] == "구직·아르바이트"
    assert result["extracted_facts"][0]["detail"] == "기업은행 역량검사를 봤어요"
    assert result["extracted_facts"][0]["channel"] is None
    assert result["extracted_facts"][0]["requestedAction"] is None


def test_plain_refusal_does_not_retract_a_fact(monkeypatch):
    previous = merge_facts([], [fact()], [], 1)
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {"next_question": "다른 안내가 있었나요?", "retracted_fact_ids": [previous[0]["factId"]]})
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "아니요"}], {"extractedFacts": previous})
    assert not result["retracted_fact_ids"]


def test_clear_activity_and_channel_do_not_need_five_questions(monkeypatch):
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {
        "next_question": "추가로 물어볼까요?", "extracted_facts": [fact(channel="이메일")], "enough_info": False,
    })
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "기업은행 역량검사를 봤고 안내는 이메일로 받았어요"}])
    assert result["done"]


def test_fallback_retains_contact_channel():
    result = scenario_service._fallback([fact(channel="이메일")])
    assert result["scenario"]["channel"] == "이메일"


def test_missing_context_does_not_repeat_the_same_fallback_question():
    from app.services.context_facts import followup
    asked = []
    for _ in range(3):
        question = followup([], asked)
        assert question not in asked
        asked.append(question)
    assert "나중에" in followup([], asked)


def test_legitimate_recruitment_notice_does_not_require_a_scam_request(monkeypatch):
    monkeypatch.setattr(context_turn, "generate_json", lambda *a, **k: {
        "next_question": "어떤 요구가 있었나요?", "enough_info": False,
        "extracted_facts": [fact(channel="이메일", impersonatedIdentity="기업은행", requestedAction=None, riskFactors=[])],
    })
    result = context_turn.run_context_turn("s", [{"role": "user", "text": "기업은행 역량검사를 봤고 안내는 이메일로 받았어요"}])
    assert result["done"]
    assert result["extracted_facts"][0]["requestedAction"] is None
    assert result["extracted_facts"][0]["riskFactors"] == []


def test_fact_source_turn_stays_correct_when_history_is_compacted(monkeypatch):
    monkeypatch.setattr(discovery, "run_context_turn", lambda *a, **k: {
        "extracted_facts": [fact(detail="은행 면접 일정을 이메일로 받았어요")],
        "next_question": "면접은 언제인가요?", "done": False,
    })
    with TestClient(app) as client:
        response = client.post("/internal/ai/discovery/next", json={
            "conversation": [], "knownFacts": {"intakeTurn": 11}, "currentInput": "면접 안내를 받았어요",
        })
    assert response.status_code == 200
    facts = response.json()["data"]["facts"]
    assert facts["intakeTurn"] == 12
    assert facts["extractedFacts"][0]["sourceTurn"] == 12
