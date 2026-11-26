"""사기범이 보내는 사진·화면 소품([사진:상품|...] 등) 정리 테스트"""
import copy
import json

import app.services.training_turn as training
from app.guard import output_guard, scam_media
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


def test_모양이_조금_달라도_정해진_모양으로_고친다():
    out = scam_media.clean("실물이에요 [사진: 인증샷 | 전자레인지 | 김민수]", [], "")
    assert out.endswith("[사진:상품|전자레인지|김민수]")
    assert scam_media.clean("보냈어요 ［사진：입금 확인｜35만원］", [], "박지훈").endswith("[사진:입금확인|35만원|박지훈]")


def test_한_메시지에_하나만_남긴다():
    out = scam_media.clean("확인하세요 [사진:입금확인|35만원|김] [공문|수사기관|사건 조회]", [], "")
    assert scam_media.find(out) == ["사진:입금확인"]


def test_대화_전체_한도를_넘으면_지운다():
    assert scam_media.clean("또 보내요 [사진:송장|한결택배|자전거]", ["송장이에요 [사진:송장|한결택배|자전거]"], "") == "또 보내요"
    assert scam_media.find(scam_media.clean("하나 더 [사진:상품|자전거|이]", ["[사진:상품|자전거|이]"], "")) == ["사진:상품"]


def test_값의_긴_숫자와_빈_값을_정리한다():
    out = scam_media.clean("송장 [사진:송장|한결택배|5012-3456-7890]", [], "")
    assert "5012" not in out and out.endswith("[사진:송장|한결택배|물건]")


def test_잘려서_닫히지_않은_표시는_지운다():
    assert scam_media.clean("문서 확인하세요 [공문|서울중앙지방검찰청|사건 조회 결과 통", [], "") == "문서 확인하세요"


def test_말풍선_줄바꿈은_살린다():
    out = scam_media.clean("네 있어요!\n실물 사진이에요\n[사진:상품|전자레인지|김민수]", [], "")
    assert out.split("\n") == ["네 있어요!", "실물 사진이에요", "[사진:상품|전자레인지|김민수]"]


def test_평가용으로_읽는_말로_바꾼다():
    assert scam_media.describe("송장이에요 [사진:송장|한결택배|자전거]") == "송장이에요 (택배 송장 사진을 보냄)"


def _state(**kw):
    base = dict(current_scene=1, total_scenes=3, scene_request_type="송금", next_scene_request_type="송금",
                user_turns=1, min_turns=4, max_turns=15)
    base.update(kw)
    return output_guard.TurnState(**base)


def test_검증기는_사진_소품을_모르는_대괄호로_보지_않는다():
    data = {"move": "신뢰", "personaMessage": "네 있어요!\n실물 사진이에요 [사진:상품|전자레인지|김민수]",
            "nextScene": 1, "requestType": "송금"}
    errors, _ = output_guard.validate(json.dumps(data, ensure_ascii=False), _state())
    assert not [e for e in errors if "대괄호" in e]


def test_사진_소품_뒤에_계좌_소품이_와도_끝_위치_규칙을_지킨다():
    data = {"move": "요구", "personaMessage": "실물이에요 [사진:상품|전자레인지|김] 입금은 이 계좌로 부탁드려요 [모의계좌]",
            "nextScene": 1, "requestType": "송금"}
    errors, _ = output_guard.validate(json.dumps(data, ensure_ascii=False), _state())
    assert not [e for e in errors if "맨 끝" in e or "명분" in e]


def test_날짜는_계좌번호로_보지_않는다():
    data = {"move": "신뢰", "personaMessage": "2026-11-24 접수 건이라 오늘까지 처리돼야 해요", "nextScene": 1, "requestType": "송금"}
    errors, _ = output_guard.validate(json.dumps(data, ensure_ascii=False), _state())
    assert not [e for e in errors if "숫자" in e]


def test_피해_예방_같은_사칭범_말투는_막지_않는다():
    data = {"move": "신뢰", "personaMessage": "피해 예방 차원에서 확인드리는 거예요. 요즘 사칭이 많아 주의하세요.",
            "nextScene": 1, "requestType": "송금"}
    errors, _ = output_guard.validate(json.dumps(data, ensure_ascii=False), _state())
    assert not [e for e in errors if "금지어" in e]


def test_훈련_한_턴에서_사진_소품이_정리되어_나간다(monkeypatch):
    monkeypatch.setattr(training, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "neutral", "userAction": "물건을 물어봄", "reason": "질문"},
        "move": "신뢰", "speaker": None,
        "personaMessage": "네 아직 있어요!\n이름 써서 찍었어요 [사진: 상품 | 자전거 | 김민수] [공문|수사기관|사건]",
        "requestMade": False, "requestType": "대화 지속", "nextScene": 1, "newFacts": ["판매 물건: 자전거"],
        "scenarioComplete": False, "realConcern": False})
    r = training.run_training_turn("s1", _scenario(), 1, _history("자전거 아직 있나요?"))
    assert "[사진:상품|자전거|김민수]" in r["personaMessage"]
    assert "[공문" not in r["personaMessage"]
    assert "\n" in r["personaMessage"]
