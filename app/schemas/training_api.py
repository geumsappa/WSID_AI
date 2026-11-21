from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.discovery import DiscoveryMessage

FraudType = Literal["SECONDHAND_TRADE", "SMISHING", "MESSENGER_PHISHING",
                    "LOAN_FRAUD", "INSTITUTION_IMPERSONATION", "INVESTMENT_FRAUD"]


class ScenarioRequest(BaseModel):
    facts: dict = Field(max_length=30)
    fraudTypeCode: FraudType


class ScenarioResult(BaseModel):
    scenario: dict
    mockPageTemplateCode: str | None


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: dict
    messages: list[DiscoveryMessage] = Field(max_length=60)
    userInput: str = Field(min_length=1, max_length=1000)
    turnCount: int = Field(ge=1, le=15)
    currentScene: int | None = Field(default=None, ge=1, le=15)


class MockPage(BaseModel):
    templateCode: str
    title: str
    institutionName: str
    screenCode: str
    isSimulation: Literal[True] = True


class SimulationResult(BaseModel):
    reply: str
    recognizedActions: list[str]
    shouldFinish: bool
    mockPage: MockPage | None
    nextScene: int | None = Field(default=None, ge=1, le=15)
    # 사용자 답변에서 이름·주소처럼 패턴이 없는 개인정보를 AI가 찾아 가린 문장 (예: "[이름, 주소 입력함]").
    # 가릴 게 없으면 None. Spring은 원문 대신 이 값을 저장하고 화면에도 이 값을 보여 줘요.
    maskedInput: str | None = Field(default=None, max_length=300)


class EvaluationAction(BaseModel):
    actionCode: str = Field(min_length=1, max_length=100)


class EvaluationRequest(BaseModel):
    scenario: dict
    conversation: list[DiscoveryMessage] = Field(max_length=100)
    actions: list[EvaluationAction] = Field(max_length=100)


class MockPageEvaluation(BaseModel):
    openedPage: bool
    attemptedMockLogin: bool
    checkedOfficialChannel: bool
    clickedAppLink: bool = False


class EvaluationResult(BaseModel):
    overallFeedback: str = Field(min_length=1, max_length=1000)
    strengths: list[str] = Field(max_length=10)
    improvements: list[str] = Field(max_length=10)
    turnFeedback: list = Field(default_factory=list, max_length=0)
    mockPageEvaluation: MockPageEvaluation
    recommendations: list[str] = Field(max_length=10)
