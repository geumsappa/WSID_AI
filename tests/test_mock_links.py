from app.schemas.training_api import ScenarioRequest, SimulationRequest
from app.services import training_api
from app.services.scenario import _fallback


def test_recruitment_link_gets_internal_page_even_without_financial_template(monkeypatch):
    scenario = _fallback([])["scenario"]
    scenario["mockPage"] = "채용"
    monkeypatch.setattr(training_api, "run_scenario", lambda *args: {"scenario": scenario})
    result = training_api.scenario(ScenarioRequest(facts={"activity": "채용 지원"}, fraudTypeCode="SECONDHAND_TRADE"))
    assert result.mockPageTemplateCode == "BANK_NOTICE"


def test_reply_with_link_request_always_includes_page_and_internal_marker(monkeypatch):
    scenario = _fallback([])["scenario"]
    scenario["mockPage"] = None
    monkeypatch.setattr(training_api, "run_training_turn", lambda *args: {
        "judgment": {}, "infoDisclosure": {}, "requestType": "링크 클릭",
        "personaMessage": "아래에서 본인 확인을 진행해 주세요.", "done": False,
    })
    result = training_api.simulation(SimulationRequest(scenario=scenario, messages=[], userInput="네?", turnCount=1))
    assert result.mockPage is not None
    assert result.mockPage.isSimulation
    assert "[모의링크]" in result.reply
    assert "http" not in result.reply


def test_completed_training_does_not_offer_an_active_page(monkeypatch):
    monkeypatch.setattr(training_api, "run_training_turn", lambda *args: {
        "judgment": {}, "infoDisclosure": {}, "requestType": "링크 클릭",
        "personaMessage": "훈련을 마칩니다.", "done": True,
    })
    result = training_api.simulation(SimulationRequest(scenario=_fallback([])["scenario"], messages=[], userInput="종료", turnCount=1))
    assert result.mockPage is None
    assert "[모의링크]" not in result.reply
