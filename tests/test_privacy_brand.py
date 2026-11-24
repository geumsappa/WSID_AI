"""개인정보 가림 보강(한글 숫자, 나눠 보낸 번호, 프론트가 가린 표시)과 실제 브랜드 이름 바꾸기."""
from app.guard.brand_names import fictionalize, fictionalize_tree
from app.guard.pii_masker import korean_digits_to_arabic
from app.services.training_turn import _mask_last


def test_한글로_읽어_쓴_번호도_숫자로_바꿔서_가림():
    assert korean_digits_to_arabic("공일공 일이삼사 오육칠팔이요") == "010 1234 5678이요"
    assert korean_digits_to_arabic("이삼일 뒤에 갈게요") == "이삼일 뒤에 갈게요"  # 짧은 건 그대로
    masked, provided = _mask_last("공일공 일이삼사 오육칠팔이요", [], "", "")
    assert "일이삼사" not in masked and provided == ["전화번호"]


def test_두_메시지로_나눠_보낸_번호의_뒷조각을_가림():
    masked, provided = _mask_last("5678이요", ["계좌번호"], "연락처 알려 주세요", "010-1234")
    assert masked == "[전화번호]이요" and provided == ["전화번호"]
    assert _mask_last("5시에 봐요", [], "", "네 알겠어요") == ("5시에 봐요", [])


def test_프론트가_먼저_가린_이름도_입력으로_셈():
    masked, provided = _mask_last("[이름] [전화번호]", ["성함", "연락처"], "성함이랑 연락처 주세요", "")
    assert set(provided) == {"성함", "전화번호"}


def test_실제_브랜드는_가상_이름으로_조사까지_맞춤():
    assert fictionalize("당근마켓 채팅에서 카톡으로 보내요") == "모두마켓 채팅에서 메신저로 보내요"
    assert fictionalize("KB국민은행 계좌로, 토스트는 그대로") == "한빛은행 계좌로, 토스트는 그대로"
    assert fictionalize_tree({"allowedOrganizations": ["국민은행", "KB국민은행"]}) == {"allowedOrganizations": ["한빛은행"]}


def test_팀_합의_일부_정보는_입력으로_세지_않음():
    asked = "재배송 접수하려면 본인 확인이 필요해요. 성함이랑 연락처를 여기 채팅에 적어 주세요."
    assert _mask_last("성만 알려드릴게요. 김이요", ["이름", "전화번호"], asked, "") == ("성만 알려드릴게요. 김이요", [])
    masked, provided = _mask_last("뒷자리 네 개만 알려줄게 1234", ["카드번호"], "카드 번호 좀 알려줘", "")
    assert "1234" not in masked and provided == []  # 가리긴 하지만 '입력함'은 아님
    assert _mask_last("[카드번호]", ["카드번호"], "", "")[1] == ["카드번호"]  # 앞에서 가려 온 전체 번호는 그대로 셈
    masked, provided = _mask_last("그건 모르겠고 저 다음 주에 강남구 테헤란로 123으로 이사 가요", ["이름"], asked, "")
    assert provided == ["주소"] and "모르겠" in masked
    assert "ㅋㅋ" in _mask_last("제 이름은 홍길동이에요ㅋㅋ", ["이름"], asked, "")[0]


def test_미루는_답의_낱말을_이름으로_가리지_않음():
    asked = "VIP방 입장하려면 성함이랑 연락처, 수익금 받을 계좌번호가 필요해요."
    assert _mask_last("지금은 좀 그렇고 나중에요", ["이름", "전화번호", "계좌번호"], asked, "") == ("지금은 좀 그렇고 나중에요", [])
    assert _mask_last("다음에 알려드릴게요", ["이름"], asked, "") == ("다음에 알려드릴게요", [])
    assert _mask_last("김철수요", ["이름"], asked, "")[1] == ["이름"]
