"""SIM-03 ③ 가상 훈련 대화 한 턴 (SIM-007 대사 생성 / SIM-008 대응 판정 / SIM-011 장면·선택지).

이 단계의 핵심: 가상 사기범이 개인정보를 **직접 요구**하고, 사용자가 그걸 **실제로 입력했는지** 기록하는 것.

프롬프트: app/prompts/training_turn.md (판정 → 다음 수(move) → 대사 순서의 JSON)
서버가 맡는 일: docs/training_prompt_notes.md 참고 (상태값 세기, 가림, 출력 검사, 덮어쓰기)

흐름:
  1) 대화가 비어있으면 → 시나리오의 첫 메시지 + 선택지 (LLM 안 씀)
  2) 사용자 마지막 답변에서 개인정보 감지 → 값은 가리고 종류만 기록 (대화는 계속)
  3) LLM 한 번 호출로: 사용자 답변 판정 + 다음 수(move) + 사기범 대사 + 장면 진행
     → output_guard 로 검사, 고칠 수 없는 위반이면 1번만 다시 요청, 그래도 안 되면 기본 대사
  4) 개인정보 요구 보장:
     - 개인정보 요구 장면인데 사기범 대사에 요구가 빠져 있으면 → 요구 문장을 덧붙임
     - 사용자가 "알려드릴게요"라고만 하고 실제 값을 안 썼으면 → 대화창에 직접 적으라고 한 번 더 요구
  5) 가드레일: 대사·선택지에 실존 기관명/URL/전화번호/모방범죄 우려 표현이 있으면 기본 대사로 교체
  6) 종료: 마지막 장면 완료 or 최대 턴 도달
"""
import json
import logging
import re
from pathlib import Path

from app import fallback
from app.config import settings
from app.guard import output_guard, pii_masker
from app.guard.scenario_guard import find_copycat_risk, find_unsafe_text
from app.guard.sensitive import NOT_NAMES, SURNAMES, mask_name_address, mask_requested_numbers, mask_sensitive
from app.llm.llm_client import LLMError, generate_json
from app.scenario_data import ALLOWED_REQUESTS, is_unavailable_info

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "training_turn.md"
OUTCOMES = {"safe", "risky", "neutral"}
INFO_REQUEST = "개인정보 제공"
TYPE_IT_MARK = "여기에 적어 주세요"  # '직접 입력하라'는 재요구를 이미 했는지 알아보는 표시
# 실제 사기범은 처음 몇 마디는 신뢰를 쌓는 데 써요. 이 답변 수까지는 요구 문장을 억지로 덧붙이지 않아요.
WARMUP_TURNS = 1
APP_CLICK_EVENT = "[훈련 행동: 가상 앱 설치 링크 누름]"
TRANSFER_EVENT = "[훈련 행동: 안내받은 계좌로 송금 버튼 누름]"
# 훈련은 실제 문자·앱 화면을 보내지 않아요. 사용자가 알 수 없는 코드를 꺼내는 대사는 바꿔요.
# ("인증번호는 앱 설치 후 표시돼요"처럼 요구가 아닌 말도 다음 턴에 그 번호를 묻게 만들어서 함께 바꿔요)
UNAVAILABLE_THING = re.compile(r"인증\s*(번호|코드)|OTP|보안\s*카드|승인\s*번호|확인\s*코드|앱\s*(화면)?에\s*(표시|뜬|나온|나타난)", re.I)
# 사기범이 역할을 깨고 예방 조언을 하는 대사 (예: "실제 정보는 보내지 마시고 공식 경로에서 확인하세요")
ROLE_BREAK = re.compile(r"보내지\s*마(세요|시고|십시오)|입력하지\s*마(세요|시고)|적지\s*마(세요|시고)"
                        r"|공식\s*(경로|채널|앱|홈페이지|사이트|대표\s*번호|고객센터)(에서|로|으로)\s*(직접\s*|따로\s*|별도로\s*)?확인(해\s*(주세요|보세요)|하세요|하시기\s*바랍니다)"
                        r"|사기(일|인|에\s*주의|를\s*조심)|주의하세요|훈련(입니다|이에요|이라서|이니)")
# 실제로 무언가를 시키는 말 (링크·정보·돈·앱). 두 번 연속 이런 말 없이 맴돌면 시간 끌기로 봐요
DEMAND_WORDS = re.compile(r"\[모의링크\]|\[모의앱\]|\[모의계좌\]|링크|계좌|성함|이름|주소|생년월일|연락처|번호|입금|송금|보내\s*주|설치|입력|적어|등록|수수료|인증")
# 사기범 대사에 들어가면 안 되는 내부 표시 (가림 표시, 버튼 사건, 가상 자료 표시)
FAKE_MARK = re.compile(r"\[\s*(?:가림|훈련\s*행동|가상)[^\]]*\]")
# 대화 속 인물 대사에서 "가상" 같은 설명 말을 빼요 (몰입을 깨는 표현)
VIRTUAL_WORD = re.compile(r"\(가상\)|가상의\s*|가상\s+(?=[가-힣])")
# 사용자가 앱 링크를 누르지 않았는데 설치가 끝났다고 말하는 대사
INSTALL_DONE = re.compile(r"설치(가|는|를)?\s*(확인|완료)\s*(됐|되었|했|하셨|되셨)|설치(하셨|해\s*주셨)(군요|네요|으니|으면)")




def run_training_turn(session_id: str, scenario: dict, current_scene: int, history: list[dict],
                      state: dict | None = None) -> dict:
    """
    scenario: ②에서 받은 scenario (camelCase 키)
    history 예: [{"role": "persona", "text": "..."}, {"role": "user", "text": "..."}]
    state: 지난 턴에 돌려준 trainingState (확정 설정·직전 수·장면별 횟수). 없으면 대화 기록으로 짐작해요.
    """
    scenes = scenario.get("keyScenes") or []
    total_scenes = max(1, len(scenes))
    current_scene = min(max(1, current_scene), total_scenes)
    max_turns = int(scenario.get("maxTurns") or settings.training_max_turns)
    min_turns = min(int(scenario.get("minTurns") or settings.training_min_turns), max_turns)

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
            training_state=_new_state(1, "접근"),
        )

    if history[-1]["role"] != "user":
        raise ValueError("history 의 마지막은 사용자(user) 답변이어야 합니다.")

    user_turns = sum(1 for m in history if m["role"] == "user")
    scene_now = _scene(scenes, current_scene)          # 사용자가 답할 때의 장면
    asked_info = _requested(scene_now)                 # 그 장면에서 사기범이 요구한 정보
    persona_lines = [m["text"] for m in history if m["role"] == "persona"]
    state = _load_state(state, current_scene, scene_now, persona_lines)

    # 2) 개인정보 감지 (값은 버리고 종류만) + 서버가 확인한 사건 정리
    masked_last, provided = _mask_last(history[-1]["text"], asked_info, persona_lines[-1] if persona_lines else "")
    events, events_this_turn = _event_log(history)

    # 3) LLM 호출 → 출력 검사 → (규칙 위반이면 1번 다시) → 그래도 안 되면 기본 대사
    used_fallback = False
    guard_state = output_guard.TurnState(
        current_scene=current_scene, total_scenes=total_scenes,
        scene_request_type=scene_now.get("request", "대화 지속"),
        next_scene_request_type=_scene(scenes, min(current_scene + 1, total_scenes)).get("request", "대화 지속"),
        user_turns=user_turns, min_turns=min_turns, max_turns=max_turns,
        prop_counts={p: sum(line.count(p) for line in persona_lines) for p in output_guard.PROPS},
        speaker_changes=_speaker_changes(persona_lines), allowed_requests=tuple(ALLOWED_REQUESTS))
    # prompt(규칙)는 매 턴 똑같아서 캐시되고, 매 턴 바뀌는 서버 정보는 대본 맨 앞에 붙여요 (비용 절약)
    prompt, server_info = _build_prompt(scenario, current_scene, total_scenes, user_turns, max_turns, min_turns,
                                        state, events, guard_state)
    transcript = server_info + "\n\n---\n\n" + _build_transcript(history, masked_last, asked_info)
    try:
        data = generate_json(prompt, transcript, max_tokens=1600)
    except LLMError:
        logger.warning("session=%s 훈련 LLM 실패 → 기본 대사", session_id)
        data = {}
        used_fallback = True

    new_format = "move" in data  # 새 프롬프트(training_turn.md) 형식인지. 예전 형식(테스트용 가짜 응답 등)이면 예전 규칙을 써요
    if new_format:
        errors, _ = output_guard.validate(json.dumps(data, ensure_ascii=False), guard_state)
        serious = [e for e in errors if not _server_fixable(e)]
        if serious:
            logger.info("session=%s 훈련 출력 규칙 위반 → 다시 요청: %s", session_id, serious)
            try:
                retried = generate_json(prompt, transcript + output_guard.retry_note(serious),
                                        max_tokens=1600, attempts=1)
                errors2, _ = output_guard.validate(json.dumps(retried, ensure_ascii=False), guard_state)
                if "move" in retried and not [e for e in errors2 if not _server_fixable(e)]:
                    data = retried
                else:
                    raise LLMError("still invalid")
            except LLMError:
                # 판정은 살리고 대사만 장면에 맞는 기본 대사로 바꿔요
                logger.warning("session=%s 재요청도 규칙 위반 → 기본 대사", session_id)
                data = {**data, "personaMessage": "", "speaker": None, "move": "요구", "requestMade": True}
                used_fallback = True

    # 판정 정리
    raw_judgment = data.get("judgment") if isinstance(data.get("judgment"), dict) else {}
    judgment = _clean_judgment(raw_judgment, scenario)
    # 규칙이 놓친 개인정보는 LLM 이 '가려지지 않은 정보'로 신고해요 (요구받은 것 중에서만 반영)
    # LLM 은 "이름"처럼 종류 이름으로 신고해요 → 시나리오 표기("예금주 성함" 등)로 맞춰서 반영
    llm_disclosed = [label for x in _str_list(raw_judgment.get("unmaskedInfoTypes")) + _str_list(raw_judgment.get("disclosedInfo"))
                     for label in [_asked_label(x, asked_info, persona_lines[-1] if persona_lines else "")]
                     if label and label not in provided]
    if llm_disclosed:
        provided += list(dict.fromkeys(llm_disclosed))
        # 값이 어디 있는지 알 수 없으므로 답변 전체를 '무엇을 넘겼는지'만 남기고 바꿈
        masked_last = f"[{', '.join(provided)} 입력함]"

    if new_format:
        # 서버가 정답을 아는 값(가림 결과·사건·턴 수)은 서버 값으로 덮어써요
        overridden = output_guard.apply_server_overrides(
            {**data, "judgment": dict(judgment)}, guard_state, provided, events_this_turn)
        data["scenarioComplete"] = overridden.get("scenarioComplete", False)
        risky_event = bool(set(events_this_turn) & RISKY_EVENTS)
    else:
        risky_event = False
    if provided or risky_event:
        logger.info("session=%s 훈련 중 개인정보 입력/위험 행동: %s %s", session_id, provided, events_this_turn)  # 종류만 기록
        judgment["outcome"] = "risky"  # 실제로 입력했다면 LLM 판단과 상관없이 위험 행동
        judgment["userAction"] = judgment["userAction"] or (f"{', '.join(provided)}을(를) 입력함" if provided else "요구받은 행동을 함")
        judgment["reason"] = judgment["reason"] or "요구받은 개인정보를 대화창에 실제로 입력함"
    elif used_fallback and not judgment["reason"]:
        judgment["reason"] = "AI 판정 실패 (기본값)"

    # 실제 피해를 말하면 훈련을 멈추고 도움 안내를 보여줘요 (사기범 대사 대신)
    if data.get("realConcern") or data.get("move") == "중단":
        logger.info("session=%s 실제 피해 가능성 → 훈련 중단", session_id)
        judgment["outcome"] = "neutral" if not provided else judgment["outcome"]
        return _result(
            persona_message=fallback.TRAINING_REAL_CONCERN_LINE, current_scene=current_scene,
            request_type="대화 지속", requested_info=[], choices=[], judgment=judgment,
            info_disclosure={"requested": asked_info, "provided": provided, "disclosed": bool(provided)},
            masked_text=_shown(masked_last, history), done=True, end_reason="real_concern", real_concern=True,
            fallback_used=used_fallback, training_state=state)

    # 장면 진행 (한 번에 1칸까지만)
    try:
        next_scene = int(data.get("nextScene", current_scene))
    except (TypeError, ValueError):
        next_scene = current_scene
    next_scene = min(max(current_scene, next_scene), min(current_scene + 1, total_scenes))

    move = str(data.get("move") or "")
    request_type = str(data.get("requestType", "")).strip()
    persona_message = str(data.get("personaMessage", "")).strip()[:350]
    persona_message, _ = mask_sensitive(persona_message)  # 혹시 숫자를 지어내도 가림
    persona_message = FAKE_MARK.sub("", persona_message).strip()  # LLM 이 가림·사건 표시를 흉내 내면 지워요
    choices = _clean_choices(data.get("choices"))
    type_it = False

    if not new_format:
        # 4-a) "알려드릴게요"라고만 하고 실제 값을 안 썼다면 → 직접 적으라고 한 번 더 요구
        last_persona = persona_lines[-1] if persona_lines else ""
        text_info = [info for info in asked_info if not _is_photo_info(info)]
        type_it = bool(
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
    if (photo_info and judgment["outcome"] == "risky" and not any(_is_photo_info(info) for info in provided)
            and not _mentions(persona_message, photo_info)):
        next_scene = current_scene
        request_type = INFO_REQUEST
        persona_message = f"{persona_message.rstrip()} 확인을 위해 {', '.join(photo_info)} 보내 주세요.".strip()
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

    # 5-a) 사기범이 역할을 깨고 예방 조언을 하면 장면에 맞는 사기범 대사로 바꿔요 (조언은 결과 리포트에서 해요)
    if ROLE_BREAK.search(persona_message):
        logger.info("session=%s 역할을 깬 대사 교체", session_id)
        persona_message = _fallback_line(scene_next, request_type)
        used_fallback = True
    persona_message = VIRTUAL_WORD.sub("", persona_message).strip()

    # 5-c) 같은 부탁을 되풀이하거나(직전 대사와 거의 같음), 두 번 연속 아무것도 요구하지 않고 맴돌면
    #      장면을 진행시키는 요구를 해요. 첫 답변(신뢰 쌓기)과 마지막 인사는 빼요
    #      새 형식에서는 신뢰 쌓기·압박·물러남처럼 일부러 요구하지 않는 수가 있어서 '같은 말 반복'만 봐요
    recent_persona = [_strip_speaker(t)[1] for t in persona_lines[-2:]]
    repeated = any(_similar(persona_message, old) >= 0.55 for old in recent_persona)
    stalling = (not new_format and user_turns > WARMUP_TURNS and not type_it and recent_persona
                and not DEMAND_WORDS.search(persona_message) and not DEMAND_WORDS.search(recent_persona[-1]))
    if (repeated or stalling) and not data.get("scenarioComplete") and move not in ("마무리",):
        forward = scene_next if scene_next.get("request", "대화 지속") != "대화 지속" else _scene(scenes, min(next_scene + 1, total_scenes))
        forward_request = forward.get("request", "개인정보 제공")
        if forward_request == "대화 지속":
            forward_request = INFO_REQUEST
        logger.info("session=%s 맴도는 대사 진행 (repeated=%s) -> %s", session_id, repeated, forward_request)
        # 시간 끌기면 자연스러운 앞말은 살리고 요구를 이어 붙여요. 같은 말 반복이면 요구 대사로 바꿔요
        line = (_request_info_line(_requested(forward) or [fallback.TRAINING_DEFAULT_INFO])
                if forward_request == INFO_REQUEST else _fallback_line(forward, forward_request))
        persona_message = line if repeated else f"{persona_message.rstrip()} {line}"[:350]
        if forward is not scene_next and next_scene == current_scene:  # 장면은 한 번에 한 칸까지만
            next_scene = min(next_scene + 1, total_scenes)
            scene_next = forward
        request_type = forward_request
        data["requestMade"] = True
        used_fallback = True

    # 5-b) 훈련에서 일어나지 않은 일을 전제로 한 대사 바로잡기
    #  - 인증번호·OTP·앱 화면 속 번호처럼 사용자가 받을 수 없는 값을 요구하면
    #  - 앱 설치 링크를 누르지도 않았는데 설치가 확인됐다고 하면
    app_clicked = any(m["role"] == "event" and m["text"] == APP_CLICK_EVENT for m in history)
    false_install = not app_clicked and bool(INSTALL_DONE.search(persona_message))
    if false_install or _asks_unavailable(persona_message):
        logger.info("session=%s 훈련과 맞지 않는 대사 교체 (app_clicked=%s)", session_id, app_clicked)
        persona_message, request_type = _consistent_line(
            scene_next, "앱 설치" if false_install else request_type, app_clicked)
        choices = [c for c in choices if not UNAVAILABLE_THING.search(c)]

    # 4-b) 개인정보 요구 장면에 들어왔는데 아직 한 번도 요구하지 않았다면 → 요구 문장을 덧붙임
    #      (처음 몇 마디는 신뢰를 쌓는 단계라 덧붙이지 않아요. 새 형식에서는 '요구' 수일 때만)
    next_info = _requested(scene_next)
    wants_request = (move in ("요구", "확전") or data.get("requestMade")) if new_format else True
    if (scene_next.get("request") == INFO_REQUEST and next_info and not type_it and user_turns > WARMUP_TURNS
            and wants_request):
        asked_before = any(m["role"] == "persona" and _mentions(m["text"], next_info) for m in history)
        if not asked_before and not _mentions(persona_message, next_info):
            persona_message = f"{persona_message.rstrip()} {_request_info_line(next_info)}"
        request_type = INFO_REQUEST

    if len(choices) < 2:
        choices = list(fallback.TRAINING_CHOICES.get(request_type, fallback.TRAINING_CHOICES["대화 지속"]))

    # 6) 종료 판단
    #  - 최대 답변 수에 닿으면 끝
    #  - 사용자가 확실하게 끊으면(차단·신고·공식 채널 확인·대화 중단) 끝: 잘 막은 경우라 더 끌지 않아요
    #  - 사용자가 훈련 자체를 그만두겠다고 하면(move=마무리) 최소 답변 수와 상관없이 끝
    #  - 시나리오 마지막 장면이 끝나도 최소 답변 수 전에는 끝내지 않고 사기범이 추가 요구로 이어가요
    last_user_text = history[-1]["text"]
    decisive_exit = judgment["outcome"] == "safe" and user_turns >= 2 and bool(DECISIVE_EXIT.search(last_user_text))
    end_reason = None
    if user_turns >= max_turns:
        end_reason = "max_turns"
    elif decisive_exit:
        end_reason = "user_exit"
    elif new_format and move == "마무리" and data.get("scenarioComplete"):
        end_reason = "user_exit" if user_turns < min_turns else "scenario_complete"
    elif (bool(data.get("scenarioComplete")) and current_scene >= total_scenes and not type_it
          and user_turns >= min_turns):
        end_reason = "scenario_complete"
    done = end_reason is not None
    if done:
        choices = []
        if end_reason == "max_turns":
            persona_message = fallback.TRAINING_END_LINE

    # 화자가 바뀌면 "[화자: 이름] " 을 앞에 붙여요 (프론트가 이름표로 보여줘요). 대화 전체에서 2번까지만
    speaker = str(data.get("speaker") or "").strip()[:30] if new_format and not done else ""
    if speaker and speaker != _current_speaker(persona_lines) and guard_state.speaker_changes < output_guard.SPEAKER_LIMIT \
            and not find_unsafe_text(speaker, scenario.get("allowedOrganizations", [])):
        persona_message = f"[화자: {speaker}] {persona_message}"

    new_state = _next_state(state, data, move, current_scene, next_scene) if new_format else state

    logger.info("session=%s turn=%d scene=%d->%d move=%s outcome=%s provided=%s done=%s fallback=%s",
                session_id, user_turns, current_scene, next_scene, move or "-", judgment["outcome"],
                provided, done, used_fallback)

    return _result(
        persona_message=persona_message,
        current_scene=next_scene,
        request_type=request_type,
        requested_info=[] if done else (asked_info if type_it else next_info),
        choices=choices,
        judgment=judgment,
        info_disclosure={"requested": asked_info, "provided": provided, "disclosed": bool(provided)},
        masked_text=_shown(masked_last, history),
        done=done,
        end_reason=end_reason,
        real_concern=False,
        fallback_used=used_fallback,
        training_state=new_state,
        request_made=bool(data.get("requestMade")) if new_format else None,
        move=move or None,
    )


# ---------------- 도우미 함수들 ----------------

def _scene(scenes: list[dict], order: int) -> dict:
    if 1 <= order <= len(scenes):
        return scenes[order - 1]
    return {}


def _requested(scene: dict) -> list[str]:
    # 예전에 저장된 시나리오에 인증번호 같은 값이 남아 있어도 요구하지 않아요
    return [str(x) for x in (scene.get("requestedInfo") or []) if str(x).strip() and not is_unavailable_info(str(x))]


def _asks_unavailable(text: str) -> bool:
    return bool(UNAVAILABLE_THING.search(text))


def _consistent_line(scene: dict, request_type: str, app_clicked: bool) -> tuple[str, str]:
    """훈련에서 실제로 할 수 있는 요구로 바꾼 대사와 요구 종류."""
    if request_type == "앱 설치" and not app_clicked:
        return "아직 설치가 확인되지 않네요. 아까 보내드린 앱부터 설치해 주셔야 다음 처리가 됩니다.", "앱 설치"
    if request_type in ("송금", "링크 클릭"):
        return _fallback_line(scene, request_type), request_type
    info = [item for item in _requested(scene) if not _is_photo_info(item)] or ["성함", "계좌번호"]
    lead = "설치는 확인됐습니다. " if app_clicked else "확인을 마무리하려면 "
    return f"{lead}{', '.join(info)} {TYPE_IT_MARK}.", INFO_REQUEST


def _mentions(text: str, info: list[str]) -> bool:
    compact = text.replace(" ", "")
    return any(i.replace(" ", "") in compact for i in info)


def _str_list(value) -> list[str]:
    return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else []


# 사용자가 대화를 확실하게 끊는 말 (차단·신고·공식 채널로 직접 확인·대화 중단)
DECISIVE_EXIT = re.compile(
    r"차단|신고|112|1332|대화(를)?\s*(그만|중단|끝)|더\s*(이상|는)\s*(연락|대화|답)"
    r"|공식\s*(앱|홈페이지|사이트|번호|고객센터|채널)|고객센터에\s*(직접|전화|확인)|직접\s*(전화|확인)해\s*볼")




def _build_prompt(scenario, current_scene, total_scenes, user_turns, max_turns, min_turns,
                  state: dict, events: list[str], guard_state) -> str:
    # 근거 사례 출처 같은 내부 값은 빼고, 연기·판정에 필요한 것만 보여줌
    shown = {k: scenario.get(k) for k in
             ("riskType", "fraudSubtype", "title", "channel", "persona", "background", "keyScenes", "expectedActions", "allowedOrganizations", "completedPageSubmission")}
    counts = guard_state.prop_counts
    values = {
        "SCENARIO": json.dumps(shown, ensure_ascii=False, indent=1),
        "ESTABLISHED_FACTS": "\n".join(f"- {f}" for f in state.get("facts", [])) or "(아직 없음)",
        "CURRENT_SCENE": str(current_scene),
        "TOTAL_SCENES": str(total_scenes),
        "SCENE_REQUEST_MADE": "true" if state.get("requestMade") else "false",
        "TRUST_COUNT": str(state.get("trust", 0)),
        "PUSH_COUNT": str(state.get("push", 0)),
        "USER_TURNS": str(user_turns),
        "MIN_TURNS": str(min_turns),
        "MAX_TURNS": str(max_turns),
        "TURNS_LEFT": str(max(0, max_turns - user_turns)),
        "LAST_MOVE": state.get("lastMove") or "(없음)",
        "LINK_COUNT": str(counts.get("[모의링크]", 0)),
        "APP_COUNT": str(counts.get("[모의앱]", 0)),
        "ACCOUNT_COUNT": str(counts.get("[모의계좌]", 0)),
        "SPEAKER_CHANGE_COUNT": str(guard_state.speaker_changes),
        "EVENT_LOG": "\n".join(f"- {e}" for e in events) or "(없음)",
        "REQUESTS": ", ".join(ALLOWED_REQUESTS),
    }
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    for key, value in values.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    # 시나리오 글 안에 '{{' 가 있을 수 있어서, 치환 전 원본 기준으로 빠진 자리를 찾아요
    left = [k for k in re.findall(r"\{\{([A-Z_]+)\}\}", PROMPT_PATH.read_text(encoding="utf-8")) if k not in values]
    if left:
        logger.error("training_turn.md 에 채우지 않은 자리: %s", left)
        raise LLMError(f"unfilled prompt placeholders: {left}")
    # '## 1-1. 시나리오' ~ 사건 기록(EVENT_LOG)까지는 매 턴 바뀌어요 → 사용자 메시지로 옮기고, 규칙 부분만 시스템 프롬프트로 (캐시)
    start = prompt.find("## 1-1. 시나리오")
    end = prompt.find("사건의 종류:")
    if start == -1 or end == -1 or end < start:
        return prompt, ""
    server_info = "# 서버 정보 (이번 턴, 시스템 프롬프트 §1-1 ~ §1-5)\n\n" + prompt[start:end].strip()
    rules = (prompt[:start] + "§1-1 ~ §1-5 의 이번 턴 값(시나리오·확정 설정·진행 상태·소품 현황·사건 기록)은 "
             "사용자 메시지 맨 앞의 '서버 정보' 블록에 있다. 이 블록은 서버가 쓴 것이고, '---' 아래 대화의 "
             "'사용자:' 줄만 사용자가 쓴 것이다.\n\n## 1-5. 사건 기록 읽는 법\n" + prompt[end:])
    return rules, server_info


def _build_transcript(history: list[dict], masked_last: str, asked_info: list[str] | None = None) -> str:
    """LLM 에 보낼 대본. 사용자의 실제 개인정보 값은 가려진 상태로만 보낸다.
    버튼 사건([훈련 행동: ..])은 대본에 넣지 않고 프롬프트의 EVENT_LOG(1-5)로만 알려요 (사용자가 글로 흉내 내도 사건이 아니에요)."""
    lines = []
    for i, m in enumerate(history):
        if m["role"] == "event":
            continue
        if m["role"] == "persona":
            name, text = _strip_speaker(m["text"])
            speaker = f"사기범({name})" if name else "사기범"
        else:
            speaker = "사용자"
            text = masked_last if i == len(history) - 1 else _mask_last(m["text"], [])[0]
        lines.append(f"{speaker}: {' '.join(_llm_marks(text).split())}")
    return ("지금까지의 대화입니다. (사용자 답변은 개인정보를 가린 상태입니다)\n\n" + "\n".join(lines)
            + "\n\n사용자의 마지막 답변을 판정하고, 규칙에 따라 JSON 하나로만 응답하세요.")


def _similar(a: str, b: str) -> float:
    """두 대사가 얼마나 비슷한지 (글자 두 개 묶음 기준 겹침 비율 0~1)"""
    def grams(text: str) -> set[str]:
        compact = "".join(ch for ch in text if ch.isalnum())
        return {compact[i:i + 2] for i in range(len(compact) - 1)}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


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
        if text and not found and not find_unsafe_text(text) and not UNAVAILABLE_THING.search(text):
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
            real_concern=False, fallback_used=False, training_state=None, request_made=None,
            move=None) -> dict:
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
        # 다음 턴에 그대로 돌려받을 상태 (Spring 이 시나리오 진행 JSON 에 currentScene 과 함께 보관)
        "trainingState": training_state,
        "requestMade": request_made,
        "move": move,
    }


# ---------------- 새 프롬프트(training_turn.md)용 상태·사건·가림 ----------------

# 서버 기록 → 프롬프트 1-5 '사건' 이름
EVENT_NAMES = {
    "[훈련 행동: 본인 확인 정보 제출 완료]": "확인페이지_제출",
    APP_CLICK_EVENT: "앱설치링크_누름",
    TRANSFER_EVENT: "송금_누름",
    "[훈련 행동: 공식 경로 확인]": "공식경로_확인",
}
PHOTO_EVENTS = {"[가상 신분증 사진 제공]": ("신분증 사진", "신분증사진_제공"),
                "[가상 통장 사진 제공]": ("통장 사진", "통장사진_제공"),
                "[가상 사진 제공]": ("사진", "사진_제공")}
RISKY_EVENTS = {"앱설치링크_누름", "송금_누름", "확인페이지_제출", "신분증사진_제공", "통장사진_제공", "사진_제공"}
# Spring 이 먼저 가린 표시 ([계좌번호] 등)와 이 서버가 가린 표시
MASK_LABELS = ("주민등록번호", "카드번호", "전화번호", "휴대폰번호", "계좌번호", "이메일", "비밀번호",
               "인증정보", "민감정보", "이름", "성함", "주소", "생년월일", "연락처", "숫자정보")
# 시나리오 requestedInfo 이름 → pii_masker 종류
EXPECT_MAP = (("성함", "이름"), ("이름", "이름"), ("주소", "주소"), ("생년월일", "생년월일"), ("연락처", "휴대폰번호"),
              ("전화", "휴대폰번호"), ("휴대폰", "휴대폰번호"), ("계좌", "계좌번호"), ("카드", "카드번호"),
              ("비밀번호", "비밀번호"), ("주민", "주민등록번호"), ("이메일", "이메일"))
# 형식만 보고도 개인정보라고 확신할 수 있는 종류 (요구받지 않았어도 '입력함'으로 셈)
SURE_TYPES = {"주민등록번호", "카드번호", "휴대폰번호", "이메일", "계좌번호", "비밀번호"}
MAX_FACTS = 12


NAME_WORDS = re.compile(r"성함|이름|예금주|본명|실명")
# 성씨로 시작하지만 이름이 아닌 흔한 말 (이름을 요구받은 직후에도 가리지 않아요)
COMMON_WORDS = {"지금", "이거", "이건", "이게", "이제", "정말", "진짜", "조금", "오늘", "고객", "주소", "문자", "성함", "이름",
                "계좌", "송금", "우리", "제가", "저는", "전화", "번호", "은행", "확인", "하나", "한번", "안녕", "감사", "고마워",
                "연락", "인증", "카드", "주민", "황당", "장난", "정도", "이상", "김치", "조심", "임시", "신한", "국민", "하나은",
                "우체국", "농협", "기업", "구매", "판매", "거래", "배송", "택배", "문의", "전에", "이미", "이번", "오후", "오전",
                "남편", "아내", "엄마", "아빠", "친구", "동생", "형님", "누나", "언니", "오빠", "선생", "사장", "기사", "직원"}
REFUSAL = re.compile(r"\?|왜|싫|안\s?돼|안\s?할|안\s?드|못\s|못해|아니|사기|누구|뭐|무슨|어디|의심|그만")


def _asked_label(kind: str, asked_info: list[str], last_persona: str = "") -> str | None:
    """'이름' 같은 종류 이름을 시나리오가 쓰는 표기로 바꿔요. 요구받지 않은 종류면 None."""
    if kind in asked_info:
        return kind
    for word, k in EXPECT_MAP:
        if k == kind or word in kind:
            label = next((info for info in asked_info if word in info), None)
            if label:
                return label
    if kind in ("이름", "성함") and NAME_WORDS.search(last_persona):
        return "이름"
    return None


def _mask_names(text: str, asked_info: list[str], last_persona: str) -> tuple[str, bool]:
    """이름을 요구받은 직후 답에 섞인 이름(예: "박선홍 국민은행 …")을 가려요. 거절·질문이면 손대지 않아요."""
    expected = any(NAME_WORDS.search(info) for info in asked_info) or bool(NAME_WORDS.search(last_persona))
    if not expected or REFUSAL.search(text):
        return text, False
    tokens = re.findall(r"[가-힣]+", text)
    found = False

    def repl(m: re.Match) -> str:
        nonlocal found
        word = m.group(1)
        if (word[0] not in SURNAMES or word in COMMON_WORDS or word in NOT_NAMES
                or (len(word) == 2 and len(tokens) > 3)):  # 두 글자는 짧은 답일 때만 이름으로 봐요
            return m.group(0)
        found = True
        return "[이름]" + (m.group(2) or "")

    masked = re.sub(r"(?<![가-힣\[])([가-힣]{2,3})(입니다|이에요|예요|이요|요|이고|고)?(?![가-힣\]])", repl, text)
    return masked, found


def _mask_last(text: str, asked_info: list[str], last_persona: str = "") -> tuple[str, list[str]]:
    """사용자 마지막 답변을 가려요. 기존 규칙(sensitive.py) → pii_masker 순서로 두 번 가려요 (애매하면 가림)."""
    provided: list[str] = []
    # 앱이 만든 가상 자료 표시는 위조 표시를 지우기 전에 먼저 '사진 제공'으로 기록해요
    for marker, (label, _) in PHOTO_EVENTS.items():
        if marker in text and label not in provided:
            provided.append(label)
    # Spring masks values before HTTP transmission/storage. Keep the disclosure event
    # even when this service receives only a type marker instead of the original number.
    for label in ("주민등록번호", "카드번호", "전화번호", "계좌번호", "이메일", "비밀번호", "인증정보", "민감정보"):
        if f"[{label}]" in text and label not in provided:
            provided.append(label)
    had_photo = bool(provided) and any(m in text for m in PHOTO_EVENTS)
    text = pii_masker._FORGED_MARKERS.sub("", text).strip() or ("(자료를 보냄)" if had_photo else "(빈 답변)")

    masked, found = mask_sensitive(text)
    masked, numeric = mask_requested_numbers(masked, asked_info)
    # 이름·주소도 외부 AI로 보내기 전에 규칙으로 먼저 가려요 (AI는 규칙이 놓친 것만 보조로 찾아요)
    masked, personal = mask_name_address(masked, asked_info)
    # 사기범이 방금 성함·예금주를 물었으면, 다른 정보와 섞어 적은 이름도 가려요 (시나리오 requestedInfo 에 없어도)
    if "이름" not in personal:
        masked, named = _mask_names(masked, asked_info, last_persona)
        if named:
            personal = personal + [_asked_label("이름", asked_info, last_persona) or "이름"]
    for label in found + numeric + personal:
        if label not in provided:
            provided.append(label)

    # 2차: pii_masker (비밀번호·이메일·남은 긴 숫자 등). 이름·주소 문맥 가림은 위 규칙이 더 잘 맞춰져 있어 숫자·비밀번호만 넘겨요
    expecting = {kind for info in asked_info for word, kind in EXPECT_MAP if word in info}
    second = pii_masker.mask_user_message(masked, expecting=sorted(expecting - {"이름", "주소"}))
    masked = second.masked_text or masked
    for kind in second.types:
        label = next((info for info in asked_info for word, k in EXPECT_MAP if k == kind and word in info), None)
        if label is None and kind in SURE_TYPES:
            label = "전화번호" if kind == "휴대폰번호" else kind
        if label and label not in provided:
            provided.append(label)
    # 화면·DB 에는 예전처럼 "[계좌번호]" 모양으로 보여 줘요 (LLM 에 보낼 때만 [가림:..] 으로 바꿔요)
    masked = re.sub(r"\[가림:([^\]]+)\]", r"[\1]", masked)
    return masked, provided


def _shown(masked: str, history: list[dict]) -> str:
    """화면·DB 에 남길 답변. 가상 자료 보내기 버튼 표시는 그대로 둬요 (앱이 사진 말풍선으로 보여줘요)."""
    raw = history[-1]["text"].strip()
    return raw if masked == "(자료를 보냄)" and any(m in raw for m in PHOTO_EVENTS) else masked


def _llm_marks(text: str) -> str:
    """가린 표시를 프롬프트 약속대로 [가림:종류] 로 바꿔요. "[이름, 주소 입력함]" 도 풀어서 바꿔요."""
    def multi(m: re.Match) -> str:
        return " ".join(f"[가림:{x.strip()}]" for x in m.group(1).split(",") if x.strip())
    text = re.sub(r"\[([^\]\[]+?)\s*입력함\]", multi, text)
    return re.sub(r"\[(" + "|".join(MASK_LABELS) + r")\]", r"[가림:\1]", text)


def _event_log(history: list[dict]) -> tuple[list[str], list[str]]:
    """서버가 확인한 사건 목록 ("3턴: 송금_누름")과 이번 답변 직전·이번 답변에서 생긴 사건."""
    log: list[str] = []
    this_turn: list[str] = []
    turn = 0
    last_persona = max((i for i, m in enumerate(history) if m["role"] == "persona"), default=-1)
    for i, m in enumerate(history):
        names = []
        if m["role"] == "event" and m["text"] in EVENT_NAMES:
            names = [EVENT_NAMES[m["text"]]]
            label_turn = turn + 1  # 다음 답변을 하기 전에 누른 버튼
        elif m["role"] == "user":
            turn += 1
            names = [event for marker, (_, event) in PHOTO_EVENTS.items() if marker in m["text"]]
            label_turn = turn
        for name in names:
            log.append(f"{label_turn}턴: {name}")
            if i > last_persona:
                this_turn.append(name)
    return log, this_turn


def _strip_speaker(text: str) -> tuple[str | None, str]:
    m = re.match(r"\s*\[화자:\s*([^\]]{1,30})\]\s*", text)
    return (m.group(1).strip(), text[m.end():]) if m else (None, text)


def _speaker_changes(persona_lines: list[str]) -> int:
    return sum(1 for line in persona_lines if _strip_speaker(line)[0])


def _current_speaker(persona_lines: list[str]) -> str | None:
    for line in reversed(persona_lines):
        name = _strip_speaker(line)[0]
        if name:
            return name
    return None


def _server_fixable(error: str) -> bool:
    """서버가 직접 고칠 수 있는 규칙 위반 (소품 위치·한도, 장면 번호, 화자 한도) 은 다시 묻지 않아요 (응답이 2배로 느려져서)."""
    # '알 수 없는 대괄호'는 "[한빛택배]" 같은 업체 표기일 때가 많아서 다시 묻지 않고, 가림·사건 흉내 표시만 서버가 지워요
    return any(word in error for word in ("소품", "[모의", "한도", "nextScene", "requestType", "첫 메시지", "명분 없이", "대괄호"))


def _new_state(scene: int, last_move: str) -> dict:
    return {"facts": [], "lastMove": last_move, "scene": scene, "trust": 0, "push": 0, "requestMade": False}


def _load_state(raw, current_scene: int, scene_now: dict, persona_lines: list[str]) -> dict:
    """지난 턴 상태를 읽어요. 없거나(예전 백엔드) 깨졌으면 대화 기록으로 짐작해요."""
    if isinstance(raw, dict) and isinstance(raw.get("facts", []), list):
        state = {
            "facts": [mask_sensitive(str(f))[0][:80] for f in raw.get("facts", []) if str(f).strip()][-MAX_FACTS:],
            "lastMove": str(raw.get("lastMove") or "(없음)")[:10],
            "scene": int(raw.get("scene") or current_scene) if str(raw.get("scene") or "").isdigit() or isinstance(raw.get("scene"), int) else current_scene,
            "trust": _small_int(raw.get("trust")), "push": _small_int(raw.get("push")),
            "requestMade": bool(raw.get("requestMade")),
        }
    else:
        state = _new_state(current_scene, "(없음)")
        state["trust"] = 1 if len(persona_lines) >= 2 else 0
        state["requestMade"] = _request_seen(scene_now, persona_lines)
    if state["scene"] != current_scene:  # 장면이 바뀌었으면 장면별 횟수는 새로 세요
        state.update(scene=current_scene, trust=0, push=0, requestMade=_request_seen(scene_now, persona_lines[-1:]))
    return state


def _small_int(value) -> int:
    try:
        return max(0, min(9, int(value)))
    except (TypeError, ValueError):
        return 0


def _request_seen(scene: dict, persona_lines: list[str]) -> bool:
    request = scene.get("request", "")
    prop = {"링크 클릭": "[모의링크]", "앱 설치": "[모의앱]", "송금": "[모의계좌]"}.get(request)
    if prop:
        return any(prop in line for line in persona_lines)
    info = _requested(scene)
    return bool(info) and any(_mentions(line, info) for line in persona_lines)


def _next_state(state: dict, data: dict, move: str, current_scene: int, next_scene: int) -> dict:
    facts = list(state.get("facts", []))
    for fact in _str_list(data.get("newFacts"))[:3]:
        fact = mask_sensitive(fact)[0][:80]
        if "[가림:" not in fact and fact not in facts:
            facts.append(fact)
    made = bool(data.get("requestMade"))
    if next_scene != current_scene:
        return {"facts": facts[-MAX_FACTS:], "lastMove": move, "scene": next_scene, "trust": 0, "push": 0,
                "requestMade": made and move == "확전"}
    return {"facts": facts[-MAX_FACTS:], "lastMove": move, "scene": current_scene,
            "trust": state.get("trust", 0) + (1 if move == "신뢰" or (move == "답변후진행" and not made) else 0),
            "push": state.get("push", 0) + (1 if move == "압박" else 0),
            "requestMade": state.get("requestMade", False) or made}
