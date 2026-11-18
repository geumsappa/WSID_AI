"""③ 훈련 대화 테스트. 실행: python -m pytest  (LLM_PROVIDER=mock 상태에서)"""
import copy

import app.services.training_turn as training
from app.llm.llm_client import LLMError
from app.scenario_data import FALLBACK_SCENARIOS


def _scenario():
    sc = copy.deepcopy(FALLBACK_SCENARIOS["중고거래 사기"]["scenario"])
    sc["maxTurns"] = 15
    return sc


def _history(*user_answers):
    h = [{"role": "persona", "text": "안녕하세요! 물건 아직 있나요?"}]
    for i, a in enumerate(user_answers):
        h.append({"role": "user", "text": a})
        if i < len(user_answers) - 1:
            h.append({"role": "persona", "text": "다음 메시지"})
    return h


def test_시작하면_첫_메시지와_선택지():
    r = training.run_training_turn("s1", _scenario(), 1, [])
    assert r["personaMessage"] == _scenario()["openingMessage"]
    assert len(r["choices"]) == 3
    assert r["judgment"] is None


def test_거절하면_safe():
    r = training.run_training_turn("s1", _scenario(), 1, _history("앱 밖 링크는 안 열게요"))
    assert r["judgment"]["outcome"] == "safe"
    assert r["judgment"]["achievedActionIds"]


def test_응하면_risky():
    r = training.run_training_turn("s1", _scenario(), 1, _history("네 링크 눌러볼게요"))
    assert r["judgment"]["outcome"] == "risky"
    assert r["judgment"]["violatedActionIds"]


def test_개인정보를_입력하면_LLM과_상관없이_risky이고_값은_가림(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "safe"}, "personaMessage": "감사합니다", "nextScene": 2})
    r = training.run_training_turn("s1", _scenario(), 2, _history("계좌는 110-123-456789 입니다"))
    assert r["judgment"]["outcome"] == "risky"
    assert r["sensitiveTypes"] == ["계좌번호"]
    assert "456789" not in r["maskedText"]
    assert not r["done"]  # 대화는 계속


def test_사기범_대사에_실존기관이나_URL이_있으면_기본_대사로_교체(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "neutral"},
        "personaMessage": "국민은행 안전결제입니다 http://fake.kr 에서 인증하세요",
        "nextScene": 1, "requestType": "링크 클릭"})
    r = training.run_training_turn("s1", _scenario(), 1, _history("네?"))
    assert "국민은행" not in r["personaMessage"] and "http" not in r["personaMessage"]
    assert "[모의링크]" in r["personaMessage"]
    assert r["fallbackUsed"]


def test_사기범이_개인정보를_요구하는_건_허용(monkeypatch):
    msg = "판매대금 받으시려면 계좌번호랑 인증번호 알려주세요"
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "risky"}, "personaMessage": msg, "nextScene": 2,
        "requestType": "개인정보 제공"})
    r = training.run_training_turn("s1", _scenario(), 1, _history("네 눌렀어요"))
    assert r["personaMessage"] == msg


def test_장면은_한번에_한칸까지만_진행(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "risky"}, "personaMessage": "좋아요", "nextScene": 3})
    r = training.run_training_turn("s1", _scenario(), 1, _history("네"))
    assert r["currentScene"] == 2


def test_시나리오에_없는_행동_id는_버림(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "safe", "achievedActionIds": ["A1", "Z9"]},
        "personaMessage": "흠", "nextScene": 1})
    r = training.run_training_turn("s1", _scenario(), 1, _history("안 할게요"))
    assert r["judgment"]["achievedActionIds"] == ["A1"]


def test_마지막_장면_끝나면_종료():
    r = training.run_training_turn("s1", _scenario(), 3, _history("안 보낼게요, 공식 앱에서 확인할게요"))
    assert r["done"] and r["endReason"] == "scenario_complete"
    assert r["choices"] == []


def test_최대_턴이면_종료():
    sc = _scenario()
    sc["maxTurns"] = 2
    r = training.run_training_turn("s1", sc, 1, _history("음", "글쎄요"))
    assert r["done"] and r["endReason"] == "max_turns"


def test_LLM이_실패해도_훈련은_계속(monkeypatch):
    def boom(*a, **k):
        raise LLMError("x")
    monkeypatch.setattr(training, "generate_json", boom)
    r = training.run_training_turn("s1", _scenario(), 1, _history("네?"))
    assert r["personaMessage"] and r["fallbackUsed"] and not r["done"]
    assert len(r["choices"]) == 3


# ---------- 개인정보 요구·입력 기록 (리포트의 핵심) ----------
def _scenario_of(subtype):
    sc = copy.deepcopy(FALLBACK_SCENARIOS[subtype]["scenario"])
    sc["maxTurns"] = 15
    return sc


def test_개인정보_장면에_들어가면_사기범이_반드시_요구함(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "risky"}, "personaMessage": "네 감사합니다.", "nextScene": 2})
    r = training.run_training_turn("s1", _scenario(), 1, _history("네 눌렀어요"))
    assert "계좌번호" in r["personaMessage"] and "적어 주세요" in r["personaMessage"]
    assert r["inputExpected"] and r["requestedInfo"] == ["계좌번호", "전화번호"]


def test_말로만_동의하면_직접_입력하라고_다시_요구():
    r = training.run_training_turn("s1", _scenario(), 2, _history("네, 알려드릴게요"))
    assert r["judgment"]["outcome"] == "risky"
    assert r["currentScene"] == 2 and not r["done"]          # 다음 장면으로 안 넘어감
    assert "계좌번호" in r["personaMessage"] and "여기에 적어 주세요" in r["personaMessage"]
    assert r["inputExpected"]
    assert r["infoDisclosure"] == {"requested": ["계좌번호", "전화번호"], "provided": [], "disclosed": False}


def test_실제로_입력하면_요구와_제공이_기록되고_값은_안_남음():
    r = training.run_training_turn("s1", _scenario(), 2, _history("110-123-456789 이고 010-1234-5678 이에요"))
    assert r["infoDisclosure"]["requested"] == ["계좌번호", "전화번호"]
    assert set(r["infoDisclosure"]["provided"]) == {"계좌번호", "전화번호"}
    assert r["infoDisclosure"]["disclosed"] and r["judgment"]["outcome"] == "risky"
    assert "456789" not in r["maskedText"] and "5678" not in r["maskedText"]
    assert not r["done"]


def test_요구받은_인증번호를_숫자로만_답해도_잡힘():
    sc = _scenario_of("지인·가족 사칭")  # 장면2: 신분증 사진, 카드번호, 인증번호 요구
    r = training.run_training_turn("s1", sc, 2, _history("482913"))
    assert r["infoDisclosure"]["provided"] == ["인증번호"]
    assert "482913" not in r["maskedText"]


def test_이름처럼_패턴이_없는_정보는_LLM_판단으로_기록하고_답변은_통째로_가림(monkeypatch):
    sc = _scenario_of("택배 사칭")  # 장면2: 이름, 전화번호, 카드번호 요구
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "risky", "disclosedInfo": ["이름", "혈액형"]},
        "personaMessage": "확인됐습니다.", "nextScene": 3})
    r = training.run_training_turn("s1", sc, 2, _history("김아무개입니다"))
    assert r["infoDisclosure"]["provided"] == ["이름"]        # 요구받지 않은 '혈액형'은 버림
    assert "김아무개" not in r["maskedText"]


def test_사기범_대사가_범행_요령이면_기본_대사로_교체(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "neutral"},
        "personaMessage": "1단계로 앱을 깔고 2단계로 인증서를 등록하세요", "nextScene": 1})
    r = training.run_training_turn("s1", _scenario(), 1, _history("네?"))
    assert "단계" not in r["personaMessage"] and r["fallbackUsed"]


def test_backend_masked_card_submission_counts_as_disclosure(monkeypatch):
    seen = []

    def reply(system, transcript, **kwargs):
        seen.append(transcript)
        return {"judgment": {"outcome": "safe"}, "personaMessage": "확인했습니다", "nextScene": 2}

    monkeypatch.setattr(training, "generate_json", reply)
    result = training.run_training_turn("masked-input", _scenario(), 2, _history("카드번호는 [카드번호] 입니다"))
    assert result["judgment"]["outcome"] == "risky"
    assert result["infoDisclosure"]["provided"] == ["카드번호"]
    assert "[카드번호]" in seen[0]
    assert not result["done"]
