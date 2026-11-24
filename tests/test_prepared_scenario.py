"""Information sufficiency is approved before readiness, not during story generation."""
from copy import deepcopy
import json
import pytest

from app.api_errors import AiApiError
from app.schemas.discovery import DiscoveryRequest
from app.schemas.training_api import ScenarioRequest
from app.schemas.context_graph import DraftReview
from app.services import discovery, training_api
from app.services.prompt_templates import PROMPTS, load_prompt, response_schema


def ready(graph_data):
    return discovery.discover(DiscoveryRequest(conversation=[], knownFacts={},
        currentInput=graph_data["evidence"][0]["quote"]))


def test_insufficient_plan_continues_chat_instead_of_exposing_ready(grounded_model, graph_data, review_data):
    bad = deepcopy(review_data)
    bad.update(approved=False, issues=[{"code": "INSUFFICIENT_CONTEXT", "detail": "이용한 서비스 확인이 필요함"}],
               followupQuestion="어떤 서비스를 이용하셨나요?")
    bad["checks"]["sufficientContext"] = False
    grounded_model[1]["semantic_review"] = bad
    def decide(payload):
        if payload.get("mode") == "PLAN_CLARIFICATION":
            return {"updates": [], "personalizationTarget": "SERVICE", "requiredTopics": ["SERVICE"],
                    "readinessBasis": None, "contextSufficient": False, "nextTopic": "SERVICE",
                    "nextQuestion": "어떤 서비스를 이용하셨나요?"}
        text = graph_data["evidence"][0]["quote"]
        return {"updates": [{"topic": topic, "turn": 1, "quote": text} for topic in ("ACTIVITY", "TRANSACTION")],
                "personalizationTarget": "OTHER", "contextSufficient": True}
    grounded_model[1]["discovery_brief"] = decide
    result = ready(graph_data)
    assert not result.canGenerateScenario
    assert not result.facts["discoveryReady"]
    assert "preparedScenario" not in result.facts
    assert result.nextQuestion == "어떤 서비스를 이용하셨나요?"
    assert result.facts["verificationIssues"] == ["INSUFFICIENT_CONTEXT"]
    assert not any(task in {"scenario_generate", "scenario_classify"} for task, _ in grounded_model[0])


def test_generation_uses_receipt_and_has_no_sufficiency_review(grounded_model, graph_data):
    result = ready(graph_data)
    assert result.canGenerateScenario and result.facts["preparedScenario"]
    assert len(json.dumps(result.facts, ensure_ascii=False)) < 16000
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "generation_plan", "semantic_review"]
    grounded_model[0].clear()
    training_api.scenario(ScenarioRequest(facts=result.facts, fraudTypeCode="SMISHING"))
    assert [task for task, _ in grounded_model[0]] == ["scenario_generate", "draft_review", "scenario_classify"]
    review = next(p for task, p in grounded_model[0] if task == "draft_review")
    assert review["contextVerified"] is True
    assert review["reviewScope"] == "DRAFT_AGAINST_ACCEPTED_PLAN"
    schema = json.loads(response_schema(DraftReview))
    assert "sufficientContext" not in schema["$defs"]["DraftReviewChecks"]["properties"]
    assert "INSUFFICIENT_CONTEXT" not in schema["$defs"]["DraftReviewIssue"]["properties"]["code"]["enum"]
    assert "- sufficientContext:" not in load_prompt(PROMPTS / "draft_review.md")


def test_rejected_drafts_do_not_change_the_approved_plan(grounded_model, graph_data, review_data):
    facts = ready(graph_data).facts
    bad = deepcopy(review_data)
    bad.update(approved=False, issues=[{"code": "ACTOR_CHANGED", "detail": "생성 본문이 승인된 주체와 다름"}])
    bad["checks"]["actorsPreserved"] = False
    grounded_model[1]["draft_review"] = bad
    grounded_model[0].clear()
    with pytest.raises(AiApiError):
        training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    writers = [p for task, p in grounded_model[0] if task == "scenario_generate"]
    assert len(writers) == 4  # bounded rewrite and patch under the same plan
    assert all(p["plan"] == writers[0]["plan"] for p in writers)
    assert not any(task in {"generation_plan", "semantic_review", "scenario_classify"} for task, _ in grounded_model[0])


@pytest.mark.parametrize("change", ["receipt", "source"])
def test_changed_receipt_or_source_is_rejected_before_model_call(grounded_model, graph_data, change):
    facts = ready(graph_data).facts
    if change == "receipt":
        token = facts["preparedScenario"]
        facts["preparedScenario"] = ("A" if token[0] != "A" else "B") + token[1:]
    else:
        facts["discoveryEvidence"].append({"turn": 2, "text": "추가한 다른 상황"})
    grounded_model[0].clear()
    with pytest.raises(AiApiError) as error:
        training_api.scenario(ScenarioRequest(facts=facts, fraudTypeCode="SMISHING"))
    assert error.value.status == 422
    assert not grounded_model[0]


@pytest.mark.parametrize("reply", ["모르겠네, 카톡으로 오지 않을까", "뭘 더 얘기해야해?"])
def test_confirmed_mentoring_is_planned_without_requiring_fictional_contact_details(grounded_model, reply):
    messages = [
        {"turn": 1, "text": "어제 부캠에서 멘토링 받았어"},
        {"turn": 2, "text": "플젝 어떻게 개선할지, 발표때 뭘 준비할지"},
        {"turn": 3, "text": "둘 다 괜찮은거같아"},
        {"turn": 4, "text": "부캠 최종 프로젝트에선 원래 멘토를 붙여준대. 운영 측에서"},
        {"turn": 5, "text": "멘토님이 우리 플젝에서는 사례를 한 5가지 정도로 줄여서 그거를 고도화하라했어"},
        {"turn": 6, "text": "그냥 멘토님이라고 불러"},
        {"turn": 7, "text": "팀원 3명이서 했어"},
    ]
    def fact(turn, **kwargs):
        return {"status": "KNOWN", "turn": turn, "quote": messages[turn-1]["text"], **kwargs}
    topics = {"ACTIVITY": fact(1), "ACTIVITY_DETAIL": fact(2), "PURPOSE": fact(4),
              "INTERACTION": fact(5), "PERSON_NAME": fact(6, name="멘토님"), "PEOPLE": fact(7),
              "ACCESS": fact(3, status="PARTIAL")}
    grounded_model[1]["discovery_brief"] = {
        "updates": [], "personalizationTarget": "PERSON", "requiredTopics": ["ACCESS"],
        "readinessBasis": None, "nextTopic": "ACCESS", "nextQuestion": "어떤 경로로 연락하면 자연스러울까요?"}
    known = {"intakeTurn": 7, "discoveryEvidence": messages, "knownTopics": topics,
             "questionHistory": [{"topic": "ACCESS", "afterTurn": 2, "question": "어떤 내용으로 연락하면 자연스러울까요?"}]}
    result = discovery.discover(DiscoveryRequest(conversation=[], knownFacts=known, currentInput=reply))
    assert result.canGenerateScenario
    assert result.facts["preparedScenario"]
    assert not result.facts["basicScenarioOffered"]
    assert "ACCESS" not in result.facts["intakeRequirements"]["topics"]
    basis = result.facts["readinessBasis"]
    assert basis["evidence"] == [{"turn": 5, "quote": messages[4]["text"]}]
    assert [task for task, _ in grounded_model[0]] == ["discovery_brief", "generation_plan", "semantic_review"]


@pytest.mark.parametrize("code,context_ok,followup", [
    ("UNSUPPORTED_FACT", True, "어떤 내용을 더 말씀해 주시겠어요?"),
    ("OTHER", False, ""),
    ("INSUFFICIENT_CONTEXT", False, ""),
])
def test_plan_failure_does_not_become_user_information_shortage(grounded_model, graph_data, review_data, code, context_ok, followup):
    bad = deepcopy(review_data)
    bad.update(approved=False, issues=[{"code": code, "detail": "계획 수정 필요"}], followupQuestion=followup)
    bad["checks"]["sufficientContext"] = context_ok
    grounded_model[1]["semantic_review"] = bad
    with pytest.raises(AiApiError) as error:
        ready(graph_data)
    assert error.value.status == 503
    assert "정보가 부족" not in error.value.message
    assert "훈련 계획" in error.value.message
    assert not any(payload.get("mode") == "PLAN_CLARIFICATION" for _, payload in grounded_model[0])


def test_interaction_candidate_keeps_identity_gap_closed_until_answered():
    from app.services.readiness import interaction_candidate
    topics = {key: {"status": "KNOWN", "turn": 1, "quote": "직원에게 배송 일정을 물었어요"}
              for key in ("ACTIVITY", "INTERACTION", "PEOPLE")}
    assert interaction_candidate(topics, "PERSON") is None
    assert interaction_candidate(topics, "SERVICE") is None
    topics["PERSON_NAME"] = {"status": "PARTIAL", "turn": 1, "quote": "직원"}
    assert interaction_candidate(topics, "PERSON") is None


def test_structural_plan_failure_reenters_source_recovery_before_failing_chat(grounded_model, graph_data, monkeypatch):
    from app.services import scenario_planner
    from app.services.context_graph import ContextIntegrityError
    original = scenario_planner.prepare_generation_plan
    attempts = []
    def prepare(messages, **kwargs):
        attempts.append((deepcopy(messages), deepcopy(kwargs.get("recovery_feedback"))))
        if len(attempts) == 1:
            raise ContextIntegrityError("confirmed_person_name_missing")
        return original(messages, **kwargs)
    monkeypatch.setattr(scenario_planner, "prepare_generation_plan", prepare)
    result = ready(graph_data)
    assert result.canGenerateScenario and result.facts["preparedScenario"]
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0]
    assert attempts[1][1] == ["STRUCTURAL_PLAN_ERROR: confirmed_person_name_missing"]
    assert not any(p.get("mode") == "PLAN_CLARIFICATION" for _, p in grounded_model[0])


def test_structural_recovery_is_bounded_and_logs_code_only(grounded_model, graph_data, monkeypatch, caplog):
    from app.services import scenario_planner
    from app.services.context_graph import ContextIntegrityError
    attempts = []
    def prepare(*args, **kwargs):
        attempts.append(1)
        raise ContextIntegrityError("unsupported_quote")
    monkeypatch.setattr(scenario_planner, "prepare_generation_plan", prepare)
    with pytest.raises(AiApiError):
        ready(graph_data)
    assert len(attempts) == 2
    assert "check=unsupported_quote" in caplog.text
    assert graph_data["evidence"][0]["quote"] not in caplog.text


def test_plan_format_repair_receives_the_rejected_object(monkeypatch, graph_data):
    from app.services import scenario_planner
    from app.schemas.context_graph import ContextGraph
    from app.services.context_graph import check_graph
    messages = [{"turn": row["sourceTurn"], "text": row["quote"]} for row in graph_data["evidence"]]
    bad = deepcopy(graph_data)
    bad["evidence"][0]["quote"] = "원문에 없는 인용"
    calls = []
    def generate(system, content, **kwargs):
        payload = json.loads(content)
        calls.append(payload)
        return deepcopy(bad if len(calls) == 1 else graph_data)
    monkeypatch.setattr(scenario_planner, "generate_json", generate)
    result = scenario_planner.ask("generation_plan", {"messages": messages}, ContextGraph,
                                  validate=lambda graph: check_graph(graph, messages))
    assert result.model_dump() == graph_data
    assert calls[1]["rejectedOutput"] == bad
    assert calls[1]["schemaErrors"] == [{"path": "generation_plan", "type": "unsupported_quote"}]
    assert calls[0]["messages"] == calls[1]["messages"]
