from hmac import compare_digest
from typing import Annotated

from fastapi import Header

from app.api_errors import AiApiError
from app.config import settings


def require_internal_key(
    key: Annotated[str | None, Header(alias="X-Internal-Api-Key")] = None,
) -> None:
    if not settings.internal_api_key:
        raise AiApiError(503, "AI_SERVICE_UNAVAILABLE", "내부 AI 인증 설정을 확인해 주세요.")
    if not key or not compare_digest(key.encode(), settings.internal_api_key.encode()):
        raise AiApiError(401, "UNAUTHORIZED", "내부 API 인증에 실패했어요.")
