"""시나리오 분류 목록과 mock·테스트용 출력 표본. 실제 생성 실패의 대체 응답으로 사용하지 않는다.

- SIM-003: 위험 유형은 명세의 6분류(대분류) + 세부 유형으로 관리
- 팀 결정: 보이스피싱(전화) 유형은 제외 → 채널에 '전화'가 없음
- 기관·회사 이름은 전부 가상 (실존 이름 사용 금지)
- 링크는 실제 주소 대신 [모의링크] 로만 표시 → 화면에서 모의 페이지로 연결
"""

# SIM-003 위험 유형 대분류 (요구사항 명세 기준 6종)
RISK_TYPES = ["메신저피싱", "스미싱", "대출빙자", "기관사칭", "투자·가상자산", "기타"]

# 세부 유형 → 대분류. 시나리오는 세부 유형 단위로 만들고, 대분류는 여기서 자동으로 정해짐
SUBTYPE_TO_RISK_TYPE = {
    "결제·환불 사칭": "스미싱",
    "지인·가족 사칭": "메신저피싱",
    "택배 사칭": "스미싱",
    "부고·청첩장 사칭": "스미싱",
    "대출 사기": "대출빙자",
    "공공기관 사칭": "기관사칭",
    "투자 리딩방": "투자·가상자산",
    "중고거래 사기": "기타",
    "채용·아르바이트 사기": "기타",
}
# Public training codes are independent from the user's everyday activity categories.
SUBTYPE_TO_FRAUD_CODE = {
    "결제·환불 사칭": "SMISHING",
    "지인·가족 사칭": "MESSENGER_PHISHING", "택배 사칭": "SMISHING", "부고·청첩장 사칭": "SMISHING",
    "대출 사기": "LOAN_FRAUD", "공공기관 사칭": "INSTITUTION_IMPERSONATION",
    "투자 리딩방": "INVESTMENT_FRAUD", "중고거래 사기": "SECONDHAND_TRADE", "채용·아르바이트 사기": "JOB_SCAM",
}
ALLOWED_SUBTYPES = list(SUBTYPE_TO_RISK_TYPE)

ALLOWED_CHANNELS = ["문자", "메신저", "중고거래 앱 채팅", "이메일", "SNS 메시지"]

# 사기범이 각 장면에서 사용자에게 시키려는 행동
ALLOWED_REQUESTS = ["링크 클릭", "개인정보 제공", "송금", "앱 설치", "대화 지속"]

# 모의 페이지 템플릿 (SIM-014). 없으면 None
ALLOWED_MOCK_PAGES = ["금융", "공공민원", "채용"]

FALLBACK_SCENARIOS = {
    "중고거래 사기": {
        "analysis": {
            "riskSignals": [
                {"signal": "중고거래 앱 사용", "reason": "앱 밖 결제 링크로 유도하는 사기의 주요 대상"},
            ],
            "judgmentPoints": [
                {"id": "J1", "description": "앱 밖에서 보낸 결제 링크를 열지 판단"},
                {"id": "J2", "description": "판매 대금을 받기 위해 정보 입력·송금을 요구받을 때 판단"},
            ],
        },
        "scenario": {
            "fraudSubtype": "중고거래 사기",
            "title": "안전결제 링크를 보내온 구매자",
            "channel": "중고거래 앱 채팅",
            "persona": {"name": "김도윤", "role": "구매 희망자", "organization": "모두마켓 이용자", "tone": "친절하지만 서두르는 말투"},
            "background": "모두마켓에 올린 물건을 사고 싶다는 사람에게서 채팅이 왔습니다.",
            "openingMessage": "안녕하세요! 올리신 물건 아직 있나요? 제가 지방이라 직거래는 어렵고, 안전결제로 바로 살게요.",
            "keyScenes": [
                {"order": 1, "judgmentPointId": "J1", "goal": "앱 밖 결제 페이지로 유도", "tactic": "지방이라 직거래 불가, 빨리 사고 싶다며 호감을 얻음", "request": "링크 클릭", "requestedInfo": []},
                {"order": 2, "judgmentPointId": "J2", "goal": "가짜 결제 페이지에서 정보 입력 유도", "tactic": "결제가 끝났으니 판매 대금을 받으려면 계좌 인증이 필요하다고 함", "request": "개인정보 제공", "requestedInfo": ["계좌번호", "전화번호"]},
                {"order": 3, "judgmentPointId": "J2", "goal": "추가 송금 유도", "tactic": "수수료가 안 들어와 결제가 보류됐다며 재촉", "request": "송금", "requestedInfo": []},
            ],
            "expectedActions": [
                {"id": "A1", "judgmentPointId": "J1", "description": "앱 밖에서 보낸 결제 링크를 열지 않는다"},
                {"id": "A2", "judgmentPointId": "J2", "description": "판매 대금을 받으려고 계좌 인증·수수료 송금을 하지 않는다"},
                {"id": "A3", "judgmentPointId": "J2", "description": "앱 안의 공식 거래 화면에서 결제 상태를 직접 확인한다"},
            ],
            "mockPage": "금융",
        },
    },
    "택배 사칭": {
        "analysis": {
            "riskSignals": [
                {"signal": "택배·배송 이용", "reason": "배송 문제를 핑계로 한 문자 링크 사기의 주요 대상"},
            ],
            "judgmentPoints": [
                {"id": "J1", "description": "출처가 불분명한 배송 문자 링크를 열지 판단"},
                {"id": "J2", "description": "주소 확인 명목으로 개인정보를 요구받을 때 판단"},
            ],
        },
        "scenario": {
            "fraudSubtype": "택배 사칭",
            "title": "주소 불일치로 반송된다는 배송 문자",
            "channel": "문자",
            "persona": {"name": "한빛택배 고객센터", "role": "택배사 직원 사칭", "organization": "한빛택배(가상)", "tone": "사무적이고 기한을 강조하는 말투"},
            "background": "택배를 기다리던 중, 배송에 문제가 생겼다는 문자를 받았습니다.",
            "openingMessage": "[한빛택배] 고객님 상품이 주소 불일치로 보관 중입니다. 오늘 안에 주소를 확인하지 않으면 반송됩니다. [모의링크]",
            "keyScenes": [
                {"order": 1, "judgmentPointId": "J1", "goal": "문자 속 링크 클릭 유도", "tactic": "오늘 안에 안 하면 반송된다는 시간 압박", "request": "링크 클릭", "requestedInfo": []},
                {"order": 2, "judgmentPointId": "J2", "goal": "본인 확인 명목 정보 수집", "tactic": "재배송 수수료 결제와 본인 확인이 필요하다고 함", "request": "개인정보 제공", "requestedInfo": ["이름", "전화번호", "카드번호"]},
                {"order": 3, "judgmentPointId": "J2", "goal": "앱 설치 유도", "tactic": "배송 조회 앱을 깔아야 재배송이 된다고 함", "request": "앱 설치", "requestedInfo": []},
            ],
            "expectedActions": [
                {"id": "A1", "judgmentPointId": "J1", "description": "문자 속 링크를 누르지 않고 쇼핑몰·택배사 공식 앱에서 배송 상태를 확인한다"},
                {"id": "A2", "judgmentPointId": "J2", "description": "주소 확인 명목으로 카드번호 등 개인정보를 입력하지 않는다"},
                {"id": "A3", "judgmentPointId": "J2", "description": "출처를 알 수 없는 앱을 설치하지 않는다"},
            ],
            "mockPage": None,
        },
    },
    "채용·아르바이트 사기": {
        "analysis": {
            "riskSignals": [
                {"signal": "구직·아르바이트 탐색", "reason": "고수익 재택 알바를 미끼로 한 사기의 주요 대상"},
            ],
            "judgmentPoints": [
                {"id": "J1", "description": "면접 없이 고수익을 약속하는 제안에 응할지 판단"},
                {"id": "J2", "description": "채용 절차라며 신분증·계좌 정보를 요구받을 때 판단"},
            ],
        },
        "scenario": {
            "fraudSubtype": "채용·아르바이트 사기",
            "title": "면접 없이 바로 채용된다는 재택 알바",
            "channel": "메신저",
            "persona": {"name": "이수빈", "role": "채용 담당자 사칭", "organization": "새봄물류(가상)", "tone": "친근하고 칭찬이 많은 말투"},
            "background": "구직 사이트에 이력서를 올린 뒤, 모르는 채용 담당자에게서 메신저 연락이 왔습니다.",
            "openingMessage": "안녕하세요, 새봄물류 채용팀입니다. 이력서 보고 연락드렸어요. 재택으로 하루 2시간, 일급 10만원 업무인데 관심 있으세요?",
            "keyScenes": [
                {"order": 1, "judgmentPointId": "J1", "goal": "고수익 제안으로 관심 끌기", "tactic": "면접 없이 바로 시작 가능, 인원이 얼마 안 남았다고 함", "request": "대화 지속", "requestedInfo": []},
                {"order": 2, "judgmentPointId": "J2", "goal": "급여 계좌·신분증 수집", "tactic": "급여 지급과 근로계약 때문에 필요하다고 함", "request": "개인정보 제공", "requestedInfo": ["신분증 사진", "계좌번호"]},
                {"order": 3, "judgmentPointId": "J2", "goal": "업무 앱 설치·대금 전달 유도", "tactic": "업무용 앱을 깔고 들어오는 돈을 다른 계좌로 옮기는 게 업무라고 함", "request": "앱 설치", "requestedInfo": []},
            ],
            "expectedActions": [
                {"id": "A1", "judgmentPointId": "J1", "description": "면접 없는 고수익 제안을 의심하고 회사 정보를 공식 경로로 확인한다"},
                {"id": "A2", "judgmentPointId": "J2", "description": "채용 전 단계에서 신분증 사진·계좌번호를 보내지 않는다"},
                {"id": "A3", "judgmentPointId": "J2", "description": "내 계좌로 받은 돈을 남에게 옮기는 업무를 거절한다"},
            ],
            "mockPage": "채용",
        },
    },
    "투자 리딩방": {
        "analysis": {
            "riskSignals": [
                {"signal": "투자 관심", "reason": "고수익 보장 리딩방 사기의 주요 대상"},
            ],
            "judgmentPoints": [
                {"id": "J1", "description": "수익을 보장한다는 초대에 응할지 판단"},
                {"id": "J2", "description": "본인 확인 명목의 개인정보와 입금을 요구받을 때 판단"},
            ],
        },
        "scenario": {
            "fraudSubtype": "투자 리딩방",
            "title": "수익 인증으로 가득한 투자 정보방",
            "channel": "메신저",
            "persona": {"name": "박준혁 팀장", "role": "투자 전문가 사칭", "organization": "한결투자연구소(가상)", "tone": "자신감 넘치고 수익을 강조하는 말투"},
            "background": "모르는 사람이 무료 투자 정보방에 초대하겠다며 메시지를 보냈습니다.",
            "openingMessage": "안녕하세요, 한결투자연구소입니다. 이번 주 회원님들 평균 수익률 32%였어요. 무료 체험방 자리 하나 남았는데 들어오실래요?",
            "keyScenes": [
                {"order": 1, "judgmentPointId": "J1", "goal": "수익 인증으로 신뢰 쌓기", "tactic": "다른 회원들의 수익 화면을 보여주며 원금 보장을 약속", "request": "대화 지속", "requestedInfo": []},
                {"order": 2, "judgmentPointId": "J2", "goal": "회원 등록 명목 정보 수집", "tactic": "VIP방 입장과 수익금 정산에 본인 확인이 필요하다고 함", "request": "개인정보 제공", "requestedInfo": ["이름", "전화번호", "계좌번호"]},
                {"order": 3, "judgmentPointId": "J2", "goal": "전용 앱 입금 유도", "tactic": "비공개 종목이라 전용 앱으로만 되고 오늘 마감이라며 재촉", "request": "송금", "requestedInfo": []},
            ],
            "expectedActions": [
                {"id": "A1", "judgmentPointId": "J1", "description": "원금·고수익 보장 제안을 의심한다"},
                {"id": "A2", "judgmentPointId": "J2", "description": "본인 확인 명목으로 이름·전화번호·계좌번호를 알려주지 않는다"},
                {"id": "A3", "judgmentPointId": "J2", "description": "업체가 정식 등록된 곳인지 공식 조회 서비스로 확인하기 전엔 돈을 보내지 않는다"},
            ],
            "mockPage": "금융",
        },
    },
    "지인·가족 사칭": {
        "analysis": {
            "riskSignals": [
                {"signal": "가족·지인과 메신저 연락", "reason": "가족을 사칭해 급한 부탁을 하는 메신저 피싱의 주요 대상"},
            ],
            "judgmentPoints": [
                {"id": "J1", "description": "번호가 바뀌었다는 가족 메시지를 믿을지 판단"},
                {"id": "J2", "description": "급하다며 정보·송금을 부탁받을 때 판단"},
            ],
        },
        "scenario": {
            "fraudSubtype": "지인·가족 사칭",
            "title": "휴대폰이 고장 났다는 가족의 메시지",
            "channel": "메신저",
            "persona": {"name": "가족(사칭)", "role": "가족 사칭", "organization": "없음", "tone": "다급하고 통화를 피하는 말투"},
            "background": "모르는 번호로 가족이라며 메시지가 왔습니다.",
            "openingMessage": "나야, 폰 액정이 깨져서 수리 맡기고 임시폰으로 연락해. 지금 통화는 안 되는데 급하게 부탁할 게 있어.",
            "keyScenes": [
                {"order": 1, "judgmentPointId": "J1", "goal": "가족이라고 믿게 하기", "tactic": "폰이 고장 나서 통화가 안 된다고 하며 확인을 피함", "request": "대화 지속", "requestedInfo": []},
                {"order": 2, "judgmentPointId": "J2", "goal": "결제 정보 수집", "tactic": "온라인 결제를 대신 해달라며 정보 요구", "request": "개인정보 제공", "requestedInfo": ["신분증 사진", "카드번호", "인증번호"]},
                {"order": 3, "judgmentPointId": "J2", "goal": "원격 앱 설치·송금 유도", "tactic": "보험금 신청을 도와달라며 앱 설치를 부탁하고 급한 송금 요청", "request": "송금", "requestedInfo": []},
            ],
            "expectedActions": [
                {"id": "A1", "judgmentPointId": "J1", "description": "원래 알던 번호로 직접 전화해 본인인지 확인한다"},
                {"id": "A2", "judgmentPointId": "J2", "description": "메시지로 신분증·카드번호·인증번호를 보내지 않는다"},
                {"id": "A3", "judgmentPointId": "J2", "description": "확인 전에는 앱을 설치하거나 송금하지 않는다"},
            ],
            "mockPage": None,
        },
    },
    "공공기관 사칭": {
        "analysis": {
            "riskSignals": [
                {"signal": "공공 서비스·지원금 관심", "reason": "지원금·과태료 안내를 사칭한 문자 사기의 주요 대상"},
            ],
            "judgmentPoints": [
                {"id": "J1", "description": "지원금 신청 문자의 링크를 열지 판단"},
                {"id": "J2", "description": "신청 절차라며 민감정보를 요구받을 때 판단"},
            ],
        },
        "scenario": {
            "fraudSubtype": "공공기관 사칭",
            "title": "오늘 마감이라는 생활지원금 신청 문자",
            "channel": "문자",
            "persona": {"name": "한누리 생활지원센터", "role": "공공기관 사칭", "organization": "한누리 생활지원센터(가상)", "tone": "공문서 같은 딱딱한 말투"},
            "background": "지원금 대상자로 선정됐다는 문자를 받았습니다.",
            "openingMessage": "[한누리 생활지원센터] 귀하는 생활안정지원금 대상자입니다. 금일 18시까지 미신청 시 자동 소멸됩니다. 신청: [모의링크]",
            "keyScenes": [
                {"order": 1, "judgmentPointId": "J1", "goal": "신청 링크 클릭 유도", "tactic": "오늘 마감, 미신청 시 소멸이라는 시간 압박", "request": "링크 클릭", "requestedInfo": []},
                {"order": 2, "judgmentPointId": "J2", "goal": "본인 인증 명목 정보 수집", "tactic": "지원금 입금을 위해 본인 확인이 필요하다고 함", "request": "개인정보 제공", "requestedInfo": ["이름", "주민등록번호", "계좌번호"]},
            ],
            "expectedActions": [
                {"id": "A1", "judgmentPointId": "J1", "description": "문자 링크 대신 공식 누리집이나 주민센터에서 직접 확인한다"},
                {"id": "A2", "judgmentPointId": "J2", "description": "문자로 받은 링크에 주민등록번호·계좌번호를 입력하지 않는다"},
            ],
            "mockPage": "공공민원",
        },
    },
    "대출 사기": {
        "analysis": {
            "riskSignals": [
                {"signal": "금융·대출 관심", "reason": "저금리 대환대출을 미끼로 한 사기의 주요 대상"},
            ],
            "judgmentPoints": [
                {"id": "J1", "description": "먼저 연락해 온 저금리 대출 제안을 믿을지 판단"},
                {"id": "J2", "description": "대출 전 수수료·기존 대출 상환 명목 송금을 요구받을 때 판단"},
            ],
        },
        "scenario": {
            "fraudSubtype": "대출 사기",
            "title": "기존 대출을 갚으면 저금리로 바꿔준다는 제안",
            "channel": "문자",
            "persona": {"name": "정민호 대리", "role": "대출 상담원 사칭", "organization": "새솔캐피탈(가상)", "tone": "친절하고 혜택을 강조하는 말투"},
            "background": "정부 지원 저금리 대출 대상이라는 문자를 받았습니다.",
            "openingMessage": "[새솔캐피탈] 고객님은 연 3%대 정부지원 대환대출 승인 대상입니다. 한도 조회는 상담원에게 문의 주세요.",
            "keyScenes": [
                {"order": 1, "judgmentPointId": "J1", "goal": "상담으로 끌어들이기", "tactic": "정부 지원, 한정 기간 특별 금리라고 강조", "request": "대화 지속", "requestedInfo": []},
                {"order": 2, "judgmentPointId": "J2", "goal": "심사 명목 정보 수집", "tactic": "한도 조회와 심사에 본인 정보가 필요하다고 함", "request": "개인정보 제공", "requestedInfo": ["주민등록번호", "계좌번호"]},
                {"order": 3, "judgmentPointId": "J2", "goal": "송금 유도", "tactic": "기존 대출을 먼저 갚아야 한다며 지정 계좌로 송금 요구", "request": "송금", "requestedInfo": []},
            ],
            "expectedActions": [
                {"id": "A1", "judgmentPointId": "J1", "description": "먼저 연락해 온 대출 제안을 의심한다"},
                {"id": "A2", "judgmentPointId": "J2", "description": "대출 심사를 이유로 주민등록번호·계좌번호를 메시지로 보내지 않는다"},
                {"id": "A3", "judgmentPointId": "J2", "description": "대출 전에 돈을 먼저 보내라는 요구를 거절한다"},
            ],
            "mockPage": "금융",
        },
    },
}


FALLBACK_SCENARIOS["결제·환불 사칭"] = {
    "analysis": {
        "riskSignals": [{"signal": "결제·환불 안내 사칭", "reason": "이용한 가게의 결제 확인 안내처럼 꾸며 외부 확인을 유도"}],
        "judgmentPoints": [{"id": "J1", "description": "결제 오류 안내의 발신자를 직접 확인하는지 판단"},
                           {"id": "J2", "description": "환불을 빌미로 개인정보를 요구할 때 대응"}],
    },
    "scenario": {
        "fraudSubtype": "결제·환불 사칭", "title": "결제 이후 도착한 확인 메시지", "channel": "문자",
        "persona": {"name": "결제 안내 담당자", "role": "결제 안내 직원", "organization": "이용한 가게", "tone": "정중한 안내 말투"},
        "background": "결제한 뒤 결제 오류 확인을 빙자한 메시지를 받는 가상 상황입니다.",
        "openingMessage": "이용하신 가게 결제 내역 확인 안내입니다. 결제 오류 접수 여부를 확인해 주세요. [모의링크]",
        "keyScenes": [
            {"order": 1, "judgmentPointId": "J1", "goal": "식당 안내를 믿고 외부 확인 화면을 열게 함", "tactic": "결제 오류와 환불 가능성을 강조", "request": "링크 클릭", "requestedInfo": []},
            {"order": 2, "judgmentPointId": "J2", "goal": "환불 확인을 명목으로 개인정보 제공을 유도", "tactic": "정상 환불 절차인 것처럼 안내", "request": "개인정보 제공", "requestedInfo": ["카드번호", "인증번호"]},
        ],
        "expectedActions": [
            {"id": "A1", "judgmentPointId": "J1", "description": "메시지의 링크 대신 이용한 식당이나 기존 결제 내역으로 확인한다"},
            {"id": "A2", "judgmentPointId": "J2", "description": "환불을 이유로 요구하는 카드정보나 인증번호를 제공하지 않는다"},
        ], "mockPage": "금융",
    },
}


def _add_risk_types():
    """기본 시나리오마다 대분류(riskType), 분류 이유, 근거 사례(caseId)를 채워 넣는다."""
    from app.case_db import case_ids_for_subtype

    for subtype, data in FALLBACK_SCENARIOS.items():
        data["scenario"]["basedOnCaseIds"] = case_ids_for_subtype(subtype)[:2]
        risk_type = SUBTYPE_TO_RISK_TYPE[subtype]
        data["scenario"]["riskType"] = risk_type
        data["analysis"]["riskType"] = risk_type
        data["analysis"]["riskTypeReason"] = f"사용자 상황에 '{subtype}' 수법이 가장 관련 높음 → {risk_type}"


_add_risk_types()
