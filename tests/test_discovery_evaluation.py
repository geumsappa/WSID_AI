"""Evaluation counter regression tests; never call an external model."""
import json

from evaluations.discovery_scenario.runner import CASES, score_facts, score_scenario, score_turns, summarize, run_case, write_report


def test_premature_ready_is_detected_even_if_final_turn_is_correct():
    checks = score_turns([{"result": {"canGenerateScenario": True}}, {"result": {"canGenerateScenario": False}}],
                         [{"ready": False}, {"ready": False}])
    assert not checks["turn_readiness:1"]
    assert checks["turn_readiness:2"]


def test_correction_must_remove_old_name_even_if_new_name_is_present():
    checks = score_facts({"extractedFacts": [{"detail": "기업은행이 아니라 신한은행"}]}, True,
                         {"contains": ["신한은행"], "excludes": ["기업은행"]}, [])
    assert checks["fact_contains:신한은행"]
    assert not checks["fact_excludes:기업은행"]


def test_metadata_copy_alone_does_not_pass_personalization():
    checks = score_scenario({"sourceFacts": [{"detail": "기업은행"}], "allowedOrganizations": ["기업은행"]},
                            {"code": "JOB_SCAM", "scenario_contains": ["기업은행"]})
    assert not checks["scenario_context:기업은행"]
    assert not checks["scenario_schema"]


def test_fallback_is_separate_from_functional_success_and_metrics_are_weighted():
    rows = [
        {"checks": {"fact_contains:a": True, "fact_contains:b": False}, "calls": [{"fallback": True}], "seconds": 1, "error": None},
        {"checks": {"fact_contains:a": True}, "calls": [{"fallback": False}], "seconds": 3, "error": None},
    ]
    result = summarize(rows)
    assert result["functional_passes"] == 1
    assert result["without_fallback_passes"] == 1
    assert result["fallback_rate"] == 0.5
    assert result["metrics"]["fact_contains"] == {"passed": 2, "total": 3, "rate": 0.6667}


def test_execution_error_counts_applicable_metrics_as_failures_without_saving_message(monkeypatch):
    from app.services import discovery
    def broken(*args, **kwargs):
        raise RuntimeError("secret-provider-message")
    monkeypatch.setattr(discovery, "discover", broken)
    row = run_case({"id": "failure", "inputs": ["test"], "expected": {"contains": ["은행"], "ready": False, "code": "JOB_SCAM"}, "generate_scenario": True}, 1)
    assert row["error"] == "RuntimeError"
    assert not any(row["checks"].values())
    assert "scenario_schema" in row["checks"] and "readiness" in row["checks"]
    assert "secret-provider-message" not in json.dumps(row)


def test_catalog_covers_all_training_codes_and_unique_ids():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(cases) == len({case["id"] for case in cases})
    assert {case["expected"]["code"] for case in cases if case.get("generate_scenario") and "code" in case["expected"]} == {
        "JOB_SCAM", "SMISHING", "SECONDHAND_TRADE", "LOAN_FRAUD", "MESSENGER_PHISHING", "INVESTMENT_FRAUD", "INSTITUTION_IMPERSONATION",
    }


def test_structured_actor_metric_does_not_treat_unknown_as_a_correct_peer():
    expected = {"events": [{"actionAny": ["결제"], "evidenceContains": "동료", "actorIsUser": False}]}
    facts = {"contextGraph": {"userActorId": "u", "events": [{"id": "e", "actorId": None, "action": "결제", "certainty": "CONFIRMED", "evidenceIds": ["q"]}],
                              "evidence": [{"id": "q", "quote": "동료가 결제했어요"}]}}
    assert not score_facts(facts, True, expected, [])["event_actor:0"]
    facts["contextGraph"]["events"][0]["actorId"] = "peer"
    assert score_facts(facts, True, expected, [])["event_actor:0"]


def test_review_scores_start_blank_and_report_is_marked_mock(tmp_path):
    import csv
    row = {"case_id": "x", "repeat": 1, "checks": {"execution": True}, "calls": [], "seconds": 0,
           "scenario": None, "turns": [], "error": None}
    folder = tmp_path / "report"
    write_report(folder, {"mode": "mock", "results": [row], "summary": summarize([row])})
    with (folder / "manual-review.csv").open(encoding="utf-8-sig", newline="") as stream:
        review = next(csv.DictReader(stream))
    assert review["naturalness_0_2"] == ""
    assert "mock" in (folder / "summary.md").read_text(encoding="utf-8")


def test_default_cli_overrides_external_provider_and_writes_mock_report(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = {**os.environ, "LLM_PROVIDER": "openai", "LLM_API_KEY": "unused-test-key", "LLM_MODEL": "unused-model", "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run([sys.executable, "-m", "evaluations.discovery_scenario.runner", "--case", "vague-once", "--output", str(tmp_path)],
                               env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(next(tmp_path.glob("*/results.json")).read_text(encoding="utf-8"))
    assert report["mode"] == "mock" and report["provider"] == "mock"
    assert "unused-test-key" not in json.dumps(report)


def test_live_cli_rejects_missing_key_before_creating_report(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = {**os.environ, "LLM_PROVIDER": "openai", "LLM_API_KEY": "", "LLM_MODEL": "unused-model", "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run([sys.executable, "-m", "evaluations.discovery_scenario.runner", "--live", "--case", "vague-once", "--output", str(tmp_path)],
                               env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert completed.returncode == 2
    assert not list(tmp_path.glob("*/results.json"))


def test_latency_nearest_rank_and_empty_samples():
    from evaluations.discovery_scenario.runner import latency_stats
    assert latency_stats(range(1, 21)) == {"count": 20, "mean_seconds": 10.5, "median_seconds": 10.5, "p95_seconds": 19}
    assert latency_stats([2])["p95_seconds"] == 2
    assert latency_stats([]) == {"count": 0, "mean_seconds": None, "median_seconds": None, "p95_seconds": None}


def test_stage_latency_separates_fast_fallbacks_failures_and_generated_results():
    from evaluations.discovery_scenario.runner import summarize_stages
    result = summarize_stages([
        {"stage": "discovery", "status": "success", "fallback": False, "seconds": 4},
        {"stage": "discovery", "status": "success", "fallback": True, "seconds": 0.1},
        {"stage": "discovery", "status": "failed", "fallback": False, "seconds": 10},
    ])
    discovery = result["discovery"]
    assert discovery["failure_rate"] == 0.3333
    assert discovery["fallback_rate"] == 0.3333
    assert discovery["latency"]["generated"]["mean_seconds"] == 4
    assert discovery["latency"]["fallback"]["mean_seconds"] == 0.1
    assert discovery["latency"]["failed"]["mean_seconds"] == 10
    assert result["scenario"]["failure_rate"] is None


def test_exception_is_timed_and_only_exception_type_is_stored(monkeypatch):
    import pytest
    from evaluations.discovery_scenario import runner
    ticks = iter([10, 12.75])
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(ticks))
    def broken():
        raise TimeoutError("private-detail")
    calls = []
    with pytest.raises(TimeoutError):
        runner.measure_call(calls, "scenario", broken)
    assert calls == [{"stage": "scenario", "fallback": False, "seconds": 2.75, "status": "failed", "error": "TimeoutError"}]


def test_outer_service_failure_is_counted_in_stage_stats(monkeypatch):
    from app.services import discovery
    def broken(*args, **kwargs):
        raise RuntimeError("private")
    monkeypatch.setattr(discovery, "discover", broken)
    row = run_case({"id": "failure", "inputs": ["test"], "expected": {"ready": False}}, 1)
    stages = summarize([row])["stages"]
    assert stages["discovery"]["failure_rate"] == 1
    assert stages["discovery"]["latency"]["failed"]["count"] == 1
    assert stages["scenario"]["total_calls"] == 0


def test_timing_markdown_has_separate_groups_and_missing_values():
    from evaluations.discovery_scenario.runner import timing_report
    summary = summarize([])
    markdown = "\n".join(timing_report(summary))
    for label in ("상황 파악", "시나리오 생성", "기본 대체 없이 반환", "기본 응답 대체 후 반환", "예외로 실패", "중앙값", "P95", "미측정"):
        assert label in markdown


def test_name_in_title_alone_does_not_pass_display_and_opening_checks():
    checks = score_scenario({"title": "도토리의 부탁", "persona": {"name": "친구"}, "openingMessage": "안녕"},
                            {"persona_name": "도토리"})
    assert not checks["persona_name"] and not checks["opening_name"]


def test_question_topic_must_belong_to_current_turn():
    result = {"facts": {"intakeTurn": 3, "questionHistory": [{"topic": "PERSON_NAME", "afterTurn": 2}]}, "canGenerateScenario": False}
    assert not score_turns([{"result": result}], [{"next_topic": "PERSON_NAME"}])["question_topic:1"]
    result["facts"]["questionHistory"][0]["afterTurn"] = 3
    assert score_turns([{"result": result}], [{"next_topic": "PERSON_NAME"}])["question_topic:1"]


def test_basic_training_must_keep_fiction_and_source_provenance_separate():
    scenario = {"generationMode": "BASIC", "basicCaseId": "case-a", "sourceFacts": [], "fictionalContext": "가상 설정",
        "generationEvidence": {"retrievedBeforeGeneration": [{"caseId": "case-a"}],
            "usedForGeneration": [{"caseId": "case-a"}], "sourceAttributions": ["공식 출처"]}}
    checks = score_scenario(scenario, {"mode": "BASIC"})
    assert checks["basic_facts_separated"] and checks["basic_source_preserved"]
    scenario["sourceFacts"] = [{"detail": "만든 설정을 실제 사용자 사실로 취급"}]
    scenario["generationEvidence"]["usedForGeneration"] = [{"caseId": "unretrieved"}]
    checks = score_scenario(scenario, {"mode": "BASIC"})
    assert not checks["basic_facts_separated"] and not checks["basic_source_preserved"]


def test_modes_keep_generation_failure_in_denominator_and_never_count_basic_as_personalized():
    def row(mode, scenario, error=None, checks=None):
        return {"expected_mode": mode, "scenario": scenario, "error": error, "checks": checks or {"execution": error is None},
                "calls": [], "seconds": 0}
    rows = [row("BASIC", {"generationMode": "BASIC"}), row("PERSONALIZED", None, "TimeoutError"),
            row("PERSONALIZED", {"generationMode": "BASIC"}, checks={"scenario_mode": False})]
    modes = summarize(rows)["generation_modes"]
    assert modes["BASIC"]["requested"] == 1 and modes["BASIC"]["generation_success_rate"] == 1
    assert modes["PERSONALIZED"]["requested"] == 2 and modes["PERSONALIZED"]["generation_success_rate"] == 0


def test_error_or_empty_checks_cannot_be_reported_as_a_functional_pass():
    rows = [{"checks": {"readiness": True}, "error": "RuntimeError", "calls": [], "seconds": 1},
            {"checks": {}, "error": None, "calls": [], "seconds": 0}]
    assert summarize(rows)["functional_passes"] == 0


def test_empty_or_schema_invalid_scenario_is_not_a_generation_success():
    rows = [{"expected_mode": "PERSONALIZED", "scenario": story, "error": None,
             "checks": {"scenario_schema": False}, "calls": [], "seconds": 0}
            for story in ({}, {"generationMode": "PERSONALIZED"})]
    assert summarize(rows)["generation_modes"]["PERSONALIZED"]["generated"] == 0
    assert not score_scenario({}, {})["scenario_mode"]


def test_repeated_pause_notices_are_not_counted_as_repeated_questions():
    checks = score_facts({"questionHistory": []}, False, {}, ["기본 훈련을 선택할 수 있어요", "기본 훈련을 선택할 수 있어요"])
    assert checks["no_exact_repeated_question"]
    checks = score_facts({"questionHistory": [{"question": "같은 질문?"}, {"question": "같은 질문?"}]}, False, {}, ["안내"])
    assert not checks["no_exact_repeated_question"]
