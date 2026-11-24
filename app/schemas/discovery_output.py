"""Provider-enforced response variants: ASK cannot contain a null/blank question."""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app.schemas.fast_discovery import ReadinessBasis, DetailEvidence, Topic


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ask(Model):
    type: Literal["ASK"]
    nextTopic: Topic
    nextQuestion: str = Field(min_length=1, max_length=180, pattern=r"\S")


class VerifyReady(Model):
    type: Literal["VERIFY_READY"]
    readinessBasis: ReadinessBasis


class Stop(Model):
    type: Literal["STOP"]
    evidence: DetailEvidence


class OfferBasic(Model):
    type: Literal["OFFER_BASIC"]


class ContextUpdate(DetailEvidence):
    topic: Literal["ACTIVITY", "SETTING", "SERVICE", "PEOPLE", "PURPOSE", "ACCESS", "INTERACTION", "TRANSACTION", "ROLE", "TIME", "ACTIVITY_DETAIL"]
    status: Literal["KNOWN", "PARTIAL", "DECLINED", "NOT_APPLICABLE"]


class KnownPersonName(DetailEvidence):
    topic: Literal["PERSON_NAME"]
    status: Literal["KNOWN"]
    name: str = Field(min_length=1, max_length=50, pattern=r"\S")


class UnresolvedPersonName(DetailEvidence):
    topic: Literal["PERSON_NAME"]
    status: Literal["PARTIAL", "DECLINED", "NOT_APPLICABLE"]


class MissingQuestionNeed(Model):
    topic: Topic
    status: Literal["MISSING"]
    necessity: Literal["REQUIRED", "OPTIONAL"]
    missingDetail: str = Field(min_length=1, max_length=120, pattern=r"\S")
    scenarioImpact: str = Field(min_length=1, max_length=160, pattern=r"\S")
    reason: str = Field(min_length=1, max_length=180)


class AnsweredQuestionNeed(Model):
    topic: Topic
    status: Literal["ANSWERED"]
    reason: str = Field(min_length=1, max_length=180)
    evidence: list[DetailEvidence] = Field(min_length=1, max_length=2)


class UnnecessaryQuestionNeed(Model):
    topic: Topic
    status: Literal["UNNECESSARY"]
    reason: str = Field(min_length=1, max_length=180)


class DiscoveryOutput(Model):
    updates: list[ContextUpdate | KnownPersonName | UnresolvedPersonName] = Field(default_factory=list, max_length=12)
    personalizationTarget: Literal["PERSON", "SERVICE", "OTHER", "UNDECIDED"]
    requiredTopics: list[Topic] = Field(default_factory=list, max_length=5)
    focusChanged: StrictBool = False
    questionAssessments: list[MissingQuestionNeed | AnsweredQuestionNeed | UnnecessaryQuestionNeed] = Field(default_factory=list, max_length=7)
    action: Ask | VerifyReady | Stop | OfferBasic

    def as_decision(self):
        result = self.model_dump(exclude={"action"})
        result.update(contextSufficient=False, readinessBasis=None, stopRequested=False,
                      nextTopic=None, nextQuestion="")
        if isinstance(self.action, Ask):
            result.update(nextTopic=self.action.nextTopic, nextQuestion=self.action.nextQuestion)
        elif isinstance(self.action, VerifyReady):
            result.update(contextSufficient=True, readinessBasis=self.action.readinessBasis.model_dump())
        elif isinstance(self.action, Stop):
            result["stopRequested"] = True
        return result


def output_schema(*, allow_basic):
    schema = deepcopy(DiscoveryOutput.model_json_schema())
    # OpenAI strict mode requires all object fields and forbids additional properties.
    def strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            node.pop("title", None)
            node.pop("description", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)
    strict(schema)
    if not allow_basic:
        schema["properties"]["action"]["anyOf"] = [item for item in schema["properties"]["action"]["anyOf"]
            if item.get("$ref") != "#/$defs/OfferBasic"]
        schema["$defs"].pop("OfferBasic", None)
    return schema
