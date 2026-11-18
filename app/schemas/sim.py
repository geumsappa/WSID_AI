"""Spring ↔ FastAPI 가 주고받는 JSON 모양 정의.

여기 적힌 필드 이름 = Spring 쪽 DTO 필드 이름. 둘이 반드시 같아야 합니다.
파이썬 코드 안에서는 snake_case(session_id)로 쓰지만,
JSON 으로 나갈 때/들어올 때는 Java 스타일인 camelCase(sessionId)로 자동 변환됩니다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Message(CamelModel):
    role: Literal["ai", "user"]
    text: str = Field(min_length=1, max_length=1000)


class ContextTurnRequest(CamelModel):
    session_id: str = Field(description="Spring 이 만든 훈련 세션 ID")
    history: list[Message] = Field(default_factory=list, max_length=30,
                                   description="지금까지의 대화. 비어있으면 첫 질문을 돌려줌")

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "sessionId": "test-session-1",
                    "history": [
                        {"role": "ai", "text": "최근에 어디 다녀오셨거나 새로 시작한 일이 있으세요?"},
                        {"role": "user", "text": "오늘 택배 주소가 틀렸다고 링크 눌러서 수정하라는 문자 받았어요"},
                    ],
                }
            ]
        },
    )


class Fact(CamelModel):
    """SIM-002 입력 정보 분석 결과: 사실 1개"""
    category: str = Field(description="거래(중고·쇼핑) | 구직·아르바이트 | 택배·배송 | 금융·대출 | 투자 | 가족·지인 연락 | 공공기관·민원 | 여행·이동 | 기타")
    detail: str = Field(description="사실 한 문장")
    channel: str | None = Field(default=None, description="채널: 문자, 메신저, 중고거래 앱 채팅, 이메일 등")
    impersonated_identity: str | None = Field(default=None, description="사칭 신분: 상대가 자신을 누구라고 했는지")
    requested_action: str | None = Field(default=None, description="요구 행동: 링크 클릭 | 개인정보 제공 | 송금 | 앱 설치 | 대화 지속")
    risk_factors: list[str] = Field(default_factory=list, description="금융사기 위험 요소 (예: 출처 불명 링크, 시간 압박)")


class ContextTurnResponse(CamelModel):
    next_question: str = Field(description="화면에 보여줄 다음 AI 질문 (또는 안내 문구)")
    extracted_facts: list[Fact] = Field(description="이번 답변에서 새로 뽑은 사실. Spring 이 세션에 누적 저장")
    sensitive_detected: bool = Field(description="이번 답변에 개인정보가 있었는지. 대화는 계속 진행됨")
    sensitive_types: list[str] = Field(description="입력된 개인정보 종류. Spring 이 '턴 번호 + 종류'만 세션에 누적 → 리포트에 반영")
    masked_text: str | None = Field(default=None, description="개인정보를 가린 사용자 답변. Spring 은 원문 대신 이걸 history 에 저장")
    done: bool = Field(description="true 면 상황 파악 끝 → 시나리오 생성 단계로")
    real_concern: bool = Field(description="true 면 사용자가 '실제 피해' 상황일 수 있음 → 피해 대응 메뉴 안내 권장")
    fallback_used: bool = Field(description="LLM 실패로 기본 문구를 썼는지")


# ================= SIM-02 시나리오 생성 =================

class ScenarioRequest(CamelModel):
    session_id: str
    facts: list[Fact] = Field(default_factory=list, max_length=30,
                              description="①단계에서 Spring 이 누적한 extractedFacts 전체")

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "sessionId": "test-session-1",
                    "facts": [
                        {"category": "거래(중고·쇼핑)", "detail": "중고거래 앱으로 자전거를 판매함",
                         "channel": "중고거래 앱 채팅", "impersonatedIdentity": None,
                         "requestedAction": None, "riskFactors": []},
                        {"category": "택배·배송", "detail": "주소 불일치로 반송된다는 택배 문자를 받음",
                         "channel": "문자", "impersonatedIdentity": "택배사",
                         "requestedAction": "링크 클릭", "riskFactors": ["출처 불명 링크", "시간 압박"]},
                    ],
                }
            ]
        },
    )


class RiskSignal(CamelModel):
    signal: str
    reason: str


class JudgmentPoint(CamelModel):
    id: str = Field(description="J1, J2 ... 훈련에서 시험할 판단 지점")
    description: str


class Analysis(CamelModel):
    risk_type: str = Field(description="SIM-003 위험 유형: 메신저피싱 | 스미싱 | 대출빙자 | 기관사칭 | 투자·가상자산 | 기타")
    risk_type_reason: str = Field(description="그 유형으로 분류한 이유")
    used_facts: list[str] = Field(description="시나리오를 고르는 데 쓴 사용자 사실")
    risk_signals: list[RiskSignal]
    judgment_points: list[JudgmentPoint]


class Persona(CamelModel):
    name: str
    role: str
    organization: str
    tone: str


class KeyScene(CamelModel):
    order: int
    judgment_point_id: str
    goal: str = Field(description="사기범이 이 장면에서 노리는 것")
    tactic: str = Field(description="심리 압박·속임 수법")
    request: str = Field(description="링크 클릭 | 개인정보 제공 | 송금 | 앱 설치 | 대화 지속")
    requested_info: list[str] = Field(description="요구하는 개인정보 종류")


class ExpectedAction(CamelModel):
    id: str = Field(description="A1, A2 ... 리포트에서 잘했는지 판정할 행동")
    judgment_point_id: str
    description: str


class CaseRef(CamelModel):
    """SIM-005 시나리오의 근거가 된 공개 사례"""
    case_id: str
    title: str
    issuer: str = Field(description="주의 안내를 발표한 기관")
    alert_no: str | None = None
    source_host: str = Field(description="sourceUrl 이 어떤 페이지인지 (기관 원문 사본 / 언론 보도 등)")
    source_date: str | None = None
    source_url: str


class Scenario(CamelModel):
    risk_type: str = Field(description="위험 유형 대분류 (6종)")
    fraud_subtype: str = Field(description="세부 유형: 지인·가족 사칭, 택배 사칭, 대출 사기, 공공기관 사칭, 투자 리딩방, 중고거래 사기, 채용·아르바이트 사기")
    title: str
    channel: str = Field(description="문자 | 메신저 | 중고거래 앱 채팅 | 이메일 | SNS 메시지 → 화면 스타일 결정")
    persona: Persona
    background: str = Field(description="훈련 시작 시 사용자에게 보여줄 상황 설명")
    opening_message: str = Field(description="가상 인물의 첫 메시지 (훈련 채팅 첫 말풍선)")
    allowed_organizations: list[str] = Field(default_factory=list, max_length=50,
                                           description="입력 사실에서 확인해 이 훈련에 허용한 기관명")
    key_scenes: list[KeyScene]
    expected_actions: list[ExpectedAction]
    mock_page: str | None = Field(description="금융 | 공공민원 | 채용 | null")
    based_on_cases: list[CaseRef] = Field(default_factory=list, description="근거 사례 (리포트에 출처로 표시)")
    max_turns: int


class ScenarioResponse(CamelModel):
    analysis: Analysis
    scenario: Scenario = Field(description="Spring 이 세션에 저장 → ③ 훈련 대화 API 에 그대로 넘김")
    fallback_used: bool


# ================= SIM-03 ③ 훈련 대화 =================

class TrainingMessage(CamelModel):
    role: Literal["persona", "user"] = Field(description="persona = 가상 사기범, user = 사용자")
    text: str = Field(min_length=1, max_length=1000)


class TrainingTurnRequest(CamelModel):
    session_id: str
    scenario: Scenario = Field(description="② 시나리오 API 응답의 scenario 를 그대로")
    current_scene: int = Field(default=1, ge=1, description="직전 응답의 currentScene (처음엔 1)")
    history: list[TrainingMessage] = Field(default_factory=list, max_length=40,
                                           description="훈련 대화 기록. 비어있으면 첫 메시지를 돌려줌")


class Judgment(CamelModel):
    outcome: Literal["safe", "risky", "neutral"] = Field(
        description="safe=잘 대응 / risky=요구에 응함(낚임) / neutral=판단할 행동 없음")
    user_action: str = Field(description="사용자가 한 행동 요약")
    achieved_action_ids: list[str] = Field(description="지킨 expectedActions id")
    violated_action_ids: list[str] = Field(description="어긴 expectedActions id")
    reason: str = Field(description="판정 이유 (리포트용, 훈련 중엔 화면에 안 보임)")


class InfoDisclosure(CamelModel):
    """리포트의 핵심 재료: 이번 턴에 무엇을 요구받았고, 사용자가 실제로 무엇을 넘겼는가"""
    requested: list[str] = Field(description="사용자가 답할 때 사기범이 요구하고 있던 개인정보 종류")
    provided: list[str] = Field(description="사용자가 이번 답변에 실제로 입력한 개인정보 종류 (값은 저장 안 함)")
    disclosed: bool = Field(description="하나라도 넘겼으면 true")


class TrainingTurnResponse(CamelModel):
    persona_message: str = Field(description="가상 사기범의 다음 말풍선")
    current_scene: int = Field(description="다음 요청 때 그대로 보내주세요")
    request_type: str = Field(description="사기범이 지금 시키려는 행동. '링크 클릭'이면 [모의링크]를 모의 페이지로 연결")
    requested_info: list[str] = Field(description="사기범이 지금 요구하고 있는 개인정보 종류")
    input_expected: bool = Field(description="true 면 사기범이 개인정보를 직접 입력하라고 요구 중 → 입력창을 강조")
    choices: list[str] = Field(description="사용자 답변 선택지 버튼 (직접 입력도 가능)")
    judgment: Judgment | None = Field(description="사용자 마지막 답변 판정. 첫 턴엔 null")
    info_disclosure: InfoDisclosure
    sensitive_detected: bool = Field(description="infoDisclosure.disclosed 와 같음")
    sensitive_types: list[str] = Field(description="infoDisclosure.provided 와 같음")
    masked_text: str | None = Field(description="개인정보를 가린 사용자 답변 → 원문 대신 저장")
    done: bool
    end_reason: str | None = Field(description="scenario_complete | max_turns")
    real_concern: bool = Field(description="true 면 사용자가 '실제 피해' 상황일 수 있음 → 피해 대응 메뉴 안내 권장")
    fallback_used: bool


# ================= SIM-010 ⑤ 결과 리포트 =================

class TurnRecord(CamelModel):
    """③ 훈련 대화에서 Spring 이 턴마다 누적한 기록"""
    turn: int = Field(ge=1)
    scene: int = Field(default=1, ge=1)
    user_text: str | None = Field(default=None, max_length=1000,
                                  description="그 턴의 사용자 답변 (③ 응답의 maskedText, 개인정보 가려진 것)")
    judgment: Judgment
    info_disclosure: InfoDisclosure


class MockPageEvent(CamelModel):
    """④ 모의 페이지에서 Spring 이 기록한 사용자 행동 (SIM-014)"""
    action: Literal["링크 열기", "페이지 닫기", "로그인 시도", "정보 입력", "공식 채널 확인"]
    input_types: list[str] = Field(default_factory=list, description="입력한 칸의 종류 (예: 계좌번호). 값은 보내지 않음")


class ReportRequest(CamelModel):
    session_id: str
    scenario: Scenario = Field(description="② 응답의 scenario 그대로")
    turns: list[TurnRecord] = Field(default_factory=list, max_length=40)
    mock_page_events: list[MockPageEvent] = Field(default_factory=list, max_length=50)
    end_reason: str | None = Field(default=None, description="scenario_complete | max_turns | user_quit")


class InfoEvent(CamelModel):
    turn: int
    scene: int
    requested: list[str]
    provided: list[str]


class InfoProtection(CamelModel):
    """리포트의 핵심: 개인정보를 요구받았을 때 실제로 넘겼는가"""
    requested: list[str] = Field(description="훈련 중 요구받은 개인정보 종류 전체")
    provided: list[str] = Field(description="실제로 넘긴 종류")
    protected: list[str] = Field(description="요구받았지만 지킨 종류")
    disclosed: bool
    events: list[InfoEvent] = Field(description="턴별 요구·제공 기록")
    message: str = Field(description="화면에 그대로 보여줄 한 줄 요약")


class ActionResult(CamelModel):
    action_id: str
    description: str
    turns: list[int]
    recovered_later: bool = Field(default=False, description="놓친 행동: 어긴 뒤 나중에 바로잡았는지")


class MissedSignal(CamelModel):
    signal: str
    explanation: str
    turn: int | None


class TurnFeedback(CamelModel):
    turn: int
    scene: int
    outcome: Literal["safe", "risky", "neutral"]
    user_action: str
    provided_info: list[str]
    feedback: str


class MockPageResult(CamelModel):
    actions: list[str]
    input_types: list[str]
    risky: bool
    message: str


class OfficialContact(CamelModel):
    name: str
    contact: str
    when: str


class FraudTypeInfo(CamelModel):
    risk_type: str
    subtype: str


class ReportResponse(CamelModel):
    headline: str = Field(description="결과 한 줄 (점수·등급 없음)")
    overview: str
    info_protection: InfoProtection
    good_actions: list[ActionResult]
    missed_actions: list[ActionResult]
    missed_risk_signals: list[MissedSignal]
    turn_feedback: list[TurnFeedback]
    mock_page: MockPageResult | None
    recommended_actions: list[str]
    official_contacts: list[OfficialContact] = Field(description="고정값 (AI 가 만들지 않음)")
    based_on_cases: list[CaseRef]
    fraud_type: FraudTypeInfo
    end_reason: str | None
    disclaimer: str
    fallback_used: bool
