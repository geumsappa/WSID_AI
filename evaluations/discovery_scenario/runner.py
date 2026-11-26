"""Run with python -m evaluations.discovery_scenario.runner (no network by default)."""
import argparse
import csv
import hashlib
import json
import math
import statistics
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

CASES = Path(__file__).with_name("cases.json")


def score_facts(facts, ready, expected, questions):
    """Explicit assertions, not an LLM grading its own answers."""
    extracted = facts.get("extractedFacts", [])
    text = json.dumps(extracted, ensure_ascii=False)
    checks = {}
    for word in expected.get("contains", []):
        checks[f"fact_contains:{word}"] = word in text
    for word in expected.get("excludes", []):
        checks[f"fact_excludes:{word}"] = word not in text
    if "category" in expected:
        checks["fact_category"] = any(f.get("category") == expected["category"] for f in extracted)
    if "code" in expected:
        checks["fact_type"] = facts.get("fraudTypeCode") == expected["code"]
    if "acceptableCodes" in expected:
        checks["fact_type"] = facts.get("fraudTypeCode") in expected["acceptableCodes"]
    graph = facts.get("contextGraph") or {}
    events = graph.get("events", [])
    evidence = {item["id"]: item["quote"] for item in graph.get("evidence", [])}
    for index, expectation in enumerate(expected.get("events", [])):
        matching = [event for event in events if event.get("certainty") == "CONFIRMED"
                    and any(word in event.get("action", "") for word in expectation["actionAny"])
                    and any(expectation["evidenceContains"] in evidence.get(key, "") for key in event.get("evidenceIds", []))]
        checks[f"event_actor:{index}"] = any(event.get("actorId") is not None and
            ((event["actorId"] == graph.get("userActorId")) is expectation["actorIsUser"]) for event in matching)
    if "ready" in expected:
        checks["readiness"] = ready is expected["ready"]
    if expected.get("no_invented_request"):
        checks["no_invented_request"] = all(not f.get("requestedAction") and not f.get("riskFactors") for f in extracted)
    details = ["".join(f.get("detail", "").split()).rstrip(".!?") for f in extracted]
    checks["no_exact_duplicate_facts"] = len(details) == len(set(details))
    if not ready:
        checks["followup_present"] = bool(questions and questions[-1].strip())
        actual_questions = [item["question"] for item in facts["questionHistory"] if item.get("question")] if isinstance(facts.get("questionHistory"), list) else questions
        checks["no_exact_repeated_question"] = len(actual_questions) == len(set(actual_questions))
    return checks


def score_scenario(scenario, expected):
    from app.schemas.sim import Scenario
    checks = {}
    try:
        Scenario.model_validate(scenario)
        checks["scenario_schema"] = True
    except ValueError:
        checks["scenario_schema"] = False
    if "code" in expected:
        checks["scenario_type"] = scenario.get("fraudTypeCode") == expected["code"]
    if "acceptableCodes" in expected:
        checks["scenario_type"] = scenario.get("fraudTypeCode") in expected["acceptableCodes"]
    if "channel" in expected:
        checks["scenario_channel"] = scenario.get("channel") == expected["channel"]
    # Exclude sourceFacts/allowedOrganizations: copying input metadata is not personalization.
    visible = json.dumps({k: scenario.get(k) for k in ("title", "persona", "background", "openingMessage")}, ensure_ascii=False)
    for word in expected.get("scenario_contains", []):
        checks[f"scenario_context:{word}"] = word in visible
    checks["scenario_mode"] = bool(scenario) and scenario.get("generationMode", "PERSONALIZED") == expected.get("mode", "PERSONALIZED")
    if "persona_name" in expected:
        checks["persona_name"] = (scenario.get("persona") or {}).get("name") == expected["persona_name"]
        checks["opening_name"] = expected["persona_name"] in (scenario.get("openingMessage") or "")
    if "min_scenes" in expected:
        checks["scene_count"] = len(scenario.get("keyScenes") or []) >= expected["min_scenes"]
    if expected.get("mode") == "BASIC":
        checks["basic_facts_separated"] = scenario.get("sourceFacts") == [] and bool(scenario.get("fictionalContext"))
        evidence = scenario.get("generationEvidence") or {}
        used = evidence.get("usedForGeneration") or []
        retrieved = {row.get("caseId") for row in evidence.get("retrievedBeforeGeneration", [])}
        checks["basic_source_preserved"] = (bool(used) and bool(evidence.get("sourceAttributions"))
            and all(row.get("caseId") in retrieved for row in used)
            and scenario.get("basicCaseId") in {row.get("caseId") for row in used})
    return checks


def score_turns(turns, expectations):
    checks = {}
    for index, expected in enumerate(expectations):
        present = index < len(turns)
        result = turns[index]["result"] if present else {}
        facts = result.get("facts", {})
        if "ready" in expected:
            checks[f"turn_readiness:{index + 1}"] = present and result.get("canGenerateScenario") is expected["ready"]
        if "basic_offered" in expected:
            checks[f"basic_offer:{index + 1}"] = present and bool(facts.get("basicScenarioOffered")) is expected["basic_offered"]
        if "next_topic" in expected:
            history = facts.get("questionHistory") or []
            current = history[-1] if history and history[-1].get("afterTurn") == facts.get("intakeTurn") else {}
            checks[f"question_topic:{index + 1}"] = present and current.get("topic") == expected["next_topic"]
        if "mode" in expected:
            checks[f"intake_mode:{index + 1}"] = present and facts.get("scenarioMode", "PERSONALIZED") == expected["mode"]
        if "name" in expected:
            name = facts.get("knownTopics", {}).get("PERSON_NAME", {})
            checks[f"confirmed_name:{index + 1}"] = present and name.get("status") == "KNOWN" and name.get("name") == expected["name"]
    return checks



def latency_stats(seconds):
    """Seconds; P95 uses the nearest rank (ceil(0.95 * n)), no interpolation."""
    values = sorted(seconds)
    if not values:
        return {"count": 0, "mean_seconds": None, "median_seconds": None, "p95_seconds": None}
    return {"count": len(values), "mean_seconds": round(statistics.mean(values), 6),
            "median_seconds": round(statistics.median(values), 6),
            "p95_seconds": round(values[math.ceil(0.95 * len(values)) - 1], 6)}


def measure_call(calls, stage, operation):
    """Measure the whole AI service including validation, retries and fallback."""
    call = {"stage": stage, "fallback": False, "seconds": 0, "status": "failed", "error": None}
    calls.append(call)
    begin = time.perf_counter()
    try:
        result = operation()
        call["status"] = "success"
        return result
    except Exception as error:
        call["error"] = type(error).__name__
        raise
    finally:
        call["seconds"] = round(time.perf_counter() - begin, 6)


def summarize_stages(calls):
    stages = {}
    for stage in ("discovery", "scenario"):
        selected = [call for call in calls if call.get("stage") == stage]
        failed = [call for call in selected if call["status"] == "failed"]
        fallback = [call for call in selected if call["status"] == "success" and call["fallback"]]
        generated = [call for call in selected if call["status"] == "success" and not call["fallback"]]
        stages[stage] = {
            "total_calls": len(selected), "failed_calls": len(failed), "fallback_calls": len(fallback),
            "failure_rate": round(len(failed) / len(selected), 4) if selected else None,
            "fallback_rate": round(len(fallback) / len(selected), 4) if selected else None,
            "latency": {name: latency_stats([call["seconds"] for call in group])
                        for name, group in (("all", selected), ("generated", generated), ("fallback", fallback), ("failed", failed))},
        }
    return stages


def functional_pass(row):
    return row.get("error") is None and bool(row["checks"]) and all(row["checks"].values())


def summarize_modes(rows):
    result = {}
    for mode in ("PERSONALIZED", "BASIC"):
        selected = [row for row in rows if row.get("expected_mode") == mode]
        generated = [row for row in selected if row.get("error") is None and bool(row.get("scenario"))
                     and row["checks"].get("scenario_schema", True)
                     and row["scenario"].get("generationMode", "PERSONALIZED") == mode]
        passed = sum(functional_pass(row) for row in selected)
        attempted = sum(any(call.get("stage") == "scenario" for call in row["calls"]) for row in selected)
        calls = [call for row in selected for call in row["calls"] if call.get("stage") == "scenario"]
        result[mode] = {"requested": len(selected), "attempted": attempted, "blocked_before_generation": len(selected) - attempted,
            "generated": len(generated), "functional_passes": passed,
            "attempt_success_rate": round(len(generated) / attempted, 4) if attempted else None,
            "generation_success_rate": round(len(generated) / len(selected), 4) if selected else None,
            "functional_pass_rate": round(passed / len(selected), 4) if selected else None,
            "generation_latency": latency_stats([call["seconds"] for call in calls])}
    return result


def summarize(rows):
    metrics = defaultdict(lambda: {"passed": 0, "total": 0})
    for row in rows:
        for name, passed in row["checks"].items():
            metric = metrics[name.split(":", 1)[0]]
            metric["total"] += 1
            metric["passed"] += int(passed)
    for metric in metrics.values():
        metric["rate"] = round(metric["passed"] / metric["total"], 4)
    calls = [call for row in rows for call in row["calls"]]
    llm_calls = [call for row in rows for call in row.get("model_calls", [])]
    task_names = sorted({call["stage"] for call in llm_calls})
    return {
        "runs": len(rows), "completed": sum(r["error"] is None for r in rows),
        "functional_passes": sum(functional_pass(r) for r in rows),
        "without_fallback_passes": sum(functional_pass(r) and bool(r["calls"]) and not any(c["fallback"] for c in r["calls"]) for r in rows),
        "fallback_calls": sum(c["fallback"] for c in calls), "service_calls": len(calls),
        "fallback_rate": round(sum(c["fallback"] for c in calls) / len(calls), 4) if calls else None,
        "mean_run_seconds": round(sum(r["seconds"] for r in rows) / len(rows), 3) if rows else None,
        "metrics": dict(metrics), "stages": summarize_stages(calls), "generation_modes": summarize_modes(rows),
        "run_latency": latency_stats([row["seconds"] for row in rows]),
        "model_stages": {task: {"calls": sum(call["stage"] == task for call in llm_calls),
                         "failures": sum(call["stage"] == task and call["status"] == "failed" for call in llm_calls),
                         "latency": latency_stats([call["seconds"] for call in llm_calls if call["stage"] == task])} for task in task_names},
    }


def run_case(case, repeat):
    from app.services import discovery, training_api
    from app.services import scenario, scenario_planner, fast_discovery, basic_training
    from app.schemas.discovery import DiscoveryRequest
    from app.schemas.training_api import ScenarioRequest

    row = {"case_id": case["id"], "repeat": repeat, "checks": {}, "calls": [], "model_calls": [], "reviews": [], "verified_context": None, "intake_decisions": [], "candidate_comparisons": [], "turns": [], "scenario": None, "error": None,
           "expected_mode": case["expected"].get("mode", "PERSONALIZED") if case.get("generate_scenario") else None}
    facts, conversation, questions, ready = {}, [], [], False
    started = time.perf_counter()

    def observe(original):
        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            row["calls"][-1]["fallback"] = bool(result.get("fallback_used"))
            if result.get("contextGraph"):
                row["verified_context"] = result["contextGraph"]
            if result.get("review"):
                row["reviews"].append(result["review"])
            return result
        return wrapped

    def observe_model(original):
        def wrapped(system, content, **kwargs):
            task = system.splitlines()[0].removeprefix("TASK: ")
            result = measure_call(row["model_calls"], task, lambda: original(system, content, **kwargs))
            if task == "semantic_review":
                row.setdefault("semantic_review_attempts", []).append(scenario_planner.safe_tree(result))
            if task == "scenario_generate":
                row.setdefault("scenario_draft_attempts", []).append(scenario_planner.safe_tree(result))
                payload = json.loads(content)
                row.setdefault("scenario_generation_attempts", []).append({"mode": payload.get("mode", "GENERATE"),
                    "issues": payload.get("retryIssues", []), "response": scenario_planner.safe_tree(result)})
            if task == "scenario_classify":
                payload = json.loads(content)
                row.setdefault("classification_attempts", []).append({
                    "retrieval": payload.get("referenceRetrieval", {}),
                    "retrievedCaseIds": [item["id"] for item in payload.get("referenceTechniques", [])],
                    "response": scenario_planner.safe_tree(result)})
            if task == "discovery_question_review":
                row.setdefault("question_need_reviews", []).append(scenario_planner.safe_tree(result))
            if task == "discovery_brief":
                row.setdefault("discovery_decisions", []).append(scenario_planner.safe_tree(result))
            if task in ("intake_plan", "candidate_plan", "generation_plan"):
                row["intake_decisions"].append(scenario_planner.safe_tree(result))
            if task == "candidate_compare":
                row["candidate_comparisons"].append(scenario_planner.safe_tree(result))
            return result
        return wrapped

    try:
        with patch.object(discovery, "run_context_turn", side_effect=observe(discovery.run_context_turn)), patch.object(training_api, "run_scenario", side_effect=observe(training_api.run_scenario)), patch.object(basic_training, "generate_basic", side_effect=observe(basic_training.generate_basic)), patch.object(scenario_planner, "generate_json", side_effect=observe_model(scenario_planner.generate_json)), patch.object(scenario, "generate_json", side_effect=observe_model(scenario.generate_json)), patch.object(fast_discovery, "generate_json", side_effect=observe_model(fast_discovery.generate_json)):
            for user_input in case["inputs"]:
                result = measure_call(row["calls"], "discovery", lambda: discovery.discover(
                    DiscoveryRequest(conversation=conversation, knownFacts=facts, currentInput=user_input)))
                facts, ready = result.facts, result.canGenerateScenario
                questions.append(result.nextQuestion)
                row["turns"].append({"input": user_input, "result": result.model_dump()})
                conversation.append({"sender": "USER", "content": user_input, "phase": "DISCOVERY"})
                if result.nextQuestion:
                    conversation.append({"sender": "AI", "content": result.nextQuestion, "phase": "DISCOVERY"})
            conversational_expected = case["expected"]
            if facts.get("pipelineVersion") == 2:
                # Facts/actor gold labels are evaluated only after deferred verification, not from copied input.
                conversational_expected = {key: value for key, value in case["expected"].items()
                                           if key in {"ready"}}
            row["checks"].update(score_facts(facts, ready, conversational_expected, questions))
            row["checks"].update(score_turns(row["turns"], case.get("turn_expectations", [])))
            if case.get("generate_scenario"):
                if ready:
                    generated = measure_call(row["calls"], "scenario", lambda: training_api.scenario(
                        ScenarioRequest(facts=facts, fraudTypeCode=facts.get("fraudTypeCode") or case["expected"].get("code", "SMISHING"))))
                    row["scenario"] = generated.scenario
                    row["checks"].update(score_scenario(generated.scenario, case["expected"]))
                    if row["verified_context"] is not None and generated.scenario.get("generationMode", "PERSONALIZED") != "BASIC":
                        from app.schemas.context_graph import ContextGraph
                        from app.services.context_graph import project_facts
                        verified = {"contextGraph": row["verified_context"],
                                    "extractedFacts": project_facts(ContextGraph.model_validate(row["verified_context"])),
                                    "fraudTypeCode": generated.scenario.get("fraudTypeCode")}
                        row["checks"].update(score_facts(verified, ready, case["expected"], questions))
                else:
                    row["checks"].update({name: False for name in score_scenario({}, case["expected"])})
                    row["checks"]["generation_ready"] = False
            row["checks"]["execution"] = True
    except Exception as error:
        # Provider messages may contain secrets. Save only the exception class.
        row["error"] = type(error).__name__
        row["checks"]["execution"] = False
        # Errors cannot silently disappear from applicable metric denominators.
        pending = score_facts(facts, ready, case["expected"], questions)
        row["checks"].update(score_turns(row["turns"], case.get("turn_expectations", [])))
        if case.get("generate_scenario"):
            pending.update(score_scenario({}, case["expected"]))
        for name in pending:
            row["checks"].setdefault(name, False)
    row["seconds"] = round(time.perf_counter() - started, 3)
    return row



def timing_report(summary):
    def seconds(value):
        return "미측정" if value is None else f"{value:.6f}"

    def rate(value):
        return "미측정" if value is None else f"{value:.1%}"

    lines = ["", "## AI 서비스 처리 시간", "",
             "단위: 초. 서비스 호출부터 최종 결과 반환/예외까지 측정하며 재시도·검증·기본 응답 처리를 포함합니다.",
             "외부 LLM API만의 시간이나 FE·Spring·네트워크 전체 대기 시간이 아닙니다.", "",
             "| 기능 | 결과 구분 | 호출 수 | 평균(초) | 중앙값(초) | P95(초) |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for stage, title in (("discovery", "상황 파악"), ("scenario", "시나리오 생성")):
        data = summary["stages"][stage]
        for group, label in (("all", "전체"), ("generated", "기본 대체 없이 반환"), ("fallback", "기본 응답 대체 후 반환"), ("failed", "예외로 실패")):
            stats = data["latency"][group]
            lines.append(f"| {title} | {label} | {stats['count']} | {seconds(stats['mean_seconds'])} | {seconds(stats['median_seconds'])} | {seconds(stats['p95_seconds'])} |")
    lines.extend(["", "| 기능 | 실패 수 / 전체 호출 수 | 실패율 | 대체 응답 수 / 전체 호출 수 | 대체율 |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for stage, title in (("discovery", "상황 파악"), ("scenario", "시나리오 생성")):
        data = summary["stages"][stage]
        lines.append(f"| {title} | {data['failed_calls']} / {data['total_calls']} | {rate(data['failure_rate'])} | {data['fallback_calls']} / {data['total_calls']} | {rate(data['fallback_rate'])} |")
    run = summary["run_latency"]
    lines.extend(["", f"사례 전체 처리 시간({run['count']}회): 평균 {seconds(run['mean_seconds'])}초 / 중앙값 {seconds(run['median_seconds'])}초 / P95 {seconds(run['p95_seconds'])}초.",
                  "한 사례에는 여러 입력 턴과 시나리오 생성이 포함될 수 있습니다.",
                  "P95는 시간을 정렬한 뒤 ceil(0.95 × 표본 수)번째 값을 사용합니다. 표본이 적으면 참고값입니다.",
                  "호출이 없는 항목은 0초 대신 미측정으로 표시합니다. 기본 대체 없이 반환됐어도 기능 기준에 실패할 수 있습니다.",
                  "실패율은 서비스가 예외를 반환한 비율입니다. 내부에서 잡힌 타임아웃은 대체 응답에 포함될 수 있어 외부 API 타임아웃 비율과 다릅니다."])
    if summary.get("model_stages"):
        lines.extend(["", "## 모델 호출 단계별 시간", "", "| 단계 | 호출 수 | 호출 오류 | 평균(초) | 중앙값(초) | P95(초) |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
        for name, data in summary["model_stages"].items():
            stats = data["latency"]
            lines.append(f"| {name} | {data['calls']} | {data['failures']} | {seconds(stats['mean_seconds'])} | {seconds(stats['median_seconds'])} | {seconds(stats['p95_seconds'])} |")
    return lines


def write_report(folder, report):
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (folder / "manual-review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case_id", "repeat", "expected_mode", "generated_mode", "naturalness_0_2", "plausibility_0_2", "fact_fiction_separation_0_2", "notes", "scenario", "questions"])
        for row in report["results"]:
            writer.writerow([row["case_id"], row["repeat"], row.get("expected_mode", ""),
                             (row.get("scenario") or {}).get("generationMode", "PERSONALIZED" if row.get("scenario") else ""),
                             "", "", "", "", json.dumps(row["scenario"], ensure_ascii=False), json.dumps([t["result"]["nextQuestion"] for t in row["turns"]], ensure_ascii=False)])
    summary = report["summary"]
    lines = ["# 상황 파악·시나리오 생성 평가", "", f"실행 모드: {report['mode']} / 반복 결과: {summary['runs']}건", "",
             "mock 결과는 평가 도구 점검용이며 실제 AI 품질 점수가 아닙니다.", "",
             f"기능 기준 통과: {summary['functional_passes']} / {summary['runs']}",
             f"대체 응답 없이 기준 통과: {summary['without_fallback_passes']} / {summary['runs']}",
             f"대체 응답 사용: {summary['fallback_calls']} / {summary['service_calls']} 서비스 호출", "",
             "| 자동 평가 항목 | 통과 | 평가 수 | 비율 |", "| --- | ---: | ---: | ---: |"]
    for name, metric in summary["metrics"].items():
        lines.append(f"| {name} | {metric['passed']} | {metric['total']} | {metric['rate']:.1%} |")
    lines.extend(["", "## 맞춤·기본 훈련 분리", "",
        "기본 훈련 성공은 맞춤 생성 성공으로 합산하지 않습니다. 생성 성공과 전체 기능 기준 통과도 구분합니다.", "",
        "| 종류 | 생성 기대 사례 | 실제 생성 호출 | 호출 전 미완료 | 생성 완료 | 전체 기능 통과 |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for mode, data in summary.get("generation_modes", {}).items():
        lines.append(f"| {mode} | {data['requested']} | {data['attempted']} | {data['blocked_before_generation']} | {data['generated']} | {data['functional_passes']} |")
    lines.extend(timing_report(summary))
    lines.extend(["", "미실행 항목은 측정하지 않음. 예외는 해당 평가 항목의 실패로 집계합니다.",
                  "자연스러움·개연성·사실/가상 구분은 manual-review.csv에서 수동 채점하세요. 공란은 미평가입니다."])
    (folder / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use configured external LLM; incurs API usage")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("evaluation-results"))
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    cases_bytes = CASES.read_bytes()
    cases = json.loads(cases_bytes)
    if args.case_ids:
        unknown = set(args.case_ids) - {case["id"] for case in cases}
        if unknown:
            parser.error("Unknown case: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case["id"] in args.case_ids]
    if args.list:
        for case in cases:
            print(case["id"], case["description"])
        return 0
    if not args.live:
        os.environ["LLM_PROVIDER"] = "mock"
    from app.config import settings
    if args.live and (settings.llm_provider not in ("openai", "anthropic") or not settings.llm_api_key or not settings.llm_model):
        parser.error("--live requires LLM_PROVIDER=openai/anthropic, LLM_API_KEY and LLM_MODEL")
    artifact_sha256 = {name: hashlib.sha256((Path(__file__).resolve().parents[2] / name).read_bytes()).hexdigest()
                  for name in ("app/services/fast_discovery.py", "app/services/basic_training.py", "app/services/scenario.py",
                               "app/services/scenario_planner.py", "app/services/scenario_classification.py", "app/services/prompt_templates.py", "app/services/scenario_retrieval.py", "app/services/scenario_revision.py", "app/guard/scenario_guard.py",
                               "app/schemas/context_graph.py", "app/schemas/scenario_revision.py", "app/schemas/fast_discovery.py", "app/schemas/sim.py",
                               "app/data/scenario_patterns.json", "evaluations/discovery_scenario/runner.py")}
    prompt_sha256 = {name: hashlib.sha256((Path(__file__).resolve().parents[2] / "app" / "prompts" / name).read_bytes()).hexdigest()
                                for name in ("discovery_brief.md", "discovery_question_review.md", "candidate_plan.md", "candidate_compare.md", "intake_plan.md", "generation_plan.md", "single_generation_plan.md", "intake_sufficiency.md", "context_facts.md", "semantic_review.md", "scenario.md", "scenario_repair.md", "scenario_quality_criteria.md", "scenario_classify.md")}
    rows = []
    for case in cases:
        for repeat in range(1, args.repeats + 1):
            row = run_case(case, repeat)
            rows.append(row)
            print(f"{case['id']} #{repeat}: {'PASS' if functional_pass(row) else 'FAIL'} ({row['seconds']}s)")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {"created_at": stamp, "mode": "live" if args.live else "mock", "provider": settings.llm_provider,
              "model": settings.llm_model if args.live else "mock", "results": rows, "summary": summarize(rows),
              "repeats": args.repeats, "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
              "protocol": "single-plan-joint-review-v3",
              "artifact_sha256": artifact_sha256,
              "pipeline_reasoning_effort": settings.pipeline_reasoning_effort or "provider-default",
              "discovery_reasoning_effort": settings.discovery_reasoning_effort or "provider-default",
              "prompt_sha256": prompt_sha256}
    folder = args.output / stamp
    write_report(folder, report)
    print(f"Report: {folder.resolve()}")
    # Mock scores describe fixtures, not real model quality. Live functional failures are nonzero.
    return int(args.live and report["summary"]["functional_passes"] != len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
