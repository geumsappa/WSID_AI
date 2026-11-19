"""모든 내부 API가 X-Internal-Api-Key를 요구하므로 테스트용 키를 공통으로 설정합니다."""
from types import SimpleNamespace

import pytest

INTERNAL_KEY = "test-internal-key"
INTERNAL_HEADERS = {"X-Internal-Api-Key": INTERNAL_KEY}


@pytest.fixture(autouse=True)
def internal_api_key(monkeypatch):
    from app import dependencies
    monkeypatch.setattr(dependencies, "settings", SimpleNamespace(internal_api_key=INTERNAL_KEY))
