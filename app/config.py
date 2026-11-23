"""설정값 모음. .env 파일에 적은 값을 읽어옵니다."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # 같은 폴더의 .env 파일을 읽어서 환경변수로 등록


@dataclass(frozen=True)
class Settings:
    internal_api_key: str = os.getenv("AI_API_KEY", "").strip()
    ocr_engine: str = os.getenv("OCR_ENGINE", "paddle").strip().lower()
    # 사진 분류·요약 전용 모델. 비우면 LLM_MODEL 사용 (빠른 모델로 따로 지정 가능)
    ocr_classify_model: str = os.getenv("OCR_CLASSIFY_MODEL", "").strip()
    # 사진 분류·요약 대기(초). 넘으면 imageType=OTHER, summary=null로 대체
    ocr_classify_timeout_seconds: float = float(os.getenv("OCR_CLASSIFY_TIMEOUT_SECONDS", "10"))
    # 다른 OCR 요청이 처리 중일 때 기다리는 최대 시간(초). OCR 15초 예산 안에서 차감
    ocr_queue_wait_seconds: float = float(os.getenv("OCR_QUEUE_WAIT_SECONDS", "5"))
    # 사진 전체 글자 인식 제한(대기 포함). CPU가 느리면 사진 한 장에 15초를 넘길 수 있어 환경별로 조정.
    # Spring AI_OCR_READ_TIMEOUT은 이 값 + 분류 대기·분류 시간보다 길어야 해요.
    ocr_timeout_seconds: float = float(os.getenv("OCR_TIMEOUT_SECONDS", "15"))
    ocr_executable: str = os.getenv("OCR_EXECUTABLE", "tesseract").strip()
    ocr_languages: str = os.getenv("OCR_LANGUAGES", "kor+eng").strip()
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
    llm_model: str = os.getenv("LLM_MODEL", "").strip()
    llm_base_url: str = os.getenv("LLM_BASE_URL", "").strip()
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "10"))
    # 피해 대응 RAG 검색용 임베딩 키 (Claude 에는 임베딩 API 가 없어요).
    # 비우면 LLM_PROVIDER=openai 일 때만 LLM_API_KEY 를 쓰고, 그 밖에는 글자 겹침 검색으로 대신해요.
    openai_api_key: str = (os.getenv("OPENAI_API_KEY", "").strip()
                           or (os.getenv("LLM_API_KEY", "").strip()
                               if os.getenv("LLM_PROVIDER", "mock").strip().lower() == "openai" else ""))
    # 상황 파악 질문용 모델 (비우면 LLM_MODEL). 질문 만들기는 가벼운 일이라 claude-haiku-5-5 처럼 싼 모델로 충분해요
    discovery_model: str = os.getenv("DISCOVERY_MODEL", "").strip()
    # Claude 대사 다양성 (training_prompt_notes.md §5-3 권장 0.6~0.8)
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    max_context_turns: int = int(os.getenv("MAX_CONTEXT_TURNS", "5"))
    planning_timeout_seconds: float = float(os.getenv("PLANNING_TIMEOUT_SECONDS", "25"))
    verification_timeout_seconds: float = float(os.getenv("VERIFICATION_TIMEOUT_SECONDS", "20"))
    pipeline_reasoning_effort: str = os.getenv("PIPELINE_REASONING_EFFORT", "").strip()
    discovery_reasoning_effort: str = os.getenv("DISCOVERY_REASONING_EFFORT", "").strip()
    # 상황 답변: 호출 60초, 재시도·교정 포함 110초. BE 120초·FE 130초와 함께 조정해요.
    discovery_timeout_seconds: float = float(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "60"))
    discovery_total_timeout_seconds: float = float(os.getenv("DISCOVERY_TOTAL_TIMEOUT_SECONDS", "110"))
    # 시나리오는 길게 생성하므로 대기시간을 따로 둠 (훈련 시작 전 로딩 화면 1번)
    scenario_total_timeout_seconds: float = float(os.getenv("SCENARIO_TOTAL_TIMEOUT_SECONDS", "110"))
    scenario_timeout_seconds: float = float(os.getenv("SCENARIO_TIMEOUT_SECONDS", "25"))
    solution_reply_timeout_seconds: float = float(os.getenv("SOLUTION_REPLY_TIMEOUT_SECONDS", "12"))
    evaluation_timeout_seconds: float = float(os.getenv("EVALUATION_TIMEOUT_SECONDS", "25"))
    # 가상 훈련 대화 전체 최대 턴 수 (요구사항 초안: 핵심 장면 12턴 / 전체 15턴)
    training_max_turns: int = int(os.getenv("TRAINING_MAX_TURNS", "15"))
    # 이 답변 수 전에는 시나리오가 끝나도 종료하지 않고 대화를 이어가요 (사용자가 확실히 끊으면 예외)
    training_min_turns: int = int(os.getenv("TRAINING_MIN_TURNS", "6"))


settings = Settings()
