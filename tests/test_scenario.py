"""② 시나리오 생성 테스트. 실행: python -m pytest  (LLM_PROVIDER=mock 상태에서)"""
import app.services.scenario as scenario_service
from app.guard.scenario_guard import find_unsafe_text, validate_and_normalize
from app.llm.llm_client import LLMError
from app.scenario_data import FALLBACK_SCENARIOS, RISK_TYPES, pick_subtype


def _good_llm_output():
    import copy
    return copy.deepcopy(FALLBACK_SCENARIOS["중고거래 사기"])


# ---------- 가드레일 ----------
def test_기본_시나리오_7종은_전부_가드레일_통과():
    for subtype, data in FALLBACK_SCENARIOS.items():
        result, errors = validate_and_normalize(data)
        assert result, f"{subtype}: {errors}"
        assert result["scenario"]["riskType"] in RISK_TYPES


def test_실존_기관명_차단():
    assert find_unsafe_text("국민은행 고객센터입니다")
    assert find_unsafe_text("CJ대한통운 배송 안내")


def test_실제_URL_전화번호_차단():
    assert find_unsafe_text("여기로 접속 http://abc.kr")
    assert find_unsafe_text("bit.ly/abcd 로 확인")
    assert find_unsafe_text("고객센터 1588-1234")
    assert not find_unsafe_text("여기서 확인하세요 [모의링크]")


def test_허용되지_않은_세부유형_차단():
    data = _good_llm_output()
    data["scenario"]["fraudSubtype"] = "보이스피싱"
    result, errors = validate_and_normalize(data)
    assert result is None and errors


def test_시나리오에_실존이름_있으면_폐기():
    data = _good_llm_output()
    data["scenario"]["openingMessage"] = "당근에서 보고 연락드려요"
    result, errors = validate_and_normalize(data)
    assert result is None


def test_전화_채널은_차단():
    data = _good_llm_output()
    data["scenario"]["channel"] = "전화"
    result, _ = validate_and_normalize(data)
    assert result is None


# ---------- 유형 선택 ----------
def test_세부유형으로_대분류가_자동_결정():
    data = _good_llm_output()
    data["scenario"]["fraudSubtype"] = "택배 사칭"
    data["scenario"]["basedOnCaseIds"] = ["FC-03"]
    data["analysis"]["riskType"] = "기타"  # LLM 이 대분류를 잘못 적어도
    result, _ = validate_and_normalize(data)
    assert result["scenario"]["riskType"] == "스미싱"  # 표 기준으로 바로잡음
    assert result["analysis"]["riskType"] == "스미싱"


def test_6분류에_없는_위험유형은_차단():
    data = _good_llm_output()
    data["analysis"]["riskType"] = "보이스피싱"
    result, _ = validate_and_normalize(data)
    assert result is None


def test_사실_카테고리로_유형_선택():
    facts = [{"category": "구직·아르바이트", "detail": "알바 구함"},
             {"category": "구직·아르바이트", "detail": "면접 다님"},
             {"category": "택배·배송", "detail": "택배 기다림"}]
    assert pick_subtype(facts) == "채용·아르바이트 사기"


def test_사실이_없으면_택배_기본():
    assert pick_subtype([]) == "택배 사칭"


# ---------- 전체 흐름 ----------
def test_mock으로_시나리오_생성():
    r = scenario_service.run_scenario("s1", [{"category": "거래(중고·쇼핑)", "detail": "자전거 판매"}])
    assert r["scenario"]["fraudSubtype"] == "중고거래 사기"
    assert r["scenario"]["riskType"] == "기타"
    assert r["analysis"]["riskType"] == "기타"
    assert r["scenario"]["maxTurns"] > 0
    assert r["analysis"]["judgmentPoints"]
    assert not r["fallback_used"]


def test_LLM이_계속_실패하면_사실에_맞는_기본_시나리오(monkeypatch):
    def boom(*a, **k):
        raise LLMError("x")
    monkeypatch.setattr(scenario_service, "generate_json", boom)
    r = scenario_service.run_scenario("s1", [{"category": "투자", "detail": "코인 투자"}])
    assert r["fallback_used"]
    assert r["scenario"]["fraudSubtype"] == "투자 리딩방"
    assert r["scenario"]["riskType"] == "투자·가상자산"
    assert r["analysis"]["usedFacts"] == ["코인 투자"]


def test_가드레일_위반_시나리오는_재생성(monkeypatch):
    bad = _good_llm_output()
    bad["scenario"]["openingMessage"] = "토스로 결제했어요 http://fake.com"
    good = _good_llm_output()
    calls = iter([bad, good])
    monkeypatch.setattr(scenario_service, "generate_json", lambda *a, **k: next(calls))
    r = scenario_service.run_scenario("s1", [{"category": "거래(중고·쇼핑)", "detail": "자전거 판매"}])
    assert not r["fallback_used"]
    assert "http" not in r["scenario"]["openingMessage"]


def test_입력_사실의_개인정보는_가려서_사용(monkeypatch):
    seen = {}

    def capture(system, user, **k):
        seen["user"] = user
        return _good_llm_output()
    monkeypatch.setattr(scenario_service, "generate_json", capture)
    scenario_service.run_scenario("s1", [{"category": "거래(중고·쇼핑)", "detail": "010-1234-5678로 연락함"}])
    assert "1234" not in seen["user"]


def test_사실의_채널_사칭_요구정보가_LLM에_전달됨(monkeypatch):
    seen = {}

    def capture(system, user, **k):
        seen["user"] = user
        return _good_llm_output()
    monkeypatch.setattr(scenario_service, "generate_json", capture)
    scenario_service.run_scenario("s1", [{
        "category": "택배·배송", "detail": "반송 문자 받음", "channel": "문자",
        "impersonatedIdentity": "택배사", "requestedAction": "링크 클릭", "riskFactors": ["시간 압박"]}])
    assert "채널: 문자" in seen["user"] and "상대가 밝힌 신분: 택배사" in seen["user"]
    assert "요구: 링크 클릭" in seen["user"] and "위험 요소: 시간 압박" in seen["user"]


# ---------- SIM-005/006 사례 DB · 모방범죄 · 개인정보 요구 장면 ----------
from app.case_db import cases_for_prompt, load_cases  # noqa: E402
from app.guard.scenario_guard import find_copycat_risk  # noqa: E402
from app.scenario_data import SUBTYPE_TO_RISK_TYPE  # noqa: E402


def test_사례_DB_형식_점검():
    cases = load_cases()
    assert 10 <= len(cases) <= 20
    for cid, c in cases.items():
        assert c["riskType"] in RISK_TYPES, cid
        assert SUBTYPE_TO_RISK_TYPE[c["subtype"]] == c["riskType"], cid
        assert c["sourceUrl"].startswith("https://"), cid
        assert c["issuer"] and c["summary"] and c["correctResponse"], cid
    for risk_type in RISK_TYPES:  # 6분류 모두 사례가 하나 이상
        assert any(c["riskType"] == risk_type for c in cases.values()), risk_type


def test_LLM에_보내는_사례_요약에는_실존이름_URL이_없음():
    assert find_unsafe_text(cases_for_prompt()) == []


def test_근거_사례가_출처와_함께_붙음():
    r = scenario_service.run_scenario("s1", [{"category": "택배·배송", "detail": "택배 문자 받음"}])
    refs = r["scenario"]["basedOnCases"]
    assert refs and all(ref["sourceUrl"].startswith("https://") and ref["issuer"] for ref in refs)


def test_DB에_없는_사례를_근거로_들면_폐기():
    data = _good_llm_output()
    data["scenario"]["basedOnCaseIds"] = ["FC-99"]
    assert validate_and_normalize(data)[0] is None


def test_근거_사례가_없거나_유형이_다르면_폐기():
    data = _good_llm_output()
    data["scenario"]["basedOnCaseIds"] = []
    assert validate_and_normalize(data)[0] is None
    data["scenario"]["basedOnCaseIds"] = ["FC-10"]  # 투자 사례를 중고거래(기타) 시나리오 근거로
    assert validate_and_normalize(data)[0] is None


def test_개인정보를_요구하는_장면이_없으면_폐기():
    data = _good_llm_output()
    for sc in data["scenario"]["keyScenes"]:
        sc["request"], sc["requestedInfo"] = "링크 클릭", []
    result, errors = validate_and_normalize(data)
    assert result is None and any("개인정보를 요구하는 장면" in e for e in errors)


def test_기본_시나리오는_전부_개인정보_요구_장면이_있음():
    for subtype, data in FALLBACK_SCENARIOS.items():
        assert any(sc["request"] == "개인정보 제공" and sc["requestedInfo"]
                   for sc in data["scenario"]["keyScenes"]), subtype


def test_모방범죄_검사():
    assert find_copycat_risk("1단계 앱 설치, 2단계 인증서 등록")
    assert find_copycat_risk("비밀번호를 입력한 뒤 이체 버튼을 클릭하세요")
    assert find_copycat_risk("대포통장으로 받으면 추적이 안 됩니다")
    assert not find_copycat_risk("오늘 안에 확인하지 않으면 반송됩니다. [모의링크]")


def test_단계별_절차_대본이나_너무_긴_대본은_폐기():
    data = _good_llm_output()
    data["scenario"]["keyScenes"][0]["tactic"] = "1단계로 링크를 열게 하고 2단계로 인증하게 한다"
    assert validate_and_normalize(data)[0] is None
    data = _good_llm_output()
    data["scenario"]["openingMessage"] = "안녕하세요 " * 60
    assert validate_and_normalize(data)[0] is None
