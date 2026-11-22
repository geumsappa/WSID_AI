"""Synthetic, offline regression coverage for source-based personalized intake."""
from copy import deepcopy
import pytest

from app.api_errors import AiApiError
from app.schemas.discovery import DiscoveryRequest
from app.schemas.training_api import ScenarioRequest
from app.services import discovery, training_api


def ask(text, state=None):
    return discovery.discover(DiscoveryRequest(conversation=[], knownFacts=state or {}, currentInput=text))


def fact(topic, turn, quote, status="KNOWN", **extra):
    return dict(topic=topic, turn=turn, quote=quote, status=status, **extra)


def decision(model, updates, *, target="PERSON", next_topic=None, question="", sufficient=True, required=None):
    model[1]["discovery_brief"] = dict(updates=updates, personalizationTarget=target,
        requiredTopics=required if required is not None else ([next_topic] if next_topic else []), contextSufficient=sufficient, nextTopic=next_topic, nextQuestion=question)


def test_activity_and_companion_alone_cannot_complete_even_with_model_approval(grounded_model):
    decision(grounded_model, [fact("ACTIVITY", 1, "간식을 먹었어요")], next_topic="PEOPLE")
    first = ask("간식을 먹었어요")
    decision(grounded_model, [fact("PEOPLE", 2, "친구랑요")], next_topic="PERSON_NAME",
             question="같이 먹은 친구는 뭐라고 부르면 될까요? 별명도 괜찮아요.")
    second = ask("친구랑요", first.facts)
    assert not first.canGenerateScenario and not second.canGenerateScenario
    assert "별명" in second.nextQuestion
    assert second.facts["questionHistory"][-1]["topic"] == "PERSON_NAME"
    assert "readinessEvidence" not in second.facts
    assert len(grounded_model[0]) == 2


def test_name_then_actual_transaction_completes_and_remembers_both(grounded_model):
    decision(grounded_model, [fact("ACTIVITY", 1, "친구와 간식을 먹었어요"), fact("PEOPLE", 1, "친구와")],
             next_topic="PERSON_NAME", required=["TRANSACTION"])
    first = ask("친구와 간식을 먹었어요")
    decision(grounded_model, [fact("PERSON_NAME", 2, "도토리", name="도토리")], next_topic="TRANSACTION",
             question="간식값은 누가 계산했어요?", required=["TRANSACTION"])
    second = ask("별명은 도토리", first.facts)
    assert not second.canGenerateScenario
    assert second.nextQuestion == "간식값은 누가 계산했어요?"
    decision(grounded_model, [fact("TRANSACTION", 3, "도토리가 계산했고 나중에 보내주기로 했어요")], required=["TRANSACTION"])
    third = ask("도토리가 계산했고 나중에 보내주기로 했어요", second.facts)
    assert third.canGenerateScenario
    assert third.facts["knownTopics"]["PERSON_NAME"]["name"] == "도토리"
    assert third.facts["knownTopics"]["TRANSACTION"]["turn"] == 3
    assert len(third.facts["questionHistory"]) == 2


def test_person_target_cannot_skip_name_even_if_requirements_omit_it(grounded_model):
    text = "친구와 이용했고 친구가 요금을 냈어요"
    decision(grounded_model, [fact("ACTIVITY", 1, "친구와 이용했고"), fact("PEOPLE", 1, "친구와"),
                            fact("TRANSACTION", 1, "친구가 요금을 냈어요")], next_topic="PERSON_NAME")
    result = ask(text)
    assert not result.canGenerateScenario
    assert result.facts["questionHistory"][-1]["topic"] == "PERSON_NAME"


def test_name_refusal_is_not_reasked_or_filled_with_an_invented_name(grounded_model):
    decision(grounded_model, [fact("ACTIVITY", 1, "친구와 이용했고"), fact("PEOPLE", 1, "친구와"),
                            fact("TRANSACTION", 1, "친구가 요금을 냈어요")], next_topic="PERSON_NAME")
    first = ask("친구와 이용했고 친구가 요금을 냈어요")
    decision(grounded_model, [fact("PERSON_NAME", 2, "말하고 싶지 않아요", "DECLINED")], next_topic="PERSON_NAME")
    second = ask("말하고 싶지 않아요", first.facts)
    assert second.canGenerateScenario
    assert second.facts["knownTopics"]["PERSON_NAME"]["name"] is None
    assert len(second.facts["questionHistory"]) == 1


def test_named_person_is_not_mandatory_for_a_service_contact(grounded_model):
    text = "동네 문화센터를 친구와 이용했고 제가 창구에서 요금을 냈어요"
    decision(grounded_model, [fact("ACTIVITY", 1, "친구와 이용했고"), fact("PEOPLE", 1, "친구와"),
                            fact("TRANSACTION", 1, "제가 창구에서 요금을 냈어요"), fact("SERVICE", 1, "동네 문화센터")], target="SERVICE")
    result = ask(text)
    assert result.canGenerateScenario
    assert "PERSON_NAME" not in result.facts["knownTopics"]


def test_a_rich_answer_with_a_nickname_can_finish_in_one_turn(grounded_model):
    text = "친구 도토리와 이용했고 도토리가 요금을 냈어요"
    decision(grounded_model, [fact("ACTIVITY", 1, "이용했고"), fact("PEOPLE", 1, "친구 도토리와"),
        fact("PERSON_NAME", 1, "도토리", name="도토리"), fact("TRANSACTION", 1, "도토리가 요금을 냈어요")])
    result = ask(text)
    assert result.canGenerateScenario
    assert result.facts["questionHistory"] == []


def test_unstated_name_is_rejected_before_it_can_enter_state(grounded_model):
    decision(grounded_model, [fact("PERSON_NAME", 1, "친구랑", name="도토리")])
    with pytest.raises(AiApiError):
        ask("친구랑")


def test_partial_context_cannot_make_ready_and_clarification_is_bounded(grounded_model):
    decision(grounded_model, [fact("ACTIVITY", 1, "서비스를 이용했어요")], target="SERVICE", next_topic="ACCESS", required=["ACCESS"])
    first = ask("서비스를 이용했어요")
    decision(grounded_model, [fact("ACCESS", 2, "기기로요", "PARTIAL")], target="SERVICE", next_topic="ACCESS",
             question="이용 신청은 누가 했나요?", required=["ACCESS"])
    second = ask("기기로요", first.facts)
    assert not second.canGenerateScenario and second.nextQuestion == "이용 신청은 누가 했나요?"
    decision(grounded_model, [fact("ACCESS", 3, "그 기기로요", "PARTIAL")], target="SERVICE", next_topic="ACCESS", required=["ACCESS"])
    third = ask("그 기기로요", second.facts)
    assert not third.canGenerateScenario
    assert third.facts["intakePaused"]
    assert len(third.facts["questionHistory"]) == len(second.facts["questionHistory"])
    assert sum(item["topic"] == "ACCESS" for item in third.facts["questionHistory"]) == 2


def named_generation(grounded_model, graph_data, plan_data, review_data):
    from app.schemas.context_graph import Approach
    name = "도토리"
    text = "친구 도토리와 이용했고 도토리가 이용요금을 냈어요"
    graph_data["entities"][1]["label"] = name
    graph_data["evidence"][0]["quote"] = text
    first = {key: deepcopy(value) for key, value in plan_data.items() if key in Approach.model_fields}
    first.update(desiredOutcome="정산 확인을 빌미로 정보 요구", escalation="대화 후 요청")
    second = {**first, "id": "plan_b", "pretext": "요금 정산 안내", "escalation": "급한 확인을 요구"}
    grounded_model[1]["candidate_plan"] = dict(context=graph_data, realConcern=False,
        anchorClaims=plan_data["anchorClaims"], personalizationBasis=plan_data["personalizationBasis"], candidates=[first, second])
    decision(grounded_model, [fact("ACTIVITY", 1, "이용했고"), fact("PEOPLE", 1, "친구 도토리와"),
        fact("PERSON_NAME", 1, name, name=name), fact("TRANSACTION", 1, "도토리가 이용요금을 냈어요")])
    return ask(text).facts


def test_received_name_reaches_persona_and_opening(grounded_model, graph_data, plan_data, review_data, generation_data):
    facts = named_generation(grounded_model, graph_data, plan_data, review_data)
    generation_data["scenario"]["persona"]["name"] = "도토리"
    generation_data["scenario"]["openingMessage"] = "나 도토리야. 이용요금 정산 때문에 연락했어."
    result = training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    assert result.scenario["persona"]["name"] == "도토리"
    assert "도토리" in result.scenario["openingMessage"]
    payload = next(payload for task, payload in grounded_model[0] if task == "candidate_plan")
    assert payload["personalizationTarget"] == "PERSON"
    assert payload["confirmedTopics"]["PERSON_NAME"]["name"] == "도토리"


def test_generic_persona_cannot_pass_even_if_semantic_reviewer_approves(grounded_model, graph_data, plan_data, review_data):
    facts = named_generation(grounded_model, graph_data, plan_data, review_data)
    with pytest.raises(AiApiError):
        training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    payloads = [payload for task, payload in grounded_model[0] if task == "scenario_generate"]
    assert len(payloads) == 2
    assert payloads[1]["retryIssues"] == ["CONFIRMED_PERSON_NAME_NOT_USED"]
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


def test_confirmed_name_cannot_be_dropped_from_the_fact_graph(grounded_model, graph_data, plan_data, review_data):
    facts = named_generation(grounded_model, graph_data, plan_data, review_data)
    graph_data["entities"][1]["label"] = "친구"
    with pytest.raises(AiApiError):
        training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    assert not any(task == "scenario_generate" for task, _ in grounded_model[0])


def test_unselected_inventory_topic_is_never_used_as_fallback(grounded_model):
    decision(grounded_model, [fact("ACTIVITY", 1, "작업을 도왔어요")], target="OTHER", sufficient=False)
    result = ask("작업을 도왔어요")
    assert not result.canGenerateScenario
    assert result.facts["questionHistory"] == []
    assert "결제" not in result.nextQuestion
    assert result.facts["intakePaused"]


def test_selected_question_must_be_a_needed_gap(grounded_model):
    decision(grounded_model, [fact("ACTIVITY", 1, "작업을 도왔어요")], target="OTHER",
             next_topic="TRANSACTION", required=["INTERACTION"], sufficient=False)
    result = ask("작업을 도왔어요")
    assert result.facts["questionHistory"] == []
    assert not result.canGenerateScenario


def test_user_chosen_form_of_address_is_enough_for_identity(grounded_model):
    text = "형이 온라인 강의 화면을 못 찾아서 제가 메뉴를 찾아줬어요"
    decision(grounded_model, [fact("ACTIVITY", 1, "메뉴를 찾아줬어요"), fact("PEOPLE", 1, "형"),
        fact("PERSON_NAME", 1, "형", name="형"), fact("ROLE", 1, "제가 메뉴를 찾아줬어요")])
    result = ask(text)
    assert result.canGenerateScenario
    assert result.facts["knownTopics"]["PERSON_NAME"]["name"] == "형"
    assert result.facts["questionHistory"] == []


def test_blocked_optional_detail_is_reassessed_without_other_inventory_questions(grounded_model):
    text = "형이 온라인 강의 화면을 못 찾아서 제가 메뉴를 찾아줬어요"
    evidence = [dict(turn=1, text=text)]
    topics = {item["topic"]: {key:value for key,value in item.items() if key != "topic"} for item in [
        fact("ACTIVITY", 1, "메뉴를 찾아줬어요"), fact("PEOPLE", 1, "형"),
        fact("PERSON_NAME", 1, "형", name="형"), fact("ROLE", 1, "제가 메뉴를 찾아줬어요"),
        fact("SERVICE", 2, "이름은 몰라요", "DECLINED")]}
    known = dict(intakeTurn=2, discoveryEvidence=evidence+[dict(turn=2,text="이름은 몰라요")], knownTopics=topics,
                 questionHistory=[dict(topic="SERVICE",afterTurn=1,question="서비스 이름은 무엇인가요?")])
    def respond(payload):
        if payload.get("mode") == "REASSESS":
            return dict(updates=[], personalizationTarget="PERSON", requiredTopics=["ROLE"], contextSufficient=True, focusChanged=True)
        return dict(updates=[], personalizationTarget="PERSON", requiredTopics=["SERVICE"],
                    contextSufficient=False,nextTopic="SERVICE",nextQuestion="서비스 이름은 무엇인가요?")
    grounded_model[1]["discovery_brief"] = respond
    result = ask("기억이 안 나요",known)
    assert result.canGenerateScenario
    assert result.facts["questionHistory"] == known["questionHistory"]
    assert "SERVICE" not in result.facts["intakeRequirements"]["topics"]
    assert len(grounded_model[0]) == 2
    assert grounded_model[0][-1][1]["mode"] == "REASSESS"


def test_paused_acknowledgements_return_nonblank_reply_and_new_facts_can_resume(grounded_model):
    decision(grounded_model,[fact("ACTIVITY",1,"작업을 도왔어요")],target="OTHER",sufficient=False)
    first=ask("작업을 도왔어요")
    decision(grounded_model,[],target="OTHER",sufficient=False)
    second=ask("알겠어요",first.facts)
    assert first.nextQuestion and second.nextQuestion.strip()
    assert second.nextQuestion != first.nextQuestion
    assert "?" not in second.nextQuestion
    assert second.facts["questionHistory"] == first.facts["questionHistory"]
    assert not second.canGenerateScenario
    decision(grounded_model,[fact("ROLE",3,"자료 찾는 일을 도왔어요")],target="OTHER")
    third=ask("자료 찾는 일을 도왔어요",second.facts)
    assert third.canGenerateScenario
    assert "intakePaused" not in third.facts


def test_invalid_evidence_gets_one_bounded_correction_before_use(grounded_model):
    answers = []
    def respond(payload):
        answers.append(payload)
        quote = "사용자가 하지 않은 행동" if len(answers) == 1 else "메뉴 찾기를 도왔어요"
        return dict(updates=[fact("ACTIVITY",1,"메뉴 찾기를 도왔어요"),fact("ROLE",1,quote)],
                    contextSufficient=True,personalizationTarget="OTHER",requiredTopics=[])
    grounded_model[1]["discovery_brief"] = respond
    result=ask("메뉴 찾기를 도왔어요")
    assert result.canGenerateScenario
    assert len(answers)==2 and "formatCorrection" in answers[1]
    assert result.facts["knownTopics"]["ROLE"]["quote"] == "메뉴 찾기를 도왔어요"


def test_service_ready_claim_cannot_skip_identifying_the_service(grounded_model):
    text = "카페에서 코딩하다 충전기를 빌리지 못했어"
    decision(grounded_model, [fact("ACTIVITY", 1, "코딩하다"), fact("INTERACTION", 1, "충전기를 빌리지 못했어")], target="SERVICE")
    result = ask(text)
    assert not result.canGenerateScenario
    assert result.facts["questionHistory"][-1]["topic"] == "SERVICE"
    assert "readinessEvidence" not in result.facts


def test_confirmed_service_is_reused_and_missing_time_is_asked_once(grounded_model):
    text = "이디야에서 코딩하다 충전기를 빌리지 못했어"
    decision(grounded_model, [fact("ACTIVITY", 1, "코딩하다"), fact("SERVICE", 1, "이디야"), fact("INTERACTION", 1, "충전기를 빌리지 못했어")], target="SERVICE", required=["TIME"], next_topic="TIME", question="언제쯤 방문하셨어요?", sufficient=False)
    first = ask(text)
    assert not first.canGenerateScenario and first.facts["questionHistory"][-1]["topic"] == "TIME"
    decision(grounded_model, [fact("TIME", 2, "어제")], target="SERVICE", required=["TIME"])
    second = ask("어제", first.facts)
    assert second.canGenerateScenario
    assert second.facts["knownTopics"]["SERVICE"]["quote"] == "이디야"
    assert second.facts["knownTopics"]["TIME"]["quote"] == "어제"
    assert len(second.facts["questionHistory"]) == 1


def test_unknown_service_is_not_reasked_or_invented(grounded_model):
    text = "카페에서 코딩하다 충전기를 빌리지 못했어"
    decision(grounded_model, [fact("ACTIVITY", 1, "코딩하다"), fact("INTERACTION", 1, "충전기를 빌리지 못했어")], target="SERVICE")
    first = ask(text)
    decision(grounded_model, [fact("SERVICE", 2, "이름은 기억 안나", "DECLINED")], target="SERVICE")
    second = ask("이름은 기억 안나", first.facts)
    assert second.canGenerateScenario
    assert second.facts["knownTopics"]["SERVICE"]["status"] == "DECLINED"
    assert len(second.facts["questionHistory"]) == 1


def test_rich_service_context_needs_no_extra_questions(grounded_model):
    text = "어제 이디야에서 코딩하다 충전기를 빌리지 못했고 앱으로 주문했어"
    decision(grounded_model, [fact("ACTIVITY", 1, "코딩하다"), fact("SERVICE", 1, "이디야"), fact("TIME", 1, "어제"), fact("INTERACTION", 1, "충전기를 빌리지 못했고"), fact("ACCESS", 1, "앱으로 주문했어")], target="SERVICE", required=["TIME", "ACCESS"])
    result = ask(text)
    assert result.canGenerateScenario
    assert result.facts["questionHistory"] == []


def test_rich_answer_retains_more_than_five_topics_in_one_call(grounded_model):
    text = "어제 이디야에서 혼자 코딩하다 충전기를 못 빌렸고 앱으로 주문했어"
    decision(grounded_model, [fact("ACTIVITY", 1, "코딩하다"), fact("SETTING", 1, "이디야에서"), fact("PEOPLE", 1, "혼자"), fact("SERVICE", 1, "이디야"), fact("TIME", 1, "어제"), fact("INTERACTION", 1, "충전기를 못 빌렸고"), fact("ACCESS", 1, "앱으로 주문했어")], target="SERVICE", required=["TIME", "ACCESS", "INTERACTION"])
    result = ask(text)
    assert result.canGenerateScenario
    assert len(result.facts["knownTopics"]) == 7
    assert len(grounded_model[0]) == 1
    assert result.facts["questionHistory"] == []


def test_no_more_details_after_cafe_pause_returns_valid_public_response(grounded_model):
    from fastapi.testclient import TestClient
    from app.main import app
    evidence = [{"turn": 1, "text": "카페 갔어"}, {"turn": 2, "text": "플젝"}, {"turn": 3, "text": "아니"}]
    state = {"intakeTurn": 3, "intakePaused": True, "discoveryEvidence": evidence,
        "knownTopics": {"ACTIVITY": {"status": "KNOWN", "turn": 2, "quote": "플젝"},
                        "INTERACTION": {"status": "DECLINED", "turn": 3, "quote": "아니"}},
        "questionHistory": [], "intakeRequirements": {"target": "OTHER", "topics": []}}
    decision(grounded_model, [], target="OTHER", sufficient=False)
    with TestClient(app) as client:
        response = client.post("/internal/ai/discovery/next", headers={"X-Internal-Api-Key": "test-internal-key"},
            json={"conversation": [], "knownFacts": state, "currentInput": "뭐 없는데"})
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["nextQuestion"].strip()
    assert not result["canGenerateScenario"]
    assert result["facts"]["intakePaused"] is True
    assert result["facts"]["knownTopics"]["ACTIVITY"]["quote"] == "플젝"
    assert len(grounded_model[0]) == 1


@pytest.mark.parametrize("closed_status", ["NOT_APPLICABLE", "DECLINED"])
def test_no_social_interaction_keeps_other_context_routes_open(grounded_model, closed_status):
    state = {"intakeTurn": 2, "discoveryEvidence": [{"turn": 1, "text": "공유 공간에 갔어요"}, {"turn": 2, "text": "혼자 문서 정리했어요"}],
        "knownTopics": {"SETTING": {"status": "KNOWN", "turn": 1, "quote": "공유 공간"},
                        "ACTIVITY": {"status": "KNOWN", "turn": 2, "quote": "문서 정리했어요"},
                        "PEOPLE": {"status": "KNOWN", "turn": 2, "quote": "혼자"}},
        "questionHistory": [{"topic": "INTERACTION", "afterTurn": 2, "question": "주고받은 이야기가 있었나요?"}]}
    calls = []
    def answer(payload):
        calls.append(payload)
        if payload.get("mode") == "REASSESS":
            assert "INTERACTION" not in payload["exploration"]["openContextTopics"]
            assert "INTERACTION" in payload["exploration"]["closedTopics"]
            return dict(updates=[], personalizationTarget="SERVICE", contextSufficient=False,
                requiredTopics=["SERVICE"], nextTopic="SERVICE", nextQuestion="어느 곳을 이용하셨어요?")
        return dict(updates=[fact("INTERACTION", 3, "없어요", closed_status)],
            personalizationTarget="UNDECIDED", contextSufficient=False, requiredTopics=[], nextTopic=None)
    grounded_model[1]["discovery_brief"] = answer
    result = ask("없어요", state)
    assert not result.canGenerateScenario
    assert result.nextQuestion == "어느 곳을 이용하셨어요?"
    assert "intakePaused" not in result.facts
    assert result.facts["questionHistory"][-1]["topic"] == "SERVICE"
    assert len(calls) == 2


def test_context_route_exploration_respects_closed_and_exhausted_topics():
    from app.services.fast_discovery import _exploration
    topics = {"ACTIVITY": {"status": "KNOWN"}, "INTERACTION": {"status": "NOT_APPLICABLE"},
              "SERVICE": {"status": "DECLINED"}, "ACCESS": {"status": "PARTIAL"}}
    trace = _exploration(topics, [{"topic": "ACCESS"}, {"topic": "ACCESS"}])
    assert trace["humanInteractionRequired"] is False
    assert not {"INTERACTION", "SERVICE", "ACCESS"} & set(trace["openContextTopics"])
    assert "TIME" in trace["openContextTopics"]


def test_known_activity_can_be_deepened_once_without_reasking_activity(grounded_model):
    decision(grounded_model, [fact("SETTING", 1, "카페"), fact("ACTIVITY", 1, "코딩했어요")], target="UNDECIDED", sufficient=False,
        next_topic="ACTIVITY_DETAIL", question="어떤 작업을 하고 계셨어요?")
    first = ask("카페에서 코딩했어요")
    assert first.facts["questionHistory"][-1]["topic"] == "ACTIVITY_DETAIL"
    decision(grounded_model, [fact("ACTIVITY_DETAIL", 2, "프로젝트요", "PARTIAL")], target="UNDECIDED", sufficient=False,
        next_topic="ACTIVITY_DETAIL", question="어떤 걸 만드는 프로젝트인가요?")
    second = ask("프로젝트요", first.facts)
    assert second.nextQuestion == "어떤 걸 만드는 프로젝트인가요?"
    decision(grounded_model, [fact("ACTIVITY_DETAIL", 3, "그냥 프로젝트", "PARTIAL")], target="UNDECIDED", sufficient=False,
        next_topic="ACTIVITY_DETAIL", question="무슨 프로젝트인가요?")
    third = ask("그냥 프로젝트", second.facts)
    assert third.facts["intakePaused"]
    assert len(third.facts["questionHistory"]) == 2
    assert third.facts["knownTopics"]["ACTIVITY"]["quote"] == "코딩했어요"


@pytest.mark.parametrize("status", ["KNOWN", "DECLINED"])
def test_resolved_or_declined_activity_detail_is_not_reasked(grounded_model, status):
    decision(grounded_model, [fact("ACTIVITY", 1, "코딩했어요"), fact("ACTIVITY_DETAIL", 1, "중고책 앱을 만들었어요" if status == "KNOWN" else "말하고 싶지 않아요", status)],
        target="UNDECIDED", sufficient=False, next_topic="ACTIVITY_DETAIL", question="어떤 작업인가요?")
    text = "코딩했어요. " + ("중고책 앱을 만들었어요" if status == "KNOWN" else "말하고 싶지 않아요")
    result = ask(text)
    assert result.facts["questionHistory"] == []
    assert result.nextQuestion != "어떤 작업인가요?"


@pytest.mark.parametrize("approved", [True, False])
def test_concrete_solo_task_can_support_readiness_without_social_interaction(grounded_model, approved):
    text = "혼자 산책 코스 기록과 추천 웹 서비스를 만들었어요"
    decision(grounded_model, [fact("ACTIVITY", 1, "웹 서비스를 만들었어요"),
        fact("ACTIVITY_DETAIL", 1, "산책 코스 기록과 추천 웹 서비스"), fact("PEOPLE", 1, "혼자")],
        target="OTHER", sufficient=approved)
    result = ask(text)
    assert result.canGenerateScenario is approved
    if approved:
        assert result.facts["readinessEvidence"]["quote"] == "산책 코스 기록과 추천 웹 서비스"
        assert "intakePaused" not in result.facts
        assert result.facts["questionHistory"] == []
    else:
        assert "readinessEvidence" not in result.facts
