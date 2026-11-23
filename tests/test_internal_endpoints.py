import io
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import dependencies
from app.main import app
from app.services import ocr

HEADERS = {"X-Internal-Api-Key": "test-internal-key"}
OCR = "/internal/ai/ocr/extract"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dependencies, "settings", SimpleNamespace(internal_api_key="test-internal-key"))
    monkeypatch.setattr("app.main.initialize_ocr", lambda: None)
    with TestClient(app) as test_client:
        yield test_client


def test_ocr_returns_text_with_sensitive_output(client, monkeypatch):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="PNG")
    files = {"images": ("test.png", image.getvalue(), "image/png")}

    assert client.post(OCR, files=files).status_code == 401
    monkeypatch.setattr(ocr, "_classify", lambda _: ("SMS", "거래 안내입니다."))
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "거래 안내 문구")
    response = client.post(OCR, files=files, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"recognizedText": "거래 안내 문구", "usable": True, "imageType": "SMS",
                                                         "summary": "거래 안내입니다.", "maskedText": "거래 안내 문구"}}

    # 원문은 그대로, maskedText는 전화번호·링크·인증번호 뒤 숫자를 가린 글자
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "테스트 전화 010-1234-5678 http://bad.kr/a 인증번호 482913")
    data = client.post(OCR, files=files, headers=HEADERS).json()["data"]
    assert data["recognizedText"] == "테스트 전화 010-1234-5678 http://bad.kr/a 인증번호 482913"
    assert "010-1234-5678" not in data["maskedText"] and "bad.kr" not in data["maskedText"]
    assert "482913" not in data["maskedText"] and "[전화번호]" in data["maskedText"] and "[링크]" in data["maskedText"]
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "")
    response = client.post(OCR, files=files, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OCR_FAILED"


def test_ocr_classifier_failure_keeps_text(client, monkeypatch):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="PNG")
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "송금했습니다")
    def fail(_):
        raise RuntimeError("model failure")
    monkeypatch.setattr(ocr, "_classify", fail)
    result = client.post(OCR, files={"images": ("test.png", image.getvalue(), "image/png")}, headers=HEADERS)
    assert result.json()["data"] == {"recognizedText": "송금했습니다", "usable": True, "imageType": "OTHER", "summary": None,
                                     "maskedText": "송금했습니다"}


def test_ocr_classifier_timeout_keeps_text(client, monkeypatch):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="PNG")
    release = threading.Event()
    finished = threading.Event()
    def slow(_):
        try:
            release.wait(2)
            return "SMS", "요약"
        finally:
            finished.set()
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "문자 내용")
    monkeypatch.setattr(ocr, "_classify", slow)
    monkeypatch.setattr(ocr, "CLASSIFY_TIMEOUT_SECONDS", 0.01)
    try:
        result = client.post(OCR, files={"images": ("test.png", image.getvalue(), "image/png")}, headers=HEADERS)
        assert result.json()["data"] == {"recognizedText": "문자 내용", "usable": True, "imageType": "OTHER", "summary": None,
                                         "maskedText": "문자 내용"}
    finally:
        release.set()
        assert finished.wait(2)


def test_ocr_classification_masks_input_and_checks_output(monkeypatch):
    captured = []
    def fake(_system, prompt, _fake_result, *, model, timeout):
        captured.append(prompt)
        assert timeout == ocr.CLASSIFY_TIMEOUT_SECONDS
        return {"imageType": "INVALID", "summary": "연락처 010-1234-5678"}
    monkeypatch.setattr(ocr, "generate_json", fake)
    result = ocr._classify("전화 010-1234-5678 링크 https://example.com/path 인증번호 482913")
    assert result == ("OTHER", None)
    assert all(secret not in captured[0] for secret in ("010-1234-5678", "example.com", "482913"))


def test_ocr_classification_uses_openai_settings(monkeypatch):
    from rag import config as rag_config, openai_client

    captured = {}
    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            captured["request"] = kwargs
            message = SimpleNamespace(content='{"imageType":"SMS","summary":"배송 안내 문자입니다."}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        def close(self):
            pass

    monkeypatch.setattr(rag_config, "FAKE", False)
    monkeypatch.setattr(openai_client, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(openai_client, "settings", SimpleNamespace(
        llm_api_key="test-key", llm_model="test-model", llm_base_url=""))
    monkeypatch.setattr(ocr, "settings", SimpleNamespace(ocr_classify_model=""))
    assert ocr._classify("배송 안내 010-1234-5678") == ("SMS", "배송 안내 문자입니다.")
    assert captured["request"]["model"] == "test-model"
    assert captured["client"]["api_key"] == "test-key"
    assert captured["client"]["timeout"] == ocr.CLASSIFY_TIMEOUT_SECONDS
    assert "010-1234-5678" not in captured["request"]["messages"][1]["content"]

    monkeypatch.setattr(ocr, "settings", SimpleNamespace(ocr_classify_model="fast-model"))
    ocr._classify("배송 안내")
    assert captured["request"]["model"] == "fast-model"


def test_ocr_ordered_text():
    assert ocr._ordered_text(["오른쪽", "아래", "왼쪽"], [[100, 10, 160, 30], [5, 50, 40, 70], [5, 12, 50, 32]]) == "왼쪽 오른쪽\n아래"


def _png():
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="PNG")
    return {"images": ("test.png", image.getvalue(), "image/png")}


def test_paddle_load_failure_keeps_server_up_and_ocr_returns_503(monkeypatch):
    import sys
    monkeypatch.setattr(dependencies, "settings", SimpleNamespace(internal_api_key="test-internal-key"))
    monkeypatch.setattr(ocr, "settings", SimpleNamespace(ocr_engine="paddle"))
    monkeypatch.setattr(ocr, "_paddle", None)
    monkeypatch.setitem(sys.modules, "paddleocr", None)  # 설치 안 된 환경 흉내 → ImportError
    ocr.initialize_ocr()  # 예외 없이 끝나야 함
    with TestClient(app) as test_client:  # 실제 lifespan으로 서버 기동
        assert test_client.get("/health").status_code == 200
        response = test_client.post(OCR, files=_png(), headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


def test_invalid_engine_does_not_crash_startup(monkeypatch):
    monkeypatch.setattr(ocr, "settings", SimpleNamespace(ocr_engine="unknown"))
    ocr.initialize_ocr()


def test_ocr_text_is_cut_to_1000_chars(client, monkeypatch):
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "가" * 1500)
    monkeypatch.setattr(ocr, "_classify", lambda text: ("OTHER", None) if len(text) == 1000 else ("SMS", "길이 오류"))
    data = client.post(OCR, files=_png(), headers=HEADERS).json()["data"]
    assert data["recognizedText"] == "가" * 1000
    assert data["imageType"] == "OTHER"  # 분류에도 잘린 텍스트가 전달됨


def test_busy_slot_waits_briefly_then_runs():
    from concurrent.futures import ThreadPoolExecutor
    pool, slot = ThreadPoolExecutor(max_workers=1), threading.BoundedSemaphore(1)
    slot.acquire()
    threading.Timer(0.2, slot.release).start()
    future = ocr._submit_bounded(pool, slot, lambda: "ok", wait=2)
    assert future is not None and future.result(timeout=1) == "ok"


def test_busy_slot_gives_up_after_wait():
    from concurrent.futures import ThreadPoolExecutor
    pool, slot = ThreadPoolExecutor(max_workers=1), threading.BoundedSemaphore(1)
    slot.acquire()
    try:
        assert ocr._submit_bounded(pool, slot, lambda: "ok", wait=0.05) is None
    finally:
        slot.release()
