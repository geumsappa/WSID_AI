"""사기 유형에 맞지 않는 절차를 권하는 ANSWER를 코드로 막는지 확인해요."""
import asyncio

import pytest
from pydantic import TypeAdapter

from assist.contracts import AnswerResponse, AssistRequest
from assist.graph import AssistService
from assist.model import FakeAssistModel
from assist.procedure_guard import CAVEAT_TEXT, HONEST_TEXT, check, repair
from rag import config

from tests.test_assist import Retriever, answer_request

SECONDHAND = "SECONDHAND_TRADE"
INVESTMENT = "INVESTMENT_FRAUD"


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "ASSIST_LANGFUSE_ENABLED", False)


def kinds(text, fraud_type):
    return {v.kind for v in check(text, fraud_type)}


@pytest.mark.parametrize("text", [
    "돈을 보낸 은행에 바로 지급정지를 요청하세요.",
    "1. 경찰에 신고해 지급정지를 요청하고 거래 내역은 지우지 마세요.",
    "피해구제 신청서를 3영업일 안에 내세요.",
    "잔액이 있으면 피해금 환급을 받을 수 있어요.",
])
def test_secondhand_blocks_refund_procedures_as_actions(text):
    assert kinds(text, SECONDHAND) == {"SECONDHAND_REFUND"}


@pytest.mark.parametrize("text", [
    "중고거래 사기는 보이스피싱과 달리 은행에 지급정지를 요청해도 받아들여지지 않을 수 있어요.",
    "물건 거래를 가장한 사기는 피해구제 신청 대상이 아니에요.",
    "중고거래는 환급 절차를 쓸 수 없어요.",
    "경찰청 사이버범죄 신고시스템에 신고하고, 지급명령이나 소액소송을 검토하세요.",
])
def test_secondhand_allows_negated_mentions_and_civil_steps(text):
    assert kinds(text, SECONDHAND) == set()


@pytest.mark.parametrize("fraud_type", [None, "MESSENGER_PHISHING", "SMISHING", "LOAN_FRAUD", "INSTITUTION_IMPERSONATION"])
def test_telecom_fraud_types_may_recommend_payment_stop(fraud_type):
    assert kinds("돈을 보낸 금융회사에 바로 지급정지를 요청하세요.", fraud_type) == set()


def test_investment_needs_both_caveats_when_recommending_payment_stop():
    bare = "금융회사와 경찰에 바로 지급정지를 요청하세요."
    assert kinds(bare, INVESTMENT) == {"INVESTMENT_CAVEAT", "INVESTMENT_HONEST"}
    assert kinds(bare + " " + CAVEAT_TEXT, INVESTMENT) == {"INVESTMENT_HONEST"}
    assert kinds(bare + " " + CAVEAT_TEXT + " " + HONEST_TEXT, INVESTMENT) == set()
    assert kinds("대화방과 송금 내역을 보관하고 경찰에 사기로 신고하세요.", INVESTMENT) == set()


@pytest.mark.parametrize("fraud_type", [None, SECONDHAND, INVESTMENT, "SMISHING"])
def test_no_type_may_promise_recovery(fraud_type):
    assert "PROMISE" in kinds("신고하면 보낸 돈을 전액 돌려받을 수 있어요.", fraud_type)
    assert "PROMISE" not in kinds("잔액 범위에서만 돌려받을 수 있어 전액이 보장되지 않아요.", fraud_type)


def answered(text):
    return AnswerResponse(status="ANSWERED", answer=text, referenceCodes=["APP_REMOVE_GUIDE"], reasonCode=None)


def test_repair_drops_bad_items_and_renumbers():
    text = "더 보내지 말고 증거를 지키세요.\n1. 은행에 지급정지를 요청하세요.\n2. 대화와 송금 내역을 캡처해 두세요.\n3. 경찰에 사기로 신고하세요."
    fixed = repair(answered(text), SECONDHAND)
    assert fixed.answer == "더 보내지 말고 증거를 지키세요.\n1. 대화와 송금 내역을 캡처해 두세요.\n2. 경찰에 사기로 신고하세요."


def test_repair_adds_investment_caveats():
    fixed = repair(answered("금융회사와 경찰에 바로 지급정지를 요청하세요."), INVESTMENT)
    assert fixed.answer.endswith(CAVEAT_TEXT + " " + HONEST_TEXT) and not check(fixed.answer, INVESTMENT)


def test_repair_without_anything_left_is_no_evidence():
    fixed = repair(answered("은행에 지급정지를 요청하세요."), SECONDHAND)
    assert fixed.status == "UNABLE_TO_ANSWER" and fixed.reasonCode == "NO_EVIDENCE" and fixed.answer is None


class ScriptedModel(FakeAssistModel):
    def __init__(self, *answers):
        self.answers = list(answers)
        self.feedbacks = []

    async def answer(self, request, feedback=None):
        self.feedbacks.append(feedback)
        return answered(self.answers.pop(0))


def run_answer(model, **changes):
    request = TypeAdapter(AssistRequest).validate_python(answer_request(**changes))
    return asyncio.run(AssistService(model, Retriever()).run(request))


def test_violation_retries_once_with_reason():
    model = ScriptedModel("은행에 지급정지를 요청하세요.", "경찰에 사기로 신고하고 소액소송을 검토하세요.")
    result = run_answer(model, fraudType=SECONDHAND)
    assert result.answer == "경찰에 사기로 신고하고 소액소송을 검토하세요."
    assert model.feedbacks[0] is None and "SECONDHAND_TRADE" in model.feedbacks[1]


def test_retry_still_wrong_is_repaired():
    model = ScriptedModel("은행에 지급정지를 요청하세요.", "경찰에 신고하세요.\n1. 은행에 지급정지를 요청하세요.")
    assert run_answer(model, fraudType=SECONDHAND).answer == "경찰에 신고하세요."


def test_no_time_left_repairs_without_retry(monkeypatch):
    monkeypatch.setattr(config, "ASSIST_RETRY_MIN_SECONDS", 10_000)
    model = ScriptedModel("경찰에 신고하세요. 은행에 지급정지를 요청하세요.")
    assert run_answer(model, fraudType=SECONDHAND).answer == "경찰에 신고하세요."
    assert model.feedbacks == [None]


def test_clean_answer_is_not_retried():
    model = ScriptedModel("돈을 보낸 금융회사에 바로 지급정지를 요청하세요.")
    assert run_answer(model, fraudType="SMISHING").status == "ANSWERED"
    assert model.feedbacks == [None]
