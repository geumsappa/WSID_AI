"""Small conversational decision; this is not a verified scenario plan."""
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class EvidenceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn: StrictInt = Field(ge=1, le=1000000)
    text: str = Field(min_length=1, max_length=1000)


class DetailEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn: StrictInt = Field(ge=1)
    quote: str = Field(min_length=1, max_length=180)


class DiscoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ready: StrictBool
    question: str = Field(max_length=180)
    detail: DetailEvidence | None = None

    @model_validator(mode="after")
    def require_evidence_or_question(self):
        if self.ready and self.detail is None:
            raise ValueError("Ready decisions need a user-stated contextual detail")
        if not self.ready and not self.question.strip():
            raise ValueError("Incomplete decisions need a question")
        return self
