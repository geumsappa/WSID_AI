"""환경 설정. 값은 .env 또는 환경변수에서 읽어요. 키를 코드에 쓰지 마세요."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # python-dotenv 가 있으면 .env 를 자동으로 읽음
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# 임베딩 모델: 텍스트 전용이면 gemini-embedding-001 로 충분
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.getenv("GEMINI_EMBED_DIM", "768"))
# 답변 생성 모델: 쓸 수 있는 이름은 `python -m rag.list_models` 로 확인 후 .env 에서 바꾸세요
CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-3.8-flash")
# 기본 모델이 503(과부하)·429(사용량 초과)일 때 차례로 쓸 예비 모델 (쉼표로 구분)
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
# Gemini 한 번 호출에 기다리는 최대 시간(초)
TIMEOUT_SEC = int(os.getenv("RAG_TIMEOUT_SEC", "30"))
# 1 이면 Gemini 를 부르지 않는 가짜 임베딩·가짜 설명으로 동작 (키 없이 파이프라인 확인용)
FAKE = os.getenv("RAG_FAKE", "0") == "1"

REFERENCES_PATH = ROOT / "data" / "references" / "references.jsonl"
INDEX_DIR = ROOT / "data" / "index"

# Spring AI-07 계약 (SolutionExplainRequest / VerifiedReference / SolutionExplainResult)
FACT_KEYS = ("transferredMoney", "contactContinues", "sharedInformation", "installedApp")
FACT_VALUES = ("YES", "NO", "UNKNOWN")
MAX_REFERENCES = 10
MAX_TITLE = 200
MAX_EXCERPT = 2000
MAX_EXPLANATION = 2000
CODE_PATTERN = r"[A-Z0-9_]{1,100}"
