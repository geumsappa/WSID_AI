"""실제 회사·서비스 이름을 가상의 이름으로 바꿔요.

사기범 역할에 실제 브랜드(당근마켓, 은행, 택배사 등)가 나오면 그 회사가 사기에 관련된 것처럼 보일 수 있어서
시나리오와 사기범 대사에서는 가상의 이름만 써요. 사용자가 상황 파악 때 쓴 원래 말은 바꾸지 않아요.

공공기관(경찰청·검찰청·금융감독원 등)은 '기관 사칭' 수법을 알아보는 게 훈련의 핵심이라 그대로 둬요.
"""
from __future__ import annotations

import re

# (실제 이름 정규식, 가상 이름) — 긴 이름부터 바꿔요
_BRANDS: list[tuple[str, str]] = [
    # 중고거래·쇼핑
    (r"당근\s?마켓|당근\s?페이", "모두마켓"),
    (r"번개\s?장터", "한빛장터"),
    (r"중고\s?나라|헬로\s?마켓", "이웃장터"),
    (r"쿠팡|11번가|지마켓|G마켓|위메프|티몬|무신사|에이블리", "한빛몰"),
    # 메신저·포털·간편결제
    (r"카카오\s?톡|카톡", "메신저"),
    (r"카카오\s?페이|네이버\s?페이|토스\s?페이|페이코|삼성\s?페이", "간편페이"),
    (r"카카오\s?뱅크|토스\s?뱅크|케이\s?뱅크", "한빛뱅크"),
    (r"(?<![가-힣])토스(?![트터카])", "간편송금 앱"),
    (r"네이버|다음\s?메일|구글", "포털"),
    (r"텔레그램|인스타그램|인스타|페이스북|라인(?=\s?메신저)", "SNS"),
    # 은행·카드·증권
    (r"(?:KB\s?)?국민\s?은행|신한\s?은행|우리\s?은행|하나\s?은행|(?:NH\s?)?농협(?:\s?은행)?|(?:IBK\s?)?기업\s?은행"
     r"|SC\s?제일\s?은행|씨티\s?은행|새마을\s?금고|신협|부산\s?은행|대구\s?은행|iM\s?뱅크|경남\s?은행|광주\s?은행", "한빛은행"),
    (r"(?:KB\s?)?국민\s?카드|신한\s?카드|삼성\s?카드|현대\s?카드|롯데\s?카드|우리\s?카드|하나\s?카드|BC\s?카드|비씨\s?카드", "한빛카드"),
    (r"키움\s?증권|미래에셋|삼성\s?증권|한국투자\s?증권|NH\s?투자\s?증권", "한빛증권"),
    # 택배·우편
    (r"CJ\s?대한통운|대한통운|로젠\s?택배|한진\s?택배|롯데\s?택배|경동\s?택배|우체국\s?택배", "한빛택배"),
    (r"우체국", "우편센터"),
    # 통신
    (r"SK\s?텔레콤|(?<![A-Za-z])SKT(?![A-Za-z])|LG\s?유플러스|LG\s?U\+|(?<![A-Za-z])KT(?![A-Za-z])", "한빛통신"),
]
# 바꾼 이름 뒤 조사를 받침에 맞게 고쳐요 ("카톡으로" → "메신저로")
_JOSA = r"(으로|로|은|는|이랑|랑|이|가|을|를|과|와)?"
_PAIRS = {"으로": ("으로", "로"), "로": ("으로", "로"), "은": ("은", "는"), "는": ("은", "는"),
          "이랑": ("이랑", "랑"), "랑": ("이랑", "랑"), "이": ("이", "가"), "가": ("이", "가"),
          "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와")}
_COMPILED = [(re.compile(f"(?:{pattern}){_JOSA}"), name) for pattern, name in _BRANDS]


def _josa(name: str, particle: str | None) -> str:
    if not particle:
        return ""
    last = name[-1]
    if not ("가" <= last <= "힣"):
        return particle  # 영문 등은 원래 조사 그대로
    final = (ord(last) - 0xAC00) % 28  # 받침 번호 (0이면 받침 없음, 8은 ㄹ)
    with_final, without = _PAIRS[particle]
    if with_final == "으로":
        return "로" if final in (0, 8) else "으로"
    return with_final if final else without


def fictionalize(text: str) -> str:
    """글 속 실제 브랜드 이름을 가상 이름으로 바꿔요."""
    if not isinstance(text, str) or not text:
        return text
    for pattern, name in _COMPILED:
        text = pattern.sub(lambda m, name=name: name + _josa(name, m.group(m.lastindex) if m.lastindex else None), text)
    return text


def fictionalize_tree(value):
    """시나리오 JSON 같은 중첩 구조 안의 모든 글을 바꿔요 (키는 그대로)."""
    if isinstance(value, str):
        return fictionalize(value)
    if isinstance(value, list):
        result = [fictionalize_tree(item) for item in value]
        # allowedOrganizations 처럼 이름 목록은 바꾼 뒤 겹치는 이름을 하나로
        return list(dict.fromkeys(result)) if all(isinstance(item, str) for item in result) else result
    if isinstance(value, dict):
        return {key: fictionalize_tree(item) for key, item in value.items()}
    return value
