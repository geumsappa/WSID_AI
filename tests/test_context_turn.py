"""자동 테스트. 실행: python -m pytest  (LLM_PROVIDER=mock 상태에서)

코드를 고친 뒤 이걸 돌려서 전부 통과하면, 기존 기능을 망가뜨리지 않았다는 뜻입니다.
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


# ---------- 전체 흐름 (mock LLM) ----------
def test_첫_질문():
    r = run_context_turn("s1", [])
    assert r["next_question"] and not r["done"]


def test_일반_답변이면_다음_질문과_사실():
    history = [
        {"role": "ai", "text": "최근에 뭐 하셨어요?"},
        {"role": "user", "text": "당근마켓에서 자전거 팔았어요"},
    ]
    r = run_context_turn("s1", history)
    assert not r["sensitive_detected"]
    assert r["extracted_facts"][0]["category"] == "거래(중고·쇼핑)"


def test_민감정보가_있어도_대화는_계속되고_종류만_기록():
    history = [
        {"role": "ai", "text": "최근에 뭐 하셨어요?"},
        {"role": "user", "text": "택배 문자 와서 010-1234-5678로 연락했어요"},
    ]
    r = run_context_turn("s1", history)
    assert r["sensitive_detected"]
    assert r["sensitive_types"] == ["전화번호"]
    assert "다시 입력" not in r["next_question"]       # 재입력 안내 없음
    assert "1234" not in r["masked_text"]              # 실제 값은 돌려주지 않음
    assert "[전화번호]" in r["masked_text"]
    assert r["extracted_facts"]                         # 사실 추출도 정상 진행


def test_민감정보_없으면_masked_text는_원문과_같음():
    history = [
        {"role": "ai", "text": "최근에 뭐 하셨어요?"},
        {"role": "user", "text": "택배 기다리고 있어요"},
    ]
    r = run_context_turn("s1", history)
    assert not r["sensitive_detected"]
    assert r["masked_text"] == "택배 기다리고 있어요"


def test_질문_상한이어도_정보가_없으면_준비완료가_아님():
    history = []
    for i in range(5):
        history += [{"role": "ai", "text": f"질문{i}"}, {"role": "user", "text": f"답변{i}"}]
    r = run_context_turn("s1", history)
    assert not r["done"]


# ---------- SIM-002 항목별 사실 추출 ----------
import app.services.context_turn as context_service  # noqa: E402


def test_1단계는_AI_질문을_막지_않음(monkeypatch):
    q = "문자에서 계좌번호를 알려달라고 하던가요?"
    monkeypatch.setattr(context_service, "generate_json", lambda *a, **k: {"next_question": q, "extracted_facts": []})
    r = run_context_turn("s1", [{"role": "ai", "text": "?"}, {"role": "user", "text": "문자 받았어요"}])
    assert r["next_question"] == q and not r["fallback_used"]


def test_수상한_문자_이야기에서_채널_사칭_요구행동_추출():
    history = [
        {"role": "ai", "text": "최근에 받은 문자 있으세요?"},
        {"role": "user", "text": "오늘 택배 반송된다고 링크 누르라는 문자 받았어요"},
    ]
    r = run_context_turn("s1", history)
    f = r["extracted_facts"][0]
    assert f["category"] == "택배·배송"
    assert f["channel"] == "문자"
    assert f["impersonatedIdentity"] == "택배사"
    assert f["requestedAction"] == "링크 클릭"
    assert "출처 불명 링크" in f["riskFactors"]


def test_추출_항목_정리_허용값만_남기고_개인정보는_가림(monkeypatch):
    monkeypatch.setattr(context_service, "generate_json", lambda *a, **k: {
        "next_question": "그 문자는 누구라고 하던가요?",
        "extracted_facts": [{
            "category": "이상한카테고리", "detail": "010-1234-5678 번호로 문자 받음",
            "channel": "문자", "impersonatedIdentity": "null",
            "requestedAction": "춤추기", "riskFactors": ["시간 압박", ""],
        }],
        "real_concern": True,
    })
    history = [{"role": "ai", "text": "?"}, {"role": "user", "text": "문자 받았어요"}]
    r = run_context_turn("s1", history)
    f = r["extracted_facts"][0]
    assert f["category"] == "기타"
    assert "1234" not in f["detail"]
    assert f["impersonatedIdentity"] is None
    assert f["requestedAction"] is None
    assert f["riskFactors"] == ["시간 압박"]
    assert r["real_concern"] is True
