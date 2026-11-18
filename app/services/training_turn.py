"""SIM-03 ③ 가상 훈련 대화 한 턴 (SIM-007 대사 생성 / SIM-008 대응 판정 / SIM-011 장면·선택지).

이 단계의 핵심: 가상 사기범이 개인정보를 **직접 요구**하고, 사용자가 그걸 **실제로 입력했는지** 기록하는 것.

흐름:
  1) 대화가 비어있으면 → 시나리오의 첫 메시지 + 선택지 (LLM 안 씀)
  2) 사용자 마지막 답변에서 개인정보 감지 → 값은 가리고 종류만 기록 (대화는 계속)
  3) LLM 한 번 호출로: 사용자 답변 판정 + 사기범 다음 대사 + 선택지 + 장면 진행
  4) 개인정보 요구 보장:
     - 개인정보 요구 장면인데 사기범 대사에 요구가 빠져 있으면 → 요구 문장을 덧붙임
     - 사용자가 "알려드릴게요"라고만 하고 실제 값을 안 썼으면 → 대화창에 직접 적으라고 한 번 더 요구
  5) 가드레일: 대사·선택지에 실존 기관명/URL/전화번호/모방범죄 우려 표현이 있으면 기본 대사로 교체
  6) 종료: 마지막 장면 완료 or 최대 턴 도달
"""
import json
import logging
from pathlib import Path

from app import fallback
from app.config import settings
from app.guard.scenario_guard import find_copycat_risk, find_unsafe_text
from app.guard.sensitive import mask_requested_numbers, mask_sensitive
from app.llm.llm_client import LLMError, generate_json
from app.scenario_data import ALLOWED_REQUESTS

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "training_turn.md"
OUTCOMES = {"safe", "risky", "neutral"}
INFO_REQUEST = "개인정보 제공"
TYPE_IT_MARK = "여기에 적어 주세요"  # '직접 입력하라'는 재요구를 이미 했는지 알아보는 표시


def run_training_turn(session_id: str, scenario: dict, current_scene: int, history: list[dict]) -> dict:
    """
    scenario: ②에서 받은 scenario (camelCase 키)
    history 예: [{"role": "persona", "text": "..."}, {"role": "user", "text": "..."}]
    """
    scenes = scenario.get("keyScenes") or []
    total_scenes = max(1, len(scenes))
    current_scene = min(max(1, current_scene), total_scenes)
    max_turns = int(scenario.get("maxTurns") or settings.training_max_turns)

    # 1) 시작: 첫 메시지
    if not history:
        first = _scene(scenes, 1)
        request = first.get("request", "대화 지속")
        return _result(
            persona_message=scenario.get("openingMessage") or fallback.TRAINING_PERSONA_LINES["대화 지속"],
            current_scene=1,
            request_type=request,
            requested_info=_requested(first),
            choices=fallback.TRAINING_CHOICES.get(request, fallback.TRAINING_CHOICES["대화 지속"]),
        )

    if history[-1]["role"] != "user":
        raise ValueError("history 의 마지막은 사용자(user) 답변이어야 합니다.")

    user_turns = sum(1 for m in history if m["role"] == "user")
    scene_now = _scene(scenes, current_scene)          # 사용자가 답할 때의 장면
    asked_info = _requested(scene_now)                 # 그 장면에서 사기범이 요구한 정보

    # 2) 개인정보 감지 (값은 버리고 종류만)
    masked_last, provided = mask_sensitive(history[-1]["text"])
    masked_last, numeric = mask_requested_numbers(masked_last, asked_info)
    provided += [n for n in numeric if n not in provided]
    # Spring masks values before HTTP transmission/storage. Keep the disclosure event
    # even when this service receives only a type marker instead of the original number.
    marker_types = ("주민등록번호", "카드번호", "전화번호", "계좌번호", "이메일",
                    "비밀번호", "인증정보", "민감정보")
    provided += [label for label in marker_types
                 if f"[{label}]" in history[-1]["text"] and label not in provided]
    # These are app-generated events. No image, base64, file name or user URL is passed to the model.
    photo_markers = {"[가상 신분증 사진 제공]": "신분증 사진",
                     "[가상 통장 사진 제공]": "통장 사진", "[가상 사진 제공]": "사진"}
    provided += [label for marker, label in photo_markers.items()
                 if marker in history[-1]["text"] and label not in provided]

    # 3) LLM 호출
    used_fallback = False
    try:
        data = generate_json(
            _build_prompt(scenario, current_scene, total_scenes, user_turns, max_turns),
            _build_transcript(history, masked_last),
            max_tokens=1000,
        )
    except LLMError:
        logger.warning("session=%s 훈련 LLM 실패 → 기본 대사", session_id)
        data = {}
        used_fallback = True

    # 판정 정리
    judgment = _clean_judgment(data.get("judgment"), scenario)
    # 이름·주소처럼 숫자 패턴으로 못 잡는 정보는 LLM 이 '넘겼다'고 본 종류를 반영 (요구받은 것 중에서만)
    llm_disclosed = [x for x in _str_list((data.get("judgment") or {}).get("disclosedInfo")
                                          if isinstance(data.get("judgment"), dict) else [])
                     if x in asked_info and x not in provided]
    if llm_disclosed:
        provided += llm_disclosed
        # 값이 어디 있는지 알 수 없으므로 답변 전체를 '무엇을 넘겼는지'만 남기고 바꿈
        masked_last = f"[{', '.join(provided)} 입력함]"

    if provided:
        logger.info("session=%s 훈련 중 개인정보 입력: %s", session_id, provided)  # 종류만 기록
        judgment["outcome"] = "risky"  # 실제로 입력했다면 LLM 판단과 상관없이 위험 행동
        judgment["userAction"] = judgment["userAction"] or f"{', '.join(provided)}을(를) 입력함"
        judgment["reason"] = judgment["reason"] or "요구받은 개인정보를 대화창에 실제로 입력함"
    elif used_fallback:
        judgment["reason"] = "AI 판정 실패 (기본값)"

    # 장면 진행 (한 번에 1칸까지만)
    try:
        next_scene = int(data.get("nextScene", current_scene))
    except (TypeError, ValueError):
        next_scene = current_scene
    next_scene = min(max(current_scene, next_scene), min(current_scene + 1, total_scenes))

    request_type = str(data.get("requestType", "")).strip()
    persona_message = str(data.get("personaMessage", "")).strip()[:300]
    persona_message, _ = mask_sensitive(persona_message)  # 혹시 숫자를 지어내도 가림
    choices = _clean_choices(data.get("choices"))

    # 4-a) "알려드릴게요"라고만 하고 실제 값을 안 썼다면 → 직접 적으라고 한 번 더 요구
    last_persona = next((m["text"] for m in reversed(history) if m["role"] == "persona"), "")
    text_info = [info for info in asked_info if not _is_photo_info(info)]
    type_it = (
        scene_now.get("request") == INFO_REQUEST and text_info
        and judgment["outcome"] == "risky" and not provided
        and TYPE_IT_MARK not in last_persona
    )
    if type_it:
        next_scene = current_scene
        request_type = INFO_REQUEST
        persona_message = fallback.TRAINING_TYPE_IT_LINE.replace("{info}", ", ".join(text_info))
        choices = list(fallback.TRAINING_TYPE_IT_CHOICES)

    # Agreeing to send a photo is not the app's submission event. Keep the photo
    # request visible so the next turn still offers the fixed virtual material.
    photo_info = [info for info in asked_info if _is_photo_info(info)]
    if photo_info and judgment["outcome"] == "risky" and not any(_is_photo_info(info) for info in provided):
        next_scene = current_scene
        request_type = INFO_REQUEST
        persona_message = f"확인을 위해 {', '.join(photo_info)} 보내 주세요."
        choices = list(fallback.TRAINING_TYPE_IT_CHOICES)
        type_it = True

    scene_next = _scene(scenes, next_scene)
    if request_type not in ALLOWED_REQUESTS:
        request_type = scene_next.get("request", "대화 지속")

    # 5) 가드레일: 실존 기관명·URL·전화번호·모방범죄 우려 → 기본 대사
    problems = (find_unsafe_text(persona_message, scenario.get("allowedOrganizations", [])) + find_copycat_risk(persona_message)) if persona_message else []
    if not persona_message or problems:
        if problems:
            logger.warning("session=%s 사기범 대사 가드레일 차단: %s", session_id, problems)
        persona_message = _fallback_line(scene_next, request_type)
        used_fallback = True

    # 4-b) 개인정보 요구 장면에 들어왔는데 아직 한 번도 요구하지 않았다면 → 요구 문장을 덧붙임
    next_info = _requested(scene_next)
    if scene_next.get("request") == INFO_REQUEST and next_info and not type_it:
        asked_before = any(m["role"] == "persona" and _mentions(m["text"], next_info) for m in history)
        if not asked_before and not _mentions(persona_message, next_info):
            persona_message = f"{persona_message.rstrip()} {_request_info_line(next_info)}"
        request_type = INFO_REQUEST

    if len(choices) < 2:
        choices = list(fallback.TRAINING_CHOICES.get(request_type, fallback.TRAINING_CHOICES["대화 지속"]))

    # 6) 종료 판단
    end_reason = None
    if user_turns >= max_turns:
        end_reason = "max_turns"
    elif bool(data.get("scenarioComplete")) and current_scene >= total_scenes and not type_it:
        end_reason = "scenario_complete"
    done = end_reason is not None
    if done:
        choices = []
        if end_reason == "max_turns":
            persona_message = fallback.TRAINING_END_LINE

    logger.info("session=%s turn=%d scene=%d->%d outcome=%s provided=%s done=%s fallback=%s",
                session_id, user_turns, current_scene, next_scene, judgment["outcome"],
                provided, done, used_fallback)

    return _result(
        persona_message=persona_message,
        current_scene=next_scene,
        request_type=request_type,
        requested_info=[] if done else (asked_info if type_it else next_info),
        choices=choices,
        judgment=judgment,
        info_disclosure={"requested": asked_info, "provided": provided, "disclosed": bool(provided)},
        masked_text=masked_last,
        done=done,
        end_reason=end_reason,
        real_concern=bool(data.get("realConcern", False)),
        fallback_used=used_fallback,
    )


# ---------------- 도우미 함수들 ----------------

def _scene(scenes: list[dict], order: int) -> dict:
    if 1 <= order <= len(scenes):
        return scenes[order - 1]
    return {}


def _requested(scene: dict) -> list[str]:
    return [str(x) for x in (scene.get("requestedInfo") or []) if str(x).strip()]


def _mentions(text: str, info: list[str]) -> bool:
    compact = text.replace(" ", "")
    return any(i.replace(" ", "") in compact for i in info)


def _str_list(value) -> list[str]:
    return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else []


def _build_prompt(scenario, current_scene, total_scenes, user_turns, max_turns) -> str:
    # 근거 사례 출처 같은 내부 값은 빼고, 연기·판정에 필요한 것만 보여줌
    shown = {k: scenario.get(k) for k in
             ("riskType", "fraudSubtype", "title", "channel", "persona", "background", "keyScenes", "expectedActions", "allowedOrganizations", "completedPageSubmission")}
    return (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{SCENARIO}}", json.dumps(shown, ensure_ascii=False, indent=1))
        .replace("{{CURRENT_SCENE}}", str(current_scene))
        .replace("{{TOTAL_SCENES}}", str(total_scenes))
        .replace("{{USER_TURNS}}", str(user_turns))
        .replace("{{MAX_TURNS}}", str(max_turns))
        .replace("{{REQUESTS}}", ", ".join(ALLOWED_REQUESTS))
    )


def _build_transcript(history: list[dict], masked_last: str) -> str:
    """LLM 에 보낼 대본. 사용자의 실제 개인정보 값은 가려진 상태로만 보낸다."""
    lines = []
    for i, m in enumerate(history):
        speaker = "사기범" if m["role"] == "persona" else "훈련에서 확인한 행동" if m["role"] == "event" else "사용자"
        text = masked_last if i == len(history) - 1 else mask_sensitive(m["text"])[0]
        lines.append(f"{speaker}: {' '.join(text.split())}")
    return ("지금까지의 훈련 대화입니다.\n\n" + "\n".join(lines)
            + "\n\n사용자의 마지막 답변을 판정하고, 규칙에 따라 JSON 으로만 응답하세요.")


def _clean_judgment(raw, scenario: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    valid_ids = {a.get("id") for a in scenario.get("expectedActions") or []}
    outcome = str(raw.get("outcome", "neutral")).strip()

    def ids(key):
        return [i for i in (raw.get(key) or []) if isinstance(i, str) and i in valid_ids][:5]

    return {
        "outcome": outcome if outcome in OUTCOMES else "neutral",
        "userAction": mask_sensitive(str(raw.get("userAction", "")).strip())[0][:150],
        "achievedActionIds": ids("achievedActionIds"),
        "violatedActionIds": ids("violatedActionIds"),
        "reason": mask_sensitive(str(raw.get("reason", "")).strip())[0][:200],
    }


def _clean_choices(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for c in raw[:3]:
        text = str(c).strip()[:40]
        _, found = mask_sensitive(text)
        if text and not found and not find_unsafe_text(text):
            out.append(text)
    return out


def _is_photo_info(info: str) -> bool:
    return any(word in info for word in ("사진", "이미지", "캡처", "스크린샷", "신분증", "주민등록증", "운전면허", "여권", "통장"))


def _request_info_line(info: list[str]) -> str:
    photos = [item for item in info if _is_photo_info(item)]
    text = [item for item in info if not _is_photo_info(item)]
    parts = []
    if text:
        parts.append(f"{', '.join(text)} {TYPE_IT_MARK}.")
    if photos:
        parts.append(f"{', '.join(photos)} 보내 주세요.")
    return " ".join(parts)


def _fallback_line(scene: dict, request_type: str) -> str:
    if request_type == INFO_REQUEST and any(_is_photo_info(info) for info in _requested(scene)):
        return "확인을 위해 " + _request_info_line(_requested(scene))
    line = fallback.TRAINING_PERSONA_LINES.get(request_type, fallback.TRAINING_PERSONA_LINES["대화 지속"])
    info = ", ".join(_requested(scene)) or fallback.TRAINING_DEFAULT_INFO
    return line.replace("{info}", info)


def _result(persona_message, current_scene, request_type, requested_info, choices, judgment=None,
            info_disclosure=None, masked_text=None, done=False, end_reason=None,
            real_concern=False, fallback_used=False) -> dict:
    disclosure = info_disclosure or {"requested": [], "provided": [], "disclosed": False}
    return {
        "personaMessage": persona_message,
        "currentScene": current_scene,
        "requestType": request_type,
        "requestedInfo": requested_info,
        "inputExpected": request_type == INFO_REQUEST and bool(requested_info),
        "choices": choices,
        "judgment": judgment,
        "infoDisclosure": disclosure,
        "sensitiveDetected": bool(disclosure["provided"]),
        "sensitiveTypes": disclosure["provided"],
        "maskedText": masked_text,
        "done": done,
        "endReason": end_reason,
        "realConcern": real_concern,
        "fallbackUsed": fallback_used,
    }
