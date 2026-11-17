"""설정값 모음. .env 파일에 적은 값을 읽어옵니다."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # 같은 폴더의 .env 파일을 읽어서 환경변수로 등록


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
    llm_model: str = os.getenv("LLM_MODEL", "").strip()
    llm_base_url: str = os.getenv("LLM_BASE_URL", "").strip()
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "10"))
    max_context_turns: int = int(os.getenv("MAX_CONTEXT_TURNS", "5"))
    # 시나리오는 길게 생성하므로 대기시간을 따로 둠 (훈련 시작 전 로딩 화면 1번)
    scenario_timeout_seconds: float = float(os.getenv("SCENARIO_TIMEOUT_SECONDS", "25"))
    # 가상 훈련 대화 전체 최대 턴 수 (요구사항 초안: 핵심 장면 12턴 / 전체 15턴)
    training_max_turns: int = int(os.getenv("TRAINING_MAX_TURNS", "15"))


settings = Settings()
