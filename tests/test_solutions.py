"""피해 대응 API 테스트 (키 없이 가짜 모드).  실행: python -m pytest tests/test_solutions.py -q"""
import os
import sys
from pathlib import Path

os.environ["RAG_FAKE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from rag import index  # noqa: E402
from app.schemas.solution import SolutionAnswerResult, SolutionExtractResult, SolutionFacts  # noqa: E402
from app.services.solution import run_answer, run_explain, run_extract  # noqa: E402

FACTS = {"transferredMoney": "YES", "contactContinues": "NO", "sharedInformation": "NO", "installedApp": "NO"}
UNKNOWN = {k: "UNKNOWN" for k in FACTS}


@pytest.fixture(scope="module", autouse=True)
def fake_index(tmp_path_factory):
    # 가짜 임베딩 인덱스는 임시 폴더에 만들어요. data/index(실제 Gemini 인덱스)를 덮어쓰지 않게요.
    # 다른 테스트가 rag.config 를 먼저 불러왔으면 .env 의 RAG_FAKE=0 이 이미 적용돼 있어요.
    # 그래서 여기서 가짜 모드를 직접 켜요 (실제 Gemini 를 부르면 무료 한도 429 에러가 나요).
    from rag import config
    original = (config.INDEX_DIR, config.FAKE)
    config.INDEX_DIR = tmp_path_factory.mktemp("index")
    config.FAKE = True
    index.build()
    yield
    config.INDEX_DIR, config.FAKE = original


def test_extract_secondhand():
    r = run_extract("당근에서 중고거래로 50만원 송금했는데 판매자가 연락두절됐어요")
    SolutionExtractResult.model_validate(r)
    assert r["facts"]["transferredMoney"] == "YES"
    assert r["fraudType"] == "SECONDHAND_TRADE"
    assert set(r["uncertainFields"]) == {k for k, v in r["facts"].items() if v == "UNKNOWN"}


def test_extract_masks_sensitive_before_ai():
    r = run_extract("주민번호 990101-1234567 알려줬어요")
    SolutionExtractResult.model_validate(r)


def test_explain_requires_valid_facts():
    with pytest.raises(ValueError):
        run_explain({"transferredMoney": "MAYBE"}, [])


def test_answer_with_facts():
    r = run_answer(SolutionFacts.model_validate(FACTS), "경찰에 신고했는데 이제 뭘 하면 돼요?", "SECONDHAND_TRADE")
    SolutionAnswerResult.model_validate(r)
    assert r["status"] == "ANSWERED" and r["answer"]
    assert set(r["referenceCodes"]) <= {x["code"] for x in r["references"]}


def test_secondhand_never_gets_refund_procedure_refs():
    # 환급 절차(P1)는 빼고, "중고거래는 환급 대상 제외" 문단(P2)은 남아도 돼요
    r = run_answer(FACTS, "돈 돌려받을 수 있어요?", "SECONDHAND_TRADE")
    assert r["status"] == "ANSWERED"
    assert not any(x["code"] == "REF_LAW_REFUND_PROCEDURE_V1_P1" for x in r["references"])


def test_answer_asks_back_when_needed_fact_unknown():
    r = run_answer(UNKNOWN, "돈 돌려받을 수 있어요?")
    assert r["status"] == "NEED_USER_INPUT" and r["clarifyFactKey"] == "transferredMoney" and r["clarifyQuestion"]


def test_answer_without_confirmed_facts_searches_by_question():
    r = run_answer(UNKNOWN, "신고는 어디에 해요?")
    assert r["status"] == "ANSWERED" and r["references"]


def test_out_of_scope_is_unable():
    assert run_answer(FACTS, "요즘 코인 추천해 주세요")["status"] == "UNABLE_TO_ANSWER"


def test_spring_given_references_only():
    refs = [{"code": "REF_A_V1_P1", "title": "지급정지", "excerpt": "금융회사에 지급정지를 요청합니다."}]
    r = run_answer(FACTS, "뭐부터 해요?", None, refs)
    assert r["references"] == refs and r["referenceCodes"] == ["REF_A_V1_P1"]


def test_router_contract():
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/internal/ai/solutions/extract", json={"description": "검찰이라고 해서 300만원 이체했어요"})
    assert r.status_code == 200 and set(r.json()) >= {"facts", "uncertainFields"}
    r = c.post("/internal/ai/solutions/answer", json={"facts": FACTS, "question": "이제 뭐 해요?", "fraudType": "SECONDHAND_TRADE"})
    assert r.status_code == 200 and r.json()["status"] == "ANSWERED"
    assert c.post("/internal/ai/solutions/answer", json={"facts": FACTS, "question": ""}).status_code == 422
    r = c.post("/internal/ai/solutions/explain", json={"facts": FACTS, "verifiedReferences": [
        {"code": "REF_A_V1_P1", "title": "t", "excerpt": "지급정지를 먼저 요청합니다."}]})
    assert r.status_code == 200 and r.json()["referenceCodes"] == ["REF_A_V1_P1"]
