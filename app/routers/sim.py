"""시뮬레이션 관련 API 주소(엔드포인트) 모음."""
from fastapi import APIRouter, HTTPException

from app.schemas.sim import (
    ContextTurnRequest,
    ContextTurnResponse,
    ReportRequest,
    ReportResponse,
    ScenarioRequest,
    ScenarioResponse,
    TrainingTurnRequest,
    TrainingTurnResponse,
)
from app.services.context_turn import run_context_turn
from app.services.report import run_report
from app.services.scenario import run_scenario
from app.services.training_turn import run_training_turn

# 주소는 팀 API 명세(AI-01 ~ AI-05)와 같아야 합니다. Spring 이 이 주소로 호출해요.
router = APIRouter(prefix="/internal/ai", tags=["내부 AI (훈련 시뮬레이션)"])


@router.post("/discovery/next", response_model=ContextTurnResponse,
             summary="AI-01 상황 파악 다음 질문 (① 상황 입력·분석)")
def context_turn(req: ContextTurnRequest):
    history = [m.model_dump() for m in req.history]
    try:
        return run_context_turn(req.session_id, history)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/scenarios/generate", response_model=ScenarioResponse,
             summary="AI-03 훈련 시나리오 생성 (② 유형 분류·위험 분석·시나리오·검증)")
def scenario(req: ScenarioRequest):
    facts = [f.model_dump() for f in req.facts]
    return run_scenario(req.session_id, facts)


@router.post("/simulation/reply", response_model=TrainingTurnResponse,
             summary="AI-04 시뮬레이션 AI 응답 (③ 사기범 대사 + 대응 판정 + 선택지)")
def training_turn(req: TrainingTurnRequest):
    scenario = req.scenario.model_dump(by_alias=True)  # camelCase 키로
    history = [m.model_dump() for m in req.history]
    try:
        return run_training_turn(req.session_id, scenario, req.current_scene, history)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/training/evaluate", response_model=ReportResponse,
             summary="AI-05 훈련 최종 평가 (⑤ 개인정보 제공 여부 · 잘한/놓친 대응 · 턴별 피드백)")
def report(req: ReportRequest):
    scenario = req.scenario.model_dump(by_alias=True)
    turns = [t.model_dump(by_alias=True) for t in req.turns]
    events = [e.model_dump(by_alias=True) for e in req.mock_page_events]
    return run_report(req.session_id, scenario, turns, events, req.end_reason)
