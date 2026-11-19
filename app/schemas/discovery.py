from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class DiscoveryMessage(BaseModel):
    sender: Literal["AI", "USER", "SYSTEM"]
    content: str = Field(min_length=1, max_length=1000)
    phase: str | None = None


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation: list[DiscoveryMessage] = Field(max_length=30)
    knownFacts: dict = Field(max_length=30)
    currentInput: str = Field(min_length=1, max_length=1000)


class DiscoveryResult(BaseModel):
    facts: dict
    nextQuestion: str
    canGenerateScenario: bool


class ContextModelOutput(BaseModel):
    next_question: str = Field(default="", max_length=1000)
    extracted_facts: list[dict] = Field(default_factory=list, max_length=5)
    enough_info: StrictBool = False
    real_concern: StrictBool = False
    retracted_fact_ids: list[str] = Field(default_factory=list, max_length=30)
