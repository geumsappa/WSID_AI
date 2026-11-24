"""AI 서버 시작점. 실행: python -m uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from app.api_errors import AiApiError, error_response, handle_ai_error, handle_validation
from app.config import settings
from app.routers import assist, ocr, sim, solutions
from app.services.ocr import initialize_ocr

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_ocr()
    yield


app = FastAPI(title="sim-ai-server (어떡하지 AI 서버)", version="0.1.0", lifespan=lifespan)
app.include_router(sim.router)
app.include_router(ocr.router)
app.include_router(solutions.router)
app.include_router(assist.router)
app.add_exception_handler(AiApiError, handle_ai_error)
app.add_exception_handler(RequestValidationError, handle_validation)

_error_logger = logging.getLogger("app.errors")


@app.middleware("http")
async def hide_unexpected_errors(request: Request, call_next):
    """예상 못 한 오류가 나도 로그에 사용자 입력이 찍히지 않게 해요.
    기본 오류 출력(traceback)은 오류 메시지를 그대로 보여 주는데, 검증 오류 메시지에는 입력값이 들어 있을 수 있어요.
    그래서 오류 종류·주소·코드 위치만 남겨요."""
    try:
        return await call_next(request)
    except Exception as error:  # noqa: BLE001 - 모든 예상 밖 오류를 같은 방식으로 감춰요
        frame = error.__traceback__
        while frame and frame.tb_next:
            frame = frame.tb_next
        where = f"{frame.tb_frame.f_code.co_filename.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}:{frame.tb_lineno}" if frame else "?"
        _error_logger.error("예상 못 한 오류 path=%s type=%s at=%s", request.url.path, type(error).__name__, where)
        return error_response(500, "AI_INTERNAL_ERROR", "AI 서버에서 오류가 났어요.")


@app.get("/health", tags=["상태 확인"])
def health():
    """서버가 살아있는지 확인용. Spring 이나 Docker 가 이 주소로 확인합니다."""
    return {"status": "ok", "llmProvider": settings.llm_provider}
