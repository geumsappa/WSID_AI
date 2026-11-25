"""환경 설정. 값은 .env 또는 환경변수에서 읽어요. 키를 코드에 쓰지 마세요."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # python-dotenv 가 있으면 .env 를 자동으로 읽음
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# 검색 임베딩. local(기본): 이 서버 CPU에서 직접 실행(키·외부 호출 없음) / openai: OpenAI API.
# 공급자·모델·차원을 바꾸면 인덱스를 다시 만들어야 합니다 (python -m rag.index, 서버는 --if-stale).
EMBED_PROVIDER = (os.getenv("EMBED_PROVIDER") or "local").strip().lower()
if EMBED_PROVIDER not in ("openai", "local"):
    raise ValueError("EMBED_PROVIDER must be openai or local")
if EMBED_PROVIDER == "local":
    # 근거 82개·질문 101개 비교에서 1위 정답률이 가장 높았던 모델 (OpenAI 3-small 60% → 84%)
    EMBED_MODEL = (os.getenv("LOCAL_EMBED_MODEL") or "").strip() or "intfloat/multilingual-e5-large"
    EMBED_DIM = int((os.getenv("LOCAL_EMBED_DIM") or "").strip() or "1024")
else:
    EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    EMBED_DIM = int(os.getenv("OPENAI_EMBED_DIM", "1536"))
# 모델 호출 대기 상한(초)
TIMEOUT_SEC = int(os.getenv("RAG_TIMEOUT_SEC", "30"))
# assist의 각 HTTP 단계 예산. CLI/OpenAI 기본 timeout과 독립적이다.
ASSIST_PLAN_TIMEOUT_SEC = float(os.getenv("ASSIST_PLAN_TIMEOUT_SEC", "15"))
ASSIST_ANSWER_TIMEOUT_SEC = float(os.getenv("ASSIST_ANSWER_TIMEOUT_SEC", "15"))
ASSIST_MAX_RESEARCHES = int(os.getenv("ASSIST_MAX_RESEARCHES", "1"))
ASSIST_RETRY_MIN_SECONDS = float(os.getenv("ASSIST_RETRY_MIN_SECONDS", "7"))
ASSIST_LLM_MODEL = os.getenv("ASSIST_LLM_MODEL", "").strip()
ASSIST_REASONING_EFFORT = os.getenv("ASSIST_REASONING_EFFORT", "").strip().lower()
if not 0 <= ASSIST_MAX_RESEARCHES <= 2 or not 0 < ASSIST_RETRY_MIN_SECONDS < float("inf"):
    raise ValueError("invalid assist retry settings")
if ASSIST_REASONING_EFFORT not in ("", "low"):
    raise ValueError("ASSIST_REASONING_EFFORT must be empty or low")
if not (0 < ASSIST_PLAN_TIMEOUT_SEC < float("inf") and 0 < ASSIST_ANSWER_TIMEOUT_SEC < float("inf")):
    raise ValueError("assist timeout must be a finite positive number")
# 1 이면 외부 모델을 부르지 않는 가짜 임베딩·가짜 설명으로 동작 (키 없이 파이프라인 확인용)
FAKE = os.getenv("RAG_FAKE", "0") == "1"


def embed_provider() -> str:
    """인덱스 meta에 적는 임베딩 공급자. 만들 때와 읽을 때 값이 다르면 인덱스를 거절해요."""
    return "fake" if FAKE else EMBED_PROVIDER

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
