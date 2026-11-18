"""피해 대응(긴급대응) API 주소 모음. Spring 이 이 주소로 호출해요.

| 주소 | 팀 명세 | 언제 |
|---|---|---|
| POST /internal/ai/solutions/extract | AI-06 | SOL-01 상담 시작, SOL-03 상황 설명(FACT_UPDATE) |
| POST /internal/ai/solutions/explain | AI-07 | 대응 카드가 정해진 뒤 '왜 이렇게 해야 하는지' 설명 |
| POST /internal/ai/solutions/answer  | 가안  | SOL-03 질문(QUESTION) · 되물은 질문에 답한 뒤(CLARIFICATION_ANSWER) |

오류: 입력 형식 오류 400/422, AI 실패·근거 인덱스 없음 503 (Spring 은 AI 설명 없이 카드만 보여 주면 돼요)
"""
from fastapi import APIRouter, HTTPException

from app.schemas.solution import (
    SolutionAnswerRequest,
    SolutionAnswerResult,
    SolutionExplainRequest,
    SolutionExplainResult,
    SolutionExtractRequest,
    SolutionExtractResult,
)
from app.services.solution import AiUnavailable, run_answer, run_explain, run_extract

router = APIRouter(prefix="/internal/ai/solutions", tags=["내부 AI (피해 대응)"])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AiUnavailable as e:
        raise HTTPException(status_code=503, detail=f"AI_UNAVAILABLE: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/extract", response_model=SolutionExtractResult, response_model_by_alias=True,
             summary="AI-06 피해 사실 추출 (상황 설명 → 4개 사실 YES/NO/UNKNOWN)")
def extract(req: SolutionExtractRequest):
    return _call(run_extract, req.description)


@router.post("/explain", response_model=SolutionExplainResult, response_model_by_alias=True,
             summary="AI-07 근거 기반 대응 설명 (사실 + 검토된 근거 → 설명)")
def explain(req: SolutionExplainRequest):
    refs = [r.model_dump(by_alias=True) for r in req.verified_references]
    return _call(run_explain, req.facts, refs)


@router.post("/answer", response_model=SolutionAnswerResult, response_model_by_alias=True,
             summary="(가안) 피해 대응 질문 답변 — 근거 안에서 답하거나, 필요한 사실을 되묻거나, 답변 불가")
def answer(req: SolutionAnswerRequest):
    refs = [r.model_dump(by_alias=True) for r in req.verified_references] if req.verified_references is not None else None
    history = [t.model_dump(by_alias=True) for t in req.history] if req.history else None
    return _call(run_answer, req.facts, req.question, req.fraud_type, refs, history)
