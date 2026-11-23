from typing import Literal

from pydantic import BaseModel, Field


class ReplyCard(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    priority: str = Field(default="", max_length=20)
    actions: list[str] = Field(default_factory=list, max_length=10)


class SolutionReplyRequest(BaseModel):
    """AI-08 요청. facts: transferredMoney 등 → YES/NO/UNKNOWN. cards: 검토된 대응 카드의 제목·할 일"""
    facts: dict[str, str] = Field(default_factory=dict)
    cards: list[ReplyCard] = Field(default_factory=list, max_length=10)
    userText: str | None = Field(default=None, max_length=1000)
    kind: Literal["start", "update", "correct"] = "update"


class SolutionReplyResult(BaseModel):
    message: str = Field(min_length=1, max_length=500)
