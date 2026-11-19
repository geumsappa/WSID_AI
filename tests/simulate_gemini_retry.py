"""Run against local Postman simulation; synthetic data only, no real Gemini credentials."""
import logging
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AI_API_KEY"] = "synthetic-internal-key"
os.environ["RAG_FAKE"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from app.main import app  # noqa: E402
from app.routers.assist import get_service  # noqa: E402
from assist.graph import AssistService  # noqa: E402
from assist.model import GeminiAssistModel  # noqa: E402
from rag import gemini  # noqa: E402

base = os.environ.get("SIMULATION_BASE", "http://127.0.0.1:8768/gemini")
gemini._client = genai.Client(api_key="synthetic-provider-key", http_options=types.HttpOptions(
    base_url=base, api_version="v1beta", retry_options=types.HttpRetryOptions(attempts=1)))
app.dependency_overrides[get_service] = lambda: AssistService(GeminiAssistModel(), object())
body = {"phase":"PLAN", "question":"앱 설치를 확인할까요?", "questionIntent":None,
        "facts":{"transferredMoney":"YES", "sharedInformation":"NO", "installedApp":"UNKNOWN",
                 "contactContinues":"UNKNOWN"}, "askedFactKeys":[], "remainingClarifications":3}
headers = {"X-Internal-Api-Key":"synthetic-internal-key", "X-Request-Id":str(uuid4())}

class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []
    def emit(self, record):
        self.messages.append(record.getMessage())

capture = LogCapture()
logger = logging.getLogger('uvicorn.error.assist')
logger.addHandler(capture)
logger.setLevel(logging.INFO)
try:
    with TestClient(app) as client:
        for code, failures, expected_status, expected_attempts in [(429,1,200,2),(503,1,200,2),(500,2,503,2),(400,2,503,1)]:
            httpx.post(base + '/control/reset', json={'status':code,'failures':failures}).raise_for_status()
            response = client.post('/internal/ai/solutions/assist', headers=headers, json=body)
            attempts = httpx.get(base + '/control/status').json()['attempts']
            assert response.status_code == expected_status and attempts == expected_attempts
            if expected_status == 200:
                assert response.json()['data']['status'] == 'NEED_USER_INPUT'
            else:
                assert f'GEMINI_{code}' in '\n'.join(capture.messages)
            assert 'SYNTHETIC_PRIVATE_MARKER' not in response.text + '\n'.join(capture.messages)
            print(f'{code}: HTTP {response.status_code}, upstream attempts {attempts}; private marker absent')
finally:
    logger.removeHandler(capture)
    app.dependency_overrides.clear()
    gemini._client.close()
