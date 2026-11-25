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
    ClarificationDraft, EvidenceAssessment, ModelClarification, PlanRequest, PlanResponse,
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
    monkeypatch.setattr(config, "ASSIST_LANGFUSE_ENABLED", False)
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
    # 질문 문장은 근거 검색 결과로 만든다. 한 번의 검색에 추가 확인 검색어가 함께 들어간다.
    assert len(retriever.queries) == 1 and "추가 확인:" in retriever.queries[0][0]


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


def test_langfuse_exports_public_reference_labels_without_conversation(client, monkeypatch, tmp_path):
    import langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from assist import observability

    exporter = InMemorySpanExporter()
    sdk = langfuse.Langfuse
    monkeypatch.setattr(langfuse, "Langfuse", lambda **kwargs: sdk(**kwargs, span_exporter=exporter))
    monkeypatch.setattr(observability.config, "ASSIST_LANGFUSE_ENABLED", True)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test-assist")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-assist")
    monkeypatch.setenv("LANGFUSE_SAMPLE_RATE", "1")
    catalog = tmp_path / "references.jsonl"
    catalog.write_text(json.dumps({"code": REFERENCE["code"], "title": "Public catalog title",
                                  "source_url": "https://example.org/public-guide"}) + "\n")
    monkeypatch.setattr(observability.config, "REFERENCES_PATH", catalog)
    observability._public_reference_catalog.cache_clear()
    observability._client.cache_clear()
    http, _ = client
    plan_id, answer_id = uuid4(), uuid4()
    question = "Test private question about the 앱"
    excerpt = "Test private excerpt for the app guide"
    broken_payload = "Test private upstream exception"
    try:
        plan = post(http, plan_request(question=question, remainingClarifications=0),
                    headers={"X-Request-Id": str(plan_id)})
        assert plan.json()["data"] == evidence_response()
        answer = post(http, answer_request(question=question,
                      verifiedReferences=[{"code": REFERENCE["code"], "title": REFERENCE["title"], "excerpt": excerpt}]),
                      headers={"X-Request-Id": str(answer_id)})
        assert answer.json()["data"]["status"] == "ANSWERED"

        class BrokenModel(FakeAssistModel):
            async def answer(self, request):
                raise ValueError(broken_payload)

        app.dependency_overrides[get_service] = lambda: AssistService(BrokenModel(), Retriever())
        assert post(http, answer_request()).status_code == 502
        observability._client().flush()
    finally:
        observability._client().shutdown()
        observability._client.cache_clear()
        observability._public_reference_catalog.cache_clear()

    spans = exporter.get_finished_spans()
    plan_root = next(span for span in spans if span.name == "assist.plan")
    assert plan_root.context.trace_id == int(sdk.create_trace_id(seed=str(plan_id)), 16)
    assert [span.name for span in spans if span.parent and span.parent.span_id == plan_root.context.span_id] == [
        "assist.assess", "assist.search", "assist.evaluate",
    ]
    attributes = {span.name: dict(span.attributes) for span in spans
                  if span.name != "assist.answer" or span.context.trace_id == int(sdk.create_trace_id(seed=str(answer_id)), 16)}
    assert REFERENCE["code"] in str(attributes["assist.search"])
    assert REFERENCE["code"] in str(attributes["assist.evaluate"])
    assert "candidate_references" in str(attributes["assist.plan"])
    assert "verified_reference_codes" in str(attributes["assist.answer"])
    assert "cited_reference_codes" in str(attributes["assist.answer"])
    for stage in ("assist.search", "assist.evaluate", "assist.answer"):
        assert "Public catalog title" in str(attributes[stage])
        assert "https://example.org/public-guide" in str(attributes[stage])
    exported = "\n".join(span.to_json() for span in spans)
    assert "INVALID_AI_RESPONSE" in exported
    for private_text in (question, excerpt, REFERENCE["title"], REFERENCE["excerpt"],
                         answer.json()["data"]["answer"], broken_payload):
        assert private_text not in exported


def test_out_of_scope_is_business_result():
    result = run(plan_request(question="내일 날씨 알려줘"))
    assert result.status == "UNABLE_TO_ANSWER" and result.reasonCode == "OUT_OF_SCOPE"
    assert set(result.model_dump()) == PLAN_KEYS


def test_out_of_scope_can_return_brief_guidance_without_search():
    class ChitchatModel(FakeAssistModel):
        async def assess(self, request):
            return Assessment(decision="OUT_OF_SCOPE", questionIntent=None, factKey=None,
                              guidance="펭귄은 새예요.")

    retriever = Retriever()
    result = run(plan_request(question="펭귄이 새야?", remainingClarifications=5), ChitchatModel(), retriever)
    assert result.reasonCode == "OUT_OF_SCOPE" and result.guidance == "펭귄은 새예요."
    assert not retriever.queries


def test_no_evidence_guidance_stays_uncited():
    class InsufficientModel(SearchModel):
        async def evaluate(self, request, intent, references):
            return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None,
                                      guidance="상대가 어떤 기관이라고 말했나요?")

    result = run(plan_request(), InsufficientModel(), Retriever([]))
    assert result.reasonCode == "NO_EVIDENCE" and result.guidance == "상대가 어떤 기관이라고 말했나요?"
    assert result.candidateReferences == []
    unsafe = AnswerResponse.model_validate(answered_response(status="UNABLE_TO_ANSWER", answer=None,
        referenceCodes=[], reasonCode="NO_EVIDENCE", guidance="https://example.com"))
    assert "guidance" not in unsafe.model_dump()


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


def test_research_is_bounded_to_initial_plus_one_and_can_recover():
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

    for recover, count, expected in [(False, 2, "UNABLE_TO_ANSWER"), (True, 2, "EVIDENCE_READY")]:
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
    {"remainingClarifications": 6}, {"askedFactKeys": ["installedApp", "installedApp"]},
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


def test_judge_model_text_is_ignored_and_question_comes_from_evidence_for_both_paths():
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
        content = result.clarification.content
        assert "010" not in content and "example" not in content
        assert "설치" in content  # 근거 문단(REFERENCE.excerpt)에 실제로 나온 표지어
        assert result.clarification.options == ["YES", "NO", "UNKNOWN"]


GOOD_REFS = [{"code": "APP_REMOVE_GUIDE", "version": "1", "title": "악성 앱 대응",
              "excerpt": "상대가 설치하라고 한 원격제어 앱을 설치했다면 즉시 삭제하고 휴대폰을 점검한다."},
             {"code": "LEAK_GUIDE", "version": "1", "title": "정보 유출 대응",
              "excerpt": "비밀번호나 인증번호를 알려 줬다면 즉시 변경하고 금융회사에 알린다."}]


def check(content, key="installedApp", codes=("APP_REMOVE_GUIDE",), question=None):
    from assist.clarify import validate_clarification
    return validate_clarification(key=key, content=content, cited_codes=list(codes),
                                  references=GOOD_REFS, question=question)


@pytest.mark.parametrize("content,key,codes,question", [
    ("상대가 설치하라고 한 원격제어 앱을 설치하셨나요?", "installedApp", ("APP_REMOVE_GUIDE",), None),
    ("상대에게 비밀번호나 인증번호를 알려 주셨나요?", "sharedInformation", ("LEAK_GUIDE",), None),
    ("판매자가 보낸 링크로 앱을 설치하셨나요?", "installedApp", ("APP_REMOVE_GUIDE",), "판매자가 보낸 링크를 눌렀어요"),
])
def test_grounded_clarification_passes(content, key, codes, question):
    assert check(content, key, codes, question).ok


@pytest.mark.parametrize("content,codes,reason", [
    (None, ("APP_REMOVE_GUIDE",), "EMPTY"),
    ("앱을 설치하셨나요?", ("UNKNOWN_CODE",), "UNGROUNDED_CITATION"),
    ("앱을 설치하셨나요?", (), "UNGROUNDED_CITATION"),
    ("앱을 설치하셨나요?", ("LEAK_GUIDE",), "EVIDENCE_OFF_TARGET"),
    ("지금도 상대와 연락하고 계신가요?", ("APP_REMOVE_GUIDE",), "OFF_TARGET"),
    ("상대가 보낸 QR로 앱을 설치하셨나요?", ("APP_REMOVE_GUIDE",), "UNSUPPORTED_SPECIFIC"),
    ("검찰 직원이 알려 준 앱을 설치하셨나요?", ("APP_REMOVE_GUIDE",), "UNSUPPORTED_SPECIFIC"),
    ("원격제어 앱을 설치하셨죠?", ("APP_REMOVE_GUIDE",), "LEADING_OR_VALUE_REQUEST"),
    ("앱을 설치하지 않으셨나요?", ("APP_REMOVE_GUIDE",), "LEADING_OR_VALUE_REQUEST"),
    ("설치한 앱 이름을 알려 주세요?", ("APP_REMOVE_GUIDE",), "LEADING_OR_VALUE_REQUEST"),
    ("어떤 번호로 받은 앱을 설치하셨나요?", ("APP_REMOVE_GUIDE",), "LEADING_OR_VALUE_REQUEST"),
    ("앱을 설치하셨나요? 삭제하실 건가요?", ("APP_REMOVE_GUIDE",), "NOT_SINGLE_POLITE_QUESTION"),
    ("앱을 설치했어요. 그 앱이 있나요?", ("APP_REMOVE_GUIDE",), "MULTIPLE_SENTENCES"),
    ("앱을 설치하셨나요", ("APP_REMOVE_GUIDE",), "NOT_SINGLE_POLITE_QUESTION"),
    ("www.bad.kr 앱을 설치하셨나요?", ("APP_REMOVE_GUIDE",), "FORBIDDEN"),
])
def test_ungrounded_or_unsafe_clarification_is_rejected(content, codes, reason):
    result = check(content, codes=codes)
    assert not result.ok and result.reason == reason


class DraftModel(FakeAssistModel):
    def __init__(self, content, codes):
        self.draft = ClarificationDraft(content=content, referenceCodes=list(codes))
        self.evaluated_with = []

    async def clarify(self, request, key, references):
        return self.draft

    async def evaluate(self, request, intent, references):
        self.evaluated_with.append(request.remainingClarifications)
        return await super().evaluate(request, intent, references)


def test_model_written_grounded_question_is_returned_verbatim():
    model = DraftModel("상대가 설치하라고 한 원격제어 앱을 설치하셨나요?", ["APP_REMOVE_GUIDE"])
    result = run(plan_request(), model=model, retriever=Retriever(GOOD_REFS))
    assert result.status == "NEED_USER_INPUT"
    assert result.clarification.content == "상대가 설치하라고 한 원격제어 앱을 설치하셨나요?"


@pytest.mark.parametrize("content,codes", [
    ("검찰이 알려 준 앱을 설치하셨죠?", ["APP_REMOVE_GUIDE"]),   # 근거 없는 구체성 + 유도
    ("앱을 설치하셨나요?", ["NOT_RETRIEVED"]),                  # 검색되지 않은 근거 인용
    (None, []),                                                 # 근거로 질문을 만들 수 없음
])
def test_rejected_question_is_not_asked_and_falls_back_to_conditional_answer(content, codes):
    model, retriever = DraftModel(content, codes), Retriever(GOOD_REFS)
    result = run(plan_request(), model=model, retriever=retriever)
    assert result.status == "EVIDENCE_READY" and result.clarification is None
    assert model.evaluated_with == [0]  # 다시 묻지 않는 조건부 평가
    assert len(retriever.queries) == 1  # 추가 검색 없이 같은 근거 재사용


def test_evidence_without_fact_anchor_does_not_produce_question():
    refs = [{"code": "GENERAL_GUIDE", "version": "1", "title": "일반 안내", "excerpt": "의심되면 대화를 멈추고 확인한다."}]
    result = run(plan_request(), retriever=Retriever(refs))
    assert result.status == "EVIDENCE_READY" and result.clarification is None



def test_eligible_clarifications_respect_intent_unknown_and_history():
    from assist.model import allowed_fact_keys
    req = PlanRequest.model_validate(plan_request(question="신분증 사진을 보냈어요", facts={
        "transferredMoney": "UNKNOWN", "sharedInformation": "UNKNOWN",
        "installedApp": "UNKNOWN", "contactContinues": "UNKNOWN"}))
    assert allowed_fact_keys(req, "INFO_LEAK_RESPONSE") == ["sharedInformation"]
    req = req.model_copy(update={"askedFactKeys": ["sharedInformation"]})
    assert allowed_fact_keys(req, "INFO_LEAK_RESPONSE") == []
    req = req.model_copy(update={"remainingClarifications": 0})
    assert allowed_fact_keys(req, "INFO_LEAK_RESPONSE") == []


def test_evaluate_receives_only_five_shortened_references():
    refs = [{**REFERENCE, "code": f"REF_{i}", "excerpt": "가" * 1000} for i in range(10)]
    class CheckModel(SearchModel):
        async def evaluate(self, request, intent, references):
            assert len(references) == 5
            assert all(len(ref["excerpt"]) == 600 for ref in references)
            return EvidenceAssessment(sufficient=True, referenceCodes=[references[0]["code"]], retryQuery=None)
    assert run(plan_request(), model=CheckModel(), retriever=Retriever(refs)).status == "EVIDENCE_READY"


def test_no_research_when_time_budget_is_insufficient(monkeypatch):
    from rag import config
    monkeypatch.setattr(config, "ASSIST_RETRY_MIN_SECONDS", 20)
    class RetryModel(SearchModel):
        async def evaluate(self, request, intent, references):
            return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery="다른 검색")
    retriever = Retriever()
    result = run(plan_request(), model=RetryModel(), retriever=retriever)
    assert result.status == "UNABLE_TO_ANSWER" and result.reasonCode == "NO_EVIDENCE"
    assert len(retriever.queries) == 1


def test_stage_logs_exclude_private_input_and_evidence(caplog):
    secret = "PRIVATE_QUERY_MARKER"
    refs = [{**REFERENCE, "excerpt": "PRIVATE_EVIDENCE_MARKER"}]
    with caplog.at_level(logging.INFO, logger="uvicorn.error.assist"):
        result = run(plan_request(question=secret), model=SearchModel(), retriever=Retriever(refs))
    assert result.status == "EVIDENCE_READY"
    assert all(f"stage={name}" in caplog.text for name in ("assess", "search", "evaluate"))
    assert secret not in caplog.text and "PRIVATE_EVIDENCE_MARKER" not in caplog.text


def test_openai_evaluation_receives_explicit_allowed_keys(monkeypatch):
    from assist.model import OpenAIAssistModel
    payloads = []
    async def generate(self, system, payload, schema):
        payloads.append(payload)
        return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None)
    monkeypatch.setattr(OpenAIAssistModel, "_generate", generate)
    request = PlanRequest.model_validate(plan_request(askedFactKeys=["installedApp"]))
    asyncio.run(OpenAIAssistModel().evaluate(request, "DEVICE_SAFETY", [REFERENCE]))
    assert payloads[0]["allowedFactKeys"] == []
    assert payloads[0]["allowClarification"] is False


def test_openai_assist_model_override_and_low_option(monkeypatch):
    from app import config as app_config
    from rag import config, openai_client
    from assist.model import OpenAIAssistModel
    calls = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(create=self.complete))
        async def complete(self, **kwargs):
            calls.append(kwargs)
            text = '{"decision":"SEARCH","questionIntent":"GENERAL","factKey":null}'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    monkeypatch.setattr(app_config, "settings", SimpleNamespace(llm_provider="openai", llm_model="default-model"))
    monkeypatch.setattr(openai_client, "async_client", Client)
    monkeypatch.setattr(config, "ASSIST_LLM_MODEL", "")
    monkeypatch.setattr(config, "ASSIST_REASONING_EFFORT", "")
    asyncio.run(OpenAIAssistModel()._generate("system", {}, Assessment))
    assert calls[0]["model"] == "default-model" and "reasoning_effort" not in calls[0]
    monkeypatch.setattr(config, "ASSIST_LLM_MODEL", "assist-model")
    monkeypatch.setattr(config, "ASSIST_REASONING_EFFORT", "low")
    result = asyncio.run(OpenAIAssistModel()._generate("system", {}, Assessment))
    assert result.decision == "SEARCH" and calls[1]["model"] == "assist-model"
    assert calls[1]["reasoning_effort"] == "low"


# ---- v1.2: 네 사실 밖의 조건 질문 -------------------------------------------------------------
REFUND_REF = {
    "code": "REF_LAW_REFUND_PROCEDURE_V1_P2", "version": "1",
    "title": "전기통신금융사기 피해금 환급 절차와 적용 대상 (통신사기피해환급법) - 적용 대상과 제외",
    "excerpt": ("적용 대상: 전화·문자·메신저·PC 등 전기통신으로 속여 송금·이체하게 하거나 개인정보를 알아내 이체한 경우. "
                "제외: 물건·서비스를 주겠다며 속인 사기(중고거래, 온라인 쇼핑, 게임 아이템, 사이버 경매), "
                "로맨스스캠·조건만남 같은 비정상 거래, 해킹. 제외 유형은 경찰 신고와 민사 절차로 대응한다."),
}
REFUND_QUOTE = "제외: 물건·서비스를 주겠다며 속인 사기(중고거래, 온라인 쇼핑, 게임 아이템, 사이버 경매)"
REFUND_QUESTION = "물건이나 서비스를 받기로 하고 보내신 돈인가요?"
MONEY_QUESTION = "돈을 보냈는데 돌려받을 수 있나요?"


def condition_request(**changes):
    facts = {**FACTS, "transferredMoney": "YES"}
    return plan_request(**{"question": MONEY_QUESTION, "facts": facts, "confirmedConditions": [], **changes})


def confirmed(answer="YES", quote=REFUND_QUOTE, code=REFUND_REF["code"]):
    from assist.contracts import condition_id
    return {"conditionId": condition_id(code, quote), "referenceCode": code, "quote": quote, "answer": answer}


class ConditionModel(SearchModel):
    """근거 문단의 제외 조건을 인용해 네 사실 밖의 조건을 묻는 모델."""
    def __init__(self, quote=REFUND_QUOTE, content=REFUND_QUESTION, code=REFUND_REF["code"]):
        self.proposal = {"referenceCode": code, "quote": quote, "content": content}
        self.seen, self.feedbacks, self.clarify_calls = [], [], 0

    async def assess(self, request):
        return Assessment(decision="NEED_USER_INPUT", questionIntent="TRANSFER_RESPONSE", factKey=None)

    async def evaluate(self, request, intent, references, feedback=None):
        self.seen.append(request)
        self.feedbacks.append(feedback)
        if request.remainingClarifications > 0 and not request.confirmedConditions:
            return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None,
                                      clarification=ModelClarification(**self.proposal))
        return EvidenceAssessment(sufficient=True, referenceCodes=[references[0]["code"]], retryQuery=None)

    async def clarify(self, request, target, references):
        self.clarify_calls += 1
        return await super().clarify(request, target, references)


def test_condition_beyond_four_facts_is_asked_with_quoted_evidence():
    model = ConditionModel()
    result = run(condition_request(), model=model, retriever=Retriever([REFUND_REF]))
    assert result.status == "NEED_USER_INPUT"
    c = result.clarification
    assert c.kind == "CONDITION" and c.factKey is None
    assert c.content == REFUND_QUESTION and not hasattr(c.condition, "statement")
    assert c.condition.referenceCode == REFUND_REF["code"] and c.condition.quote in REFUND_REF["excerpt"]
    assert c.options == ["YES", "NO", "UNKNOWN"]
    assert model.clarify_calls == 0  # 평가 단계가 쓴 질문이 검증을 통과하면 추가 호출 없음


def test_condition_clarification_http_shape_and_fact_shape_unchanged(client):
    http, _ = client
    fact = post(http, plan_request()).json()["data"]["clarification"]
    assert set(fact) == {"factKey", "content", "options"}  # v1 응답 모양 그대로

    service = AssistService(ConditionModel(), Retriever([REFUND_REF]))
    app.dependency_overrides[get_service] = lambda: service
    data = post(http, condition_request()).json()["data"]["clarification"]
    assert data["kind"] == "CONDITION" and data["factKey"] is None
    assert set(data["condition"]) == {"conditionId", "referenceCode", "quote"}  # AI 질문 문장은 조건에 없음


def test_conditions_not_asked_when_spring_does_not_opt_in():
    request = condition_request()
    request.pop("confirmedConditions")
    result = run(request, model=ConditionModel(), retriever=Retriever([REFUND_REF]))
    assert result.status != "NEED_USER_INPUT"


@pytest.mark.parametrize("quote", [
    "제외: 중고거래는 무조건 환급되지 않는다",       # 문단에 없는 인용(지어낸 조건)
    "물건을 주겠다며",                              # 너무 짧은 인용
])
def test_fabricated_quote_is_never_asked(quote):
    model = ConditionModel(quote=quote)
    result = run(condition_request(), model=model, retriever=Retriever([REFUND_REF]))
    assert result.status != "NEED_USER_INPUT"
    assert sum(1 for f in model.feedbacks if f) == 1  # 이유를 알려 주고 한 번만 다시 받음


@pytest.mark.parametrize("content", [
    "검찰 직원이 시켜서 보내신 돈인가요?",         # 근거에 없는 구체 항목
    "휴대폰 요금이 연체되셨나요?",                 # 인용 조건과 무관
    "물건을 받기로 하고 보내셨죠?",                # 유도
])
def test_bad_condition_question_is_rewritten_or_dropped(content):
    model = ConditionModel(content=content)
    result = run(condition_request(), model=model, retriever=Retriever([REFUND_REF]))
    assert model.clarify_calls == 1  # 평가 단계 질문이 거부되면 별도 작성 단계로
    if result.status == "NEED_USER_INPUT":
        assert result.clarification.content != content


def test_confirmed_condition_is_not_asked_again_and_reaches_search_and_evaluation():
    model, retriever = ConditionModel(), Retriever([REFUND_REF])
    result = run(condition_request(confirmedConditions=[confirmed("YES")]), model=model, retriever=retriever)
    assert result.status == "EVIDENCE_READY"
    assert f"{REFUND_QUOTE}=YES" in retriever.queries[0][0]
    assert model.seen[0].confirmedConditions[0].answer == "YES"


def test_same_quote_already_confirmed_is_not_asked_again():
    class AsksAgain(ConditionModel):
        async def evaluate(self, request, intent, references, feedback=None):
            self.seen.append(request)
            self.feedbacks.append(feedback)
            if request.remainingClarifications > 0:
                return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None,
                                          clarification=ModelClarification(**self.proposal))
            return EvidenceAssessment(sufficient=True, referenceCodes=[references[0]["code"]], retryQuery=None)

    reworded = confirmed("NO")
    result = run(condition_request(confirmedConditions=[reworded]), model=AsksAgain(),
                 retriever=Retriever([REFUND_REF]))
    assert result.status != "NEED_USER_INPUT"


def test_rejected_quote_gets_one_retry_with_reason_then_succeeds():
    class FixesAfterFeedback(ConditionModel):
        async def evaluate(self, request, intent, references, feedback=None):
            self.proposal["quote"] = REFUND_QUOTE if feedback else "제외: 중고거래는 무조건 환급 불가"
            return await super().evaluate(request, intent, references, feedback)

    model = FixesAfterFeedback()
    result = run(condition_request(), model=model, retriever=Retriever([REFUND_REF]))
    assert result.status == "NEED_USER_INPUT" and result.clarification.kind == "CONDITION"
    assert model.feedbacks[0] is None and "글자 그대로" in model.feedbacks[1]


def test_tampered_confirmed_condition_is_rejected():
    tampered = {**confirmed("YES"), "quote": "기관을 사칭해 돈을 보내게 한 경우"}
    with pytest.raises(ValidationError):
        TypeAdapter(AssistRequest).validate_python(condition_request(confirmedConditions=[tampered]))


def test_answer_request_carries_confirmed_conditions():
    request = TypeAdapter(AssistRequest).validate_python(
        answer_request(confirmedConditions=[confirmed("YES")]))
    dumped = request.model_dump()["confirmedConditions"][0]
    assert set(dumped) == {"conditionId", "referenceCode", "quote", "answer"} and dumped["quote"] == REFUND_QUOTE


def test_condition_clarification_rejected_for_v1_request():
    from assist.contracts import Condition, condition_id
    cond = Condition(conditionId=condition_id(REFUND_REF["code"], REFUND_QUOTE),
                     referenceCode=REFUND_REF["code"], quote=REFUND_QUOTE)
    response = PlanResponse(status="NEED_USER_INPUT", questionIntent="TRANSFER_RESPONSE",
                            clarification=Clarification(kind="CONDITION", factKey=None, condition=cond,
                                                        content=REFUND_QUESTION, options=["YES", "NO", "UNKNOWN"]),
                            candidateReferences=[], reasonCode=None)
    v1 = PlanRequest.model_validate({**condition_request(), "confirmedConditions": None})
    with pytest.raises(ValueError):
        response.validate_for(v1)
    response.validate_for(PlanRequest.model_validate(condition_request()))


def test_v12_never_asks_four_facts_and_goes_straight_to_search():
    """카드 섹션에서 네 사실이 확정돼 오므로 v1.2에서는 다시 묻지 않고 바로 근거 검색으로 간다."""
    class AsksFact(SearchModel):
        async def assess(self, request):
            return Assessment(decision="NEED_USER_INPUT", questionIntent="DEVICE_SAFETY", factKey="installedApp")

        async def evaluate(self, request, intent, references, feedback=None):
            return EvidenceAssessment(sufficient=False, referenceCodes=[], retryQuery=None,
                                      clarification=ModelClarification(factKey="installedApp"))

    retriever = Retriever()
    result = run(plan_request(confirmedConditions=[]), model=AsksFact(), retriever=retriever)
    assert result.clarification is None or result.clarification.kind == "CONDITION"
    assert len(retriever.queries) >= 1


def test_confirmed_facts_are_phrased_into_search_query():
    retriever = Retriever()
    run(plan_request(facts={**FACTS, "transferredMoney": "YES"}), model=SearchModel(), retriever=retriever)
    assert "확정된 피해 상황: 사기범에게 돈을 송금했다" in retriever.queries[0][0]


def test_negated_condition_question_is_rejected():
    from assist.clarify import ConditionSeed, validate_clarification
    seed = ConditionSeed(referenceCode=REFUND_REF["code"], quote=REFUND_QUOTE)
    for text in ("물건이나 서비스를 받기로 하고 보낸 돈이 아닌가요?", "물건이나 서비스 거래가 아니었나요?"):
        check = validate_clarification(key=None, content=text, cited_codes=[REFUND_REF["code"]],
                                       references=[REFUND_REF], question=MONEY_QUESTION, condition=seed)
        assert not check.ok and check.reason == "LEADING_OR_VALUE_REQUEST"


def _ref(code, excerpt="근거 문단입니다. 피해 대응 절차를 안내합니다."):
    return {**REFERENCE, "code": code, "title": code, "excerpt": excerpt}


class BranchRetriever(Retriever):
    """본 검색과 갈림길 검색에 서로 다른 결과를 준다."""
    def __init__(self, main, branch):
        super().__init__(main)
        self.branch = deepcopy(branch)

    async def search(self, query, facts):
        self.queries.append((query, deepcopy(facts)))
        return deepcopy(self.branch if query.endswith("제외 대상·요건") else self.references)


def test_v12_adds_branch_search_into_evaluated_top_five():
    from assist.graph import MAX_EVALUATION_REFERENCES

    class Seen(ConditionModel):
        async def evaluate(self, request, intent, references, feedback=None):
            self.evaluated = [r["code"] for r in references]
            return await super().evaluate(request, intent, references, feedback)

    main = [_ref(f"REF_MAIN_{i}") for i in range(8)]
    retriever = BranchRetriever(main, [REFUND_REF, _ref("REF_BRANCH_2")])
    model = Seen()
    run(condition_request(), model=model, retriever=retriever)
    assert len(retriever.queries) == 2 and retriever.queries[1][0].endswith("제외 대상·요건")
    top = model.evaluated[:MAX_EVALUATION_REFERENCES]
    assert REFUND_REF["code"] in top and "REF_BRANCH_2" in top  # 갈림길 문단이 판정 상위 5개 안에


def test_v1_request_keeps_single_search():
    retriever = BranchRetriever([_ref("REF_MAIN_0")], [REFUND_REF])
    request = condition_request()
    request.pop("confirmedConditions")
    run(request, model=SearchModel(), retriever=retriever)
    assert len(retriever.queries) == 1  # v1(주원님 흐름·사진 안내)은 검색 방식이 바뀌지 않음


def test_interleave_branch_places_and_dedupes():
    from assist.graph import interleave_branch
    refs = [_ref(c) for c in ("A", "B", "C", "D")]
    out = [r["code"] for r in interleave_branch(refs, [_ref("X"), _ref("C")])]
    assert out == ["A", "X", "B", "C", "D"]


def test_confirmed_condition_reference_is_kept_in_evidence():
    class PicksOther(ConditionModel):
        async def evaluate(self, request, intent, references, feedback=None):
            return EvidenceAssessment(sufficient=True, referenceCodes=["REF_MAIN_0"], retryQuery=None)

    retriever = BranchRetriever([_ref("REF_MAIN_0")], [REFUND_REF])
    result = run(condition_request(confirmedConditions=[confirmed("NO")]), model=PicksOther(), retriever=retriever)
    assert result.status == "EVIDENCE_READY"
    assert [r.code for r in result.candidateReferences] == ["REF_MAIN_0", REFUND_REF["code"]]
