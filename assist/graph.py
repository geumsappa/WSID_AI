"""체크포인터·메모리 없이 매 HTTP 요청마다 새 상태로 실행하는 PLAN/ANSWER 그래프."""
from __future__ import annotations

from typing import TypedDict
import logging
import time

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from rag import config

from .contracts import (
    AnswerRequest, AnswerResponse, Assessment, CandidateReference, Clarification,
    PlanRequest, PlanResponse, QuestionIntent,
)
from .context import REFERENTIAL, drop_refund_notice, previous_ai, previous_user, topic_codes, topic_text
from .model import AssistModel, CLARIFICATION_TEXT, INTENT_QUERIES, allowed_fact_keys
from .upstream import request_id

MAX_EVALUATION_REFERENCES = 5
MAX_EVALUATION_EXCERPT = 600
logger = logging.getLogger("uvicorn.error.assist")


def related_fact(request: PlanRequest, intent: QuestionIntent, key: str) -> bool:
    """의도에 연결된 사실이거나, 질문 원문이 그 사실의 행동을 직접 언급할 때만 물을 수 있다.
    후속 요청(question=null)은 원문이 없으므로 의도 연결만 본다."""
    return key in allowed_fact_keys(request, intent)


class AssistState(TypedDict, total=False):
    request: PlanRequest | AnswerRequest
    assessment: Assessment
    intent: QuestionIntent
    query: str
    searches: int
    references: list[dict]
    retry: bool
    result: PlanResponse | AnswerResponse
    deadline: float


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
                {"request": request, "searches": 0, "retry": False,
                 "deadline": time.monotonic() + (config.ASSIST_PLAN_TIMEOUT_SEC if request.phase == "PLAN" else config.ASSIST_ANSWER_TIMEOUT_SEC)},
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
        started = time.monotonic()
        assessment = await self.model.assess(request)
        logger.info("assist request_id=%s stage=assess elapsed_ms=%d decision=%s",
                    request_id.get(), round((time.monotonic() - started) * 1000), assessment.decision)
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
        # v1.1: 질문이 없거나 짧으면("둘 다 알려줘") 직전 사용자 말을 붙여 무엇을 묻는지 검색해요
        prev = previous_user(request)
        if prev and len(request.question or "") < 40:
            query += "\n이전 질문: " + prev[:300]
        if request.question and REFERENTIAL.search(request.question) and previous_ai(request):
            query += "\n이전 답변: " + previous_ai(request)[:300]
        if request.fraudType is not None:
            query += "\n사기 유형: " + request.fraudType
        query += "\n사실: " + ", ".join(f"{key}={value}" for key, value in request.facts.model_dump().items())
        return {"intent": intent, "assessment": assessment, "query": query}

    async def _search(self, state: AssistState) -> dict:
        started = time.monotonic()
        request = state["request"]
        extra = {"fraud_type": request.fraudType} if request.fraudType is not None else {}
        references = await self.retriever.search(state["query"], request.facts.model_dump(), **extra)
        # 질문 주제로 꼭 넣을 근거를 앞에 두고(검색 결과와 합쳐 최대 10개), 중고거래면 환급 안내 문단은 빼요
        text = topic_text(request)
        forced = self.retriever.by_codes(topic_codes(text)) if hasattr(self.retriever, "by_codes") else []
        if forced:
            seen = {ref["code"] for ref in forced}
            references = (forced + [ref for ref in references if ref["code"] not in seen])[:10]
        references = drop_refund_notice(references, request.fraudType, text)
        logger.info("assist request_id=%s stage=search elapsed_ms=%d n=%d",
                    request_id.get(), round((time.monotonic() - started) * 1000), len(references))
        if len(references) > 10:
            raise ValueError("too many retrieved references")
        candidates = [CandidateReference.model_validate(ref) for ref in references]
        if len({ref.code for ref in candidates}) != len(candidates):
            raise ValueError("duplicate retrieved reference code")
        return {"references": references, "searches": state["searches"] + 1, "retry": False}

    async def _evaluate(self, state: AssistState) -> dict:
        started = time.monotonic()
        reassessed = False
        references = [{**ref, "excerpt": ref["excerpt"][:MAX_EVALUATION_EXCERPT]}
                      for ref in state["references"][:MAX_EVALUATION_REFERENCES]]
        assessment = await self.model.evaluate(state["request"], state["intent"], references)
        logger.info("assist request_id=%s stage=evaluate elapsed_ms=%d sufficient=%s retry=%s clarify=%s",
                    request_id.get(), round((time.monotonic() - started) * 1000),
                    assessment.sufficient, assessment.retryQuery is not None,
                    assessment.clarification.factKey if assessment.clarification else "NONE")
        if assessment.clarification is not None:
            clarification = self._clarify(state["request"], state["intent"], assessment.clarification.factKey)
            if clarification is not None:
                return {"retry": False, "result": clarification}
            # 모델이 허용 목록을 무시한 예외 상황에서만, 시간이 충분하면 조건부 답변을 재평가한다.
            if (state["searches"] > 1
                    or state["deadline"] - time.monotonic() < config.ASSIST_RETRY_MIN_SECONDS):
                return {"retry": False, "result": unable(state["intent"], "NO_EVIDENCE")}
            conditional = state["request"].model_copy(update={"remainingClarifications": 0})
            reassessed = True
            started = time.monotonic()
            assessment = await self.model.evaluate(conditional, state["intent"], references)
            logger.info("assist request_id=%s stage=evaluate elapsed_ms=%d sufficient=%s retry=%s clarify=%s",
                        request_id.get(), round((time.monotonic() - started) * 1000),
                        assessment.sufficient, assessment.retryQuery is not None,
                        assessment.clarification.factKey if assessment.clarification else "NONE")
            if assessment.clarification is not None:
                return {"retry": False, "result": unable(state["intent"], "NO_EVIDENCE")}
        by_code = {ref["code"]: ref for ref in references}
        if not set(assessment.referenceCodes) <= by_code.keys():
            raise ValueError("model selected unknown reference")
        if assessment.sufficient:
            return {"retry": False, "result": PlanResponse(
                status="EVIDENCE_READY", questionIntent=state["intent"], clarification=None,
                candidateReferences=[CandidateReference.model_validate(by_code[code])
                                     for code in assessment.referenceCodes], reasonCode=None,
            )}
        if (not reassessed and assessment.retryQuery is not None and state["searches"] < 1 + config.ASSIST_MAX_RESEARCHES
                and assessment.retryQuery != state["query"]):
            if state["deadline"] - time.monotonic() >= config.ASSIST_RETRY_MIN_SECONDS:
                return {"retry": True, "query": assessment.retryQuery}
            logger.info("assist request_id=%s stage=retry decision=SKIP reason=TIME_BUDGET", request_id.get())
        return {"retry": False, "result": unable(state["intent"], "NO_EVIDENCE")}

    async def _answer(self, state: AssistState) -> dict:
        # ANSWER의 그래프 경로에는 search 노드가 없다.
        result = await self.model.answer(state["request"])
        result.validate_for(state["request"])
        return {"result": result}
