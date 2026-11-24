"""리포트의 '한 것/안 한 것'을 서버 기록으로 정하는지 테스트"""
import copy

import app.services.training_api as training_api
import app.services.training_turn as training
from app.llm.llm_client import LLMError
from app.scenario_data import FALLBACK_SCENARIOS
from app.schemas.training_api import EvaluationRequest
from app.guard.scenario_guard import validate_and_normalize
from app.services import report_facts


def _scenario(log=None):
    result, errors = validate_and_normalize(copy.deepcopy(FALLBACK_SCENARIOS["중고거래 사기"]))
    assert not errors
    sc = result["scenario"]
    sc["maxTurns"] = 15
    if log is not None:
        sc["trainingState"] = {"facts": [], "log": log}
    return sc


CONV = [
    {"sender": "AI", "phase": "SIMULATION", "content": "네 아직 있어요 [사진:상품|전자레인지|김민수]"},
    {"sender": "USER", "phase": "SIMULATION", "content": "택배 되나요?"},
    {"sender": "AI", "phase": "SIMULATION", "content": "이 계좌로 보내 주세요 [모의계좌]"},
    {"sender": "USER", "phase": "SIMULATION", "content": "[계좌번호] 여기요"},
    {"sender": "AI", "phase": "SIMULATION", "content": "입금 확인했어요 [퇴장]"},
]
LOG = [
    {"t": 1, "o": "neutral", "p": [], "r": [], "e": []},
    {"t": 2, "o": "risky", "p": ["계좌번호"], "r": ["계좌번호", "예금주 성함"], "e": ["송금_누름"], "x": "scam_success"},
]


def _request(log=LOG, actions=("MOCK_TRANSFER",), conv=CONV):
    return EvaluationRequest.model_validate({"scenario": _scenario(log), "conversation": conv,
                                             "actions": [{"actionCode": a} for a in actions]})


def test_기록으로_위험_등급과_넘긴_정보를_정한다():
    f = report_facts.collect(_scenario(LOG), CONV, {"MOCK_TRANSFER"})
    assert f.grade == "위험"
    assert f.provided == ["계좌번호"] and f.requested == ["계좌번호", "예금주 성함"]
    assert f.transfers == 1 and f.sent_account
    assert any("계좌번호를 대화창에 적었어요 (2번째 답변)" in m for m in f.missed)
    assert any("예금주 성함은 알려 주지 않았어요" in d for d in f.did)
    assert report_facts.grade_sentence(f) == "결과: 위험. 계좌번호를 넘겼고, 안내받은 계좌로 송금 버튼을 눌렀어요."


def test_대화를_끊으면_안전():
    log = [{"t": 1, "o": "safe", "p": [], "r": ["계좌번호"], "e": [], "x": "user_exit"}]
    conv = CONV[:1] + [{"sender": "USER", "phase": "SIMULATION", "content": "안 살게요 연락하지 마세요"}]
    f = report_facts.collect(_scenario(log), conv, set())
    assert f.grade == "안전"
    assert any("먼저 대화를 끊었어요" in d for d in f.did)
    assert any("계좌번호를 끝까지 알려 주지 않았어요" in d for d in f.did)


def test_기록이_없는_예전_훈련은_가림_표시로_짐작한다():
    f = report_facts.collect(_scenario(None), CONV, set())
    assert f.provided == ["계좌번호"] and f.grade == "위험"


def test_LLM이_실패해도_리포트가_나온다(monkeypatch):
    def fail(*a, **k):
        raise LLMError("down")
    monkeypatch.setattr(training_api, "generate_json", fail)
    r = training_api.evaluate(_request())
    assert r.overallFeedback.startswith("결과: 위험.")
    assert r.infoDisclosure.provided == ["계좌번호"]
    assert r.mockPageEvaluation.attemptedTransfer
    assert [t.turn for t in r.turnFeedback] == [2] and r.turnFeedback[0].outcome == "risky"
    assert r.recommendations


def test_LLM이_기록과_다른_판정을_줘도_기록을_따른다(monkeypatch):
    monkeypatch.setattr(training_api, "generate_json", lambda *a, **k: {
        "overallFeedback": "판매자 사칭에 인증샷까지 받아 쉽게 믿었어요.",
        "strengths": ["택배 가능 여부를 먼저 물었어요."], "improvements": ["명분에 쉽게 수긍했어요."],
        "recommendations": ["안전결제는 앱 안에서만 쓰세요."],
        "turnFeedback": [{"turn": 2, "outcome": "safe", "userAction": "계좌번호를 보냄", "feedback": "다음엔 앱 안에서 거래하세요."},
                         {"turn": 9, "outcome": "risky", "userAction": "없는 턴", "feedback": "x"}]})
    r = training_api.evaluate(_request())
    assert r.overallFeedback == "결과: 위험. 계좌번호를 넘겼고, 안내받은 계좌로 송금 버튼을 눌렀어요. 판매자 사칭에 인증샷까지 받아 쉽게 믿었어요."
    assert [(t.turn, t.outcome) for t in r.turnFeedback] == [(2, "risky")]
    assert r.turnFeedback[0].feedback == "다음엔 앱 안에서 거래하세요."
    # A4 한 장: 개선할 점은 4개까지, 기록으로 확인된 사실이 먼저 (자리가 없으면 LLM 글은 빠져요)
    assert r.improvements[0].startswith("요구받은 계좌번호를") and len(r.improvements) == 4
    assert "택배 가능 여부를 먼저 물었어요." in r.strengths and len(r.strengths) <= 3


def test_훈련_턴마다_리포트용_기록이_쌓인다(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "risky", "userAction": "계좌번호를 적음", "reason": "요구에 응함"},
        "move": "확전", "speaker": None, "personaMessage": "네 확인했어요. 성함도 알려 주세요.",
        "requestMade": True, "requestType": "개인정보 제공", "nextScene": 1, "newFacts": [],
        "scenarioComplete": False, "realConcern": False})
    sc = _scenario(None)
    hist = [{"role": "persona", "text": "계좌번호 알려 주세요"}, {"role": "user", "text": "110-123-456789 입니다"}]
    r = training.run_training_turn("s", sc, 1, hist, state={"facts": [], "log": [{"t": 0, "o": "safe", "p": [], "r": [], "e": []}]})
    log = r["trainingState"]["log"]
    assert log[-1]["t"] == 1 and log[-1]["o"] == "risky" and "계좌번호" in log[-1]["p"]
    assert "456789" not in str(log)


def test_넘긴_정보는_한_줄로_모은다():
    log = [{"t": 3, "o": "risky", "p": ["전화번호", "이름"], "r": ["전화번호", "이름", "모의 교육 준비비"], "e": []}]
    f = report_facts.collect(_scenario(log), CONV, set())
    assert [m for m in f.missed if m.startswith("요구받은")] == [
        "요구받은 전화번호·이름을 대화창에 적었어요 (3번째 답변). 사기에 그대로 쓰일 수 있는 정보예요."]
