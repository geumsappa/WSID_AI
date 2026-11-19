"""Domain-independent facts, scenario plans and semantic verification contracts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from app.scenario_data import ALLOWED_CHANNELS, ALLOWED_SUBTYPES, ALLOWED_REQUESTS, RISK_TYPES

FraudSubtype = Literal[tuple(ALLOWED_SUBTYPES)]
DeliveryChannel = Literal[tuple(ALLOWED_CHANNELS)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entity(Model):
    id: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["USER", "PERSON", "ORGANIZATION", "GROUP", "UNKNOWN"]


class Evidence(Model):
    id: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    sourceTurn: StrictInt = Field(ge=1)
    quote: str = Field(min_length=1, max_length=200)


class Event(Model):
    id: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    actorId: str | None
    action: str = Field(min_length=1, max_length=100)
    object: str | None = Field(default=None, max_length=150)
    participantIds: list[str] = Field(default_factory=list, max_length=10)
    certainty: Literal["CONFIRMED", "UNCERTAIN", "NEGATED"]
    evidenceIds: list[str] = Field(min_length=1, max_length=5)
    category: str = Field(default="기타", max_length=40, description="Compatibility metadata; never determines the scenario")


class ContextGraph(Model):
    version: Literal[1] = 1
    userActorId: str
    entities: list[Entity] = Field(max_length=12)
    events: list[Event] = Field(max_length=16)
    evidence: list[Evidence] = Field(max_length=24)
    focusEventIds: list[str] = Field(max_length=6)
    summary: str = Field(max_length=500)


class Extraction(Model):
    context: ContextGraph
    realConcern: StrictBool = False


class ClaimBinding(Model):
    eventId: str
    actorId: str | None


class PersonalizationBasis(Model):
    eventId: str
    evidenceId: str
    detail: str = Field(min_length=1, max_length=180,
        description="A user-stated contextual detail beyond the activity, item or place label")
    scenarioImpact: str = Field(min_length=1, max_length=220,
        description="How this detail changes the counterpart, pretext, interaction or user response in the training")


class ScenarioPlan(Model):
    id: str = Field(min_length=1, max_length=50)
    anchorClaims: list[ClaimBinding] = Field(min_length=1, max_length=6)
    recipientActorId: str
    impersonatedActorId: str | None
    impersonatedRole: str = Field(min_length=1, max_length=100)
    pretext: str = Field(min_length=1, max_length=250)
    recipientReason: str = Field(min_length=1, max_length=350)
    personalizationBasis: list[PersonalizationBasis] = Field(default_factory=list, max_length=3)
    fictionalEvents: list[str] = Field(min_length=1, max_length=4)
    fraudSubtype: FraudSubtype
    channel: DeliveryChannel
    caseIds: list[str] = Field(min_length=1, max_length=2)
    missingInformation: list[str] = Field(default_factory=list, max_length=4)


class ReadyScenarioPlan(ScenarioPlan):
    missingInformation: list[str] = Field(default_factory=list, max_length=0,
        description="Selected plans have no blocking unknown facts. Do not list unconfirmed fictional claims or caveats here.")


class IntakePlan(Extraction):
    selectedPlan: ReadyScenarioPlan | None
    alternativeApproaches: list[str] = Field(default_factory=list, max_length=2)
    followupQuestion: str = Field(default="", max_length=300)


class GenerationIntakePlan(IntakePlan):
    """Generation must propose a candidate; the independent reviewer can still veto it."""
    selectedPlan: ReadyScenarioPlan


class ReviewChecks(Model):
    factsSupported: StrictBool
    actorsPreserved: StrictBool
    recipientPlausible: StrictBool
    focusPreserved: StrictBool
    fictionSeparated: StrictBool
    sufficientContext: StrictBool


class ReviewIssue(Model):
    code: Literal["UNSUPPORTED_FACT", "ACTOR_CHANGED", "EVENT_CHANGED", "RECIPIENT_MISMATCH", "FOCUS_DRIFT", "FICTION_AS_FACT", "INSUFFICIENT_CONTEXT", "NARRATIVE_MISMATCH", "OTHER"]
    detail: str = Field(min_length=1, max_length=350)


class SemanticReview(Model):
    approved: StrictBool
    checks: ReviewChecks
    issues: list[ReviewIssue] = Field(max_length=8)
    followupQuestion: str = Field(default="", max_length=300)

    def accepts(self):
        return self.approved and all(self.checks.model_dump().values()) and not self.issues

    def accepts_context(self):
        facts = (self.checks.factsSupported, self.checks.actorsPreserved, self.checks.focusPreserved, self.checks.fictionSeparated)
        unsafe = {"UNSUPPORTED_FACT", "ACTOR_CHANGED", "EVENT_CHANGED", "FOCUS_DRIFT", "FICTION_AS_FACT"}
        return all(facts) and not any(issue.code in unsafe for issue in self.issues)


class JudgmentPoint(BaseModel):
    id: str
    description: str


class RiskSignal(BaseModel):
    signal: str
    reason: str


class DraftAnalysis(BaseModel):
    riskType: Literal[tuple(RISK_TYPES)] | None = None
    riskTypeReason: str = ""
    usedFacts: list[str] = Field(default_factory=list)
    riskSignals: list[RiskSignal] = Field(default_factory=list)
    judgmentPoints: list[JudgmentPoint] = Field(min_length=1, max_length=4)


class Persona(BaseModel):
    name: str = Field(min_length=1)
    role: str
    organization: str
    tone: str


class Scene(BaseModel):
    judgmentPointId: str
    goal: str = Field(max_length=200)
    tactic: str = Field(max_length=200)
    request: Literal[tuple(ALLOWED_REQUESTS)]
    requestedInfo: list[str]


class ExpectedAction(BaseModel):
    judgmentPointId: str
    description: str


class DraftScenario(BaseModel):
    fraudSubtype: FraudSubtype
    basedOnCaseIds: list[str] = Field(min_length=1, max_length=2)
    title: str = Field(min_length=1, max_length=60)
    channel: DeliveryChannel
    persona: Persona
    background: str = Field(min_length=1, max_length=300)
    openingMessage: str = Field(min_length=1, max_length=250)
    keyScenes: list[Scene] = Field(min_length=1, max_length=4)
    expectedActions: list[ExpectedAction] = Field(min_length=1, max_length=5)
    mockPage: Literal["금융", "공공민원", "채용"] | None = None


class GeneratedScenario(Model):
    analysis: DraftAnalysis
    scenario: DraftScenario
    preservedClaims: list[ClaimBinding] = Field(min_length=1, max_length=6)
