"""키 없이 도는 테스트:  RAG_FAKE=1 python -m pytest -q   (pytest 없으면  RAG_FAKE=1 python tests/test_rag.py)"""
import os
import sys
from pathlib import Path

os.environ["RAG_FAKE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import index  # noqa: E402
from rag.explain import ExplainError, explain, validate_result  # noqa: E402
from rag.guard import has_forbidden, strip_contact_sentences  # noqa: E402
from rag.retrieve import find_references  # noqa: E402

REF = [{"code": "REF_A_V1_P1", "title": "t", "excerpt": "지급정지를 먼저 요청합니다."}]


def test_guard_matches_spring_rules():
    assert has_forbidden("자세한 내용은 www.fss.or.kr 에서")
    assert has_forbidden("112에 신고하세요")
    assert has_forbidden("1566-1188 로 전화")
    assert has_forbidden("[여기](주소)")
    assert not has_forbidden("10 000원을 보냈어요")
    assert not has_forbidden("[주의] (중요) 3단계로 진행")
    assert not has_forbidden("112개")


def test_strip_contact_sentences_keeps_meaning():
    text, n = strip_contact_sentences("먼저 지급정지를 요청한다. 연락처는 1394 이다. 그다음 피해구제를 신청한다.")
    assert n == 1 and "1394" not in text and "피해구제" in text


def test_validate_result_rules():
    validate_result(REF, {"explanation": "먼저 지급정지가 필요해요.", "referenceCodes": ["REF_A_V1_P1"]})
    for bad in (
        {"explanation": "ok", "referenceCodes": ["REF_OTHER_V1_P1"]},   # 요청에 없는 코드
        {"explanation": "ok", "referenceCodes": []},                    # 근거 있는데 인용 없음
        {"explanation": "112로 전화하세요", "referenceCodes": ["REF_A_V1_P1"]},  # 연락처
        {"explanation": "", "referenceCodes": ["REF_A_V1_P1"]},
    ):
        try:
            validate_result(REF, bad)
        except ExplainError:
            continue
        raise AssertionError(bad)


def test_pipeline_fake():
    index.build()
    refs = find_references({"transferredMoney": "YES", "installedApp": "UNKNOWN"}, k=4)
    assert 1 <= len(refs) <= 4
    assert all(not has_forbidden(r["excerpt"], strict=True) for r in refs)
    res = explain({"transferredMoney": "YES", "installedApp": "UNKNOWN"}, refs)
    assert set(res["referenceCodes"]) <= {r["code"] for r in refs}
    assert find_references({"transferredMoney": "UNKNOWN"}) == []  # 확정 사실 없으면 검색 안 함


def test_secondhand_trade_gets_no_refund_procedure():
    index.build()
    from rag.retrieve import REFUND_ONLY_PREFIXES
    refs = find_references({"transferredMoney": "YES"}, "SECONDHAND_TRADE", k=10)
    codes = [r["code"] for r in refs]
    assert not any(c.startswith(REFUND_ONLY_PREFIXES) for c in codes)  # 환급 절차 문단 없음
    assert "REF_LAW_REFUND_PROCEDURE_V1_P2" in codes                     # '적용 제외' 문단은 포함


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("통과", name)
