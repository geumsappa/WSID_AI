"""assist v1.1 선택 필드(history·fraudType)와 질문 주제 근거. 가짜 모델·가짜 검색으로 외부 호출 없음."""
import os

os.environ["RAG_FAKE"] = "1"

import asyncio  # noqa: E402
from copy import deepcopy

from pydantic import TypeAdapter, ValidationError
import pytest

from assist.context import drop_refund_notice, previous_user, topic_codes, topic_text
from assist.contracts import AnswerRequest, Assessment, AssistRequest, EvidenceAssessment, PlanRequest
from assist.graph import AssistService
from assist.model import FakeAssistModel, OpenAIAssistModel

FACTS = {"transferredMoney": "YES", "sharedInformation": "NO", "installedApp": "UNKNOWN", "contactContinues": "NO"}
REF = {"code": "GENERAL_GUIDE", "version": "1", "title": "일반 안내", "excerpt": "공식 절차를 확인합니다."}
CIVIL = {"code": "REF_EASYLAW_CIVIL_FILING_V1_P1", "version": "1", "title": "민사소송 제기", "excerpt": "소장을 냅니다."}
REFUND = {"code": "REF_LAW_REFUND_PROCEDURE_V1_P2", "version": "1", "title": "환급 적용 대상", "excerpt": "재화 거래는 제외."}


def plan(**changes):
    data = {"phase": "PLAN", "question": "둘 다 알려줘", "questionIntent": None, "facts": deepcopy(FACTS),
            "askedFactKeys": [], "remainingClarifications": 3}
    data.update(changes)
    return data


class Retriever:
    """검색 질의·유형을 기록하고, by_codes는 알고 있는 code만 돌려줘요."""

    def __init__(self, refs=None, known=None):
        self.refs = deepcopy(refs if refs is not None else [REF])
        self.known = {r["code"]: r for r in (known or [CIVIL, REFUND])}
        self.calls = []

    async def search(self, query, facts, fraud_type=None):
        self.calls.append({"query": query, "fraud_type": fraud_type})
        return deepcopy(self.refs)

    def by_codes(self, codes):
        return [deepcopy(self.known[c]) for c in codes if c in self.known]


class V1Retriever:
    """v1 시그니처(search(query, facts))만 있는 검색기: 선택 필드가 없으면 그대로 동작해야 해요."""

    def __init__(self):
        self.queries = []

    async def search(self, query, facts):
        self.queries.append(query)
        return [deepcopy(REF)]


class SearchModel(FakeAssistModel):
    def __init__(self):
        self.evaluated = []

    async def assess(self, request):
        return Assessment(decision="SEARCH", questionIntent=request.questionIntent or "GENERAL", factKey=None)

    async def evaluate(self, request, intent, references):
        self.evaluated.append([r["code"] for r in references])
        return EvidenceAssessment(sufficient=True, referenceCodes=[references[0]["code"]], retryQuery=None)


def run(body, model=None, retriever=None):
    service = AssistService(model or SearchModel(), retriever or Retriever())
    return asyncio.run(service.run(TypeAdapter(AssistRequest).validate_python(body)))


# ---- 계약 ----

def test_v1_request_without_new_fields_still_valid_and_unchanged_query():
    retriever = V1Retriever()
    result = run(plan(question="앱 관련해서 어떻게 해야 하나요?"), retriever=retriever)
    assert result.status == "EVIDENCE_READY"
    assert "이전 질문" not in retriever.queries[0] and "사기 유형" not in retriever.queries[0]


def test_history_limits_and_roles_validated():
    with pytest.raises(ValidationError):
        PlanRequest.model_validate(plan(history=[{"role": "USER", "content": "질문"}] * 11))
    with pytest.raises(ValidationError):
        PlanRequest.model_validate(plan(history=[{"role": "SYSTEM", "content": "질문"}]))
    with pytest.raises(ValidationError):
        PlanRequest.model_validate(plan(fraudType="UNKNOWN_TYPE"))


# ---- history ----

def test_short_question_uses_previous_user_turn_for_search_and_topic_refs():
    retriever, model = Retriever(), SearchModel()
    history = [{"role": "USER", "content": "민사소송 절차 알려줘"}, {"role": "AI", "content": "소장을 내요."}]
    result = run(plan(history=history), model=model, retriever=retriever)
    assert "이전 질문: 민사소송 절차 알려줘" in retriever.calls[0]["query"]
    assert model.evaluated[0][0] == CIVIL["code"]  # 질문 주제 근거가 맨 앞
    assert result.candidateReferences[0].code == CIVIL["code"]


def test_previous_user_skips_short_clarification_replies():
    req = PlanRequest.model_validate(plan(question=None, questionIntent="TRANSFER_RESPONSE", history=[
        {"role": "USER", "content": "앱도 지워야 해요?"},
        {"role": "AI", "content": "상대방이 알려준 앱을 설치한 적이 있나요?"},
        {"role": "USER", "content": "네, 설치했어요."},
    ]))
    assert previous_user(req) == "앱도 지워야 해요?"
    assert topic_text(req) == "앱도 지워야 해요?"


def test_followup_without_question_can_ask_fact_mentioned_in_original_question():
    class AskApp(FakeAssistModel):
        async def assess(self, request):
            return Assessment(decision="NEED_USER_INPUT", questionIntent="TRANSFER_RESPONSE", factKey="installedApp")

    body = plan(question=None, questionIntent="TRANSFER_RESPONSE", askedFactKeys=["transferredMoney"],
                remainingClarifications=2, history=[{"role": "USER", "content": "돈 보냈는데 앱도 지워야 해요?"}])
    assert run(body, model=AskApp()).clarification.factKey == "installedApp"
    # history가 없으면 v1처럼 의도에 연결된 사실만 물을 수 있어서 검색으로 넘어가요
    body.pop("history")
    assert run(body, model=AskApp()).status != "NEED_USER_INPUT"


# ---- fraudType ----

def test_fraud_type_passed_to_retriever_and_added_to_query():
    retriever = Retriever()
    run(plan(question="경찰에 신고했는데 다음엔 뭐 해요?", fraudType="SECONDHAND_TRADE"), retriever=retriever)
    assert retriever.calls[0]["fraud_type"] == "SECONDHAND_TRADE"
    assert "사기 유형: SECONDHAND_TRADE" in retriever.calls[0]["query"]


def test_secondhand_drops_refund_notice_unless_refund_asked():
    refs = [REFUND, REF]
    assert [r["code"] for r in drop_refund_notice(refs, "SECONDHAND_TRADE", "민사소송 절차")] == ["GENERAL_GUIDE"]
    assert len(drop_refund_notice(refs, "SECONDHAND_TRADE", "돈 돌려받을 수 있어요?")) == 2
    assert len(drop_refund_notice(refs, "INSTITUTION_IMPERSONATION", "민사소송 절차")) == 2
    model = SearchModel()
    run(plan(question="신고하고 나서 뭐 해요?", fraudType="SECONDHAND_TRADE"), model=model, retriever=Retriever(refs=[REFUND, REF]))
    assert REFUND["code"] not in model.evaluated[0]


def test_topic_codes():
    assert "REF_FSS_2024_28_ALERT_V1_P1" in topic_codes("수수료를 더 보내래요")
    assert "REF_EASYLAW_COMPLAINT_V1_P1" in topic_codes("고소는 어디에 접수해요?")
    assert topic_codes("앱 지워야 해요?") == []


# ---- 모델 입력 ----

def test_model_payload_keeps_v1_shape_without_new_fields_and_trims_history():
    v1 = PlanRequest.model_validate(plan())
    assert "history" not in OpenAIAssistModel._payload(v1) and "fraudType" not in OpenAIAssistModel._payload(v1)
    long = [{"role": "USER" if i % 2 == 0 else "AI", "content": f"{i} " + "가" * 1500} for i in range(10)]
    v11 = AnswerRequest.model_validate({"phase": "ANSWER", "question": None, "questionIntent": "GENERAL",
                                        "facts": FACTS, "verifiedReferences": [{k: v for k, v in REF.items() if k != "version"}],
                                        "history": long, "fraudType": "SECONDHAND_TRADE"})
    payload = OpenAIAssistModel._payload(v11)
    assert len(payload["history"]) == 6 and all(len(t["content"]) <= 600 for t in payload["history"])
    assert payload["history"][0]["content"].startswith("4 ")
    assert payload["fraudType"] == "SECONDHAND_TRADE"


# ---- 실제 검색기 (가짜 임베딩·임시 인덱스) ----

@pytest.fixture
def temp_index(tmp_path, monkeypatch):
    from rag import config, index
    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "index")
    index.build()


def test_real_retriever_by_codes_and_secondhand_filter(temp_index):
    from assist.retrieval import ReferenceRetriever
    from rag.retrieve import REFUND_ONLY_PREFIXES

    r = ReferenceRetriever()
    got = r.by_codes(["REF_EASYLAW_CIVIL_FILING_V1_P1", "NOT_A_CODE"])
    assert [g["code"] for g in got] == ["REF_EASYLAW_CIVIL_FILING_V1_P1"]
    assert got[0]["version"] and got[0]["excerpt"]
    refs = asyncio.run(r.search("환급 절차 지급정지", {k: "UNKNOWN" for k in FACTS}, fraud_type="SECONDHAND_TRADE"))
    assert refs and not any(ref["code"].startswith(REFUND_ONLY_PREFIXES) for ref in refs)


def test_referential_question_uses_previous_ai_answer_topics():
    req = PlanRequest.model_validate(plan(question="둘 다 알려줘", history=[
        {"role": "USER", "content": "경찰에 신고했고 뭐 해야해?"},
        {"role": "AI", "content": "형사재판 중 배상명령을 신청하거나 민사소송을 따로 해야 해요."},
    ]))
    codes = topic_codes(topic_text(req))
    assert "REF_EASYLAW_COMPENSATION_ORDER_V1_P1" in codes and "REF_EASYLAW_CIVIL_FILING_V1_P1" in codes
    retriever = Retriever()
    run(plan(question="둘 다 알려줘", history=[{"role": "USER", "content": "경찰에 신고했어요"},
                                          {"role": "AI", "content": "배상명령이나 민사소송이 있어요."}]), retriever=retriever)
    assert "이전 답변: 배상명령이나 민사소송이 있어요." in retriever.calls[0]["query"]
    # 앞 답변을 가리키지 않는 질문에는 도우미 답변을 붙이지 않아요
    req2 = PlanRequest.model_validate(plan(question="앱도 지워야 하나요?", history=[{"role": "AI", "content": "민사소송"}]))
    assert "민사소송" not in topic_text(req2)
