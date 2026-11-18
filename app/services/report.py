"""SIM-010 ⑤ 결과 리포트 (RPT-01).

원칙: **사실은 코드가 계산하고, 설명 글만 LLM 이 쓴다.**
  - 코드가 계산: 개인정보 요구·제공 기록, 잘한 행동·놓친 행동, 모의 페이지 행동, 권장 대응, 공식 연락처, 근거 사례
  - LLM 이 작성: 한 줄 요약, 총평, 턴별 피드백 문장, 놓친 위험 신호 설명
  → LLM 이 "개인정보를 안 넘겼다"고 잘못 써도 숫자·목록은 바뀌지 않음
LLM 이 실패하거나 글에 전화번호·URL·개인정보가 섞이면 판정 이유(reason)로 만든 기본 문장을 씁니다.
"""
import json
import logging
from pathlib import Path

from app import report_data
from app.case_db import get_case
from app.guard.scenario_guard import PHONE_PATTERN, URL_PATTERN
from app.guard.sensitive import mask_sensitive
from app.llm.llm_client import LLMError, generate_json

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "report.md"


def run_report(session_id: str, scenario: dict, turns: list[dict],
               mock_page_events: list[dict], end_reason: str | None) -> dict:
    turns = sorted(turns, key=lambda t: t.get("turn", 0))
    actions = {a["id"]: a for a in scenario.get("expectedActions") or []}

    info = _info_protection(turns)
    good, missed = _actions(turns, actions)
    mock_page = _mock_page(mock_page_events)
    recommended = _recommended(scenario, missed, info["provided"])

    # ---- LLM: 설명 글 ----
    used_fallback = False
    facts = _facts_for_prompt(info, good, missed, mock_page, turns, end_reason)
    try:
        data = generate_json(_build_prompt(scenario, facts), _turns_for_prompt(turns),
                             max_tokens=2000, timeout=25, attempts=2)
    except LLMError:
        logger.warning("session=%s 리포트 LLM 실패 → 기본 문장", session_id)
        data = {}
        used_fallback = True

    headline = _safe_text(data.get("headline"), 60)
    overview = _safe_text(data.get("overview"), 500)
    if not headline or not overview:
        headline, overview = _fallback_summary(info, good, missed)
        used_fallback = True

    llm_feedback = {}
    for f in data.get("turnFeedback") or []:
        if isinstance(f, dict):
            try:
                llm_feedback[int(f.get("turn"))] = _safe_text(f.get("feedback"), 300)
            except (TypeError, ValueError):
                continue

    turn_feedback = []
    for t in turns:
        j = t.get("judgment") or {}
        feedback = llm_feedback.get(t["turn"]) or _fallback_turn_feedback(t)
        if not llm_feedback.get(t["turn"]):
            used_fallback = True  # 이 턴은 기본 문장 사용
        turn_feedback.append({
            "turn": t["turn"],
            "scene": t.get("scene", 1),
            "outcome": j.get("outcome", "neutral"),
            "userAction": _safe_text(j.get("userAction"), 150),
            "providedInfo": (t.get("infoDisclosure") or {}).get("provided", []),
            "feedback": feedback,
        })

    valid_turns = {t["turn"] for t in turns}
    missed_signals = []
    for m in (data.get("missedRiskSignals") or [])[:3]:
        if not isinstance(m, dict):
            continue
        signal, explanation = _safe_text(m.get("signal"), 60), _safe_text(m.get("explanation"), 250)
        if signal and explanation:
            turn = m.get("turn") if m.get("turn") in valid_turns else None
            missed_signals.append({"signal": signal, "explanation": explanation, "turn": turn})
    if not missed_signals and missed:
        missed_signals = _fallback_missed_signals(scenario, turns)

    logger.info("session=%s 리포트 생성: 요구=%s 제공=%s good=%d missed=%d fallback=%s",
                session_id, info["requested"], info["provided"], len(good), len(missed), used_fallback)

    return {
        "headline": headline,
        "overview": overview,
        "infoProtection": info,
        "goodActions": good,
        "missedActions": missed,
        "missedRiskSignals": missed_signals,
        "turnFeedback": turn_feedback,
        "mockPage": mock_page,
        "recommendedActions": recommended,
        "officialContacts": report_data.OFFICIAL_CONTACTS,
        "basedOnCases": scenario.get("basedOnCases") or [],
        "fraudType": {"riskType": scenario.get("riskType", ""), "subtype": scenario.get("fraudSubtype", "")},
        "endReason": end_reason,
        "disclaimer": report_data.DISCLAIMER,
        "fallbackUsed": used_fallback,
    }


# ---------------- 코드가 계산하는 사실 ----------------

def _info_protection(turns: list[dict]) -> dict:
    """리포트의 핵심: 무엇을 요구받았고, 무엇을 실제로 넘겼는가."""
    requested, provided, events = [], [], []
    for t in turns:
        d = t.get("infoDisclosure") or {}
        req = [x for x in d.get("requested") or [] if x]
        prov = [x for x in d.get("provided") or [] if x]
        for x in req:
            if x not in requested:
                requested.append(x)
        for x in prov:
            if x not in provided:
                provided.append(x)
        if req or prov:
            events.append({"turn": t["turn"], "scene": t.get("scene", 1), "requested": req, "provided": prov})

    protected = [x for x in requested if x not in provided]
    if provided:
        first = next(e["turn"] for e in events if e["provided"])
        message = f"{first}번째 답변에서 처음으로 개인정보를 넘겼어요. 넘긴 정보: {', '.join(provided)}"
        if protected:
            message += f" / 지킨 정보: {', '.join(protected)}"
    elif requested:
        message = f"요구받은 개인정보({', '.join(requested)})를 하나도 넘기지 않았어요."
    else:
        message = "이번 훈련에서는 개인정보를 요구받는 장면까지 가지 않았어요."

    return {
        "requested": requested,
        "provided": provided,
        "protected": protected,
        "disclosed": bool(provided),
        "events": events,
        "message": message,
    }


def _actions(turns: list[dict], actions: dict) -> tuple[list[dict], list[dict]]:
    achieved, violated = {}, {}
    for t in turns:
        j = t.get("judgment") or {}
        for aid in j.get("achievedActionIds") or []:
            if aid in actions:
                achieved.setdefault(aid, []).append(t["turn"])
        for aid in j.get("violatedActionIds") or []:
            if aid in actions:
                violated.setdefault(aid, []).append(t["turn"])

        good, missed = [], []
    for aid, a in actions.items():
        desc = _plain(a["description"])
        recovered = aid in violated and aid in achieved and max(achieved[aid]) > max(violated[aid])
        if aid in violated:  # 한 번이라도 어겼으면 '놓친 행동'
            missed.append({"actionId": aid, "description": desc, "turns": violated[aid], "recoveredLater": recovered})
        if aid in achieved and (aid not in violated or recovered):  # 지켰거나, 어겼다가 끝내 바로잡았으면 '잘한 행동'
            good.append({"actionId": aid, "description": desc, "turns": achieved[aid], "recoveredLater": recovered})
    return good, missed

def _plain(text: str) -> str:
    """리포트는 실제 생활 조언이므로 훈련용 표현 '모의링크'를 그냥 '링크'로 바꾼다."""
    return str(text).replace("[모의링크]", "링크").replace("모의 링크", "링크").replace("모의링크", "링크")

def _mock_page(events: list[dict]) -> dict | None:
    if not events:
        return None
    acts = [e.get("action") for e in events if e.get("action") in report_data.MOCK_PAGE_ACTIONS]
    inputs = sorted({i for e in events for i in (e.get("inputTypes") or []) if i})
    risky = [a for a in acts if a in report_data.MOCK_PAGE_RISKY]
    if risky:
        message = f"모의 페이지에서 {', '.join(dict.fromkeys(risky))}까지 진행했어요."
        if inputs:
            message += f" 입력한 정보: {', '.join(inputs)}"
    elif "링크 열기" in acts:
        message = "링크는 열었지만 정보를 입력하지 않고 멈췄어요. 다음엔 링크를 열기 전에 멈춰 보세요."
    else:
        message = "모의 페이지에서 위험한 행동을 하지 않았어요."
    return {"actions": acts, "inputTypes": inputs, "risky": bool(risky), "message": message}


def _recommended(scenario: dict, missed: list[dict], provided: list[str]) -> list[str]:
    """권장 대응: 놓친 행동 → 근거 사례의 올바른 대응 → 넘긴 정보별 후속 조치 (중복 제거, 최대 6개)"""
    items = [m["description"] for m in missed]
    for ref in scenario.get("basedOnCases") or []:
        case = get_case(ref.get("caseId", ""))
        if case:
            items += case.get("correctResponse") or []
    if not items:
        items = [a["description"] for a in scenario.get("expectedActions") or []]
    items += [report_data.INFO_FOLLOWUP[p] for p in provided if p in report_data.INFO_FOLLOWUP]
    return list(dict.fromkeys(_plain(i) for i in items))[:6]


# ---------------- LLM 입출력 ----------------

def _build_prompt(scenario: dict, facts: str) -> str:
    shown = {k: scenario.get(k) for k in
             ("riskType", "fraudSubtype", "title", "channel", "persona", "keyScenes", "expectedActions")}
    return (PROMPT_PATH.read_text(encoding="utf-8")
            .replace("{{SCENARIO}}", json.dumps(shown, ensure_ascii=False, indent=1))
            .replace("{{FACTS}}", facts))


def _facts_for_prompt(info, good, missed, mock_page, turns, end_reason) -> str:
    lines = [
        f"- 개인정보: 요구받음 {info['requested'] or '없음'} / 실제로 넘김 {info['provided'] or '없음'} / 지킴 {info['protected'] or '없음'}",
        f"- 잘한 대응: {[g['description'] for g in good] or '없음'}",
        f"- 놓친 대응: {[m['description'] for m in missed] or '없음'}",
        f"- 총 답변 수: {len(turns)} / 종료 이유: {end_reason or '사용자 종료'}",
    ]
    if mock_page:
        lines.append(f"- 모의 페이지: {mock_page['message']}")
    return "\n".join(lines)


def _turns_for_prompt(turns: list[dict]) -> str:
    lines = ["훈련 판정 기록입니다.\n"]
    for t in turns:
        j = t.get("judgment") or {}
        d = t.get("infoDisclosure") or {}
        user_text = mask_sensitive(str(t.get("userText") or ""))[0]
        lines.append(
            f"- {t['turn']}턴 (장면 {t.get('scene', 1)}) 판정 {j.get('outcome', 'neutral')}"
            f" | 사용자 답변: {user_text or '(없음)'} | 행동: {j.get('userAction', '')}"
            f" | 이유: {j.get('reason', '')}"
            f" | 요구받은 정보: {d.get('requested') or '없음'} | 넘긴 정보: {d.get('provided') or '없음'}"
        )
    lines.append("\n규칙에 따라 JSON 으로만 응답하세요.")
    return "\n".join(lines)


def _safe_text(value, limit: int) -> str:
    """LLM 글 정리: 개인정보 가리고, 전화번호·URL 이 있으면 버림 (공식 연락처는 고정값으로만)."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text, _ = mask_sensitive(_plain(text))
    if URL_PATTERN.search(text) or PHONE_PATTERN.search(text):
        return ""
    return text[:limit]


# ---------------- LLM 실패 시 기본 문장 ----------------

def _fallback_summary(info, good, missed) -> tuple[str, str]:
    if info["disclosed"]:
        headline = "개인정보를 넘긴 장면이 있었어요"
    elif missed:
        headline = "몇몇 장면에서 요구에 응했어요"
    else:
        headline = "끝까지 잘 대응했어요"
    parts = [info["message"]]
    if good:
        parts.append(f"잘한 대응 {len(good)}가지가 있어요.")
    if missed:
        parts.append(f"다시 확인할 대응이 {len(missed)}가지 있어요.")
    return headline, " ".join(parts)


def _fallback_turn_feedback(turn: dict) -> str:
    j = turn.get("judgment") or {}
    provided = (turn.get("infoDisclosure") or {}).get("provided") or []
    reason = _safe_text(j.get("reason"), 200)
    if provided:
        return f"{', '.join(provided)}을(를) 입력했어요. 상대가 누구든 메시지로 이런 정보를 보내면 안 돼요."
    if j.get("outcome") == "safe":
        return reason or "요구에 응하지 않고 잘 대응했어요."
    if j.get("outcome") == "risky":
        return reason or "상대의 요구에 응했어요. 공식 채널로 먼저 확인하는 습관이 필요해요."
    return reason or "아직 판단할 행동이 없는 답변이었어요."


def _fallback_missed_signals(scenario: dict, turns: list[dict]) -> list[dict]:
    scenes = scenario.get("keyScenes") or []
    out = []
    for t in turns:
        if (t.get("judgment") or {}).get("outcome") != "risky":
            continue
        idx = t.get("scene", 1) - 1
        if 0 <= idx < len(scenes) and scenes[idx].get("tactic"):
            out.append({"signal": scenes[idx]["tactic"][:60],
                        "explanation": f"상대가 '{scenes[idx]['goal']}'을(를) 노리고 쓴 수법이에요.",
                        "turn": t["turn"]})
        if len(out) >= 3:
            break
    return out
