"""사기범 대사의 말투(반말·존댓말)를 대화 내내 하나로 맞춰요.

LLM 은 프롬프트로 말투를 고정하라고 해도 가끔 섞어 써요 ("엄마 나야 ㅠㅠ 이거 확인해 주세요").
그래서 첫 메시지(시나리오 openingMessage)의 말투를 기준으로 정하고,
- output_guard 가 섞인 대사를 찾아 LLM 에 한 번 다시 쓰게 하고
- 그래도 섞여 있으면 conform() 이 문장 끝을 기준 말투로 바꿔요. 바꿀 수 없는 문장은 빼요 (다른 문장이 남을 때만).

문장 끝 말투만 봐요. 명사로 끝나는 문장("송장 번호 확인")처럼 말투를 알 수 없는 문장은 건드리지 않아요.
"""
from __future__ import annotations

import re

CASUAL = "반말"
POLITE = "존댓말"

# 문장 끝에 붙는 이모티콘·문장부호·소품 표시는 떼어 놓고 말끝만 봐요
_TAIL = re.compile(r"(?:\s|[.?!~…,ㅠㅜㅋㅎ^;:)(♡♥\-]|[\U0001F300-\U0001FAFF☀-➿]|\[[^\]\n]{0,60}\])+$")
_SPLIT = re.compile(r"(?<=[.?!…])\s+|\n+|(?<=[ㅠㅜㅋㅎ])\s+(?=\S)|(?<=~)\s+")

_POLITE_END = re.compile(r"(?:요|니다|니까|세요|십시오|시오|시죠|죠)$")
_CASUAL_END = re.compile(
    r"(?:[했있없됐갔왔봤줬였겠었았같많싫좋알]어|괜찮아|같아|좋아|알아|몰라|고마워|미안해|부탁해|급해|"
    r"잖아|거든|거야|건데|거지|[이]야|[나너냐]야|할게|갈게|줄게|볼게|할래|줄래|볼래|"
    r"해줘|줘|해봐|봐|하자|가자|보자|알지|맞지|그치|했지|있지|없지|하지|"
    r"하니|했니|있니|없니|하냐|했냐|뭐냐|된대|한대|했대|는데|던데|돼|해|그래|[하했있겠]네|[가-힣]야)$")
# '해'로 끝나는 명사 (피해·이해 등)는 반말로 보지 않아요
_NOUN_HAE = re.compile(r"(?:피해|이해|손해|방해|위해|침해|재해|오해|훼손해|분야|시야)$")


def _split_tail(sentence: str) -> tuple[str, str]:
    m = _TAIL.search(sentence)
    if not m:
        return sentence, ""
    return sentence[:m.start()], sentence[m.start():]


def level_of_sentence(sentence: str) -> str | None:
    core, _ = _split_tail(sentence.strip())
    core = core.rstrip()
    if not core:
        return None
    if _POLITE_END.search(core):
        return POLITE
    if _CASUAL_END.search(core) and not _NOUN_HAE.search(core):
        return CASUAL
    return None


def sentences(text: str) -> list[str]:
    return [s for s in _SPLIT.split(text or "") if s and s.strip()]


def detect(text: str) -> str | None:
    """글 전체가 어느 말투인지. 문장 끝을 세어 많은 쪽 (같으면 None)"""
    counts = {POLITE: 0, CASUAL: 0}
    for s in sentences(text):
        lv = level_of_sentence(s)
        if lv:
            counts[lv] += 1
    if counts[POLITE] == counts[CASUAL]:
        return None
    return POLITE if counts[POLITE] > counts[CASUAL] else CASUAL


def mismatched(text: str, level: str | None) -> list[str]:
    """기준 말투와 다른 말투로 끝나는 문장들"""
    if level not in (POLITE, CASUAL):
        return []
    return [s for s in sentences(text) if (lv := level_of_sentence(s)) and lv != level]


# ---------------- 말끝 바꾸기 ----------------
# (끝말, 바꿀 말) 위에서부터 먼저 맞는 것

_TO_POLITE = [
    ("해줘", "해 주세요"), ("보내줘", "보내 주세요"), ("줘", "주세요"), ("해봐", "해 보세요"), ("봐", "보세요"),
    ("하자", "하시죠"), ("가자", "가시죠"), ("보자", "보시죠"),
    ("거야", "거예요"), ("이야", "이에요"), ("나야", "저예요"), ("너야", "당신이에요"),
    ("뭐냐", "뭐예요"), ("하냐", "하세요"), ("했냐", "했어요"),
    ("하니", "하세요"), ("했니", "했어요"), ("있니", "있어요"), ("없니", "없어요"),
    ("알지", "아시죠"), ("그치", "그렇죠"),
    ("지", "죠"), ("야", "예요"),
]
_TO_CASUAL = [
    ("안녕하세요", "안녕"), ("감사합니다", "고마워"), ("감사해요", "고마워"), ("죄송합니다", "미안해"), ("죄송해요", "미안해"),
    ("습니까", "어"), ("합니까", "해"), ("입니까", "야"), ("됩니까", "돼"),
    ("했습니다", "했어"), ("됐습니다", "됐어"), ("있습니다", "있어"), ("없습니다", "없어"), ("겠습니다", "겠어"),
    ("었습니다", "었어"), ("았습니다", "았어"), ("였습니다", "였어"), ("같습니다", "같아"), ("좋습니다", "좋아"),
    ("합니다", "해"), ("입니다", "이야"), ("됩니다", "돼"), ("드립니다", "줄게"), ("갑니다", "가"), ("봅니다", "봐"),
    ("보내 주세요", "보내 줘"), ("해 주세요", "해 줘"), ("주세요", "줘"), ("주시겠어요", "줄래"), ("주실래요", "줄래"),
    ("하세요", "해"), ("보세요", "봐"), ("오세요", "와"), ("가세요", "가"), ("주시죠", "줘"), ("하시죠", "하자"),
    ("거예요", "거야"), ("이에요", "이야"), ("예요", "야"), ("네요", "네"), ("군요", "구나"),
    ("죠", "지"),
]
_DROP_YO = re.compile(r"(?:[어아해돼게래데걸까지워와줘봐야]|잖아|거든|는데|던데)요$")


def _to_polite(core: str) -> str | None:
    for old, new in _TO_POLITE:
        if core.endswith(old):
            return core[: -len(old)] + new
    return core + "요"   # 했어→했어요, 할게→할게요, 잖아→잖아요, 는데→는데요, 해→해요


def _to_casual(core: str) -> str | None:
    for old, new in (("주실 수", "줄 수"), ("하실 수", "할 수"), ("보실 수", "볼 수"), ("해 주시면", "해 주면"), ("주시면", "주면")):
        core = core.replace(old, new)
    for old, new in _TO_CASUAL:
        if core.endswith(old):
            return core[: -len(old)] + new
    if _DROP_YO.search(core):
        return core[:-1]   # 했어요→했어, 할게요→할게, 잖아요→잖아
    return None


def conform(text: str, level: str | None) -> str:
    """문장마다 말끝을 기준 말투로 맞춰요. 못 바꾸는 문장은 빼요 (모두 빠지면 원문 그대로)"""
    if level not in (POLITE, CASUAL) or not text:
        return text
    parts = re.split(r"(\n+)", text)  # 줄바꿈(말풍선 나눔)은 그대로 둬요
    out: list[str] = []
    changed = False
    for part in parts:
        if not part or part.startswith("\n"):
            out.append(part)
            continue
        pieces = re.split(r"((?<=[.?!…~ㅠㅜㅋㅎ])\s+)", part)
        kept: list[str] = []
        for piece in pieces:
            lv = level_of_sentence(piece) if piece.strip() else None
            if not lv or lv == level:
                kept.append(piece)
                continue
            core, tail = _split_tail(piece)
            lead_ws = core[: len(core) - len(core.lstrip())]
            core = core.strip()
            fixed = _to_polite(core) if level == POLITE else _to_casual(core)
            changed = True
            if fixed is None:
                continue  # 못 바꾸는 문장은 빼요
            if level == CASUAL and fixed.startswith("네, "):
                fixed = "응, " + fixed[3:]
            kept.append(lead_ws + fixed + tail)
        out.append("".join(kept).strip() if kept else "")
    result = "".join(out).strip()
    result = re.sub(r"\n{2,}", "\n", result)
    if level == CASUAL:
        result = re.sub(r"(^|\s)네([.,!…])", r"\1응\2", result)  # 대답 "네" → "응"
    if not changed:
        return text
    return result if result else text
