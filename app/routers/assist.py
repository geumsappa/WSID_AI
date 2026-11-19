"""피해 대응 상담 assist (채팅 추가 질문). Spring SOL-03 QUESTION·CLARIFICATION_ANSWER가 호출해요.

POST /internal/ai/solutions/assist
  PLAN   : 질문 → (추가 확인 질문 | 근거 후보 | 답변 불가)
  ANSWER : Spring이 승인한 근거 안에서만 답변

계약: docs/assist-internal-contract-v1.md
오류: 401 인증, 422 요청 형식, 502 모델 응답 계약 위반, 503 의존 서비스 장애, 504 시간 초과
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import ValidationError

from app.api_errors import error_response
from app.dependencies import require_internal_key
from assist.contracts import AssistFailure, AssistRequest, AssistSuccess
from assist.graph import AssistService
from assist.model import FakeAssistModel, OpenAIAssistModel
from assist.retrieval import ReferenceRetriever
from assist.upstream import error_code, request_id
from rag import config

logger = logging.getLogger("uvicorn.error.assist")
router = APIRouter(prefix="/internal/ai/solutions", tags=["내부 AI (피해 대응)"])


@lru_cache(maxsize=1)
def get_service() -> AssistService:
    model = FakeAssistModel() if config.FAKE else OpenAIAssistModel()
    return AssistService(model, ReferenceRetriever())


@router.post("/assist", response_model=AssistSuccess,
             summary="상담 채팅 추가 질문 (PLAN: 되묻기·근거 후보 / ANSWER: 승인 근거로 답변)",
             responses={
                 401: {"model": AssistFailure, "description": "내부 API 키 누락·불일치"},
                 422: {"model": AssistFailure, "description": "요청 계약 또는 UUID 헤더 오류"},
                 502: {"model": AssistFailure, "description": "모델 응답 계약 위반"},
                 503: {"model": AssistFailure, "description": "내부 키 미설정 또는 의존 서비스 장애"},
                 504: {"model": AssistFailure, "description": "PLAN/ANSWER 전체 처리 시간 초과"},
             },
             dependencies=[Depends(require_internal_key)])
async def assist(body: AssistRequest, x_request_id: Annotated[UUID, Header()],
                 service: Annotated[AssistService, Depends(get_service)]):
    started = time.monotonic()
    status = "FAILED"
    reason = "NONE"
    trace_token = request_id.set(str(x_request_id))
    try:
        # 모델 호출·임베딩·재검색·재시도를 합친 요청 전체 예산.
        timeout = config.ASSIST_PLAN_TIMEOUT_SEC if body.phase == "PLAN" else config.ASSIST_ANSWER_TIMEOUT_SEC
        async with asyncio.timeout(timeout):
            result = await service.run(body)
        status = result.status
        return AssistSuccess(success=True, data=result)
    except TimeoutError:
        reason = "TIMEOUT"
        return error_response(504, "ASSIST_TIMEOUT", "AI 응답 시간이 초과됐어요.")
    except (ValidationError, ValueError):
        reason = "INVALID_AI_RESPONSE"
        return error_response(502, "INVALID_AI_RESPONSE", "AI 응답 형식이 올바르지 않아요.")
    except Exception as error:
        # SDK 예외에는 응답 원문이 들어갈 수 있으므로 예외/traceback도 출력하지 않는다.
        reason = error_code(error)
        return error_response(503, "ASSIST_UNAVAILABLE", "AI 상담을 지금 사용할 수 없어요.")
    finally:
        logger.info("assist request_id=%s phase=%s status=%s reason=%s elapsed_ms=%d",
                    str(x_request_id), body.phase, status, reason,
                    round((time.monotonic() - started) * 1000))
        request_id.reset(trace_token)
