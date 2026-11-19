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
    assert {case["expected"]["code"] for case in cases if case.get("generate_scenario")} == {
        "JOB_SCAM", "SMISHING", "SECONDHAND_TRADE", "LOAN_FRAUD", "MESSENGER_PHISHING", "INVESTMENT_FRAUD", "INSTITUTION_IMPERSONATION",
    }


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
