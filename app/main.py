"""AI 서버 시작점. 실행: python -m uvicorn app.main:app --reload"""
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api_errors import AiApiError, handle_ai_error, handle_validation
from app.config import settings
from app.routers import ocr, sim

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="sim-ai-server (어떡하지 AI 서버)", version="0.1.0")
app.include_router(sim.router)
app.include_router(ocr.router)
app.add_exception_handler(AiApiError, handle_ai_error)
app.add_exception_handler(RequestValidationError, handle_validation)


@app.get("/health", tags=["상태 확인"])
def health():
    """서버가 살아있는지 확인용. Spring 이나 Docker 가 이 주소로 확인합니다."""
    return {"status": "ok", "llmProvider": settings.llm_provider}
