"""Generation budget covers all stages/retries without changing other requests."""
import pytest
from app.llm import llm_client as llm


def test_remaining_request_budget_caps_a_stage(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    limits = []
    def provider(system, content, max_tokens, timeout, **kwargs):
        limits.append(timeout)
        return '{}'
    monkeypatch.setattr(llm, "_call_provider", provider)
    with llm.generation_budget(10):
        now[0] += 4
        assert llm.generate_json('test', '{}', timeout=25, attempts=1) == {}
    assert limits == [6]


def test_exhausted_budget_never_calls_provider(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    def forbidden(*args, **kwargs):
        pytest.fail('Expired request must not call provider')
    monkeypatch.setattr(llm, "_call_provider", forbidden)
    with llm.generation_budget(2):
        now[0] += 3
        with pytest.raises(llm.LLMError):
            llm.generate_json('test', '{}', timeout=25)


def test_retry_uses_remaining_budget_and_context_is_reset(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    limits = []
    def provider(system, content, max_tokens, timeout, **kwargs):
        limits.append(timeout)
        if len(limits) == 1:
            now[0] += 6
            raise TimeoutError()
        return '{}'
    monkeypatch.setattr(llm, "_call_provider", provider)
    with llm.generation_budget(10):
        llm.generate_json('test', '{}', timeout=20, attempts=2)
    llm.generate_json('test', '{}', timeout=20, attempts=1)
    assert limits == [10, 4, 20]


def test_nested_budget_cannot_extend_outer_deadline(monkeypatch):
    monkeypatch.setattr(llm.time, "monotonic", lambda: 100.0)
    with llm.generation_budget(5):
        with llm.generation_budget(20):
            assert llm._generation_deadline.get() == 105.0
    assert llm._generation_deadline.get() is None
