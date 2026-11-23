"""Small conversational decision; this is not a verified scenario plan."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class EvidenceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn: StrictInt = Field(ge=1, le=1000000)
    text: str = Field(min_length=1, max_length=1000)


class DetailEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn: StrictInt = Field(ge=1)
    quote: str = Field(min_length=1, max_length=180)


Topic = Literal["ACTIVITY", "SETTING", "SERVICE", "PEOPLE", "PURPOSE", "ACCESS", "INTERACTION", "TRANSACTION", "ROLE", "PERSON_NAME", "TIME", "ACTIVITY_DETAIL"]


class FactUpdate(DetailEvidence):
    topic: Topic
    status: Literal["KNOWN", "PARTIAL", "DECLINED", "NOT_APPLICABLE"] = "KNOWN"
    name: str | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def supported_name(self):
        if self.topic == "PERSON_NAME" and self.status == "KNOWN":
            if not self.name or not self.name.strip() or self.name not in self.quote:
                raise ValueError("Name or form of address must be copied from the user quotation")
        elif self.name is not None:
            raise ValueError("Names belong only to confirmed PERSON_NAME facts")
        return self


class QuestionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: Topic
    status: Literal["MISSING", "ANSWERED", "UNNECESSARY"]
    missingDetail: str | None = Field(default="", max_length=120)
    scenarioImpact: str | None = Field(default="", max_length=160)
    reason: str = Field(min_length=1, max_length=180)
    evidence: list[DetailEvidence] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def justified_question(self):
        if self.status == "MISSING" and (not (self.missingDetail or "").strip() or not (self.scenarioImpact or "").strip()):
            raise ValueError("A question needs a concrete missing detail and scenario impact")
        if self.status == "ANSWERED" and not self.evidence:
            raise ValueError("An answered need requires an exact user quotation")
        return self


class ConversationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: list[FactUpdate] = Field(default_factory=list, max_length=12)
    nextTopic: str | None = None
    focusChanged: StrictBool = False
    contextSufficient: StrictBool = False
    requiredTopics: list[Topic] = Field(default_factory=list, max_length=5)
    personalizationTarget: Literal["PERSON", "SERVICE", "OTHER", "UNDECIDED"] = "UNDECIDED"
    nextQuestion: str = Field(default="", max_length=180)
    questionAssessments: list[QuestionAssessment] = Field(default_factory=list, max_length=7)


class IntakeRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["PERSON", "SERVICE", "OTHER", "UNDECIDED"] = "UNDECIDED"
    topics: list[Topic] = Field(default_factory=list, max_length=7)
