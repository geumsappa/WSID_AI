"""⑤ 결과 리포트 테스트. 실행: python -m pytest  (LLM_PROVIDER=mock 상태에서)"""
import copy

import app.services.report as report
from app.guard.scenario_guard import validate_and_normalize
from app.llm.llm_client import LLMError
from app.scenario_data import FALLBACK_SCENARIOS


def _scenario():
    result, _ = validate_and_normalize(copy.deepcopy(FALLBACK_SCENARIOS["중고거래 사기"]))
    return result["scenario"]


def _turn(n, scene, outcome, achieved=(), violated=(), requested=(), provided=(), text="답변"):
    return {
        "turn": n, "scene": scene, "userText": text,
        "judgment": {"outcome": outcome, "userAction": f"{n}턴 행동", "achievedActionIds": list(achieved),
                     "violatedActionIds": list(violated), "reason": f"{n}턴 이유"},
        "infoDisclosure": {"requested": list(requested), "provided": list(provided), "disclosed": bool(provided)},
    }


# 링크 클릭(risky) → 말로 동의(risky) → 계좌번호 입력(risky) → 송금 거절(safe)
TURNS = [
    _turn(1, 1, "risky", violated=["A1"]),
    _turn(2, 2, "risky", violated=["A2"], requested=["계좌번호", "전화번호"], text="네 알려드릴게요"),
    _turn(3, 2, "risky", violated=["A2"], requested=["계좌번호", "전화번호"], provided=["계좌번호"], text="[계좌번호]요"),
    _turn(4, 3, "safe", achieved=["A2", "A3"]),
]


def test_개인정보_요구와_제공을_코드가_정확히_계산():
    r = report.run_report("s1", _scenario(), TURNS, [], "scenario_complete")
    info = r["infoProtection"]
    assert info["requested"] == ["계좌번호", "전화번호"]
    assert info["provided"] == ["계좌번호"]
    assert info["protected"] == ["전화번호"]
    assert info["disclosed"]
    assert "3번째" in info["message"]
    assert [e["turn"] for e in info["events"]] == [2, 3]


def test_잘한_행동과_놓친_행동():
    r = report.run_report("s1", _scenario(), TURNS, [], "scenario_complete")
    assert [g["actionId"] for g in r["goodActions"]] == ["A2", "A3"]
    missed = {m["actionId"]: m for m in r["missedActions"]}
    assert set(missed) == {"A1", "A2"}
    assert missed["A2"]["recoveredLater"]          # 2·3턴에 어겼지만 4턴에 지킴
    assert not missed["A1"]["recoveredLater"]


def test_넘긴_정보별_후속_조치와_공식연락처_근거사례():
    r = report.run_report("s1", _scenario(), TURNS, [], "scenario_complete")
    assert any("계좌번호를 넘겼다면" in x for x in r["recommendedActions"])
    assert {c["contact"] for c in r["officialContacts"]} >= {"112", "1332", "118"}
    assert r["basedOnCases"][0]["caseId"] == "FC-13"


def test_턴별_피드백이_모든_턴에_있음():
    r = report.run_report("s1", _scenario(), TURNS, [], "scenario_complete")
    assert [f["turn"] for f in r["turnFeedback"]] == [1, 2, 3, 4]
    assert r["turnFeedback"][2]["providedInfo"] == ["계좌번호"]


def test_LLM이_틀린_말을_해도_사실은_코드값(monkeypatch):
    monkeypatch.setattr(report, "generate_json", lambda *a, **k: {
        "headline": "완벽해요", "overview": "개인정보를 하나도 넘기지 않았어요",
        "turnFeedback": [{"turn": 99, "feedback": "없는 턴"}]})
    r = report.run_report("s1", _scenario(), TURNS, [], None)
    assert r["infoProtection"]["provided"] == ["계좌번호"]   # 목록은 코드 계산값 그대로
    assert len(r["turnFeedback"]) == 4                       # 없는 턴은 버리고 기본 문장으로 채움


def test_LLM_글에_전화번호_URL이_있으면_버림(monkeypatch):
    monkeypatch.setattr(report, "generate_json", lambda *a, **k: {
        "headline": "결과", "overview": "1588-1234로 신고하세요",
        "turnFeedback": [{"turn": 1, "feedback": "http://fake.kr 확인"}]})
    r = report.run_report("s1", _scenario(), TURNS, [], None)
    assert "1588" not in r["overview"] and "http" not in r["turnFeedback"][0]["feedback"]
    assert r["fallbackUsed"]


def test_리포트에_실제_값이_남지_않음(monkeypatch):
    monkeypatch.setattr(report, "generate_json", lambda *a, **k: {
        "headline": "결과", "overview": "계좌 110-123-456789 를 넘겼어요", "turnFeedback": []})
    r = report.run_report("s1", _scenario(), TURNS, [], None)
    assert "456789" not in str(r)


def test_다_지켰을_때():
    turns = [_turn(1, 1, "safe", achieved=["A1"]),
             _turn(2, 2, "safe", achieved=["A2"], requested=["계좌번호", "전화번호"])]
    r = report.run_report("s1", _scenario(), turns, [], "user_quit")
    assert not r["infoProtection"]["disclosed"]
    assert "하나도 넘기지 않았어요" in r["infoProtection"]["message"]
    assert r["missedActions"] == []


def test_모의_페이지_행동_반영():
    events = [{"action": "링크 열기", "inputTypes": []},
              {"action": "정보 입력", "inputTypes": ["계좌번호"]}]
    r = report.run_report("s1", _scenario(), TURNS, events, None)
    assert r["mockPage"]["risky"] and "계좌번호" in r["mockPage"]["message"]
    r2 = report.run_report("s1", _scenario(), TURNS, [{"action": "링크 열기"}, {"action": "페이지 닫기"}], None)
    assert not r2["mockPage"]["risky"]


def test_LLM이_실패해도_리포트는_나옴(monkeypatch):
    def boom(*a, **k):
        raise LLMError("x")
    monkeypatch.setattr(report, "generate_json", boom)
    r = report.run_report("s1", _scenario(), TURNS, [], None)
    assert r["fallbackUsed"] and r["headline"] == "개인정보를 넘긴 장면이 있었어요"
    assert len(r["turnFeedback"]) == 4 and r["missedRiskSignals"]
