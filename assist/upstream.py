"""Gemini 호출 재시도와 원문 없는 오류 분류."""
import asyncio
from contextvars import ContextVar
import logging

from google.genai.errors import APIError
import httpx
import httpx2

request_id = ContextVar("assist_request_id", default="-")
logger = logging.getLogger("uvicorn.error.assist")
RETRYABLE = {429, 500, 503}


class ReferenceIndexUnavailable(RuntimeError):
    pass


def error_code(error: Exception) -> str:
    if isinstance(error, ReferenceIndexUnavailable):
        return "INDEX_UNAVAILABLE"
    if isinstance(error, APIError) and type(error.code) is int and 100 <= error.code <= 599:
        return f"GEMINI_{error.code}"
    if isinstance(error, (TimeoutError, httpx.TimeoutException, httpx2.TimeoutException)):
        return "UPSTREAM_TIMEOUT"
    if isinstance(error, (ConnectionError, httpx.TransportError, httpx2.TransportError)):
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
    except APIError as error:
        if error.code not in RETRYABLE:
            raise
        logger.warning("assist request_id=%s stage=%s retry=1 upstream=%s",
                       request_id.get(), stage, error_code(error))
        await asyncio.sleep(0.25)
        return await operation()
