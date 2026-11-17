"""SIM-001 상황 파악 대화의 핵심 로직.

흐름:
  1) 대화가 비어있으면 → 첫 질문을 그냥 돌려줌 (LLM 안 씀)
  ※ 사실은 SIM-002 기준으로 카테고리·채널·사칭 신분·요구 행동·위험 요소로 나눠 추출
  2) 마지막 사용자 답변에 민감정보가 있으면 → 종류만 기록 (대화는 그대로 계속)
  3) 프롬프트 + 지금까지 대화(민감정보는 [주민등록번호]처럼 가림)를 LLM 에 보내 JSON 받기
  4) AI 질문이 비어 있으면 기본 질문으로 대체 (질문 내용 자체는 막지 않음)
  5) 질문 수 상한이 되면 종료
LLM 이 실패하면 어느 단계든 기본 질문(fallback)으로 대신합니다.
"""
import logging
from pathlib import Path

from app import fallback
from app.config import settings
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError, generate_json
from app.scenario_data import ALLOWED_REQUESTS

FACT_CATEGORIES = [
    "거래(중고·쇼핑)", "구직·아르바이트", "택배·배송", "금융·대출", "투자",
    "가족·지인 연락", "공공기관·민원", "여행·이동", "기타",
]

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "context_turn.md"


def run_context_turn(session_id: str, history: list[dict]) -> dict:
    """history 예: [{"role": "ai", "text": "..."}, {"role": "user", "text": "..."}]"""
    max_turns = settings.max_context_turns
    user_answer_count = sum(1 for m in history if m["role"] == "user")

    # 1) 아직 아무 대화도 없으면 첫 질문
    if not history:
        return _result(next_question=fallback.FIRST_QUESTION)

    if history[-1]["role"] != "user":
        raise ValueError("history 의 마지막은 사용자(user) 답변이어야 합니다.")

    # 2) 마지막 답변에 민감정보가 있는지 확인
    #    - 대화는 멈추지 않는다 (시뮬레이션이므로)
    #    - '무슨 종류를 입력했는지'만 기록용으로 돌려주고, 실제 값은 가린 글(masked_text)만 돌려준다
    #    - LLM 에도 가린 글만 보낸다 (아래 _build_transcript 에서 처리)
    masked_last, found_types = mask_sensitive(history[-1]["text"])
    if found_types:
        logger.info("session=%s 민감정보 감지: %s", session_id, found_types)  # 종류만 기록, 값은 기록 안 함

    # 3) LLM 호출
    system_prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{USER_ANSWER_COUNT}}", str(user_answer_count))
        .replace("{{MAX_TURNS}}", str(max_turns))
        .replace("{{REQUESTS}}", ", ".join(ALLOWED_REQUESTS))
    )
    transcript = _build_transcript(history)
    reached_limit = user_answer_count >= max_turns

    try:
        data = generate_json(system_prompt, transcript)
        next_question = str(data.get("next_question", "")).strip()
        facts = _clean_facts(data.get("extracted_facts", []))
        enough_info = bool(data.get("enough_info", False))
        real_concern = bool(data.get("real_concern", False))
        used_fallback = False
    except LLMError:
        logger.warning("session=%s LLM 실패 → 기본 질문 사용", session_id)
        next_question = fallback.fallback_question(user_answer_count)
        facts = []
        enough_info = False
        real_concern = False
        used_fallback = True

    # 4) 질문이 비어 있으면 기본 질문으로 (질문 내용은 막지 않음)
    if not next_question:
        next_question = fallback.fallback_question(user_answer_count)
        used_fallback = True

    # 5) 종료 판단
    done = reached_limit or enough_info
    if done:
        next_question = fallback.DONE_MESSAGE

    logger.info("session=%s answers=%d done=%s fallback=%s",
                session_id, user_answer_count, done, used_fallback)
    # 주의: 사용자 대화 원문은 로그에 남기지 않습니다 (팀 개인정보 원칙)

    return _result(
        next_question=next_question,
        extracted_facts=facts,
        sensitive_detected=bool(found_types),
        sensitive_types=found_types,
        masked_text=masked_last,
        done=done,
        real_concern=real_concern,
        fallback_used=used_fallback,
    )


def _build_transcript(history: list[dict]) -> str:
    """대화 목록을 LLM 이 읽기 좋은 대본 형태의 글로 바꾼다. 혹시 모를 민감정보도 한 번 더 가림."""
    lines = []
    for m in history:
        speaker = "AI" if m["role"] == "ai" else "사용자"
        text, _ = mask_sensitive(m["text"])
        text = " ".join(text.split())  # 줄바꿈 제거
        lines.append(f"{speaker}: {text}")
    return (
        "지금까지의 대화입니다.\n\n"
        + "\n".join(lines)
        + "\n\n규칙에 따라 JSON 으로만 응답하세요."
    )


def _clean_facts(raw_facts) -> list[dict]:
    """LLM 이 준 사실 목록을 정리 (SIM-002). 모양이 이상한 건 버리고, 민감정보는 가린다."""
    cleaned = []
    if not isinstance(raw_facts, list):
        return cleaned
    for f in raw_facts[:5]:
        if not isinstance(f, dict):
            continue
        detail = _masked(f.get("detail"), 200)
        if not detail:
            continue
        category = str(f.get("category", "기타")).strip()
        requested = str(f.get("requestedAction") or "").strip()
        risk_factors = f.get("riskFactors") if isinstance(f.get("riskFactors"), list) else []
        cleaned.append({
            "category": category if category in FACT_CATEGORIES else "기타",
            "detail": detail,
            "channel": _masked(f.get("channel"), 30) or None,
            "impersonatedIdentity": _masked(f.get("impersonatedIdentity"), 50) or None,
            "requestedAction": requested if requested in ALLOWED_REQUESTS else None,
            "riskFactors": [x for x in (_masked(r, 50) for r in risk_factors) if x][:5],
        })
    return cleaned


def _masked(value, limit: int) -> str:
    """빈 값/null 은 빈 글자로, 나머지는 민감정보를 가리고 길이 제한."""
    if value is None or str(value).strip().lower() in {"", "null", "none", "없음"}:
        return ""
    text, _ = mask_sensitive(" ".join(str(value).split()))
    return text[:limit]


def _result(next_question: str, extracted_facts=None, sensitive_detected=False,
            sensitive_types=None, masked_text=None, done=False, real_concern=False,
            fallback_used=False) -> dict:
    return {
        "next_question": next_question,
        "extracted_facts": extracted_facts or [],
        "sensitive_detected": sensitive_detected,
        "sensitive_types": sensitive_types or [],
        "masked_text": masked_text,
        "done": done,
        "real_concern": real_concern,
        "fallback_used": fallback_used,
    }
