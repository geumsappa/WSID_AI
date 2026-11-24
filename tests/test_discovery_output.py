"""Constrained ASK output has no nullable question branch at the provider boundary."""
import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.schemas.discovery_output import DiscoveryOutput, output_schema
from app.llm import discovery_client, llm_client


def wire(question="어느 곳에 다녀오셨나요?"):
    return {"updates": [], "personalizationTarget": "OTHER", "requiredTopics": ["SETTING"],
            "focusChanged": False, "questionAssessments": [],
            "action": {"type": "ASK", "nextTopic": "SETTING", "nextQuestion": question}}


@pytest.mark.parametrize("question", [None, "", "   "])
def test_ask_cannot_validate_a_null_or_blank_question(question):
    with pytest.raises(ValidationError):
        DiscoveryOutput.model_validate(wire(question))


def test_strict_schema_has_no_nullable_ask_question_and_no_early_basic_offer():
    schema = output_schema(allow_basic=False)
    question = schema["$defs"]["Ask"]["properties"]["nextQuestion"]
    assert question["type"] == "string" and question["minLength"] == 1 and question["pattern"] == r"\S"
    assert "anyOf" not in question
    assert "nextQuestion" in schema["$defs"]["Ask"]["required"]
    assert "OfferBasic" not in schema["$defs"]
    assert all(item.get("$ref") != "#/$defs/OfferBasic" for item in schema["properties"]["action"]["anyOf"])
    assert "OfferBasic" in output_schema(allow_basic=True)["$defs"]
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(schema)


def test_ready_is_a_separate_variant_without_question_fields():
    raw = wire()
    raw["action"] = {"type": "VERIFY_READY", "readinessBasis": {
        "topics": ["INTERACTION"], "evidence": [{"turn": 1, "quote": "배송을 문의했어요"}],
        "contactReason": "배송 문의에 관한 후속 안내"}}
    value = DiscoveryOutput.model_validate(raw)
    assert "nextQuestion" not in value.action.model_dump()
    assert value.as_decision()["contextSufficient"] is True
    assert value.as_decision()["nextTopic"] is None


def test_openai_transport_enforces_schema_instead_of_json_syntax_only(monkeypatch):
    captured = []
    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(wire())))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test-key", llm_model="test-model",
        llm_base_url="", llm_timeout_seconds=10)
    monkeypatch.setattr(llm_client, "settings", settings)
    monkeypatch.setattr(discovery_client, "settings", settings)
    monkeypatch.setattr(llm_client, "_reusable_openai_client", lambda *args: client)
    result = discovery_client.generate_json("TASK: discovery_brief", '{"basicOfferAllowed":false}',
        reuse_connection=True, attempts=1)
    assert result["nextQuestion"] == "어느 곳에 다녀오셨나요?"
    mode = captured[0]["response_format"]
    assert mode["type"] == "json_schema" and mode["json_schema"]["strict"] is True
    assert mode["json_schema"]["schema"] == output_schema(allow_basic=False)


def test_anthropic_schema_keeps_non_null_question_type(monkeypatch):
    captured = []
    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(wire()))],
                               stop_reason="end_turn", usage=None)
    settings = SimpleNamespace(llm_provider="anthropic", llm_api_key="test-key", llm_model="test-model",
        llm_temperature=0, llm_timeout_seconds=10)
    monkeypatch.setattr(llm_client, "settings", settings)
    monkeypatch.setattr(discovery_client, "settings", settings)
    monkeypatch.setattr(llm_client, "_reusable_anthropic_client", lambda *args: SimpleNamespace(messages=SimpleNamespace(create=create)))
    result = discovery_client.generate_json("TASK: discovery_brief", '{"basicOfferAllowed":false}', attempts=1)
    assert result["nextQuestion"]
    schema = captured[0]["extra_body"]["output_config"]["format"]["schema"]
    assert schema["$defs"]["Ask"]["properties"]["nextQuestion"]["type"] == "string"


def test_status_dependent_fields_are_encoded_in_the_provider_schema():
    schema = output_schema(allow_basic=False)
    missing = schema["$defs"]["MissingQuestionNeed"]
    assert missing["properties"]["missingDetail"]["type"] == "string"
    assert missing["properties"]["scenarioImpact"]["type"] == "string"
    assert schema["$defs"]["AnsweredQuestionNeed"]["properties"]["evidence"]["minItems"] == 1
    assert "name" not in schema["$defs"]["ContextUpdate"]["properties"]
    assert "name" in schema["$defs"]["KnownPersonName"]["required"]
    assert "name" not in schema["$defs"]["UnresolvedPersonName"]["properties"]


def test_valid_typed_metadata_becomes_the_existing_internal_contract():
    from app.schemas.fast_discovery import ConversationDecision
    raw = wire()
    raw["updates"] = [{"topic": "PEOPLE", "status": "KNOWN", "turn": 1, "quote": "멘토님이 사주셨어"}]
    raw["questionAssessments"] = [{"topic": "SETTING", "status": "MISSING", "necessity": "REQUIRED",
        "missingDetail": "방문 장소", "scenarioImpact": "장소와 관련한 연락 설정", "reason": "아직 말하지 않음"}]
    decision = ConversationDecision.model_validate(DiscoveryOutput.model_validate(raw).as_decision())
    assert decision.updates[0].name is None
    assert decision.questionAssessments[0].evidence == []


def test_decoder_validation_failure_is_corrected_inside_discovery(monkeypatch, caplog):
    from app.services import fast_discovery
    from app.schemas.discovery import DiscoveryRequest
    from app.services.discovery import discover
    malformed = wire()
    malformed["action"]["nextQuestion"] = None
    try:
        DiscoveryOutput.model_validate(malformed)
    except ValidationError as error:
        failure = error
    calls = []
    def generate(system, content, **kwargs):
        payload = json.loads(content)
        calls.append(payload)
        if len(calls) == 1:
            raise failure  # Simulates provider-adapter decoding, before raw was returned.
        assert payload["schemaErrors"]
        return {"updates": [{"topic": "ACTIVITY", "turn": 1, "quote": "빵 먹었어요"}],
            "personalizationTarget": "OTHER", "requiredTopics": ["SETTING"],
            "contextSufficient": False, "readinessBasis": None,
            "nextTopic": "SETTING", "nextQuestion": "어느 곳에서 드셨나요?",
            "questionAssessments": [{"topic": "SETTING", "status": "MISSING", "reason": "장소 미확인",
                "missingDetail": "장소", "scenarioImpact": "접근 장소 설정", "evidence": []}]}
    monkeypatch.setattr(fast_discovery, "generate_json", generate)
    result = discover(DiscoveryRequest(conversation=[], knownFacts={}, currentInput="빵 먹었어요"))
    assert result.nextQuestion == "어느 곳에서 드셨나요?"
    assert not result.canGenerateScenario
    assert len(calls) == 2
    assert any("validation rejected: fields=" in record.message for record in caplog.records)
