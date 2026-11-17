"""개인정보 감지·가림.

이 서비스는 사용자가 개인정보를 입력해도 대화를 막지 않습니다.
대신 (1) 실제 값은 [계좌번호] 처럼 가려서 LLM·저장소·로그 어디에도 남기지 않고
     (2) "어떤 종류를 입력했는지"만 기록해 결과 리포트에 씁니다.

AI(사기범 역할)가 개인정보를 '묻는 것'은 막지 않습니다. 묻고, 사용자가 넘기는지 보는 것이 훈련의 핵심입니다.

정규식(regex)은 '이런 모양의 글자'를 찾는 패턴입니다. 예) \\d{6} = 숫자 6개
(?<!\\d) 와 (?!\\d) 는 "앞뒤에 다른 숫자가 붙어있지 않을 때만" 이라는 뜻입니다.
"""
import re

# 순서가 중요합니다. 위에서부터 차례대로 찾아서 가립니다.
SENSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("주민등록번호", re.compile(r"(?<!\d)\d{6}\s*-?\s*[1-4]\d{6}(?!\d)")),
    ("카드번호", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("전화번호", re.compile(r"(?<!\d)01[016789][\s-]?\d{3,4}[\s-]?\d{4}(?!\d)")),
    ("계좌번호", re.compile(r"(?<!\d)\d{2,6}-\d{2,6}-\d{2,8}(?!\d)|(?<!\d)\d{10,14}(?!\d)")),
    ("이메일", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    (
        "비밀번호",
        re.compile(
            r"(비밀번호|비번|패스워드|password|pw|PIN|핀번호)\s*(은|는|:|=)\s*[A-Za-z0-9!@#$%^&*]{4,}",
            re.IGNORECASE,
        ),
    ),
]

# 사기범이 '숫자로 된 정보'를 요구한 직후라면, 짧은 숫자(4~8자리)도 그 정보로 보고 가립니다.
# 예) "인증번호 알려주세요" 다음에 사용자가 "482913" 이라고 답한 경우
NUMERIC_INFO_WORDS = ["인증번호", "OTP", "생년월일", "비밀번호", "보안카드", "CVC", "CVV", "유효기간"]
SHORT_NUMBER = re.compile(r"(?<![\d\[])\d{4,8}(?!\d)(?!\s*(원|만\s*원|천\s*원|개|명|시|분|일|월|년|번째|턴|살|층|호))")


def mask_sensitive(text: str) -> tuple[str, list[str]]:
    """민감정보를 [주민등록번호] 같은 표시로 바꾸고, 찾은 종류 목록을 돌려준다."""
    found: list[str] = []
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            found.append(label)
            text = pattern.sub(f"[{label}]", text)
    return text, found


def mask_requested_numbers(text: str, requested_info: list[str]) -> tuple[str, list[str]]:
    """요구받은 숫자형 정보(인증번호 등)에 해당하는 짧은 숫자를 가린다. (이미 가려진 글에 추가로 적용)"""
    labels = [r for r in requested_info if any(w.lower() in r.lower() for w in NUMERIC_INFO_WORDS)]
    if not labels or not SHORT_NUMBER.search(text):
        return text, []
    label = labels[0]
    return SHORT_NUMBER.sub(f"[{label}]", text), [label]
