"""주소는 외부 AI로 보내기 전에 가리고, 이름은 가리지 않고 찾기만 하는지 확인해요 (11/24 팀 결정)."""
from app.guard.sensitive import mask_name_address


def test_주소는_요구받지_않아도_가림():
    assert mask_name_address("서울시 마포구 월드컵로 12, 101동 202호예요")[0] == "[주소]예요"
    assert mask_name_address("부산광역시 해운대구 우동 123-4")[0] == "[주소]"
    assert mask_name_address("101동 1203호", ["배송지"]) == ("[배송지]", ["배송지"])


def test_이름이라고_밝히면_가리진_않고_찾음():
    assert mask_name_address("제 이름은 홍길동입니다") == ("제 이름은 홍길동입니다", ["이름"])
    assert mask_name_address("성함은 박민수요") == ("성함은 박민수요", ["이름"])
    # 다시 가리기로 하면 MASK_NAMES 하나만 바꾸면 돼요
    assert mask_name_address("제 이름은 홍길동입니다", mask_names=True)[0] == "제 이름은 [이름]입니다"


def test_이름을_요구받은_직후의_짧은_답은_이름으로_봄():
    assert mask_name_address("김철수입니다", ["성함"]) == ("김철수입니다", ["성함"])
    text, found = mask_name_address("홍길동이고 주소는 서울시 마포구 월드컵로 12", ["이름", "주소"])
    assert text == "홍길동이고 주소는 [주소]" and set(found) == {"이름", "주소"}


def test_평범한_답은_이름으로_오해하지_않음():
    for answer in ["아니요", "싫어요 왜요", "네 알겠습니다", "고객센터에 확인할게요", "마포구에 살아요"]:
        assert mask_name_address(answer, ["이름"])[0] == answer
    assert mask_name_address("홍길동")[0] == "홍길동"  # 이름을 요구받지 않았으면 그대로


def test_nickname_and_unknown_name_are_not_mistaken_for_private_values():
    for text in ["친구 도토리", "친구 도토리와 이용했고 도토리가 요금을 냈어요", "이름은 기억 안나"]:
        assert mask_name_address(text) == (text, [])


def test_street_without_house_number_is_still_masked():
    assert mask_name_address("마포구 월드컵로")[0] == "[주소]"
    assert mask_name_address("부산광역시 해운대구 우동 123-4")[0] == "[주소]"


def test_name_question_connectors_are_not_names():
    for text in ["이름이나 별명도 괜찮습니다.", "이름 또는 별명을 알려 주세요.", "실명 대신 별명으로 불러도 되나요?"]:
        assert mask_name_address(text) == (text, [])


def test_name_that_resembles_a_question_connector_still_gets_masked():
    assert mask_name_address("이름은 이나예요") == ("이름은 이나예요", ["이름"])
    assert mask_name_address("이나입니다", ["이름"]) == ("이나입니다", ["이름"])
