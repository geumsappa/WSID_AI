"""Retrieval relevance, non-forcing, reference gates, and evidence persistence."""
from copy import deepcopy
import pytest
from app.api_errors import AiApiError
from app.schemas.context_graph import PatternUse
from app.schemas.training_api import ScenarioRequest
from app.services import scenario_planner, training_api
from app.services.context_graph import ContextIntegrityError
from app.services.scenario_retrieval import retrieve_patterns, validate_uses, _index


@pytest.mark.parametrize("query,expected", [
    ("중고거래 앱에서 노트북을 구매하려고 판매자랑 채팅했어", "LP-013"),
    ("중고거래 판매자가 안전결제 링크를 보내줬어", "RC-008"),
    ("공연 티켓 양도를 받아서 입금했어", "LP-016"),
    ("재택 부업으로 리뷰 업무를 알아보고 있어", "RC-011"),
    ("건강검진 결과를 기다려", "RC-020"),
    ("공유 킥보드를 QR 찍고 이용했어", "RC-006"),
    ("주식 투자 정보를 찾아봤어", "RC-012"),
    ("직장 동료가 업무용 메신저로 상품권을 대신 사달래", "LP-032"),
    ("해외취업 알아보는데 숙소 항공권 무료 고수익 아르바이트래", "LP-033"),
    ("전세 계약하려고 임대인과 중개인을 만났어", "LP-034"),
    ("SNS 계정 로그인 안내 문자를 받았어", "LP-035"),
    ("지인한테 부고장 문자가 왔어", "LP-036"),
])
def test_expected_pattern_is_in_shortlist(query, expected):
    assert expected in {row["id"] for row in retrieve_patterns(query)["patterns"]}


@pytest.mark.parametrize("query", ["식당에서 친구랑 순두부찌개 먹었고 친구가 계산했어", "헬스장에서 벤치프레스 했어", "아빠 안전교육 메뉴 찾는걸 도와줬어", ""])
def test_unrelated_experience_does_not_force_a_reference(query):
    assert retrieve_patterns(query)["patterns"] == []


def test_runtime_registry_excludes_unverified_and_foreign_records():
    data, *_ = _index()
    assert data["records"]
    assert data["schemaVersion"] == 2
    assert not {"RC-021", "RC-009", "RC-004", "RC-007", "RC-022", "RC-026", "RC-029"} & {row["id"] for row in data["records"]}
    assert all(row["country"] == "KR" and row["review"]["status"] == "LICENSE_AND_FACTS_CHECKED" and row["source"]["license"]["code"] == "KOGL-1" and row["source"]["attributionText"] for row in data["records"])


def test_known_but_not_retrieved_id_is_not_valid_evidence():
    use = PatternUse(caseId="RC-001", technique="송금", adaptationReason="알고 지낸 관계를 사칭한 요청")
    with pytest.raises(ContextIntegrityError, match="unretrieved"):
        validate_uses([use], {"patterns": []})


def test_made_up_technique_is_rejected():
    trace = retrieve_patterns("가족 지인 휴대폰 고장 메신저 연락")
    use = PatternUse(caseId="RC-001", technique="확인하지 않은 요구", adaptationReason="검사")
    with pytest.raises(ContextIntegrityError, match="unsupported_pattern"):
        validate_uses([use], trace)


def _use_pattern(monkeypatch, grounded_model):
    trace = retrieve_patterns("가족 지인 휴대폰 고장 메신저 연락")
    assert "RC-001" in {row["id"] for row in trace["patterns"]}
    monkeypatch.setattr(scenario_planner, "retrieve_patterns", lambda text: deepcopy(trace))
    return {"caseId": "RC-001", "technique": "관계에 대한 신뢰 악용", "adaptationReason": "함께 이용한 동료 관계를 사칭하는 새 정산 요청"}


def _candidate(graph, plan, use):
    from app.schemas.context_graph import Approach
    first = {key: value for key, value in plan.items() if key in Approach.model_fields}
    first.update(desiredOutcome="정산 확인을 구실로 정보 요구", escalation="관계 신뢰에서 확인 재촉으로 진행", referenceUses=[use])
    second = {**first, "id": "other", "channel": "이메일", "impersonatedActorId": None,
              "impersonatedRole": "가상 안내 직원", "pretext": "가상 이용 안내", "referenceUses": []}
    return {"context": graph, "anchorClaims": plan["anchorClaims"], "personalizationBasis": plan["personalizationBasis"], "candidates": [first, second]}


def _single_plan(bundle):
    plan = deepcopy(bundle["candidates"][0])
    plan.update(recipientActorId=bundle["context"]["userActorId"],
        anchorClaims=bundle["anchorClaims"], personalizationBasis=bundle["personalizationBasis"],
        contactSetup={"contactBasis": "FICTIONAL_FIRST_CONTACT", "senderRole": plan["impersonatedRole"],
            "contactPremise": "훈련에서 가정한 첫 사칭 연락", "knowledgePremise": "사용자가 말한 이용 경험을 언급하는 가상 주장",
            "evidenceIds": [bundle["context"]["evidence"][0]["id"]], "fictionalAssumptions": ["새로운 사칭 연락"]})
    return {"context": bundle["context"], "selectedPlan": plan}


def _comparison(bundle, verdict):
    return {"alternativesDistinct": True, "distinctionReason": "접근 인물이 다름", "assessments": [
        {"candidateId": p["id"], "review": deepcopy(verdict), "grounding": 2 if i == 0 else 1,
         "plausibility": 2, "originality": 1, "reason": "근거 검토"} for i, p in enumerate(bundle["candidates"])]}


def test_pre_generation_evidence_survives_public_contract(monkeypatch, grounded_model, graph_data, plan_data, review_data):
    use = _use_pattern(monkeypatch, grounded_model)
    bundle = _candidate(graph_data, plan_data, use)
    verdict = {**review_data, "referencesSupported": True}
    grounded_model[1].update(generation_plan=_single_plan(bundle), semantic_review=verdict)
    result = training_api.scenario(ScenarioRequest(fraudTypeCode="MESSENGER_PHISHING", facts={"discoveryReady": True,
        "discoveryEvidence": [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]}))
    evidence = result.scenario["generationEvidence"]
    assert evidence["status"] == "USED"
    assert evidence["usedForGeneration"][0]["caseId"] == "RC-001"
    assert evidence["corpusHash"]
    assert evidence["attributionRequired"]
    assert "공공누리 제1유형" in evidence["sourceAttributions"][0]
    assert evidence["usedForGeneration"][0]["source"]["license"]["code"] == "KOGL-1"
    assert evidence["retrievedBeforeGeneration"][0]["sourceUrl"].startswith("https://")
    # Post-generation matches must never be relabelled as pre-generation evidence.
    assert result.scenario["basedOnCases"] == []
    assert not any("messages" in key or "query" in key for key in evidence)
    tasks = [task for task, _ in grounded_model[0]]
    assert tasks == ["generation_plan", "scenario_generate", "semantic_review", "scenario_classify"]


def test_reference_claim_requires_independent_comparison_support(monkeypatch, grounded_model, graph_data, plan_data, review_data):
    use = _use_pattern(monkeypatch, grounded_model)
    bundle = _candidate(graph_data, plan_data, use)
    grounded_model[1].update(candidate_plan=bundle, candidate_compare=_comparison(bundle, review_data))
    _, winner = scenario_planner.choose_generation_plan([{"turn": 1, "text": graph_data["evidence"][0]["quote"]}])
    assert winner.id == "other" and not winner.referenceUses


def test_generated_story_losing_its_reference_is_rejected(monkeypatch, grounded_model, graph_data, plan_data, review_data):
    use = _use_pattern(monkeypatch, grounded_model)
    bundle = _candidate(graph_data, plan_data, use)
    grounded_model[1].update(generation_plan=_single_plan(bundle),
        semantic_review={**review_data, "referencesSupported": False})
    with pytest.raises(AiApiError) as exc:
        training_api.scenario(ScenarioRequest(fraudTypeCode="MESSENGER_PHISHING", facts={"discoveryReady": True,
            "discoveryEvidence": [{"turn": 1, "text": graph_data["evidence"][0]["quote"]}]}))
    assert exc.value.status == 503
    assert sum(task == "scenario_generate" for task, _ in grounded_model[0]) == 2
    assert not any(task == "scenario_classify" for task, _ in grounded_model[0])


@pytest.mark.parametrize("change", ["missing", "restricted", "wrong_scope", "missing_attribution"])
def test_missing_licence_fails_closed(monkeypatch, tmp_path, change):
    import json
    from app.services import scenario_retrieval as module
    data = deepcopy(_index()[0])
    source = data["records"][0]["source"]
    if change == "missing":
        source.pop("license")
    elif change == "restricted":
        source["license"]["code"] = "KOGL-4"
    elif change == "wrong_scope":
        source["license"]["scope"] = "THIRD_PARTY_IMAGE"
    else:
        source.pop("attributionText")
    path = tmp_path / "invalid_registry.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    module._index.cache_clear()
    try:
        monkeypatch.setattr(module, "PATH", path)
        with pytest.raises(ValueError, match="licence"):
            module._index()
    finally:
        module._index.cache_clear()


def test_excluded_brand_source_is_not_silently_restored():
    data, *_ = _index()
    assert not any("넷플릭스" in row["keywords"] or "asec.ahnlab.com" in row["source"]["url"] for row in data["records"])
