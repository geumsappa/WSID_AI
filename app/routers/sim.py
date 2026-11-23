"""시뮬레이션 내부 API. 노션의 success/data 계약을 사용한다."""
from fastapi import APIRouter, Depends

from app.dependencies import require_internal_key

from app.schemas.discovery import DiscoveryRequest, DiscoveryResult
from app.schemas.internal import Success
from app.schemas.training_api import (
    EvaluationRequest, EvaluationResult, ScenarioRequest, ScenarioResult,
    SimulationRequest, SimulationResult,
)
from app.services.discovery import discover
from app.services.training_api import evaluate, scenario, simulation
from app.schemas.solution_reply import SolutionReplyRequest, SolutionReplyResult
from app.services.solution_reply import reply as solution_reply

router = APIRouter(prefix="/internal/ai", tags=["내부 AI · 훈련 시뮬레이션"],
                   dependencies=[Depends(require_internal_key)])


@router.post("/discovery/next", response_model=Success[DiscoveryResult])
def context_turn(request: DiscoveryRequest):
    return Success(data=discover(request))


@router.post("/scenarios/generate", response_model=Success[ScenarioResult])
def generate_scenario(request: ScenarioRequest):
    return Success(data=scenario(request))


@router.post("/simulation/reply", response_model=Success[SimulationResult])
def reply(request: SimulationRequest):
    return Success(data=simulation(request))


@router.post("/training/evaluate", response_model=Success[EvaluationResult])
def report(request: EvaluationRequest):
    return Success(data=evaluate(request))


# AI-08 긴급 대응 도우미 말: 확인된 사실과 검토된 대응 카드로 지금 할 일을 안내해요 (success/data 계약)
@router.post("/solutions/reply", response_model=Success[SolutionReplyResult])
def solutions_reply(request: SolutionReplyRequest):
    return Success(data=solution_reply(request))
