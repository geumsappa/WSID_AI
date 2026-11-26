"""Editable narrative fields; verified facts, actor IDs and claims cannot be patched."""
from typing import Literal
from pydantic import Field, model_validator
from app.schemas.context_graph import Model, DraftAnalysis, Scene, ExpectedAction, DeliveryChannel


class PersonaEdits(Model):
    name: str | None = None
    role: str | None = None
    organization: str | None = None
    tone: str | None = None


class StoryEdits(Model):
    title: str | None = Field(default=None, max_length=60)
    background: str | None = Field(default=None, max_length=300)
    openingMessage: str | None = Field(default=None, max_length=250)
    channel: DeliveryChannel | None = None
    persona: PersonaEdits | None = None
    keyScenes: list[Scene] | None = Field(default=None, min_length=1, max_length=4)
    expectedActions: list[ExpectedAction] | None = Field(default=None, min_length=1, max_length=5)
    mockPage: Literal["금융", "공공민원", "채용"] | None = None
    clearMockPage: bool = False


class ScenarioRevision(Model):
    scenario: StoryEdits = Field(default_factory=StoryEdits)
    analysis: DraftAnalysis | None = None

    @model_validator(mode="after")
    def has_edits(self):
        edits = self.scenario.model_dump(exclude_none=True, exclude_defaults=True)
        if not edits and self.analysis is None:
            raise ValueError("Return at least one concrete correction")
        return self
