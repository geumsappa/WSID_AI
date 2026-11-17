"""연락처·링크 금지 패턴. Spring SolutionExplanationValidator 의 FORBIDDEN 목록을 파이썬으로 옮긴 것.
근거 문단(excerpt)을 만들 때와 모델 출력을 검사할 때 같은 규칙을 씁니다.
Java 의 \\p{L} 같은 유니코드 속성은 파이썬 기본 re 에 없어서 비슷한 범위로 바꿨어요.
"""
import re
import unicodedata

NOT_UNIT = r"(?!\s*(?:개|건|명|원|만|천|억|일|월|년|시|분|초|회|장|쪽|가지|단계|%))"
FORBIDDEN = [
    re.compile(r"(?i)\b(?:[a-z][a-z0-9+.\-]*://|(?:https?|ftp|file|tel|mailto|sms|data|javascript)\s*:|www\.)"),
    re.compile(r"(?i)\b(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b"),
    re.compile(r"[\w.%+\-]+@[\w.\-]+\.[^\W\d_]{2,63}"),
    re.compile(r"(?is)</?[a-z][a-z0-9\-]*(?:\s[^<>]*)?/?>|<[^<>\s]*[@:/][^<>\s]*>|<!--"),
    re.compile(r"!?\[[^\]\r\n]*\](?:\(|\[)|^\s*\[[^\]\r\n]+\]\s*:", re.M),
    re.compile(r"(?<!\d)0\d{8,10}(?!\d)"),
    re.compile(r"(?<!\d)(?:\(?\d{2,4}\)?[\-.\s]+){1,2}\d{3,4}(?!\d)" + NOT_UNIT),
    re.compile(r"(?<!\d)1[568]\d{2}[\-.\s]*\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\+\d(?:[\-.\s]*\d){7,14}(?!\d)"),
    re.compile(r"(?<!\d)(?:112|119|118|182|1332)(?!\d)" + NOT_UNIT),
]
# 근거 문단에서는 Spring 목록에 없는 대표번호도 빼요(연락처는 카드에서만 보여주는 정책).
EXTRA_FOR_EXCERPT = [re.compile(r"(?<!\d)(?:1394|114|132)(?!\d)" + NOT_UNIT)]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def has_forbidden(text: str, strict: bool = False) -> bool:
    t = _normalize(text)
    pats = FORBIDDEN + (EXTRA_FOR_EXCERPT if strict else [])
    return any(p.search(t) for p in pats)


SENT_SPLIT = re.compile(r"(?<=[.!?다요])\s+")


def strip_contact_sentences(text: str) -> tuple[str, int]:
    """연락처·링크가 든 '문장 전체'를 뺀다(문장 일부를 잘라 의미를 바꾸지 않음).
    돌려주는 값: (남은 문단, 뺀 문장 수)"""
    kept, dropped = [], 0
    for s in SENT_SPLIT.split(text.strip()):
        if has_forbidden(s, strict=True):
            dropped += 1
        else:
            kept.append(s)
    return " ".join(kept).strip(), dropped
