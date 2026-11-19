"""실제 LangGraph + 가짜 모델로 Spring assist 계약/경계를 검증. 외부 API 호출 없음."""
import os

os.environ["RAG_FAKE"] = "1"

import asyncio  # noqa: E402
from copy import deepcopy
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
import pytest

from app import dependencies
from app.main import app
from app.routers.assist import get_service
from assist.contracts import (
    AnswerRequest, AnswerResponse, Assessment, AssistRequest, CandidateReference, Clarification,
    EvidenceAssessment, ModelClarification, PlanRequest, PlanResponse,
)
from assist.graph import AssistService
from assist.model import FakeAssistModel

FACTS = {"transferredMoney": "YES", "sharedInformation": "NO",
         "installedApp": "UNKNOWN", "contactContinues": "UNKNOWN"}
REFERENCE = {"code": "APP_REMOVE_GUIDE", "version": "1", "title": "앱 관련 안내",
             "excerpt": "설치 여부를 확인하고 안내된 절차를 확인합니다."}
PATH = "/internal/ai/solutions/assist"
PLAN_KEYS = {"status", "questionIntent", "clarification", "candidateReferences", "reasonCode"}
ANSWER_KEYS = {"status", "answer", "referenceCodes", "reasonCode"}


def plan_request(**changes):
    data = {"phase": "PLAN", "question": "앱 관련해서 어떻게 해야 하나요?", "questionIntent": None,
            "facts": deepcopy(FACTS), "askedFactKeys": [], "remainingClarifications": 3}
    data.update(changes)
    return data


def answer_request(**changes):
    data = {"phase": "ANSWER", "question": "앱 관련해서 어떻게 해야 하나요?",
            "questionIntent": "DEVICE_SAFETY", "facts": deepcopy(FACTS),
            "verifiedReferences": [{k: v for k, v in REFERENCE.items() if k != "version"}]}
    data.update(changes)
    return data


def evidence_response(**changes):
    data = {"status": "EVIDENCE_READY", "questionIntent": "DEVICE_SAFETY", "clarification": None,
            "candidateReferences": [{"code": "APP_REMOVE_GUIDE", "version": "1"}], "reasonCode": None}
    data.update(changes)
    return data


def answered_response(**changes):
    data = {"status": "ANSWERED", "answer": "검수된 자료의 안내를 확인해 주세요.",
            "referenceCodes": ["APP_REMOVE_GUIDE"], "reasonCode": None}
    data.update(changes)
    return data


class Retriever:
    def __init__(self, references=None):
        self.references = deepcopy([REFERENCE] if references is None else references)
        self.queries = []

    async def search(self, query, facts):
        self.queries.append((query, deepcopy(facts)))
        return deepcopy(self.references)


class SearchModel(FakeAssistModel):
    async def assess(self, request):
        return Assessment(decision="SEARCH", questionIntent=request.questionIntent or "DEVICE_SAFETY",
                          factKey=None, content=None)


@pytest.fixture(autouse=True)
def isolated_index(tmp_path, monkeypatch):
    # 이 파일 테스트만 빈 임시 인덱스·가짜 모드로 돌려 data/index와 다른 테스트에 영향을 주지 않는다.
    from rag import config
    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "index")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dependencies, "settings", SimpleNamespace(internal_api_key="local-contract-test-key"))
    retriever = Retriever()
    service = AssistService(FakeAssistModel(), retriever)
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client, retriever
    app.dependency_overrides.clear()


def post(client, body, **kwargs):
    headers = {"X-Internal-Api-Key": "local-contract-test-key", "X-Request-Id": str(uuid4())}
    headers.update(kwargs.pop("headers", {}))
    return client.post(PATH, json=body, headers=headers, **kwargs)


def run(request, model=None, retriever=None):
    service = AssistService(model or FakeAssistModel(), retriever or Retriever())
    return asyncio.run(service.run(TypeAdapter(AssistRequest).validate_python(request)))


def test_information_missing_proposes_one_question(client):
    http, retriever = client
    response = post(http, plan_request())
    assert response.status_code == 200
    assert response.json()["success"] is True
    data = response.json()["data"]
    assert set(data) == PLAN_KEYS
    assert data["status"] == "NEED_USER_INPUT"
    assert data["questionIntent"] == "DEVICE_SAFETY"
    assert data["clarification"]["factKey"] == "installedApp"
    assert data["clarification"]["options"] == ["YES", "NO", "UNKNOWN"]
    assert data["candidateReferences"] == [] and data["reasonCode"] is None
    assert not retriever.queries


def test_followup_uses_spring_state_and_returns_only_candidates(client):
    http, retriever = client
    request = plan_request(question=None, questionIntent="DEVICE_SAFETY", askedFactKeys=["installedApp"],
                           facts={**FACTS, "installedApp": "YES"}, remainingClarifications=2)
    response = post(http, request)
    data = response.json()["data"]
    assert response.status_code == 200
    assert data == evidence_response()
    assert "질문:" not in retriever.queries[0][0]
    assert "앱 관련해서 어떻게 해야 하나요?" not in retriever.queries[0][0]
    assert retriever.queries[0][1] == request["facts"]
    assert "excerpt" not in json.dumps(data)


def test_answer_never_searches(client):
    http, retriever = client
    response = post(http, answer_request(question=None))
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == ANSWER_KEYS
    assert data["status"] == "ANSWERED"
    assert data["referenceCodes"] == [REFERENCE["code"]]
    assert not retriever.queries


def test_out_of_scope_is_business_result():
    result = run(plan_request(question="내일 날씨 알려줘"))
    assert result.status == "UNABLE_TO_ANSWER" and result.reasonCode == "OUT_OF_SCOPE"
    assert set(result.model_dump()) == PLAN_KEYS


@pytest.mark.parametrize("changes", [
    {"remainingClarifications": 0},
    {"askedFactKeys": ["installedApp"]},  # 사용자가 모름을 선택해 UNKNOWN인 경우 포함
])
def test_required_but_unavailable_fact_never_repeats(changes):
    result = run(plan_request(**changes))
    assert result.status == "EVIDENCE_READY" and result.reasonCode is None
    assert result.clarification is None


def test_no_clarification_budget_does_not_block_answerable_question():
    result = run(plan_request(remainingClarifications=0), model=SearchModel())
    assert result.status == "EVIDENCE_READY"


def test_missing_evidence_is_not_a_question():
    result = run(plan_request(), model=SearchModel(), retriever=Retriever([]))
    assert result.status == "UNABLE_TO_ANSWER" and result.reasonCode == "NO_EVIDENCE"
    assert result.clarification is None


@pytest.mark.parametrize("asked,expected", [([], "NEED_USER_INPUT"), (["installedApp"], "EVIDENCE_READY")])
def test_evidence_can_reveal_need_for_user_information(asked, expected):
    class ConditionalEvidence(SearchModel):
        async def evaluate(self, request, intent, references):
            if request.remainingClarifications == 0:
                return EvidenceAssessment(sufficient=True, referenceCodes=[REFERENCE["code"]], retryQuery=None)
            return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None,
                                      clarification=ModelClarification(factKey="installedApp"))

    retriever = Retriever()
    result = run(plan_request(askedFactKeys=asked), model=ConditionalEvidence(), retriever=retriever)
    assert result.status == expected
    assert len(retriever.queries) == 1
    if asked:
        assert result.reasonCode is None and result.clarification is None
    else:
        assert result.clarification.factKey == "installedApp" and result.reasonCode is None


@pytest.mark.parametrize("stage", ["assess", "evaluate"])
def test_unrelated_clarification_goes_to_search(stage):
    class UnrelatedModel(SearchModel):
        async def assess(self, request):
            return Assessment(decision="NEED_USER_INPUT" if stage == "assess" else "SEARCH",
                              questionIntent="INFO_LEAK_RESPONSE",
                              factKey="contactContinues" if stage == "assess" else None)

        async def evaluate(self, request, intent, references):
            if stage == "evaluate" and request.remainingClarifications > 0:
                return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None,
                                          clarification=ModelClarification(factKey="contactContinues"))
            return EvidenceAssessment(sufficient=True, referenceCodes=[REFERENCE["code"]], retryQuery=None)

    result = run(plan_request(question="신분증 사진을 보냈는데 뭘 해야 하나요?"), model=UnrelatedModel())
    assert result.status == "EVIDENCE_READY" and result.clarification is None


class AskInstalledAppModel(SearchModel):
    """송금 의도로 분류하면서 앱 설치 여부를 묻는 모델."""
    async def assess(self, request):
        return Assessment(decision="NEED_USER_INPUT", questionIntent="TRANSFER_RESPONSE",
                          factKey="installedApp")


def test_fact_mentioned_in_question_can_be_asked_even_if_intent_differs():
    question = "중고거래 판매자가 보낸 결제 링크를 눌렀는데 돈 돌려받을 수 있나요?"
    result = run(plan_request(question=question), model=AskInstalledAppModel())
    assert result.status == "NEED_USER_INPUT" and result.clarification.factKey == "installedApp"


def test_unmentioned_fact_of_other_intent_is_not_asked():
    question = "중고거래로 50만원 송금했는데 돈 돌려받을 수 있나요?"
    result = run(plan_request(question=question), model=AskInstalledAppModel())
    assert result.status == "EVIDENCE_READY" and result.clarification is None


def test_followup_without_question_only_allows_intent_fact():
    result = run(plan_request(question=None, questionIntent="TRANSFER_RESPONSE"), model=AskInstalledAppModel())
    assert result.status == "EVIDENCE_READY" and result.clarification is None


def test_shared_id_photo_does_not_open_money_question():
    class AskMoneyModel(SearchModel):
        async def assess(self, request):
            return Assessment(decision="NEED_USER_INPUT", questionIntent="INFO_LEAK_RESPONSE",
                              factKey="transferredMoney")

    facts = {**FACTS, "transferredMoney": "UNKNOWN", "sharedInformation": "YES"}
    result = run(plan_request(question="신분증 사진을 보냈는데 뭘 해야 하나요?", facts=facts), model=AskMoneyModel())
    assert result.status == "EVIDENCE_READY" and result.clarification is None


def test_repeated_clarification_after_reassessment_is_no_evidence():
    class RepeatedQuestion(SearchModel):
        async def evaluate(self, request, intent, references):
            return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None,
                                      clarification=ModelClarification(factKey="installedApp"))

    result = run(plan_request(askedFactKeys=["installedApp"]), model=RepeatedQuestion())
    assert result.status == "UNABLE_TO_ANSWER" and result.reasonCode == "NO_EVIDENCE"


def test_research_is_bounded_to_initial_plus_two_and_can_recover():
    class RetryModel(SearchModel):
        def __init__(self, recover=False):
            self.evaluations = 0
            self.recover = recover

        async def evaluate(self, request, intent, references):
            self.evaluations += 1
            sufficient = self.recover and self.evaluations == 2
            return EvidenceAssessment(sufficient=sufficient,
                                      referenceCodes=[REFERENCE["code"]] if sufficient else [],
                                      retryQuery=None if sufficient else f"재검색 조건 {self.evaluations}")

    for recover, count, expected in [(False, 3, "UNABLE_TO_ANSWER"), (True, 2, "EVIDENCE_READY")]:
        retriever = Retriever()
        model = RetryModel(recover)
        result = run(plan_request(), model=model, retriever=retriever)
        assert len(retriever.queries) == count and model.evaluations == count
        assert result.status == expected
        assert retriever.queries[0][1] == FACTS  # NO와 UNKNOWN을 변경하지 않는다
        if not recover:
            assert result.reasonCode == "NO_EVIDENCE"


def test_followup_intent_cannot_be_reclassified():
    class WrongIntent(SearchModel):
        async def assess(self, request):
            return Assessment(decision="SEARCH", questionIntent="TRANSFER_RESPONSE", factKey=None, content=None)

    result = run(plan_request(question=None, questionIntent="DEVICE_SAFETY"), model=WrongIntent())
    assert result.questionIntent == "DEVICE_SAFETY"


def test_query_contains_original_question_and_preserves_facts():
    retriever = Retriever()
    request = plan_request()
    original = deepcopy(request)
    run(request, model=SearchModel(), retriever=retriever)
    assert request == original
    assert request["question"] in retriever.queries[0][0]
    assert "installedApp=UNKNOWN" in retriever.queries[0][0]
    assert "sharedInformation=NO" in retriever.queries[0][0]


def test_tracing_disabled_even_when_enabled_in_environment(monkeypatch):
    from langsmith.run_helpers import get_tracing_context

    class TraceCheck(FakeAssistModel):
        async def assess(self, request):
            assert get_tracing_context()["enabled"] is False
            return await super().assess(request)

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    result = run(plan_request(), model=TraceCheck())
    assert result.status == "NEED_USER_INPUT"


def test_requests_do_not_share_state():
    async def exercise():
        service = AssistService(FakeAssistModel(), Retriever())
        first = await service.run(PlanRequest.model_validate(plan_request(question="내일 날씨")))
        second = await service.run(PlanRequest.model_validate(plan_request()))
        assert first.reasonCode == "OUT_OF_SCOPE"
        assert second.status == "NEED_USER_INPUT" and second.reasonCode is None
    asyncio.run(exercise())


@pytest.mark.parametrize("key", sorted(PLAN_KEYS))
def test_all_plan_response_keys_required(key):
    data = evidence_response()
    del data[key]
    with pytest.raises(ValidationError):
        PlanResponse.model_validate(data)


@pytest.mark.parametrize("key", sorted(ANSWER_KEYS))
def test_all_answer_response_keys_required(key):
    data = answered_response()
    del data[key]
    with pytest.raises(ValidationError):
        AnswerResponse.model_validate(data)


@pytest.mark.parametrize("version", [1, True, "", "a/b", "a b", "a" * 51, "1\n"])
def test_invalid_versions(version):
    with pytest.raises(ValidationError):
        CandidateReference(code="REF_A", version=version)


def test_version_and_code_accept_contract_alphabet():
    assert CandidateReference(code="REF_A_V1_P1", version="v1.2_3-draft").version == "v1.2_3-draft"


@pytest.mark.parametrize("options", [["NO", "YES", "UNKNOWN"], ["YES", "NO"], ["YES", "NO", "UNKNOWN", "YES"]])
def test_clarification_options_are_exact(options):
    with pytest.raises(ValidationError):
        Clarification(factKey="installedApp", content="앱을 설치했나요?", options=options)


@pytest.mark.parametrize("content", ["", " ", "가" * 201, "112로 전화했나요?", "https://example.org",
                                      "<b>앱 설치</b>", "[안내](주소)", "a@example.org"])
def test_clarification_content_restrictions(content):
    with pytest.raises(ValidationError):
        Clarification(factKey="installedApp", content=content, options=["YES", "NO", "UNKNOWN"])


def test_duplicate_candidate_codes_even_with_different_version_rejected():
    with pytest.raises(ValidationError):
        PlanResponse.model_validate(evidence_response(candidateReferences=[
            {"code": "REF_A", "version": "1"}, {"code": "REF_A", "version": "2"},
        ]))


@pytest.mark.parametrize("changes", [
    {"status": "evidence_ready"}, {"questionIntent": None}, {"candidateReferences": []},
    {"reasonCode": "NO_EVIDENCE"}, {"status": "ANSWERED"},
])
def test_plan_status_consistency(changes):
    with pytest.raises(ValidationError):
        PlanResponse.model_validate(evidence_response(**changes))


@pytest.mark.parametrize("changes", [
    {"answer": ""}, {"answer": " "}, {"answer": "가" * 2001}, {"answer": "112에 전화하세요"},
    {"answer": "1588-0000"}, {"answer": "010-1234-5678"}, {"answer": "https://example.org"},
    {"answer": "<p>안내</p>"}, {"answer": "[링크](주소)"}, {"answer": "a@example.org"},
    {"referenceCodes": []}, {"referenceCodes": ["REF_A", "REF_A"]},
    {"reasonCode": "NO_EVIDENCE"}, {"status": "NEED_USER_INPUT"},
    {"status": "UNABLE_TO_ANSWER", "answer": None, "referenceCodes": [], "reasonCode": "LIMIT_REACHED"},
])
def test_answer_constraints(changes):
    with pytest.raises(ValidationError):
        AnswerResponse.model_validate(answered_response(**changes))


def test_answer_citations_must_be_subset():
    response = AnswerResponse.model_validate(answered_response(referenceCodes=["OTHER_CODE"]))
    with pytest.raises(ValueError):
        response.validate_for(AnswerRequest.model_validate(answer_request()))


@pytest.mark.parametrize("changes", [
    {"remainingClarifications": 0}, {"askedFactKeys": ["installedApp"]},
    {"facts": {**FACTS, "installedApp": "NO"}},
])
def test_clarification_cross_checks_request(changes):
    response = PlanResponse(
        status="NEED_USER_INPUT", questionIntent="DEVICE_SAFETY",
        clarification=Clarification(factKey="installedApp", content="앱을 설치했나요?",
                                    options=["YES", "NO", "UNKNOWN"]),
        candidateReferences=[], reasonCode=None,
    )
    with pytest.raises(ValueError):
        response.validate_for(PlanRequest.model_validate(plan_request(**changes)))


def test_plan_response_cross_checks_followup_intent():
    response = PlanResponse.model_validate(evidence_response(questionIntent="GENERAL"))
    with pytest.raises(ValueError):
        response.validate_for(PlanRequest.model_validate(
            plan_request(question=None, questionIntent="DEVICE_SAFETY")))


def test_model_cannot_select_nonretrieved_reference():
    class BadSelection(SearchModel):
        async def evaluate(self, request, intent, references):
            return EvidenceAssessment(sufficient=True, referenceCodes=["OTHER_CODE"], retryQuery=None)

    with pytest.raises(ValueError):
        run(plan_request(), model=BadSelection())


def test_plan_cannot_return_duplicate_retrieved_codes():
    retriever = Retriever([REFERENCE, {**REFERENCE, "version": "2"}])
    with pytest.raises(ValueError):
        run(plan_request(), model=SearchModel(), retriever=retriever)


def test_real_retriever_uses_explicit_versions_and_handles_no_yes_facts():
    from assist.retrieval import ReferenceRetriever
    from rag import index

    index.build()
    references = asyncio.run(ReferenceRetriever().search("앱 설치 예방", {key: "UNKNOWN" for key in FACTS}))
    assert 1 <= len(references) <= 10
    assert all(isinstance(ref["version"], str) for ref in references)
    assert len({ref["code"] for ref in references}) == len(references)


def test_missing_index_becomes_recoverable_error():
    from assist.retrieval import ReferenceRetriever

    with pytest.raises(RuntimeError):
        asyncio.run(ReferenceRetriever().search("피해 대응", FACTS))


def test_unknown_extra_response_keys_ignored():
    assert "extra" not in PlanResponse.model_validate(evidence_response(extra="ignored")).model_dump()
    assert "extra" not in AnswerResponse.model_validate(answered_response(extra="ignored")).model_dump()


@pytest.mark.parametrize("changes", [
    {"phase": "plan"}, {"question": None, "questionIntent": None}, {"question": " "},
    {"question": "가" * 1001}, {"remainingClarifications": True}, {"remainingClarifications": -1},
    {"remainingClarifications": 4}, {"askedFactKeys": ["installedApp", "installedApp"]},
    {"askedFactKeys": ["otherFact"]}, {"facts": {"installedApp": "UNKNOWN"}},
    {"facts": {**FACTS, "installedApp": "unknown"}},
])
def test_invalid_requests_are_sanitized(client, changes):
    http, _ = client
    response = post(http, plan_request(**changes))
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False and body["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("headers", [
    {"X-Internal-Api-Key": "wrong-key"}, {"X-Internal-Api-Key": ""},
])
def test_authentication(client, headers):
    http, _ = client
    response = post(http, plan_request(), headers=headers)
    assert response.status_code == 401


def test_missing_secret_fails_closed(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(dependencies, "settings", SimpleNamespace(internal_api_key=""))
    assert post(http, plan_request()).status_code == 503


def test_request_id_must_be_uuid_header(client):
    http, _ = client
    assert post(http, plan_request(), headers={"X-Request-Id": "not-a-uuid"}).status_code == 422
    response = http.post(PATH, json={**plan_request(), "requestId": str(uuid4())},
                         headers={"X-Internal-Api-Key": "local-contract-test-key"})
    assert response.status_code == 422  # 본문 requestId는 필수 헤더를 대신하지 못한다.


def test_invalid_model_citation_fails_entire_answer(client):
    class BadModel(FakeAssistModel):
        async def answer(self, request):
            return AnswerResponse.model_validate(answered_response(referenceCodes=["OTHER_CODE"]))
    http, retriever = client
    app.dependency_overrides[get_service] = lambda: AssistService(BadModel(), retriever)
    assert post(http, answer_request()).status_code == 502
    assert not retriever.queries


def test_model_failure_and_validation_do_not_leak_raw_data(client, caplog):
    secret = "사용자원문_PRIVATE_MARKER"

    class BrokenModel(FakeAssistModel):
        async def assess(self, request):
            raise RuntimeError(secret)

    http, retriever = client
    app.dependency_overrides[get_service] = lambda: AssistService(BrokenModel(), retriever)
    with caplog.at_level(logging.INFO):
        response = post(http, plan_request(question=secret))
        invalid = post(http, plan_request(question=secret * 100))
    assert response.status_code == 503 and invalid.status_code == 422
    assert secret not in response.text + invalid.text + caplog.text


def test_upstream_status_is_logged_without_response_body(client, caplog):
    from openai import APIStatusError
    import httpx

    class BrokenModel(FakeAssistModel):
        async def assess(self, request):
            raise APIStatusError("PRIVATE_RESPONSE_MARKER", response=httpx.Response(429, request=httpx.Request("POST", "https://example.test")), body=None)

    http, retriever = client
    app.dependency_overrides[get_service] = lambda: AssistService(BrokenModel(), retriever)
    with caplog.at_level(logging.INFO):
        response = post(http, plan_request())
    assert response.status_code == 503
    assert "OPENAI_429" in caplog.text
    assert "PRIVATE_RESPONSE_MARKER" not in caplog.text + response.text


def test_whole_request_deadline_cancels_processing(client, monkeypatch):
    from rag import config

    class SlowModel(FakeAssistModel):
        async def assess(self, request):
            await asyncio.sleep(1)

    http, retriever = client
    app.dependency_overrides[get_service] = lambda: AssistService(SlowModel(), retriever)
    monkeypatch.setattr(config, "ASSIST_PLAN_TIMEOUT_SEC", 0.01)
    response = post(http, plan_request())
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "ASSIST_TIMEOUT"


def test_openapi_has_one_phase_discriminated_endpoint():
    spec = app.openapi()
    operation = spec["paths"][PATH]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["discriminator"]["propertyName"] == "phase"
    assert set(schema["discriminator"]["mapping"]) == {"PLAN", "ANSWER"}
    headers = {p["name"] for p in operation["parameters"] if p["in"] == "header"}
    assert {"X-Internal-Api-Key", "x-request-id"} <= {h if h == "X-Internal-Api-Key" else h.lower() for h in headers}

@pytest.mark.parametrize("phase", ["PLAN", "ANSWER"])
def test_assist_phase_uses_its_own_deadline(client, monkeypatch, phase):
    from rag import config

    class SlowService:
        async def run(self, request):
            await asyncio.sleep(0.05)
            if request.phase == "PLAN":
                return PlanResponse.model_validate(evidence_response())
            return AnswerResponse.model_validate(answered_response())

    http, _ = client
    app.dependency_overrides[get_service] = lambda: SlowService()
    monkeypatch.setattr(config, "ASSIST_PLAN_TIMEOUT_SEC", 0.01 if phase == "PLAN" else 1)
    monkeypatch.setattr(config, "ASSIST_ANSWER_TIMEOUT_SEC", 0.01 if phase == "ANSWER" else 1)
    body = plan_request() if phase == "PLAN" else answer_request()
    assert post(http, body).status_code == 504
    other = answer_request() if phase == "PLAN" else plan_request()
    assert post(http, other).status_code == 200


def test_model_question_text_is_ignored_and_fixed_for_both_paths():
    class UnsafeQuestionModel(SearchModel):
        async def assess(self, request):
            return Assessment.model_validate({"decision": "NEED_USER_INPUT", "questionIntent": "DEVICE_SAFETY",
                                              "factKey": "installedApp", "content": "전화 010-1234-5678"})

    class UnsafeEvidenceModel(SearchModel):
        async def evaluate(self, request, intent, references):
            return EvidenceAssessment.model_validate({"sufficient": False, "referenceCodes": [], "retryQuery": None,
                "clarification": {"factKey": "installedApp", "content": "https://example.com", "options": []}})

    for model in (UnsafeQuestionModel(), UnsafeEvidenceModel()):
        result = run(plan_request(), model=model)
        assert result.status == "NEED_USER_INPUT"
        assert result.clarification.content == "상대방이 알려준 앱을 설치한 적이 있나요?"
        assert result.clarification.options == ["YES", "NO", "UNKNOWN"]
