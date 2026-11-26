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


# ---------------- 이름·주소 (외부 AI로 보내기 전에 규칙으로 먼저 가림) ----------------
# 이름·주소는 정해진 숫자 모양이 없어서 예전에는 AI가 찾아 가렸어요(원문이 AI로 먼저 감).
# 흔한 형태는 여기서 먼저 가리고, AI는 규칙이 놓친 것만 보조로 찾게 해요.

# 주소: "서울시 마포구 ○○로 12", "○○구 ○○동 123-4", "101동 202호" 같은 형태
ADDRESS_PATTERN = re.compile(
    r"(?:(?:[가-힣]{1,10}(?:특별시|광역시|특별자치시|특별자치도|도|시)|서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)\s+)?"   # 시·도 (없어도 됨)
    r"[가-힣]{1,10}(?:시|군|구)\s+"                                           # 시·군·구
    r"[가-힣0-9]{1,15}(?:(?:로|길)(?=\s|\d|$)|(?:동|읍|면|리)(?=\s*\d))"                                  # 도로명·동
    r"(?:\s*\d{1,5}(?:-\d{1,5})?(?:\s*번길\s*\d{1,5}(?:-\d{1,5})?)?)?"       # 번지
    r"(?:[\s,]*[가-힣A-Za-z0-9]{0,15}(?:아파트|빌라|오피스텔|맨션))?"
    r"(?:[\s,]*\d{1,4}\s*동)?(?:[\s,]*\d{1,5}\s*호)?"
)
UNIT_PATTERN = re.compile(r"(?<![\d가-힣])\d{1,4}\s*동\s*\d{1,5}\s*호")

# 이름: "제 이름은 홍길동", "성함: 홍길동" 처럼 이름이라고 밝힌 경우
NAMED_PATTERN = re.compile(r"(이름|성함|본명)(?:은|는|이|가)?\s*[:：]?\s*(?:제\s*)?([가-힣]{2,4}?)(?=$|[\s,.!?]|입니다|이에요|예요|이요|요|이고|이구요)")
# 이름을 요구받은 직후 "홍길동", "홍길동입니다", "홍길동이고 …" 처럼 답한 경우 (흔한 성씨로 시작할 때만)
SURNAMES = set("김이박최정강조윤장임한오서신권황안송류유홍전고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부")
NAME_ONLY = re.compile(r"^\s*([가-힣]{2,4}?)(?:입니다|이에요|예요|이요|요)?\s*[.!]?\s*$")
NAME_LEAD = re.compile(r"^\s*([가-힣]{2,4}?)(?:이고|이구요|이고요|고요|,)\s*")
NOT_NAMES = {"아니요", "아니오", "싫어요", "몰라요", "모르겠어요", "왜요", "네네", "그래요", "알겠어요", "잠시만요",
             "누구세요", "누구", "어디", "무슨", "기억", "모름", "또는", "별명", "닉네임", "대신", "괜찮아요", "글쎄요", "안돼요", "됐어요", "고객님", "확인", "정말"}


def _labels(requested_info: list[str], words: tuple[str, ...], default: str) -> list[str]:
    hit = [r for r in requested_info if any(w in r for w in words)]
    return hit[:1] or [default]


def _looks_like_name(word: str) -> bool:
    return 2 <= len(word) <= 4 and word[0] in SURNAMES and word not in NOT_NAMES and not word.endswith(("요", "다", "까", "죠", "네"))


# 11/24 팀 결정: 이름은 개인정보로 가리지 않아요 (상황 파악에서 이름을 받아 시나리오에 써요).
# 이름을 알려 줬는지는 그대로 찾아서 '입력함'으로 기록해요 (사기범에게 이름을 준 행동은 리포트 대상).
MASK_NAMES = False


def mask_name_address(text: str, requested_info: list[str] | None = None,
                      mask_names: bool | None = None) -> tuple[str, list[str]]:
    """주소를 [주소]로 가리고, 이름은 찾기만 해요(MASK_NAMES=False). 찾은 종류를 돌려준다.

    requested_info: 사기범이 방금 요구한 정보. '이름'을 요구받았을 때만 이름 단독 답변을 이름으로 봐요
    (평소 대화의 짧은 답을 이름으로 잘못 가리지 않게).
    """
    requested_info = requested_info or []
    found: list[str] = []
    address_label = _labels(requested_info, ("주소", "배송지", "거주지"), "주소")[0]
    if ADDRESS_PATTERN.search(text) or UNIT_PATTERN.search(text):
        text = UNIT_PATTERN.sub(f"[{address_label}]", ADDRESS_PATTERN.sub(f"[{address_label}]", text))
        found.append(address_label)

    mask_names = MASK_NAMES if mask_names is None else mask_names
    name_label = _labels(requested_info, ("이름", "성함", "성명"), "이름")[0]
    asked_name = any(w in r for r in requested_info for w in ("이름", "성함", "성명"))
    masked = False

    def named(match: re.Match) -> str:
        nonlocal masked
        # Joined conjunction "이름이나" is a question label, while a declared
        # actual name such as "이름은 이나" must still be masked.
        if match.group(0) == match.group(1) + "이나" or match.group(2) in NOT_NAMES:
            return match.group(0)
        masked = True
        return match.group(0).replace(match.group(2), f"[{name_label}]", 1) if mask_names else match.group(0)

    text = NAMED_PATTERN.sub(named, text)
    if asked_name and not masked:
        only = NAME_ONLY.match(text)
        lead = NAME_LEAD.match(text)
        if only and _looks_like_name(only.group(1)):
            if mask_names:
                text = text.replace(only.group(1), f"[{name_label}]", 1)
            masked = True
        elif lead and _looks_like_name(lead.group(1)):
            if mask_names:
                text = text.replace(lead.group(1), f"[{name_label}]", 1)
            masked = True
    if masked:
        found.append(name_label)
    return text, found
