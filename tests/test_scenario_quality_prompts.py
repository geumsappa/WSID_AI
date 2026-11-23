"""Generation and independent review must see the same acceptance criteria."""
from copy import deepcopy

from app.services import scenario, scenario_planner
from app.services.prompt_templates import PROMPTS, load_prompt


def test_generation_repair_and_review_receive_shared_criteria(monkeypatch, grounded_model, graph_data, review_data, generation_data):
    systems = []
    generate = scenario.generate_json
    def capture(system, content, **kwargs):
        systems.append(system)
        return generate(system, content, **kwargs)
    monkeypatch.setattr(scenario, "generate_json", capture)
    monkeypatch.setattr(scenario_planner, "generate_json", capture)
    reviews = []
    def review(payload):
        result = deepcopy(review_data)
        reviews.append(payload)
        if len(reviews) == 1:
            result.update(approved=False, issues=[{"code": "OTHER", "detail": "첫 대사 수정"}])
        return result
    grounded_model[1].update(
        semantic_review=review,
        scenario_generate=lambda payload: (
            {"scenario": {"openingMessage": "안녕. 이용요금 정산에 관해 잠깐 얘기할까?"}}
            if payload["mode"] == "REPAIR" else deepcopy(generation_data)),
    )
    scenario.run_scenario("test", [{"detail": graph_data["evidence"][0]["quote"]}])
    criteria = (PROMPTS / "scenario_quality_criteria.md").read_text(encoding="utf-8")
    relevant = [system for system in systems if system.splitlines()[0] in {
        "TASK: scenario_generate", "TASK: semantic_review", "TASK: candidate_plan", "TASK: candidate_compare"}]
    assert len([system for system in relevant if system.startswith("TASK: scenario_generate")]) == 2
    assert len(reviews) == 2
    assert all(criteria in system for system in relevant)
    assert all("{{SCENARIO_QUALITY_CRITERIA}}" not in system for system in relevant)
    # Shared facts still expand for candidate planning.
    assert all("{{CONTEXT_FACTS}}" not in system for system in relevant)


def test_legacy_generation_plan_uses_same_criteria():
    prompt = load_prompt(PROMPTS / "generation_plan.md")
    assert (PROMPTS / "scenario_quality_criteria.md").read_text(encoding="utf-8") in prompt
    assert "{{CONTEXT_FACTS}}" not in prompt
