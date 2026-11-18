"""AI 서버 시작점. 실행: python -m uvicorn app.main:app --reload"""
import logging

from fastapi import FastAPI

from app.config import settings
from app.routers import sim, solutions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="sim-ai-server (어떡하지 AI 서버)", version="0.1.0")
app.include_router(sim.router)
app.include_router(solutions.router)


@app.get("/health", tags=["상태 확인"])
def health():
    """서버가 살아있는지 확인용. Spring 이나 Docker 가 이 주소로 확인합니다."""
    return {"status": "ok", "llmProvider": settings.llm_provider}
