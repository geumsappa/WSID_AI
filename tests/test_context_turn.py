"""자동 테스트. 실행: python -m pytest  (LLM_PROVIDER=mock 상태에서)

숫자 마스킹과 JSON 처리 검증입니다. 의미·개연성 검증은 별도 파이프라인 테스트에서 확인합니다.
"""
from app.guard.sensitive import mask_requested_numbers, mask_sensitive
from app.llm.llm_client import parse_json
from app.services.context_turn import run_context_turn


# ---------- 가드레일 ----------
def test_주민번호_감지():
    masked, found = mask_sensitive("제 주민번호는 990101-1234567 이에요")
    assert "주민등록번호" in found
    assert "1234567" not in masked


def test_한글에_붙은_계좌번호도_감지():
    _, found = mask_sensitive("계좌는110123456789로 보냈어요")
    assert "계좌번호" in found


def test_전화번호는_계좌번호로_오인하지_않음():
    _, found = mask_sensitive("010-1234-5678로 연락왔어요")
    assert found == ["전화번호"]


def test_카드번호_감지():
    _, found = mask_sensitive("1234 5678 9012 3456")
    assert "카드번호" in found


def test_비밀번호_감지():
    _, found = mask_sensitive("비밀번호는 abcd1234 예요")
    assert "비밀번호" in found


def test_일상_문장은_통과():
    _, found = mask_sensitive("지난주에 당근마켓에서 자전거를 3만원에 팔았어요")
    assert found == []


def test_요구받은_인증번호는_짧은_숫자도_가림():
    masked, found = mask_requested_numbers("482913 이요", ["인증번호"])
    assert found == ["인증번호"] and "482913" not in masked
    # 금액이나, 숫자 정보를 요구받지 않은 경우는 건드리지 않음
    assert mask_requested_numbers("30000원 보냈어요", ["인증번호"])[1] == []
    assert mask_requested_numbers("482913", ["이름"])[1] == []


# ---------- JSON 해석 ----------
def test_코드블록이_붙은_JSON도_해석():
    raw = '```json\n{"next_question": "안녕?", "extracted_facts": []}\n```'
    assert parse_json(raw)["next_question"] == "안녕?"


def test_first_question_does_not_require_model_call():
    result = run_context_turn("s", [])
    assert result["next_question"] and not result["done"]


def test_last_message_must_be_user():
    import pytest
    with pytest.raises(ValueError):
        run_context_turn("s", [{"role": "ai", "text": "question"}])
