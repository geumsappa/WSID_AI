"""상담 assist v1 DTO와 상태·요청 간 교차 검증. 모든 nullable 키도 필수입니다."""
from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from rag.guard import has_forbidden

FactKey = Literal["transferredMoney", "sharedInformation", "installedApp", "contactContinues"]
FactValue = Literal["YES", "NO", "UNKNOWN"]
QuestionIntent = Literal[
    "TRANSFER_RESPONSE", "DEVICE_SAFETY", "INFO_LEAK_RESPONSE", "CONTACT_RESPONSE", "GENERAL"
]
ReasonCode = Literal["OUT_OF_SCOPE", "NO_EVIDENCE", "LIMIT_REACHED"]
# AI-06(/extract)이 돌려주는 fraudType과 같은 값 (계약 v1.1 선택 필드)
FraudType = Literal["MESSENGER_PHISHING", "SMISHING", "LOAN_FRAUD", "INSTITUTION_IMPERSONATION",
                    "INVESTMENT_FRAUD", "SECONDHAND_TRADE"]


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("blank text")
    return value


def safe_output(value: str) -> str:
    if has_forbidden(value):
        raise ValueError("forbidden output")
    return value


def valid_code(value: str) -> str:
    if not re.fullmatch(r"[A-Z0-9_]{1,100}", value):
        raise ValueError("invalid reference code")
    return value


def valid_version(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,50}", value):
        raise ValueError("invalid reference version")
    return value


ReferenceCode = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(valid_code)]
Version = Annotated[str, Field(min_length=1, max_length=50), AfterValidator(valid_version)]
Question = Annotated[str, Field(min_length=1, max_length=1000), AfterValidator(nonblank)]
OutputText = Annotated[
    str, Field(min_length=1, max_length=2000), AfterValidator(nonblank), AfterValidator(safe_output)
]
ClarificationText = Annotated[
    str, Field(min_length=1, max_length=200), AfterValidator(nonblank), AfterValidator(safe_output)
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class Facts(ContractModel):
    transferredMoney: FactValue
    sharedInformation: FactValue
    installedApp: FactValue
    contactContinues: FactValue


class CandidateReference(ContractModel):
    code: ReferenceCode
    version: Version


class VerifiedReference(ContractModel):
    code: ReferenceCode
    title: Annotated[str, Field(min_length=1, max_length=200), AfterValidator(nonblank)]
    excerpt: Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(nonblank)]


class ChatTurn(ContractModel):
    """이전 대화 한 턴. Spring이 저장한 가린 글(safe_content)을 그대로 보낸다."""
    role: Literal["USER", "AI"]
    content: Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(nonblank)]


# v1.1 선택 필드: 보내지 않으면(null) v1과 똑같이 동작한다.
History = Annotated[list[ChatTurn] | None, Field(default=None, max_length=10)]


def unique_codes(refs: list) -> None:
    codes = [r.code for r in refs]
    if len(set(codes)) != len(codes):
        raise ValueError("duplicate reference code")


class PlanRequest(ContractModel):
    phase: Literal["PLAN"]
    question: Question | None
    questionIntent: QuestionIntent | None
    facts: Facts
    askedFactKeys: Annotated[list[FactKey], Field(max_length=4)]
    remainingClarifications: Annotated[int, Field(ge=0, le=3)]
    history: History = None
    fraudType: FraudType | None = None

    @model_validator(mode="after")
    def check_state(self) -> Self:
        if self.question is None and self.questionIntent is None:
            raise ValueError("question or questionIntent required")
        if len(set(self.askedFactKeys)) != len(self.askedFactKeys):
            raise ValueError("duplicate asked fact key")
        return self


class AnswerRequest(ContractModel):
    phase: Literal["ANSWER"]
    question: Question | None
    questionIntent: QuestionIntent
    facts: Facts
    verifiedReferences: Annotated[list[VerifiedReference], Field(min_length=1, max_length=10)]
    history: History = None
    fraudType: FraudType | None = None

    @model_validator(mode="after")
    def check_references(self) -> Self:
        unique_codes(self.verifiedReferences)
        return self


AssistRequest = Annotated[PlanRequest | AnswerRequest, Field(discriminator="phase")]


class Clarification(ContractModel):
    factKey: FactKey
    content: ClarificationText
    options: list[FactValue]

    @model_validator(mode="after")
    def check_options(self) -> Self:
        if self.options != ["YES", "NO", "UNKNOWN"]:
            raise ValueError("invalid options or order")
        return self


class PlanResponse(ContractModel):
    status: Literal["NEED_USER_INPUT", "EVIDENCE_READY", "UNABLE_TO_ANSWER"]
    questionIntent: QuestionIntent | None
    clarification: Clarification | None
    candidateReferences: Annotated[list[CandidateReference], Field(max_length=10)]
    reasonCode: ReasonCode | None

    @model_validator(mode="after")
    def check_status(self) -> Self:
        unique_codes(self.candidateReferences)
        if self.status == "UNABLE_TO_ANSWER":
            if self.reasonCode is None or self.clarification is not None or self.candidateReferences:
                raise ValueError("invalid unable plan")
        else:
            if self.questionIntent is None or self.reasonCode is not None:
                raise ValueError("intent required; reason must be null")
            if self.status == "NEED_USER_INPUT":
                if self.clarification is None or self.candidateReferences:
                    raise ValueError("invalid clarification plan")
            elif self.clarification is not None or not self.candidateReferences:
                raise ValueError("invalid evidence plan")
        return self

    def validate_for(self, request: PlanRequest) -> None:
        if request.questionIntent is not None and self.questionIntent != request.questionIntent:
            if not (self.status == "UNABLE_TO_ANSWER" and self.questionIntent is None):
                raise ValueError("questionIntent changed")
        if self.clarification is not None:
            key = self.clarification.factKey
            if (request.remainingClarifications == 0 or key in request.askedFactKeys
                    or getattr(request.facts, key) != "UNKNOWN"):
                raise ValueError("clarification not allowed")


class AnswerResponse(ContractModel):
    status: Literal["ANSWERED", "UNABLE_TO_ANSWER"]
    answer: OutputText | None
    referenceCodes: Annotated[list[ReferenceCode], Field(max_length=10)]
    reasonCode: Literal["OUT_OF_SCOPE", "NO_EVIDENCE"] | None

    @model_validator(mode="after")
    def check_status(self) -> Self:
        if len(set(self.referenceCodes)) != len(self.referenceCodes):
            raise ValueError("duplicate citation")
        if self.status == "ANSWERED":
            if self.answer is None or not self.referenceCodes or self.reasonCode is not None:
                raise ValueError("invalid answered response")
        elif self.answer is not None or self.referenceCodes or self.reasonCode is None:
            raise ValueError("invalid unable answer")
        return self

    def validate_for(self, request: AnswerRequest) -> None:
        if not set(self.referenceCodes) <= {r.code for r in request.verifiedReferences}:
            raise ValueError("citation outside verifiedReferences")


class AssistSuccess(ContractModel):
    success: Literal[True]
    data: PlanResponse | AnswerResponse


class ErrorDetail(ContractModel):
    code: str


class AssistFailure(ContractModel):
    success: Literal[False]
    error: ErrorDetail


# 아래 둘은 LLM과만 사용하는 내부 DTO. Spring 계약에는 노출하지 않습니다.
class Assessment(ContractModel):
    decision: Literal["SEARCH", "NEED_USER_INPUT", "OUT_OF_SCOPE"]
    questionIntent: QuestionIntent | None
    factKey: FactKey | None
    @model_validator(mode="after")
    def check_decision(self) -> Self:
        if self.decision == "NEED_USER_INPUT":
            if self.factKey is None:
                raise ValueError("missing clarification")
        elif self.factKey is not None:
            raise ValueError("unexpected clarification")
        if self.decision != "OUT_OF_SCOPE" and self.questionIntent is None:
            raise ValueError("missing intent")
        return self


class ModelClarification(ContractModel):
    """모델은 질문 문구 없이 필요한 사실 키만 선택한다."""
    factKey: FactKey


class EvidenceAssessment(ContractModel):
    sufficient: bool
    referenceCodes: Annotated[list[ReferenceCode], Field(max_length=10)]
    retryQuery: Question | None
    clarification: ModelClarification | None = None

    @model_validator(mode="after")
    def check_evidence(self) -> Self:
        if len(set(self.referenceCodes)) != len(self.referenceCodes):
            raise ValueError("duplicate evidence code")
        if self.sufficient != bool(self.referenceCodes):
            raise ValueError("evidence decision and citations disagree")
        if self.clarification is not None and (self.sufficient or self.retryQuery is not None):
            raise ValueError("clarification cannot be mixed with evidence or research")
        return self
