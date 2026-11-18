import io
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
    with TestClient(app) as test_client:
        yield test_client


def test_ocr_returns_text_or_filters_sensitive_output(client, monkeypatch):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="PNG")
    files = {"images": ("test.png", image.getvalue(), "image/png")}

    assert client.post(OCR, files=files).status_code == 401
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "거래 안내 문구")
    response = client.post(OCR, files=files, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"recognizedText": "거래 안내 문구", "usable": True}}

    monkeypatch.setattr(ocr, "_recognize", lambda *_: "테스트 전화 010-1234-5678")
    assert client.post(OCR, files=files, headers=HEADERS).json()["data"] == {"recognizedText": "", "usable": False}
    monkeypatch.setattr(ocr, "_recognize", lambda *_: "")
    response = client.post(OCR, files=files, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OCR_FAILED"
