import asyncio
import logging

from google.genai.errors import APIError
import httpx
import pytest

from assist.upstream import call, error_code, request_id


@pytest.mark.parametrize("code", [429, 500, 503])
def test_transient_error_is_retried_once_without_exposing_message(code, caplog):
    attempts = []

    async def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise APIError(code, {"error": {"message": "PRIVATE_PROMPT_MARKER"}})
        return "ok"

    token = request_id.set("test-request")
    try:
        with caplog.at_level(logging.WARNING):
            assert asyncio.run(call(operation, "generation")) == "ok"
    finally:
        request_id.reset(token)
    assert len(attempts) == 2
    assert f"GEMINI_{code}" in caplog.text and "test-request" in caplog.text
    assert "PRIVATE_PROMPT_MARKER" not in caplog.text


@pytest.mark.parametrize("code,attempts_expected", [(400, 1), (401, 1), (429, 2), (500, 2), (503, 2)])
def test_failed_call_is_bounded(code, attempts_expected):
    attempts = []

    async def operation():
        attempts.append(1)
        raise APIError(code, {"error": {"message": "hidden"}})

    with pytest.raises(APIError):
        asyncio.run(call(operation, "embedding"))
    assert len(attempts) == attempts_expected


def test_timeout_and_network_errors_have_only_safe_codes():
    assert error_code(httpx.ReadTimeout("PRIVATE")) == "UPSTREAM_TIMEOUT"
    assert error_code(httpx.ConnectError("PRIVATE")) == "NETWORK_ERROR"
    assert error_code(RuntimeError("PRIVATE")) == "RUNTIME_ERROR"
    assert error_code(KeyError("PRIVATE")) == "KEY_ERROR"
    assert error_code(TypeError("PRIVATE")) == "TYPE_ERROR"


def test_retry_respects_cancellation_deadline():
    attempts = []

    async def operation():
        attempts.append(1)
        raise APIError(429, {"error": {"message": "hidden"}})

    async def run():
        async with asyncio.timeout(0.01):
            await call(operation, "generation")

    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert len(attempts) == 1
