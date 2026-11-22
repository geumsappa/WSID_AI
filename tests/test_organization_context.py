import copy

from app.guard.scenario_guard import find_unsafe_text, mentioned_organizations, validate_and_normalize
from app.scenario_data import FALLBACK_SCENARIOS
from app.services import scenario as scenario_service, training_turn


def scenario_output(subtype="중고거래 사기"):
    output = copy.deepcopy(FALLBACK_SCENARIOS[subtype])
    output["scenario"]["persona"]["organization"] = "기업은행"
    output["scenario"]["persona"]["role"] = "채용 담당자"
    output["scenario"]["openingMessage"] = "기업은행 채용 담당자입니다. 응시 확인을 위해 연락드렸습니다."
    return output


def test_only_input_fact_organizations_are_allowed():
    allowed = mentioned_organizations([{"detail": "기업은행 역량검사를 봤어요"}])
    assert "기업은행" in allowed
    assert "국민은행" not in allowed
    assert "KB" not in mentioned_organizations([{"detail": "backup 작업"}])
    assert not find_unsafe_text("기업은행 채용 담당자입니다", allowed)
    assert find_unsafe_text("국민은행 채용 담당자입니다", allowed)


def test_allowing_a_name_does_not_allow_urls_or_contact_numbers():
    assert find_unsafe_text("기업은행 https://example.org", ["기업은행"])
    assert find_unsafe_text("기업은행 1588-1234", ["기업은행"])


def test_scenario_uses_fact_allowlist_instead_of_model_declared_allowlist():
    output = scenario_output()
    assert validate_and_normalize(output)[0] is None
    result, errors = validate_and_normalize(output, ["기업은행"])
    assert not errors
    assert result["scenario"]["allowedOrganizations"] == ["기업은행"]
    output["scenario"]["openingMessage"] = "국민은행 담당자입니다"
    output["scenario"]["allowedOrganizations"] = ["국민은행"]
    assert validate_and_normalize(output, ["기업은행"])[0] is None




def test_generation_keeps_grounded_organization(pipeline_model, graph_data, plan_data, generation_data):
    from app.case_db import case_ids_for_subtype
    graph_data["events"][0].update(actorId="actor_user", action="역량검사를 봄", object="기업은행")
    graph_data["entities"][1].update(label="기업은행", kind="ORGANIZATION")
    graph_data["evidence"][0]["quote"] = "기업은행 역량검사를 봤어요"
    plan_data.update(channel="이메일")
    plan_data["anchorClaims"][0]["actorId"] = "actor_user"
    generation_data.clear()
    generation_data.update(scenario_output("채용·아르바이트 사기"))
    generation_data["scenario"].update(channel="이메일")
    for field in ("fraudSubtype", "riskType", "basedOnCaseIds"):
        generation_data["scenario"].pop(field, None)
    generation_data["analysis"].pop("riskType", None)
    generation_data["analysis"].pop("riskTypeReason", None)
    generation_data["preservedClaims"] = copy.deepcopy(plan_data["anchorClaims"])
    result = scenario_service.run_scenario("s", [{"detail": "기업은행 역량검사를 봤어요"}])
    assert "기업은행" in result["scenario"]["allowedOrganizations"]
    assert result["scenario"]["persona"]["organization"] == "기업은행"


def test_followup_dialogue_keeps_allowed_organization(monkeypatch):
    scenario = validate_and_normalize(scenario_output(), ["기업은행"])[0]["scenario"]
    scenario["maxTurns"] = 15
    monkeypatch.setattr(training_turn, "generate_json", lambda *a, **k: {
        "judgment": {"outcome": "neutral"}, "personaMessage": "기업은행 채용 담당자입니다. 확인이 필요합니다.",
        "nextScene": 1, "requestType": "대화 지속",
    })
    result = training_turn.run_training_turn("context", scenario, 1, [{"role": "user", "text": "어디라고요?"}])
    assert "기업은행" in result["personaMessage"]
    assert not result["fallbackUsed"]
