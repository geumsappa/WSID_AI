"""모의 링크·모의 앱 표시가 대화 흐름에 맞게 붙는지 확인해요."""
from app.services.training_api import APP_CLICK_EVENT, _place_links


def _persona(*lines):
    return [{"role": "persona", "text": line} for line in lines]


def test_링크_장면에서만_링크를_붙임():
    assert _place_links({"personaMessage": "확인해 주세요", "requestType": "링크 클릭"}, _persona("안녕하세요"), False).endswith("[모의링크]")
    assert "[모의링크]" not in _place_links({"personaMessage": "무슨 일이세요?", "requestType": "대화 지속"}, _persona("안녕하세요"), False)


def test_바로_앞에_보낸_링크는_다시_붙이지_않음():
    reply = _place_links({"personaMessage": "아까 보낸 링크로 들어가세요", "requestType": "링크 클릭"}, _persona("확인 [모의링크]"), False)
    assert "[모의링크]" not in reply


def test_링크는_대화_전체에서_두_번까지만():
    reply = _place_links({"personaMessage": "다시 확인 [모의링크]", "requestType": "링크 클릭"},
                         _persona("1 [모의링크]", "2", "3 [모의링크]"), False)
    assert "[모의링크]" not in reply


def test_앱_설치_장면은_모의앱으로_바꿈():
    reply = _place_links({"personaMessage": "보안 앱이에요 [모의링크]", "requestType": "앱 설치"}, _persona("안녕하세요"), False)
    assert "[모의앱]" in reply and "[모의링크]" not in reply


def test_앱_링크를_누른_뒤에는_다시_보내지_않음():
    history = _persona("앱 [모의앱]") + [{"role": "event", "text": APP_CLICK_EVENT}]
    reply = _place_links({"personaMessage": "설치 확인됐어요 [모의앱]", "requestType": "앱 설치"}, history, False)
    assert "[모의앱]" not in reply


def test_리포트_타임라인은_있는_답변_번호와_안전한_문장만_남김():
    from app.services.training_api import _turn_feedback
    raw = [
        {"turn": 2, "outcome": "risky", "userAction": "계좌번호를 적음", "feedback": "공식 앱에서 먼저 확인하세요."},
        {"turn": 1, "outcome": "safe", "userAction": "어디냐고 물음", "feedback": "상대를 확인하려 한 좋은 대응이에요."},
        {"turn": 9, "outcome": "risky", "userAction": "없는 답변", "feedback": "없는 턴"},
        {"turn": 3, "outcome": "risky", "userAction": "전화함", "feedback": "1588-1234 로 전화하세요"},
        {"turn": 3, "outcome": "maybe", "userAction": "x", "feedback": "y"},
        "이상한 값",
    ]
    result = _turn_feedback(raw, user_turns=3)
    assert [item.turn for item in result] == [1, 2]
    assert _turn_feedback(None, user_turns=3) == []
