"""Domain-independent facts, scenario plans and semantic verification contracts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictBool, StrictInt
from app.scenario_data import ALLOWED_CHANNELS, ALLOWED_REQUESTS

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


class PatternUse(Model):
    caseId: str
    technique: str = Field(min_length=1, max_length=160)
    adaptationReason: str = Field(min_length=1, max_length=250)


class ContactSetup(Model):
    contactBasis: Literal["CONFIRMED_CONTEXT", "FICTIONAL_FIRST_CONTACT"]
    senderRole: str = Field(min_length=1, max_length=100)
    contactPremise: str = Field(min_length=1, max_length=220)
    knowledgePremise: str = Field(min_length=1, max_length=220)
    evidenceIds: list[str] = Field(default_factory=list, max_length=4)
    fictionalAssumptions: list[str] = Field(default_factory=list, max_length=3)


class ScenarioPlan(Model):
    contactSetup: ContactSetup | None = None
    referenceUses: list[PatternUse] = Field(default_factory=list, max_length=3)
    _retrieval_trace: dict = PrivateAttr(default_factory=dict)
    _joint_review_required: bool = PrivateAttr(default=False)
    _review_messages: list[dict] = PrivateAttr(default_factory=list)
    id: str = Field(min_length=1, max_length=50)
    anchorClaims: list[ClaimBinding] = Field(min_length=1, max_length=6)
    recipientActorId: str
    impersonatedActorId: str | None
    impersonatedRole: str = Field(min_length=1, max_length=100)
    pretext: str = Field(min_length=1, max_length=250)
    recipientReason: str = Field(min_length=1, max_length=350)
    personalizationBasis: list[PersonalizationBasis] = Field(default_factory=list, max_length=3)
    fictionalEvents: list[str] = Field(min_length=1, max_length=4)
    channel: DeliveryChannel
    requestedActions: list[Literal[tuple(ALLOWED_REQUESTS)]] = Field(default_factory=list, max_length=4)
    desiredOutcome: str = Field(default="", max_length=160)
    escalation: str = Field(default="", max_length=600)
    missingInformation: list[str] = Field(default_factory=list, max_length=4)


class ReadyScenarioPlan(ScenarioPlan):
    missingInformation: list[str] = Field(default_factory=list, max_length=0,
        description="Selected plans have no blocking unknown facts. Do not list unconfirmed fictional claims or caveats here.")


class GenerationPlan(ReadyScenarioPlan):
    contactSetup: ContactSetup
    desiredOutcome: str = Field(min_length=1, max_length=160)
    escalation: str = Field(min_length=1, max_length=600)
    requestedActions: list[Literal[tuple(ALLOWED_REQUESTS)]] = Field(min_length=1, max_length=4)


class SinglePlanProposal(Extraction):
    selectedPlan: GenerationPlan


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
    contextDrivesPlot: StrictBool
    nonGenericProgression: StrictBool


class ReviewIssue(Model):
    code: Literal["UNSUPPORTED_FACT", "ACTOR_CHANGED", "EVENT_CHANGED", "RECIPIENT_MISMATCH", "FOCUS_DRIFT", "FICTION_AS_FACT", "INSUFFICIENT_CONTEXT", "NARRATIVE_MISMATCH", "FORCED_CONNECTION", "REPETITIVE_PATTERN", "OTHER"]
    detail: str = Field(min_length=1, max_length=350)


class SemanticReview(Model):
    referencesSupported: StrictBool = False
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


class DraftAnalysis(Model):
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
    goal: str = Field(max_length=200, description="The impersonator's goal in this scene, not the user's safe response")
    tactic: str = Field(max_length=200, description="How the impersonator tries to induce the requested action; must not contradict request")
    request: Literal[tuple(ALLOWED_REQUESTS)]
    requestedInfo: list[str]
    onCompliance: str = Field(default="", max_length=180, description="Next narrative beat after compliance; not a runtime termination rule")
    onSuspicion: str = Field(default="", max_length=180)
    onRefusal: str = Field(default="", max_length=180)


class ExpectedAction(BaseModel):
    judgmentPointId: str
    description: str


class DraftScenario(Model):
    title: str = Field(min_length=1, max_length=60, description="사용자에게 보여줄 자연스러운 짧은 제목. 8~20자 정도 권장. 긴 조건이나 사건 전체를 한 제목에 우겨넣지 않는다.")
    channel: DeliveryChannel
    persona: Persona
    background: str = Field(min_length=1, max_length=300, description="수신자 관점의 자연스러운 도입 1~2문장. 도착한 연락과 상대가 말하는 용건을 소개한다. 역할 연습·상황 확인·검증 메모를 쓰거나 사칭/거짓 여부를 미리 판정하지 않는다.")
    openingMessage: str = Field(min_length=1, max_length=250)
    keyScenes: list[Scene] = Field(min_length=1, max_length=4)
    expectedActions: list[ExpectedAction] = Field(min_length=1, max_length=5)
    mockPage: Literal["금융", "공공민원", "채용"] | None = None


class GeneratedScenario(Model):
    analysis: DraftAnalysis
    scenario: DraftScenario
    preservedClaims: list[ClaimBinding] = Field(min_length=1, max_length=6)


class CaseMatch(Model):
    caseId: str
    technique: str = Field(min_length=1, max_length=160)


class ScenarioClassification(Model):
    fraudSubtype: str = Field(min_length=1, max_length=80, description="Free descriptive label, not a fixed scenario template")
    fraudTypeCode: Literal["SECONDHAND_TRADE", "SMISHING", "MESSENGER_PHISHING", "LOAN_FRAUD", "INSTITUTION_IMPERSONATION", "INVESTMENT_FRAUD", "JOB_SCAM"]
    classificationFit: Literal["EXACT", "APPROXIMATE"]
    reason: str = Field(min_length=1, max_length=250)
    tags: list[str] = Field(min_length=1, max_length=6)
    references: list[CaseMatch] = Field(default_factory=list, max_length=2)


class Approach(Model):
    contactSetup: ContactSetup | None = None
    referenceUses: list[PatternUse] = Field(default_factory=list, max_length=3)
    id: str = Field(min_length=1, max_length=50)
    impersonatedActorId: str | None
    impersonatedRole: str = Field(min_length=1, max_length=100)
    pretext: str = Field(min_length=1, max_length=250)
    recipientReason: str = Field(min_length=1, max_length=350)
    fictionalEvents: list[str] = Field(min_length=1, max_length=4)
    channel: DeliveryChannel
    requestedActions: list[Literal[tuple(ALLOWED_REQUESTS)]] = Field(min_length=1, max_length=4)
    desiredOutcome: str = Field(min_length=1, max_length=160)
    escalation: str = Field(min_length=1, max_length=600)


class CandidateProposal(Extraction):
    anchorClaims: list[ClaimBinding] = Field(min_length=1, max_length=6)
    personalizationBasis: list[PersonalizationBasis] = Field(min_length=1, max_length=3)
    candidates: list[Approach] = Field(min_length=2, max_length=2)


class CandidateAssessment(Model):
    candidateId: str
    review: SemanticReview
    grounding: StrictInt = Field(ge=0, le=2)
    plausibility: StrictInt = Field(ge=0, le=2)
    originality: StrictInt = Field(ge=0, le=2)
    reason: str = Field(min_length=1, max_length=200)


class CandidateComparison(Model):
    alternativesDistinct: StrictBool
    distinctionReason: str = Field(min_length=1, max_length=250)
    assessments: list[CandidateAssessment] = Field(min_length=2, max_length=2)
