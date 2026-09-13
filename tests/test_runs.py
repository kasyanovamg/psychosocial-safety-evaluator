"""Suite aggregation, strict manifests, portable bundles, and offline loading."""

import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from uuid import UUID

from pydantic import ValidationError
import pytest

from psych_eval.evaluations import Evaluation, load_evaluation
from psych_eval.runs import RunArtifact, ScenarioResult, load_run, save_run
from psych_eval.scenarios import EvaluatorScenarioView
from psych_eval.transcripts import Transcript, load_transcript


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "demo/runs/relational-sycophancy-demo-v1"


@pytest.fixture(autouse=True)
def no_execution_or_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("run construction/loading must not execute models or access the network")

    for name in (
        "psych_eval.integrations.fixture_target.FixtureTarget.respond", "psych_eval.integrations.fixture_judge.FixtureJudge.assess",
        "psych_eval.runner.run_scenario", "psych_eval.evaluator.evaluate_transcript",
    ):
        monkeypatch.setattr(name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def demo():
    return load_run(BUNDLE / "run.json")


@pytest.fixture
def source():
    transcript = load_transcript(BUNDLE / "transcripts/RS-001.json")
    evaluation = load_evaluation(BUNDLE / "evaluations/RS-001.json")
    return ScenarioResult(
        scenario=evaluation.judge_input.scenario.model_copy(deep=True), title="Excluded by Friends",
        transcript=transcript, evaluation=evaluation, evaluation_status="assessed",
        transcript_ref="transcripts/RS-001.json", evaluation_ref="evaluations/RS-001.json",
    )


def build(demo, sources, **updates):
    kwargs = dict(
        run_id=demo.run_id, created_at=demo.created_at, construct=demo.construct_name,
        suite_id=demo.suite.suite_id, suite_version=demo.suite.suite_version,
        model_under_test=demo.model_under_test, judge=demo.judge,
        rubric_version=demo.rubric_version, evaluator_version=demo.evaluator_version,
        scenarios=sources,
    )
    kwargs.update(updates)
    return RunArtifact.create(**kwargs)


def sync_result(evaluation):
    fields = ("category", "evaluation_status", "findings", "zero_rationale",
              "cannot_assess_reason", "recovery", "persistence")
    evaluation["raw_judge_response"] = json.dumps({key: evaluation[key] for key in fields})


def variant(source, number, *, severity=None, evaluation_status="assessed", execution_status="completed"):
    """In-memory contract test inputs only; no new production scenario fixtures."""
    transcript = source.transcript.model_dump(mode="json")
    evaluation = source.evaluation.model_dump(mode="json")
    scenario_id = f"TEST-{number:03}"
    transcript.update(scenario_id=scenario_id, run_id=str(UUID(int=number + 1)), execution_status=execution_status)
    if execution_status in ("partial", "failed"):
        transcript["turns"] = transcript["turns"][:3 if execution_status == "partial" else 1]
        transcript["retry_count"] = 1
        transcript["failure"] = dict(failure_stage="target_execution", failure_reason="target_exception",
                                      detail="Test target failure", retry_count=1)
    evaluation.update(scenario_id=scenario_id, transcript_run_id=transcript["run_id"],
                       evaluation_id=str(UUID(int=number + 100)), execution_status=execution_status)
    evaluation["judge_input"]["transcript"] = transcript
    evaluation["judge_input"]["scenario"]["scenario_id"] = scenario_id
    if severity is not None:
        evaluation["overall_severity"] = severity
        evaluation["findings"] = evaluation["findings"][:1] if severity else []
        evaluation["zero_rationale"] = None if severity else "No positive findings."
        if severity:
            evaluation["findings"][0].update(severity=severity, severity_3_basis="both" if severity == 3 else None)
    if evaluation_status == "cannot_assess":
        evaluation.update(evaluation_status="cannot_assess", findings=[], overall_severity=None,
                           zero_rationale=None, cannot_assess_reason="Insufficient context.")
    sync_result(evaluation)
    return ScenarioResult(
        scenario=EvaluatorScenarioView(scenario_id=scenario_id, scenario_version="1.0", construct="relational_sycophancy"),
        transcript=Transcript.model_validate_json(json.dumps(transcript)),
        evaluation=Evaluation.model_validate_json(json.dumps(evaluation)) if evaluation_status in ("assessed", "cannot_assess") else None,
        evaluation_status=evaluation_status,
        transcript_ref=f"transcripts/{scenario_id}.json",
        evaluation_ref=f"evaluations/{scenario_id}.json" if evaluation_status in ("assessed", "cannot_assess") else None,
    )


def test_canonical_demo_build_load_and_exact_roundtrip(demo, source, tmp_path):
    result = build(demo, [source])
    assert result == demo == load_run(BUNDLE / "run.json", verify_references=True)
    assert build(demo, [source]) == result
    path = tmp_path / "nested/run.json"
    save_run(path, result)
    original = path.read_bytes()
    assert original == (BUNDLE / "run.json").read_bytes()
    loaded = load_run(path)  # Overview loading does not need the detail files.
    assert loaded == result
    save_run(path, loaded)
    assert path.read_bytes() == original
    entry = result.scenarios[0]
    assert (entry.scenario_id, entry.scenario_version, entry.title) == ("RS-001", "1.0", "Excluded by Friends")
    assert entry.finding_count == 4
    assert entry.severity == 3
    assert result.model_under_test == source.transcript.target
    assert result.judge == source.evaluation.judge
    assert result.model_under_test.provider == result.judge.provider == "fixture"
    assert result.judge.mode == "fixture"


def test_no_detailed_payload_or_global_score(demo):
    data = demo.model_dump(mode="json")
    forbidden = {"turns", "findings", "evidence", "rationale", "judge_input", "raw_judge_response", "overall_severity", "score"}

    def check(value):
        if isinstance(value, dict):
            assert not (set(value) & forbidden)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(data)
    assert "/private/tmp/" not in json.dumps(data)
    assert "/Users/" not in json.dumps(data)


def test_duplicate_scenario_rejected(demo, source):
    with pytest.raises(ValueError, match="duplicate scenario_id"):
        build(demo, [source, source])


def test_duplicate_artifact_references_rejected(demo, source):
    other = variant(source, 2).model_copy(update={"evaluation_ref": source.evaluation_ref})
    with pytest.raises(ValueError, match="duplicate artifact references"):
        build(demo, [source, other])


@pytest.mark.parametrize("field", ["scenario_id", "scenario_version"])
def test_scenario_identity_mismatch(demo, source, field):
    setattr(source.scenario, field, "different")
    with pytest.raises(ValueError, match="identity/version/construct"):
        build(demo, [source])


def test_evaluation_wrong_transcript(demo, source):
    other = source.model_copy(update={"transcript": source.transcript.model_copy(update={"run_id": UUID(int=777)})})
    with pytest.raises(ValueError, match="exact transcript"):
        build(demo, [other])


@pytest.mark.parametrize("field,value", [
    ("provider", "other"), ("model", "other-model"), ("system_prompt", "Different instructions"),
])
def test_mixed_target_configuration_rejected(demo, source, field, value):
    other = variant(source, 2)
    data = other.model_dump()
    data["transcript"]["target"][field] = value
    data["evaluation"]["judge_input"]["transcript"]["target"][field] = value
    with pytest.raises(ValueError, match="target configuration"):
        build(demo, [source, ScenarioResult.model_validate(data)])


def test_mixed_sampling_rejected(demo, source):
    data = variant(source, 2).model_dump()
    for target in (data["transcript"]["target"], data["evaluation"]["judge_input"]["transcript"]["target"]):
        target["sampling"]["temperature"] = 0.5
    with pytest.raises(ValueError, match="target configuration"):
        build(demo, [source, ScenarioResult.model_validate(data)])


def test_mixed_judge_rejected(demo, source):
    data = variant(source, 2).model_dump()
    data["evaluation"]["judge"]["model"] = "demo-other-judge-v1"
    with pytest.raises(ValueError, match="judge configuration"):
        build(demo, [source, ScenarioResult.model_validate(data)])


@pytest.mark.parametrize("field", ["rubric_version", "evaluator_version"])
def test_version_mismatch_rejected(demo, source, field):
    with pytest.raises(ValueError, match=field):
        build(demo, [source], **{field: "2.0"})
    # Frozen detail schemas also reject bypassed/modified future-version objects.
    other = variant(source, 2)
    other = other.model_copy(update={"evaluation": other.evaluation.model_copy(update={field: "2.0"})})
    with pytest.raises(ValidationError, match=field):
        build(demo, [source, other])


def test_construct_mismatch_rejected_without_expanding_frozen_construct(demo, source):
    with pytest.raises(ValueError, match="construct"):
        build(demo, [source], construct="other_construct")
    source.scenario.construct_name = "other_construct"
    with pytest.raises(ValidationError, match="construct"):
        build(demo, [source])


@pytest.mark.parametrize("severity", [0, 1, 2, 3])
def test_each_severity_counts_correctly(demo, source, severity):
    run = build(demo, [variant(source, 1, severity=severity)])
    assert run.results.severity_distribution == {str(number): int(number == severity) for number in range(4)}
    assert run.results.material_or_higher == int(severity >= 2)
    assert run.results.severe == int(severity == 3)
    assert run.scenarios[0].finding_count == (1 if severity else 0)


def test_twenty_scenario_contract_scales_without_composite_score(demo, source):
    run = build(demo, [variant(source, number, severity=number % 4) for number in range(20)])
    assert run.suite.scenario_count == run.execution_summary.planned == 20
    assert run.execution_summary.completed == run.evaluation_summary.assessed == 20
    assert run.results.severity_distribution == {"0": 5, "1": 5, "2": 5, "3": 5}
    assert run.results.material_or_higher == 10
    assert run.results.severe == 5


def test_failures_cannot_assess_and_unexecuted_stay_out_of_distribution(demo, source):
    sources = [
        source, variant(source, 2, evaluation_status="cannot_assess"),
        variant(source, 3, evaluation_status="failed"),
        variant(source, 4, execution_status="partial", evaluation_status="not_run"),
        variant(source, 5, execution_status="failed", evaluation_status="not_run"),
        ScenarioResult(scenario=EvaluatorScenarioView(scenario_id="TEST-006", scenario_version="1.0",
                                                      construct="relational_sycophancy"), evaluation_status="not_run"),
    ]
    run = build(demo, sources)
    assert run.execution_summary.model_dump() == dict(planned=6, completed=3, partial=1, failed=1, not_run=1)
    assert run.evaluation_summary.model_dump() == dict(assessed=1, cannot_assess=1, not_run=3, failed=1)
    assert run.results.severity_distribution == {"0": 0, "1": 0, "2": 0, "3": 1}
    assert all(entry.severity is None and entry.finding_count is None for entry in run.scenarios[1:])
    assert run.results.material_or_higher == run.results.severe == 1


def test_partial_assessment_keeps_detail_status_but_is_excluded_from_scored_rollups(demo, source):
    run = build(demo, [variant(source, 1, execution_status="partial", severity=3)])
    assert run.evaluation_summary.assessed == 1
    assert run.scenarios[0].severity is None
    assert run.scenarios[0].finding_count == 1
    assert sum(run.results.severity_distribution.values()) == 0
    assert all(count.finding_count == count.scenario_count == 0 for count in run.results.mechanisms.values())


def test_mechanism_finding_and_distinct_scenario_counts(demo, source):
    run = build(demo, [source, variant(source, 2)])
    assert run.results.mechanisms["accepting_framing"].model_dump() == dict(finding_count=4, scenario_count=2)
    assert run.results.mechanisms["epistemic_endorsement"].model_dump() == dict(finding_count=4, scenario_count=2)
    assert run.results.mechanisms["consequential_reinforcement"].model_dump() == dict(finding_count=2, scenario_count=2)
    assert run.results.mechanisms["escalation"].model_dump() == dict(finding_count=0, scenario_count=0)
    data = source.model_dump(mode="json")
    data["evaluation"]["findings"][0]["mechanisms"].append("accepting_framing")
    sync_result(data["evaluation"])
    repeated = ScenarioResult.model_validate_json(json.dumps(data))
    assert build(demo, [repeated]).results.mechanisms["accepting_framing"].finding_count == 2


@pytest.mark.parametrize("ref", [
    "../../something", "../x.json", "x/../y.json", "/private/tmp/x.json", "/Users/person/x.json",
    "C:/temp/x.json", "C:\\temp\\x.json", "\\\\server\\x.json", "https://example.com/x",
    "./x.json", "x//y.json", "", ".", "x/", " x.json", "x\x00.json", "x\n.json",
])
def test_unsafe_artifact_refs_rejected(demo, source, ref):
    with pytest.raises(ValidationError, match="safe relative"):
        build(demo, [source.model_copy(update={"transcript_ref": ref})])


@pytest.mark.parametrize("location,value", [
    (("unexpected",), 1), (("suite", "scenario_count"), 2),
    (("execution_summary", "completed"), -1), (("execution_summary", "planned"), 2),
    (("evaluation_summary", "assessed"), 0), (("evaluation_summary", "failed"), True),
    (("results", "severity_distribution", "3"), 0), (("results", "severity_distribution", "9"), 0),
    (("results", "severity_distribution", "0"), -1), (("results", "severity_distribution", "1"), 1.5),
    (("results", "material_or_higher"), 0), (("results", "severe"), 2),
    (("results", "mechanisms", "escalation", "scenario_count"), 2),
    (("results", "mechanisms", "accepting_framing", "finding_count"), 3),
    (("results", "mechanisms", "other"), {"finding_count": 0, "scenario_count": 0}),
    (("scenarios", 0, "severity"), 4), (("scenarios", 0, "severity"), "3"),
    (("scenarios", 0, "severity"), None), (("scenarios", 0, "severity"), 0),
    (("scenarios", 0, "finding_count"), 0), (("scenarios", 0, "finding_count"), None),
    (("scenarios", 0, "execution_status"), "unknown"), (("scenarios", 0, "evaluation_status"), "unknown"),
    (("scenarios", 0, "evaluation_status"), "cannot_assess"),
    (("scenarios", 0, "transcript_ref"), None), (("scenarios", 0, "evaluation_ref"), None),
    (("scenarios", 0, "evaluation_ref"), "transcripts/RS-001.json"),
    (("scenarios", 0, "mechanism_finding_counts", "accepting_framing"), 5),
    (("scenarios", 0, "mechanism_finding_counts", "other"), 0),
    (("model_under_test", "unexpected"), 1), (("judge", "unexpected"), 1),
    (("created_at",), "2026-09-08T12:00:00"), (("construct",), "other"),
])
def test_malformed_or_inconsistent_manifest_rejected_on_load(demo, tmp_path, location, value):
    data = demo.model_dump(mode="json")
    nested = data
    for key in location[:-1]:
        nested = nested[key]
    nested[location[-1]] = value
    path = tmp_path / "run.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_run(path)
    assert f"Run file: {path}" in caught.value.__notes__


@pytest.mark.parametrize("key", ["severity_distribution", "mechanisms"])
def test_missing_rollup_keys_rejected(demo, key):
    data = demo.model_dump()
    data["results"][key].pop(next(iter(data["results"][key])))
    with pytest.raises(ValidationError):
        RunArtifact.model_validate(data)


def test_missing_or_invalid_detail_status_combinations_rejected(demo, source):
    for updates in (
        {"evaluation": None}, {"transcript": None}, {"evaluation_status": "failed"},
        {"evaluation_status": "not_run"},
    ):
        with pytest.raises(ValueError):
            build(demo, [source.model_copy(update=updates)])


def test_save_revalidates_mutable_index_before_writing(demo, tmp_path):
    demo.scenarios.append(demo.scenarios[0])
    path = tmp_path / "run.json"
    with pytest.raises(ValidationError):
        save_run(path, demo)
    assert not path.exists()


def test_reference_verification_rejects_stale_index_and_config(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    path = bundle / "run.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["scenarios"][0]["scenario_version"] = "2.0"
    path.write_text(json.dumps(data), encoding="utf-8")
    load_run(path)  # Manifest-only checks cannot prove fields against unopened details.
    with pytest.raises(ValueError, match="identity/version/construct"):
        load_run(path, verify_references=True)
    data["scenarios"][0]["scenario_version"] = "1.0"
    data["model_under_test"]["model"] = "other"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="target configuration"):
        load_run(path, verify_references=True)


def test_symlink_escape_rejected(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    transcript = bundle / "transcripts/RS-001.json"
    outside = tmp_path / "outside.json"
    transcript.replace(outside)
    try:
        transcript.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Runtime cannot create the symlink needed to test bundle escape: {exc}")
    with pytest.raises(ValueError, match="outside the run bundle"):
        load_run(bundle / "run.json", verify_references=True)


def test_missing_referenced_file_is_a_storage_error(tmp_path):
    shutil.copy(BUNDLE / "run.json", tmp_path / "run.json")
    with pytest.raises(OSError):
        load_run(tmp_path / "run.json", verify_references=True)


def test_fresh_process_load_and_rebuild_needs_no_network_or_provider_sdk():
    script = r'''
import sys
def audit(event, args):
    if event.startswith("socket."):
        raise AssertionError("network attempt")
    if event == "import" and args[0].split(".")[0] in {"openai", "anthropic", "dotenv"}:
        raise AssertionError("provider SDK or environment loader import")
sys.addaudithook(audit)
from psych_eval.runs import load_run
run = load_run("demo/runs/relational-sycophancy-demo-v1/run.json", verify_references=True)
assert run.results.severe == 1
assert "openai" not in sys.modules and "anthropic" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
