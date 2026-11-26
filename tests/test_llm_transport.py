from dataclasses import replace
from types import SimpleNamespace

from app.llm import llm_client


def test_openai_transport_does_not_force_unsupported_temperature(monkeypatch):
    import openai
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        assert "temperature" not in kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(llm_client, "settings", replace(llm_client.settings, llm_provider="openai", llm_api_key="test-key", llm_model="test-model"))
    assert llm_client.generate_json("Return JSON", "{}", attempts=1) == {"ok": True}
    assert captured["model"] == "test-model"


def test_discovery_reuses_connections_without_changing_each_call_timeout(monkeypatch):
    import openai
    constructed, requests = [], []
    def client(**kwargs):
        constructed.append(kwargs)
        def create(**request):
            requests.append(request)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai, "OpenAI", client)
    monkeypatch.setattr(llm_client, "settings", replace(llm_client.settings, llm_provider="openai", llm_api_key="connection-test", llm_model="test-model"))
    llm_client._reusable_openai_client.cache_clear()
    try:
        llm_client.generate_json("JSON", "{}", timeout=3, attempts=1, reasoning_effort="none", reuse_connection=True)
        llm_client.generate_json("JSON", "{}", timeout=5, attempts=1, reasoning_effort="none", reuse_connection=True)
        assert len(constructed) == 1
        assert [request["timeout"] for request in requests] == [3, 5]
        assert all(request["reasoning_effort"] == "none" for request in requests)
    finally:
        llm_client._reusable_openai_client.cache_clear()
        for config in constructed:
            config["http_client"].close()
