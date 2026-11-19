"""체크포인터·메모리 없이 매 HTTP 요청마다 새 상태로 실행하는 PLAN/ANSWER 그래프."""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from .contracts import (
    AnswerRequest, AnswerResponse, Assessment, CandidateReference, Clarification,
    PlanRequest, PlanResponse, QuestionIntent,
)
from .model import AssistModel, CLARIFICATION_TEXT, FACT_MENTIONS, INTENT_FACTS, INTENT_QUERIES

MAX_RESEARCHES = 2


def related_fact(request: PlanRequest, intent: QuestionIntent, key: str) -> bool:
    """의도에 연결된 사실이거나, 질문 원문이 그 사실의 행동을 직접 언급할 때만 물을 수 있다.
    후속 요청(question=null)은 원문이 없으므로 의도 연결만 본다."""
    if key == INTENT_FACTS.get(intent):
        return True
    question = request.question or ""
    return any(word in question for word in FACT_MENTIONS[key])


class AssistState(TypedDict, total=False):
    request: PlanRequest | AnswerRequest
    assessment: Assessment
    intent: QuestionIntent
    query: str
    searches: int
    references: list[dict]
    retry: bool
    result: PlanResponse | AnswerResponse


def unable(intent: QuestionIntent | None, reason: str) -> PlanResponse:
    return PlanResponse(status="UNABLE_TO_ANSWER", questionIntent=intent, clarification=None,
                        candidateReferences=[], reasonCode=reason)


class AssistService:
    def __init__(self, model: AssistModel, retriever):
        self.model = model
        self.retriever = retriever
        builder = StateGraph(AssistState)
        builder.add_node("assess", self._assess)
        builder.add_node("search", self._search)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("answer", self._answer)
        builder.add_conditional_edges(START, lambda state: state["request"].phase,
                                      {"PLAN": "assess", "ANSWER": "answer"})
        builder.add_conditional_edges("assess", lambda state: "end" if "result" in state else "search",
                                      {"end": END, "search": "search"})
        builder.add_edge("search", "evaluate")
        builder.add_conditional_edges("evaluate", lambda state: "search" if state["retry"] else "end",
                                      {"search": "search", "end": END})
        builder.add_edge("answer", END)
        self.graph = builder.compile()

    async def run(self, request: PlanRequest | AnswerRequest) -> PlanResponse | AnswerResponse:
        # 환경에 LANGSMITH_TRACING이 설정돼 있어도 입력·출력 원문을 외부로 추적 전송하지 않는다.
        with tracing_context(enabled=False):
            state = await self.graph.ainvoke(
                {"request": request, "searches": 0, "retry": False},
                config={"recursion_limit": 16, "callbacks": []},
            )
        result = state["result"]
        result.validate_for(request)
        return result

    @staticmethod
    def _clarify(request: PlanRequest, intent: QuestionIntent, key: str) -> PlanResponse | None:
        if (not related_fact(request, intent, key) or getattr(request.facts, key) != "UNKNOWN"
                or request.remainingClarifications == 0 or key in request.askedFactKeys):
            return None
        return PlanResponse(status="NEED_USER_INPUT", questionIntent=intent,
                            clarification=Clarification(factKey=key, content=CLARIFICATION_TEXT[key],
                                                        options=["YES", "NO", "UNKNOWN"]),
                            candidateReferences=[], reasonCode=None)

    async def _assess(self, state: AssistState) -> dict:
        request = state["request"]
        assessment = await self.model.assess(request)
        # 후속 요청 intent는 모델 출력과 관계없이 Spring이 보낸 원래 값을 유지한다.
        intent = request.questionIntent or assessment.questionIntent
        if assessment.decision == "OUT_OF_SCOPE":
            return {"result": unable(intent, "OUT_OF_SCOPE")}
        if assessment.decision == "NEED_USER_INPUT":
            clarification = self._clarify(request, intent, assessment.factKey)
            if clarification is not None:
                return {"result": clarification}
        query = INTENT_QUERIES[intent]
        if request.question is not None:
            query += "\n질문: " + request.question
        query += "\n사실: " + ", ".join(f"{key}={value}" for key, value in request.facts.model_dump().items())
        return {"intent": intent, "assessment": assessment, "query": query}

    async def _search(self, state: AssistState) -> dict:
        references = await self.retriever.search(state["query"], state["request"].facts.model_dump())
        if len(references) > 10:
            raise ValueError("too many retrieved references")
        candidates = [CandidateReference.model_validate(ref) for ref in references]
        if len({ref.code for ref in candidates}) != len(candidates):
            raise ValueError("duplicate retrieved reference code")
        return {"references": references, "searches": state["searches"] + 1, "retry": False}

    async def _evaluate(self, state: AssistState) -> dict:
        assessment = await self.model.evaluate(state["request"], state["intent"], state["references"])
        if assessment.clarification is not None:
            clarification = self._clarify(state["request"], state["intent"], assessment.clarification.factKey)
            if clarification is not None:
                return {"retry": False, "result": clarification}
            # 재질문할 수 없으면 같은 근거로 조건부 답변 가능성을 한 번 더 평가한다.
            conditional = state["request"].model_copy(update={"remainingClarifications": 0})
            assessment = await self.model.evaluate(conditional, state["intent"], state["references"])
            if assessment.clarification is not None:
                return {"retry": False, "result": unable(state["intent"], "NO_EVIDENCE")}
        by_code = {ref["code"]: ref for ref in state["references"]}
        if not set(assessment.referenceCodes) <= by_code.keys():
            raise ValueError("model selected unknown reference")
        if assessment.sufficient:
            return {"retry": False, "result": PlanResponse(
                status="EVIDENCE_READY", questionIntent=state["intent"], clarification=None,
                candidateReferences=[CandidateReference.model_validate(by_code[code])
                                     for code in assessment.referenceCodes], reasonCode=None,
            )}
        if (assessment.retryQuery is not None and state["searches"] < 1 + MAX_RESEARCHES
                and assessment.retryQuery != state["query"]):
            return {"retry": True, "query": assessment.retryQuery}
        return {"retry": False, "result": unable(state["intent"], "NO_EVIDENCE")}

    async def _answer(self, state: AssistState) -> dict:
        # ANSWER의 그래프 경로에는 search 노드가 없다.
        result = await self.model.answer(state["request"])
        result.validate_for(state["request"])
        return {"result": result}
