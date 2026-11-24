"""
사기범이 대화 중에 보내는 '사진·화면' 소품 정리

실제 사기범은 글만 보내지 않아요. 이름을 적어 찍은 물건 인증샷, 가짜 송장, 입금 완료 캡처,
가짜 사건 조회 공문, 대출 승인 화면을 보내 믿게 만들어요. 훈련 대화에서도 이걸 보여 주려고
LLM 이 대사에 아래 표시를 붙이면 프론트가 그림 카드로 그려요 (실제 사진·실제 문서는 쓰지 않아요).

    [사진:상품|물건|판매자 이름]       물건 인증샷 (손글씨 메모에 이름·날짜)
    [사진:송장|택배사|물건]            택배 송장 사진 (운송장 번호는 가려져 있음)
    [사진:입금확인|금액|보낸 사람]      이체 완료 화면 캡처
    [공문|기관|문서 제목]              사건 조회 결과 문서 (큰 '견본' 워터마크)
    [대출승인|금융사|한도]             대출 승인 안내 화면

여기서는 LLM 이 쓴 표시를 정해진 모양으로 고치고(띄어쓰기·전각 문자·빠진 값),
한 메시지에 하나만, 대화 전체에서 종류별 한도까지만 남겨요. 값에 긴 숫자·대괄호가 있으면 지워요.
"""
from __future__ import annotations

import re

# 종류 → (대화 전체 한도, 값이 없을 때 기본값 두 개)
KINDS: dict[str, tuple[int, tuple[str, str]]] = {
    "사진:상품": (2, ("물건", "")),
    "사진:송장": (1, ("한결택배", "물건")),
    "사진:입금확인": (1, ("", "")),
    "공문": (1, ("수사기관", "사건 조회 결과")),
    "대출승인": (1, ("한빛캐피탈", "최대 3천만원")),
}
# LLM 이 조금 다르게 써도 알아보게 (예: "[사진: 입금 확인 | 35만원]", "［공문｜...］")
_TOKEN = re.compile(
    r"[\[［]\s*(사진\s*[:：]\s*(?:상품|물건|인증샷?|송장|운송장|입금\s*확인|이체\s*확인|이체\s*완료)|공문|대출\s*승인)\s*"
    r"((?:[|｜][^\]］\n]*)?)[\]］]")
_BROKEN_TAIL = re.compile(r"[\[［][^\]］\n]{0,80}$")  # 350자에서 잘려 닫히지 않은 표시
_LONG_DIGITS = re.compile(r"\d[\d\s-]{4,}\d")
PARAM_LIMIT = 14


def _kind(raw: str) -> str | None:
    s = re.sub(r"\s+", "", raw).replace("：", ":")
    if s.startswith("사진:"):
        body = s[3:]
        if body in ("상품", "물건") or body.startswith("인증"):
            return "사진:상품"
        if "송장" in body:
            return "사진:송장"
        if "입금" in body or "이체" in body:
            return "사진:입금확인"
        return None
    if s == "공문":
        return "공문"
    if s == "대출승인":
        return "대출승인"
    return None


def _param(value: str) -> str:
    value = _LONG_DIGITS.sub("", value)  # 계좌·전화번호처럼 긴 숫자는 넣지 않아요
    value = re.sub(r"[\[\]［］|｜<>{}\\]", "", value)
    value = " ".join(value.split())
    return value[:PARAM_LIMIT].strip()


def canonical(kind: str, params: list[str], persona_name: str = "") -> str:
    limit, defaults = KINDS[kind]
    a = _param(params[0]) if len(params) > 0 else ""
    b = _param(params[1]) if len(params) > 1 else ""
    a = a or defaults[0]
    b = b or defaults[1]
    # 이름이 들어갈 자리가 비면 사기범(persona) 이름을 써요
    if kind in ("사진:상품", "사진:입금확인") and not b:
        b = _param(persona_name)
    return "[" + kind + "".join("|" + p for p in (a, b) if p) + "]"


def count_used(persona_lines: list[str]) -> dict[str, int]:
    used = {kind: 0 for kind in KINDS}
    for line in persona_lines:
        for m in _TOKEN.finditer(line):
            kind = _kind(m.group(1))
            if kind:
                used[kind] += 1
    return used


def find(text: str) -> list[str]:
    """대사 안의 사진 소품 종류 목록 (검사용)"""
    return [k for m in _TOKEN.finditer(text) for k in [_kind(m.group(1))] if k]


def strip(text: str) -> str:
    """사진 소품 표시를 뺀 글 (명분 길이·소품 위치 검사용)"""
    return " ".join(_TOKEN.sub(" ", text).split())


def clean(text: str, persona_lines: list[str], persona_name: str = "") -> str:
    """LLM 대사의 사진 소품 표시를 정리해요. 한 메시지에 하나, 대화 전체 종류별 한도까지."""
    text = _BROKEN_TAIL.sub("", text).rstrip()
    used = count_used(persona_lines)
    kept = 0

    def repl(m: re.Match) -> str:
        nonlocal kept
        kind = _kind(m.group(1))
        if not kind or kept >= 1 or used[kind] >= KINDS[kind][0]:
            return ""
        kept += 1
        used[kind] += 1
        params = [p for p in re.split(r"[|｜]", m.group(2) or "")[1:]]
        return canonical(kind, params, persona_name)

    out = _TOKEN.sub(repl, text)
    # 표시를 지운 자리의 빈칸·빈 줄 정리 (줄바꿈은 말풍선 나누기라 살려요)
    out = "\n".join(" ".join(line.split()) for line in out.split("\n"))
    out = re.sub(r"\n{2,}", "\n", out).strip()
    return out


def describe(text: str) -> str:
    """평가(리포트)용: 표시를 사람이 읽는 말로 바꿔요."""
    names = {"사진:상품": "물건 인증 사진", "사진:송장": "택배 송장 사진", "사진:입금확인": "입금 완료 화면 캡처",
             "공문": "사건 조회 공문 이미지", "대출승인": "대출 승인 화면"}

    def repl(m: re.Match) -> str:
        kind = _kind(m.group(1))
        return f"({names[kind]}을 보냄)" if kind else m.group(0)
    return _TOKEN.sub(repl, text)
