"""이름·주소를 외부 AI로 보내기 전에 규칙으로 먼저 가리는지 확인해요."""
from app.guard.sensitive import mask_name_address


def test_주소는_요구받지_않아도_가림():
    assert mask_name_address("서울시 마포구 월드컵로 12, 101동 202호예요")[0] == "[주소]예요"
    assert mask_name_address("부산광역시 해운대구 우동 123-4")[0] == "[주소]"
    assert mask_name_address("101동 1203호", ["배송지"]) == ("[배송지]", ["배송지"])


def test_이름이라고_밝히면_가림():
    assert mask_name_address("제 이름은 홍길동입니다")[0] == "제 이름은 [이름]입니다"
    assert mask_name_address("성함은 박민수요")[0] == "성함은 [이름]요"


def test_이름을_요구받은_직후의_짧은_답은_이름으로_봄():
    assert mask_name_address("김철수입니다", ["성함"]) == ("[성함]입니다", ["성함"])
    text, found = mask_name_address("홍길동이고 주소는 서울시 마포구 월드컵로 12", ["이름", "주소"])
    assert text == "[이름]이고 주소는 [주소]" and set(found) == {"이름", "주소"}


def test_평범한_답은_이름으로_오해하지_않음():
    for answer in ["아니요", "싫어요 왜요", "네 알겠습니다", "고객센터에 확인할게요", "마포구에 살아요"]:
        assert mask_name_address(answer, ["이름"])[0] == answer
    assert mask_name_address("홍길동")[0] == "홍길동"  # 이름을 요구받지 않았으면 그대로
