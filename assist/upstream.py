"""OpenAI 호출 재시도와 원문 없는 오류 분류."""
import asyncio
from contextvars import ContextVar
import logging

from openai import APIStatusError, APITimeoutError, APIConnectionError
import httpx

request_id = ContextVar("assist_request_id", default="-")
logger = logging.getLogger("uvicorn.error.assist")
RETRYABLE = {429, 500, 503}


class ReferenceIndexUnavailable(RuntimeError):
    pass


def error_code(error: Exception) -> str:
    if isinstance(error, ReferenceIndexUnavailable):
        return "INDEX_UNAVAILABLE"
    if isinstance(error, APIStatusError) and 100 <= error.status_code <= 599:
        return f"OPENAI_{error.status_code}"
    if isinstance(error, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
        return "UPSTREAM_TIMEOUT"
    if isinstance(error, (ConnectionError, httpx.TransportError, APIConnectionError)):
        return "NETWORK_ERROR"
    for kind, code in ((KeyError, "KEY_ERROR"), (TypeError, "TYPE_ERROR"),
                       (AttributeError, "ATTRIBUTE_ERROR"), (RuntimeError, "RUNTIME_ERROR"),
                       (OSError, "IO_ERROR")):
        if isinstance(error, kind):
            return code
    return "INTERNAL_ERROR"


async def call(operation, stage: str):
    """지정된 HTTP 상태만 한 번 재시도. 전체 시간 예산은 호출자의 asyncio.timeout 적용."""
    try:
        return await operation()
    except APIStatusError as error:
        if error.status_code not in RETRYABLE:
            raise
        logger.warning("assist request_id=%s stage=%s retry=1 upstream=%s",
                       request_id.get(), stage, error_code(error))
        await asyncio.sleep(0.25)
        return await operation()
