"""
훈련 리포트의 '한 것 / 안 한 것'을 서버 기록으로 정해요 (LLM 짐작 아님)

근거는 세 가지예요.
1. 턴별 기록 (trainingState.log): 매 턴 서버가 확정한 판정, 넘긴 개인정보 '종류', 버튼 사건, 종료 이유
2. 버튼 사건 (actions): 링크 열기·정보 제출·앱 링크·송금·사진 보내기·공식 확인
3. 대화 기록: 사기범이 실제로 링크·앱·계좌를 보냈는지, 훈련자 답변에 남은 가림 표시([계좌번호] 등)

LLM 은 이 사실을 받아 '설명하는 글'만 써요. LLM 이 실패해도 이 사실만으로 리포트를 만들 수 있어요.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PHOTO_ACTIONS = {"SEND_MOCK_ID_CARD": "신분증 사진", "SEND_MOCK_BANKBOOK": "통장 사진",
                 "SEND_MOCK_PHOTO": "사진", "SEND_MOCK_GIFT_CARD": "상품권 사진"}
PHOTO_EVENTS = {"신분증사진_제공": "신분증 사진", "통장사진_제공": "통장 사진", "사진_제공": "사진", "상품권사진_제공": "상품권 사진"}
# 훈련자 답변에 남은 가림 표시 → 넘긴 정보 종류 (예전 훈련처럼 턴별 기록이 없을 때 씀)
_MARK = re.compile(r"\[(?:가림:)?(주민등록번호|카드번호|전화번호|휴대폰번호|계좌번호|이메일|비밀번호|인증번호|인증정보|이름|주소|생년월일|연락처)\]")
_ENTERED = re.compile(r"\[([가-힣 ,·]{2,40}) 입력함\]")
_LEFT = ("user_exit",)


@dataclass
class TurnFact:
    turn: int
    outcome: str            # safe | risky | neutral
    provided: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


@dataclass
class ReportFacts:
    requested: list[str]
    provided: list[str]                 # 넘긴 개인정보 종류 (사진 포함)
    turns: list[TurnFact]
    sent_link: bool                     # 사기범이 링크를 보냈는지
    sent_app: bool
    sent_account: bool
    asked_photo: bool
    opened_page: bool
    submitted_page: bool
    clicked_app: bool
    transfers: int
    photos: list[str]
    verified: bool                      # 공식 채널로 확인
    ended_contact: bool                 # 훈련자가 먼저 연락을 끊음
    scam_finished: bool                 # 사기범이 돈을 받고 나감
    grade: str = ""                     # 안전 | 주의 | 위험
    did: list[str] = field(default_factory=list)       # 잘한 점 (사실)
    missed: list[str] = field(default_factory=list)    # 개선할 점 (사실)

    def summary_for_llm(self) -> dict:
        return {"grade": self.grade, "requestedInfo": self.requested, "providedInfo": self.provided,
                "did": self.did, "missed": self.missed,
                "turns": [{"turn": t.turn, "outcome": t.outcome, "providedInfo": t.provided, "events": t.events}
                          for t in self.turns if t.outcome != "neutral" or t.provided or t.events]}


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침에 맞는 조사: 계좌번호를 / 이름을, 주소는 / 이름은"""
    last = word[-1] if word else ""
    if not ("가" <= last <= "힣"):
        return f"{word}{with_final}({without_final})"
    return word + (with_final if (ord(last) - 0xAC00) % 28 else without_final)


def _list(words: list[str]) -> str:
    return ", ".join(words)


def _unique(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def collect(scenario: dict, conversation: list[dict], actions: set[str]) -> ReportFacts:
    """conversation: [{"sender": "USER"|"AI"|"SYSTEM", "content": "..."}] (훈련 대화만)"""
    state = scenario.get("trainingState") if isinstance(scenario, dict) else None
    log = state.get("log") if isinstance(state, dict) and isinstance(state.get("log"), list) else []
    ai_text = " ".join(m["content"] for m in conversation if m["sender"] == "AI")
    user_msgs = [m["content"] for m in conversation if m["sender"] == "USER"]

    turns: list[TurnFact] = []
    for item in log:
        if isinstance(item, dict) and isinstance(item.get("t"), int):
            turns.append(TurnFact(item["t"], item.get("o") or "neutral", list(item.get("p") or []), list(item.get("e") or [])))
    requested = _unique(r for item in log if isinstance(item, dict) for r in (item.get("r") or []))
    provided = _unique(p for t in turns for p in t.provided)
    if not log:  # 턴별 기록이 없는 예전 훈련: 답변에 남은 가림 표시로 짐작해요
        for i, text in enumerate(user_msgs, start=1):
            found = _MARK.findall(text) + [x.strip() for m in _ENTERED.findall(text) for x in re.split(r"[,·]", m)]
            if found:
                turns.append(TurnFact(i, "risky", _unique(found)))
        provided = _unique(p for t in turns for p in t.provided)
        requested = _unique(r for scene in scenario.get("keyScenes") or [] if isinstance(scene, dict)
                            for r in (scene.get("requestedInfo") or []) if isinstance(r, str))

    photos = _unique([PHOTO_ACTIONS[a] for a in actions if a in PHOTO_ACTIONS]
                     + [PHOTO_EVENTS[e] for t in turns for e in t.events if e in PHOTO_EVENTS])
    transfers = max(sum(1 for t in turns for e in t.events if e == "송금_누름"), 1 if "MOCK_TRANSFER" in actions else 0)
    facts = ReportFacts(
        requested=requested, provided=_unique(provided + photos), turns=turns,
        sent_link="[모의링크]" in ai_text or "OPEN" in " ".join(actions), sent_app="[모의앱]" in ai_text,
        sent_account="[모의계좌]" in ai_text, asked_photo=bool(re.search(r"사진|상품권", ai_text)),
        opened_page="MOCK_PAGE_OPEN" in actions, submitted_page="MOCK_LOGIN_ATTEMPT" in actions,
        clicked_app="MOCK_APP_CLICK" in actions or any("앱설치링크_누름" in t.events for t in turns),
        transfers=transfers, photos=photos,
        verified="OFFICIAL_CHANNEL_CHECK" in actions or any("공식경로_확인" in t.events for t in turns),
        ended_contact=any(isinstance(i, dict) and i.get("x") in _LEFT for i in log),
        scam_finished=any(isinstance(i, dict) and i.get("x") == "scam_success" for i in log),
    )
    _judge(facts)
    return facts


def _turns_of(facts: ReportFacts, *, info: str | None = None, event: str | None = None) -> str:
    nums = [t.turn for t in facts.turns if (info and info in t.provided) or (event and event in t.events)]
    return f" ({', '.join(str(n) for n in nums[:3])}번째 답변)" if nums else ""


def _judge(f: ReportFacts) -> None:
    risky_action = f.submitted_page or f.clicked_app or f.transfers > 0 or bool(f.photos)
    risky_turns = [t for t in f.turns if t.outcome == "risky"]
    if f.provided or risky_action:
        f.grade = "위험"
    elif risky_turns or not (f.verified or f.ended_contact):
        f.grade = "주의"
    else:
        f.grade = "안전"

    did, missed = f.did, f.missed
    text_info = [p for p in f.provided if p not in f.photos]
    for info in text_info:
        missed.append(f"요구받은 {_josa(info, '을', '를')} 대화창에 적었어요{_turns_of(f, info=info)}. 실제였다면 그대로 사기에 쓰였을 수 있어요.")
    kept = [r for r in f.requested if r not in f.provided]
    if kept and not text_info:
        did.append(f"상대가 요구한 {_josa(_list(kept[:4]), '을', '를')} 끝까지 알려 주지 않았어요.")
    elif kept:
        did.append(f"{_josa(_list(kept[:3]), '은', '는')} 알려 주지 않았어요.")
    if f.sent_link:
        if f.submitted_page:
            missed.append("링크를 열어 가짜 확인 화면에 정보를 입력했어요. 문자·메신저로 받은 링크에는 정보를 넣지 마세요.")
        elif f.opened_page:
            missed.append("상대가 보낸 링크를 열었어요. 정보를 넣지는 않았지만, 링크를 여는 것만으로도 위험할 수 있어요.")
        else:
            did.append("상대가 보낸 링크를 열지 않았어요.")
    if f.sent_app:
        if f.clicked_app:
            missed.append(f"대화에서 받은 앱 설치 링크를 눌렀어요{_turns_of(f, event='앱설치링크_누름')}. 원격제어 앱이면 휴대폰이 통째로 넘어갈 수 있어요.")
        else:
            did.append("앱 설치 링크를 누르지 않았어요.")
    if f.sent_account:
        if f.transfers:
            times = f" {f.transfers}번" if f.transfers > 1 else ""
            missed.append(f"안내받은 계좌로{times} 송금 버튼을 눌렀어요{_turns_of(f, event='송금_누름')}. 추가 입금 요구는 전형적인 사기 신호예요.")
        else:
            did.append("안내받은 계좌로 돈을 보내지 않았어요.")
    for photo in f.photos:
        # Spring(photoFeedback)이 붙이는 문장과 같게 써서 두 번 나오지 않게 해요
        missed.append(f"{photo} 제공 요청에 응했어요. 실제 상황에서는 출처를 확인하기 전 자료를 보내지 마세요.")
    if f.asked_photo and not f.photos and re.search(r"신분증|통장|상품권", " ".join(f.requested) or ""):
        did.append("사진 자료를 보내 달라는 요구에 응하지 않았어요.")
    if f.verified:
        did.append("공식 채널로 직접 확인하는 선택을 했어요. 가장 확실한 대응이에요.")
    if f.ended_contact:
        did.append("수상한 요구에 더 응하지 않고 먼저 대화를 끊었어요.")
    elif f.scam_finished:
        missed.append("돈을 받은 상대가 바로 대화방을 나갔어요. 실제 사기에서도 송금 직후 연락이 끊겨요.")
    if not f.verified and not f.ended_contact and f.grade != "안전":
        missed.append("대화를 끊거나 공식 채널(공식 앱·대표번호)로 확인하는 단계까지 가지 않았어요.")


def grade_sentence(f: ReportFacts) -> str:
    if f.grade == "위험":
        # 말끝을 붙여 한 문장으로: "계좌번호를 넘겼고, 송금 버튼을 눌렀어요."
        stems = []
        if f.provided:
            stems.append(f"{_josa('·'.join(f.provided[:4]), '을', '를')} 넘겼")
        if f.transfers:
            stems.append("안내받은 계좌로 송금 버튼을 눌렀")
        if f.clicked_app:
            stems.append("앱 설치 링크를 눌렀")
        if f.submitted_page:
            stems.append("가짜 확인 화면에 정보를 입력했")
        body = ("고, ".join(stems) + "어요.") if stems else "요구에 응한 행동이 있었어요."
        return f"결과: 위험. {body}"
    if f.grade == "주의":
        return "결과: 주의. 정보나 돈을 넘기지는 않았지만, 요구에 응하려 하거나 끝까지 확인·차단하지 않은 순간이 있었어요."
    return "결과: 안전. 요구에 응하지 않고 대화를 끊거나 공식 채널로 확인했어요."


def fallback_turns(f: ReportFacts) -> list[dict]:
    """LLM 이 실패했을 때 기록만으로 만드는 타임라인"""
    names = {"송금_누름": "안내받은 계좌로 송금 버튼을 누름", "앱설치링크_누름": "앱 설치 링크를 누름",
             "확인페이지_제출": "링크 화면에 정보를 입력함", "공식경로_확인": "공식 채널로 확인함", **{k: f"{v}을 보냄" for k, v in PHOTO_EVENTS.items()}}
    out = []
    for t in f.turns:
        if t.outcome not in ("safe", "risky"):
            continue
        what = [names[e] for e in t.events if e in names] + ([f"{'·'.join(t.provided)} 입력"] if t.provided else [])
        action = ", ".join(what) or ("요구에 응하려 함" if t.outcome == "risky" else "의심하거나 거절함")
        feedback = ("요구받은 정보·돈·자료는 상대가 누구든 대화 중에 넘기지 말고, 먼저 공식 채널로 확인하세요."
                    if t.outcome == "risky" else "바로 응하지 않고 의심한 좋은 대응이에요.")
        out.append({"turn": t.turn, "outcome": t.outcome, "userAction": action[:150], "feedback": feedback})
    return out[:6]
