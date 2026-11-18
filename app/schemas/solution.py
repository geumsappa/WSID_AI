"""피해 대응(긴급대응) Spring ↔ FastAPI JSON 모양. 필드 이름은 Spring DTO 와 같아야 해요(camelCase 로 오감).

- AI-06 사실 추출: SolutionExtractRequest / SolutionExtractResult
- AI-07 근거 설명: SolutionExplainRequest / SolutionExplainResult (Spring SolutionExplanationValidator 와 같은 규칙)
- 질문 답변(가안): 백엔드 초안(11/17)의 assistant(AI 질문·답변)에 쓰는 API. 이름·필드는 백엔드와 확정 필요
"""
from typing import Literal

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.schemas.sim import CamelModel

FactValue = Literal["YES", "NO", "UNKNOWN"]
FactKey = Literal["transferredMoney", "contactContinues", "sharedInformation", "installedApp"]
FraudType = Literal["MESSENGER_PHISHING", "SMISHING", "LOAN_FRAUD", "INSTITUTION_IMPERSONATION",
                    "INVESTMENT_FRAUD", "SECONDHAND_TRADE"]


class SolutionFacts(CamelModel):
    transferred_money: FactValue = Field(description="사기 의심 상대에게 돈을 보냈는가")
    contact_continues: FactValue = Field(description="그 상대와 지금도 연락이 이어지는가")
    shared_information: FactValue = Field(description="개인·금융정보를 넘겼는가")
    installed_app: FactValue = Field(description="상대가 시킨 앱을 설치했는가")


class VerifiedReference(CamelModel):
    code: str = Field(pattern=r"^[A-Z0-9_]{1,100}$")
    title: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=2000)


# ---------- AI-06 사실 추출 ----------
class SolutionExtractRequest(CamelModel):
    description: str = Field(min_length=1, max_length=1000, description="사용자가 쓴 상황 설명 (개인정보는 AI 서버에서 한 번 더 가림)")

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, json_schema_extra={
        "examples": [{"description": "당근에서 중고거래로 50만원 송금했는데 판매자가 연락두절됐어요"}]})


class SolutionExtractResult(CamelModel):
    facts: SolutionFacts
    uncertain_fields: list[FactKey] = Field(description="UNKNOWN 인 항목. Spring 이 이 순서로 추가 질문")
    fraud_type: FraudType | None = Field(default=None, description="추정 사기 유형(계약 추가 제안). 질문 답변 API 에 넘기면 중고거래 환급 오안내를 막아요")


# ---------- AI-07 근거 설명 ----------
class SolutionExplainRequest(CamelModel):
    facts: SolutionFacts
    verified_references: list[VerifiedReference] = Field(max_length=10, description="Spring 이 고른 검토 완료 근거 (최대 10개)")


class SolutionExplainResult(CamelModel):
    explanation: str = Field(description="일반 텍스트, 최대 2,000자. 연락처·링크 없음")
    reference_codes: list[str] = Field(description="요청 근거 code 의 부분집합")


# ---------- 질문 답변 (가안) ----------
class SolutionAnswerRequest(CamelModel):
    facts: SolutionFacts
    question: str = Field(min_length=1, max_length=1000, description="사용자 질문 (개인정보는 AI 서버에서 한 번 더 가림)")
    fraud_type: FraudType | None = Field(default=None, description="AI-06 이 준 fraudType. 없으면 null")
    verified_references: list[VerifiedReference] | None = Field(
        default=None, max_length=10,
        description="Spring 이 근거를 직접 골라 보내면 그 안에서만 답해요. null 이면 AI 서버가 근거를 찾아요")

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, json_schema_extra={
        "examples": [{
            "facts": {"transferredMoney": "YES", "contactContinues": "NO", "sharedInformation": "NO", "installedApp": "NO"},
            "question": "경찰에 신고했는데 이제 뭘 하면 돼요?",
            "fraudType": "SECONDHAND_TRADE",
            "verifiedReferences": None,
        }]})


class SolutionAnswerResult(CamelModel):
    status: Literal["ANSWERED", "NEED_USER_INPUT", "UNABLE_TO_ANSWER"] = Field(
        description="ANSWERED: answer 를 보여 줌 / NEED_USER_INPUT: clarifyQuestion 으로 되물음 / UNABLE_TO_ANSWER: 답변 불가 안내")
    answer: str | None = Field(default=None, description="ANSWERED 일 때 답변. 일반 텍스트, 최대 2,000자, 연락처·링크 없음")
    reference_codes: list[str] = Field(default_factory=list, description="답변에 쓴 근거 code (references 의 부분집합)")
    references: list[VerifiedReference] = Field(default_factory=list, description="AI 에 넘긴 근거 전체. Spring 검증·저장용")
    clarify_fact_key: FactKey | None = Field(default=None, description="NEED_USER_INPUT 일 때 확인할 사실")
    clarify_question: str | None = Field(default=None, description="NEED_USER_INPUT 일 때 사용자에게 보여 줄 질문 (questionId 는 Spring 이 만듦)")
