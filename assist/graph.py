"""체크포인터·메모리 없이 매 HTTP 요청마다 새 상태로 실행하는 PLAN/ANSWER 그래프."""
from __future__ import annotations

from typing import TypedDict
import logging
import time

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from rag import config

from .contracts import (
    AnswerRequest, AnswerResponse, Assessment, CandidateReference, Clarification, Condition,
    PlanRequest, PlanResponse, QuestionIntent,
)
from .clarify import FACT_QUERIES, ConditionSeed, propose_condition, validate_clarification
from .context import REFERENTIAL, drop_refund_notice, previous_ai, previous_user, topic_codes, topic_text
from .model import AssistModel, INTENT_QUERIES, allowed_fact_keys
from .upstream import request_id

# 조건 제안이 검증에 걸렸을 때 모델에 돌려주는 설명. 질문·근거 원문은 담지 않는다.
CONDITION_FEEDBACK = {
    "QUOTE_NOT_IN_EVIDENCE": "quote가 문단 excerpt에 글자 그대로 없다. excerpt에서 바꾸지 말고 그대로 복사한다.",
    "UNGROUNDED_CITATION": "referenceCode가 주어진 문단이 아니다. 주어진 code 중에서 고른다.",
    "FORBIDDEN": "quote에 연락처·링크가 있다. 조건이 적힌 다른 부분을 인용한다.",
    "ALREADY_CONFIRMED": "이미 사용자가 답한 조건이다. 다른 조건을 고르거나 답변 가능 여부를 판정한다.",
    "DEFAULT": "조건 형식이 맞지 않는다. 문단 원문 인용과 상황 평서문으로 다시 제안한다.",
}
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
    clarifyKey: str | None
    pendingCondition: ConditionSeed | None
    conditionFeedback: str | None
    conditionRetried: bool
    evaluated: bool
    reassessed: bool


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
        builder.add_node("clarify", self._clarify)
        builder.add_node("answer", self._answer)
        builder.add_conditional_edges(START, lambda state: state["request"].phase,
                                      {"PLAN": "assess", "ANSWER": "answer"})
        builder.add_conditional_edges("assess", lambda state: "end" if "result" in state else "search",
                                      {"end": END, "search": "search"})
        builder.add_conditional_edges("search", lambda state: "clarify" if state.get("clarifyKey") else "evaluate",
                                      {"clarify": "clarify", "evaluate": "evaluate"})
        builder.add_conditional_edges(
            "evaluate",
            lambda state: ("search" if state["retry"]
                           else "evaluate" if state.get("conditionFeedback")
                           else "clarify" if state.get("clarifyKey") or state.get("pendingCondition") else "end"),
            {"search": "search", "evaluate": "evaluate", "clarify": "clarify", "end": END})
        builder.add_conditional_edges("clarify", lambda state: "end" if "result" in state else "evaluate",
                                      {"end": END, "evaluate": "evaluate"})
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
    def _askable(request: PlanRequest, intent: QuestionIntent, key: str) -> bool:
        return (related_fact(request, intent, key) and getattr(request.facts, key) == "UNKNOWN"
                and request.remainingClarifications > 0 and key not in request.askedFactKeys)

    @staticmethod
    def _condition_question(state: AssistState, condition: ConditionSeed, content: str | None,
                            references: list[dict]) -> PlanResponse | None:
        """평가 단계가 함께 쓴 질문이 검증을 통과하면 바로 쓴다(LLM 호출 1회 절약)."""
        if not content:
            return None
        check = validate_clarification(key=None, condition=condition, content=content,
                                       cited_codes=[condition.referenceCode], references=references,
                                       question=state["request"].question)
        logger.info("assist request_id=%s stage=clarify source=evaluate ok=%s reason=%s",
                    request_id.get(), check.ok, check.reason or "-")
        if not check.ok:
            return None
        return PlanResponse(status="NEED_USER_INPUT", questionIntent=state["intent"], candidateReferences=[],
                            clarification=Clarification(kind="CONDITION", factKey=None,
                                                        condition=condition.to_condition(content),
                                                        content=content.strip(), options=["YES", "NO", "UNKNOWN"]),
                            reasonCode=None)

    @staticmethod
    def _conditions_open(request: PlanRequest) -> bool:
        return request.conditionsEnabled and request.remainingClarifications > 0

    @staticmethod
    def _target_label(assessment) -> str:
        if assessment.clarification is None:
            return "NONE"
        return assessment.clarification.factKey or "CONDITION"

    @staticmethod
    def _evaluation_references(state: AssistState) -> list[dict]:
        return [{**ref, "excerpt": ref["excerpt"][:MAX_EVALUATION_EXCERPT]}
                for ref in state["references"][:MAX_EVALUATION_REFERENCES]]

    async def _clarify(self, state: AssistState) -> dict:
        """검색된 근거로 추가 질문 문장을 만들고, 검증을 통과할 때만 내보낸다."""
        request = state["request"]
        condition, key = state.get("pendingCondition"), state.get("clarifyKey")
        references = self._evaluation_references(state)
        started = time.monotonic()
        draft = await self.model.clarify(request, condition or key, references)
        check = validate_clarification(key=key, condition=condition, content=draft.content,
                                       cited_codes=draft.referenceCodes, references=references,
                                       question=request.question)
        logger.info("assist request_id=%s stage=clarify elapsed_ms=%d target=%s ok=%s reason=%s refs=%s",
                    request_id.get(), round((time.monotonic() - started) * 1000),
                    "CONDITION" if condition else key, check.ok,
                    check.reason or "-", ",".join(draft.referenceCodes) if check.ok else "-")
        if check.ok:
            clarification = (Clarification(kind="CONDITION", factKey=None,
                                           condition=condition.to_condition(draft.content),
                                           content=draft.content.strip(), options=["YES", "NO", "UNKNOWN"])
                             if condition else
                             Clarification(factKey=key, content=draft.content.strip(),
                                           options=["YES", "NO", "UNKNOWN"]))
            return {"clarifyKey": None, "pendingCondition": None, "result": PlanResponse(
                status="NEED_USER_INPUT", questionIntent=state["intent"], clarification=clarification,
                candidateReferences=[], reasonCode=None)}
        # 근거로 뒷받침되는 질문을 만들지 못하면 묻지 않고, 확인하지 못한 사실을 조건으로 둔 답변을 평가한다.
        conditional = request.model_copy(update={"remainingClarifications": 0})
        if state.get("evaluated"):
            if state["deadline"] - time.monotonic() < config.ASSIST_RETRY_MIN_SECONDS:
                return {"clarifyKey": None, "pendingCondition": None,
                        "result": unable(state["intent"], "NO_EVIDENCE")}
            return {"clarifyKey": None, "pendingCondition": None, "request": conditional, "reassessed": True}
        return {"clarifyKey": None, "pendingCondition": None, "request": conditional}

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
        clarify_key = None
        if assessment.decision == "NEED_USER_INPUT" and self._askable(request, intent, assessment.factKey):
            clarify_key = assessment.factKey
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
        if request.confirmedConditions:
            query += "\n확인된 상황: " + "; ".join(f"{c.statement}={c.answer}" for c in request.confirmedConditions)
        if clarify_key is not None:
            # 추가 질문의 근거와 답변 근거를 한 번의 검색으로 함께 찾는다.
            query += "\n추가 확인: " + FACT_QUERIES[clarify_key]
        return {"intent": intent, "assessment": assessment, "query": query, "clarifyKey": clarify_key}

    async def _search(self, state: AssistState) -> dict:
        started = time.monotonic()
        request = state["request"]
        extra = {"fraud_type": request.fraudType} if request.fraudType is not None else {}
        references = await self.retriever.search(state["query"], request.facts.model_dump(), **extra)
        # 질문 주제로 꼭 넣을 근거를 앞에 두고(검색 결과와 합쳐 최대 10개), 중고거래면 환급 안내 문단은 빼요
        text = topic_text(request)
        forced = self.retriever.by_codes(topic_codes(text, request.question or "")) if hasattr(self.retriever, "by_codes") else []
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
        update = await self._evaluate_once(state)
        # 피드백은 한 번의 재평가에만 쓴다. 다시 설정하지 않은 경로에서는 항상 지운다.
        update.setdefault("conditionFeedback", None)
        return update

    async def _evaluate_once(self, state: AssistState) -> dict:
        started = time.monotonic()
        reassessed = state.get("reassessed", False)
        references = self._evaluation_references(state)
        feedback = state.get("conditionFeedback")
        extra = {"feedback": feedback} if feedback else {}
        assessment = await self.model.evaluate(state["request"], state["intent"], references, **extra)
        logger.info("assist request_id=%s stage=evaluate elapsed_ms=%d sufficient=%s retry=%s clarify=%s",
                    request_id.get(), round((time.monotonic() - started) * 1000),
                    assessment.sufficient, assessment.retryQuery is not None,
                    self._target_label(assessment))
        if assessment.clarification is not None:
            proposal, key = assessment.clarification, assessment.clarification.factKey
            if not reassessed and key is not None and self._askable(state["request"], state["intent"], key):
                return {"retry": False, "clarifyKey": key, "evaluated": True}
            if not reassessed and key is None and self._conditions_open(state["request"]):
                condition, reason = propose_condition(
                    reference_code=proposal.referenceCode, quote=proposal.quote, references=references,
                    confirmed=state["request"].confirmedConditions)
                logger.info("assist request_id=%s stage=condition ok=%s reason=%s",
                            request_id.get(), condition is not None, reason or "-")
                if condition is not None:
                    ready = self._condition_question(state, condition, proposal.content, references)
                    if ready is not None:
                        return {"retry": False, "conditionFeedback": None, "result": ready}
                    return {"retry": False, "conditionFeedback": None, "pendingCondition": condition,
                            "evaluated": True}
                # 인용·문장 형식 오류는 이유를 알려 주고 한 번만 다시 제안받는다(시간이 충분할 때).
                if (not feedback and not state.get("conditionRetried")
                        and state["deadline"] - time.monotonic() >= config.ASSIST_RETRY_MIN_SECONDS):
                    return {"retry": False, "conditionRetried": True,
                            "conditionFeedback": CONDITION_FEEDBACK.get(reason, CONDITION_FEEDBACK["DEFAULT"])}
            # 모델이 허용 목록을 무시한 예외 상황에서만, 시간이 충분하면 조건부 답변을 재평가한다.
            if (reassessed or state["searches"] > 1
                    or state["deadline"] - time.monotonic() < config.ASSIST_RETRY_MIN_SECONDS):
                return {"retry": False, "result": unable(state["intent"], "NO_EVIDENCE")}
            conditional = state["request"].model_copy(update={"remainingClarifications": 0})
            reassessed = True
            started = time.monotonic()
            assessment = await self.model.evaluate(conditional, state["intent"], references)
            logger.info("assist request_id=%s stage=evaluate elapsed_ms=%d sufficient=%s retry=%s clarify=%s",
                        request_id.get(), round((time.monotonic() - started) * 1000),
                        assessment.sufficient, assessment.retryQuery is not None,
                        self._target_label(assessment))
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
