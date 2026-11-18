"""SIM-02 위험 분석 + 위험 유형 분류 + 개인화 시나리오 생성 (SIM-003/004/005/006).

흐름:
  1) ①단계에서 모인 사실을 받음 (혹시 모를 개인정보는 한 번 더 가림)
  2) 사실이 없으면 → 기본 시나리오 (LLM 안 씀)
  3) 프롬프트 + 사실 목록을 LLM 에 보내 시나리오 JSON 받기
  4) 가드레일 검사 (모양 + 실존 기관명·URL·전화번호·보이스피싱 차단)
  5) 통과 못 하면 1번 더 생성 → 그래도 안 되면 사실에 맞는 기본 시나리오로 대체
"""
import copy
import logging
from pathlib import Path

from app.case_db import cases_for_prompt
from app.config import settings
from app.guard.scenario_guard import validate_and_normalize
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError, generate_json
from app.scenario_data import (
    ALLOWED_CHANNELS,
    ALLOWED_MOCK_PAGES,
    ALLOWED_REQUESTS,
    FALLBACK_SCENARIOS,
    RISK_TYPES,
    SUBTYPE_TO_RISK_TYPE,
    pick_subtype,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "scenario.md"
MAX_ATTEMPTS = 2  # 생성 + 검사 실패 시 1번 더


def run_scenario(session_id: str, facts: list[dict]) -> dict:
    """facts 예: [{"category": "거래(중고·쇼핑)", "detail": "중고거래 앱으로 자전거 판매"}]"""
    facts = _clean_input_facts(facts)

    if not facts:
        logger.info("session=%s 사실 없음 → 기본 시나리오", session_id)
        return _fallback(facts)

    system_prompt = _build_prompt()
    user_content = "사용자가 앞선 대화에서 말한 사실 목록입니다.\n\n" + "\n".join(
        _fact_line(f) for f in facts
    ) + "\n\n규칙에 따라 JSON 으로만 응답하세요."

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = generate_json(system_prompt, user_content, max_tokens=2500,
                                 timeout=settings.scenario_timeout_seconds, attempts=1)
        except LLMError:
            logger.warning("session=%s 시나리오 LLM 실패 (시도 %d)", session_id, attempt)
            continue

        result, errors = validate_and_normalize(data)
        if result:
            result["scenario"]["maxTurns"] = settings.training_max_turns
            result["fallback_used"] = False
            logger.info("session=%s 시나리오 생성: %s", session_id, result["scenario"]["fraudSubtype"])
            return result
        logger.warning("session=%s 시나리오 가드레일 불통과 (시도 %d): %s", session_id, attempt, errors)

    logger.warning("session=%s 기본 시나리오로 대체", session_id)
    return _fallback(facts)


def _build_prompt() -> str:
    return (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{RISK_TYPES}}", ", ".join(RISK_TYPES))
        .replace("{{SUBTYPES}}", "\n".join(f"  - {sub} → 대분류 {rt}" for sub, rt in SUBTYPE_TO_RISK_TYPE.items()))
        .replace("{{CHANNELS}}", ", ".join(ALLOWED_CHANNELS))
        .replace("{{REQUESTS}}", ", ".join(ALLOWED_REQUESTS))
        .replace("{{MOCK_PAGES}}", ", ".join(ALLOWED_MOCK_PAGES))
        .replace("{{CASES}}", cases_for_prompt())
    )


def _clean_input_facts(facts: list[dict]) -> list[dict]:
    """camelCase/snake_case 어느 쪽으로 와도 받고, 혹시 모를 개인정보는 다시 가린다."""
    def get(f, camel, snake):
        v = f.get(camel, f.get(snake))
        if v is None or str(v).strip() == "":
            return None
        text, _ = mask_sensitive(" ".join(str(v).split()))
        return text[:100]

    cleaned = []
    for f in facts[:30]:
        detail = get(f, "detail", "detail")
        if not detail:
            continue
        risk = f.get("riskFactors", f.get("risk_factors")) or []
        cleaned.append({
            "category": str(f.get("category", "기타")).strip() or "기타",
            "detail": detail,
            "channel": get(f, "channel", "channel"),
            "impersonatedIdentity": get(f, "impersonatedIdentity", "impersonated_identity"),
            "requestedAction": get(f, "requestedAction", "requested_action"),
            "riskFactors": [mask_sensitive(str(r))[0][:50] for r in risk if str(r).strip()][:5],
        })
    return cleaned


def _fact_line(f: dict) -> str:
    """LLM 에 보낼 사실 한 줄. 예: - [택배·배송] 반송 문자 받음 (채널: 문자 / 사칭: 택배사 / 요구: 링크 클릭 / 위험 요소: 시간 압박)"""
    extras = []
    if f.get("channel"):
        extras.append(f"채널: {f['channel']}")
    if f.get("impersonatedIdentity"):
        extras.append(f"사칭: {f['impersonatedIdentity']}")
    if f.get("requestedAction"):
        extras.append(f"요구: {f['requestedAction']}")
    if f.get("riskFactors"):
        extras.append(f"위험 요소: {', '.join(f['riskFactors'])}")
    tail = f" ({' / '.join(extras)})" if extras else ""
    return f"- [{f['category']}] {f['detail']}{tail}"


def _fallback(facts: list[dict]) -> dict:
    subtype = pick_subtype(facts)
    raw = copy.deepcopy(FALLBACK_SCENARIOS[subtype])
    raw["analysis"]["usedFacts"] = [
        f["detail"] for f in facts
        if pick_subtype([f]) == subtype
    ][:3]
    result, errors = validate_and_normalize(raw)  # 기본 시나리오도 같은 검증을 거침
    if result is None:
        raise RuntimeError(f"기본 시나리오 '{subtype}' 가 검증을 통과하지 못함: {errors}")
    result["scenario"]["maxTurns"] = settings.training_max_turns
    result["fallback_used"] = True
    return result
