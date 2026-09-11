"""Manual transport tests use synthetic responses, never held-out judgments."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from psych_eval.evaluations import overall_severity
from psych_eval.judge import JudgeConfig, JudgeError, assemble_judge_input, request_fingerprint, validate_judge_result
from psych_eval.manual_judge import (
    ManualJudgeAttempt, ManualJudgeProvenance, import_manual_response,
    latest_valid_manual_attempt, load_manual_attempt, load_manual_history,
)
from psych_eval.scenarios import EvaluatorScenarioView
from psych_eval.suite import write_new
from psych_eval.transcripts import load_transcript


ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ManualJudgeProvenance(interface="ChatGPT", displayed_model="Test displayed model")


@pytest.fixture
def source(tmp_path):
    transcript = load_transcript(ROOT / "demo/artifacts/RS-001/transcript.json")
    identity = EvaluatorScenarioView(scenario_id=transcript.scenario_id,
                                     scenario_version=transcript.scenario_version,
                                     construct=transcript.construct_name)
    request = assemble_judge_input(transcript, identity)
    path = tmp_path / "judge-request.json"
    path.write_bytes(request.model_dump_json(indent=2).encode("utf-8"))
    return path, request, transcript


def response():
    return dict(category="relational_sycophancy", evaluation_status="assessed",
                findings=[], zero_rationale="Synthetic validation response. ",
                cannot_assess_reason=None, recovery="unevaluated", persistence="unevaluated")


def submit(source, raw):
    path, request, _ = source
    raw_path = path.parent / "synthetic-response.txt"
    raw_path.write_bytes(raw.encode("utf-8"))
    return import_manual_response(path, raw_path,
                                  expected_request_sha256=request_fingerprint(request), judge=PROVENANCE)


def test_manual_provenance_and_exact_text_without_runtime_fields(source):
    raw = " \r\n" + json.dumps(response(), indent=2).replace("\n", "\r\n") + "\r\n\t"
    attempt = submit(source, raw)
    restored = load_manual_attempt(source[0].parent / "attempts/attempt-001.json")
    assert restored == attempt
    assert restored.raw_response.encode("utf-8") == raw.encode("utf-8")
    assert restored.request_json.encode("utf-8") == source[0].read_bytes()
    assert restored.result.zero_rationale.endswith(" ")
    assert restored.recorded_at.tzinfo is not None
    assert restored.judge.model_dump() == {
        "mode": "manual", "interface": "ChatGPT", "displayed_model": "Test displayed model"}
    forbidden = {"sampling", "temperature", "seed", "token_counts", "provider", "request_id",
                 "started_at", "finished_at", "model_executed_at", "retry_count", "max_retries",
                 "target", "response_format", "suite_run_id"}
    assert not forbidden.intersection(restored.model_dump())
    assert not forbidden.intersection(restored.judge.model_dump())
    with pytest.raises(ValidationError):
        ManualJudgeProvenance(**PROVENANCE.model_dump(), sampling={"temperature": 0})


@pytest.mark.parametrize("raw", [r'{"category": "relational\_sycophancy"}', "", "{broken"])
def test_invalid_json_is_durable_failed_attempt_without_evaluation(source, raw):
    attempt = submit(source, raw)
    assert attempt.technical_status == "failed"
    assert (attempt.failure_stage, attempt.failure_reason) == ("judge_schema", "invalid_json")
    assert attempt.failure_detail
    assert attempt.result is None and attempt.overall_severity is None
    assert load_manual_history(source[0]) == [attempt]
    assert latest_valid_manual_attempt(source[0]) is None


@pytest.mark.parametrize("severity", [0, 1, 2, 3, None])
def test_validation_and_aggregation_are_shared_with_execution_path(source, severity):
    data = response()
    if severity is None:
        data.update(evaluation_status="cannot_assess", zero_rationale=None,
                    cannot_assess_reason="Synthetic unavailable content.")
    elif severity:
        turn = source[1].transcript[1]
        data.update(zero_rationale=None, findings=[dict(
            assistant_turn_id=turn.turn_id, relational_proposition="Synthetic proposition",
            severity=severity, mechanisms=["accepting_framing"], evidence=[turn.text],
            rationale="Synthetic validator exercise, not a judgment.",
            severity_3_basis="effective_establishment" if severity == 3 else None,
        )])
    raw = json.dumps(data)
    attempt = submit(source, raw)
    assert attempt.result == validate_judge_result(raw, source[2])
    assert attempt.result == validate_judge_result(raw, source[1])
    assert attempt.overall_severity == overall_severity(attempt.result) == severity
    assert attempt.technical_status == "completed"


@pytest.mark.parametrize("corruption", ["schema", "turn", "evidence", "basis"])
def test_invalid_schema_and_semantics_share_existing_rejection(source, corruption):
    data = response()
    if corruption == "schema":
        data["extra"] = "not allowed"
    else:
        turn = source[1].transcript[1]
        data.update(zero_rationale=None, findings=[dict(
            assistant_turn_id="A999" if corruption == "turn" else turn.turn_id,
            relational_proposition="Synthetic", severity=1, mechanisms=["accepting_framing"],
            evidence=["Not an exact quote"] if corruption == "evidence" else [turn.text],
            rationale="Synthetic", severity_3_basis="both" if corruption == "basis" else None,
        )])
    raw = json.dumps(data)
    with pytest.raises(JudgeError):
        validate_judge_result(raw, source[2])
    attempt = submit(source, raw)
    assert attempt.failure_reason == "invalid_response"
    assert attempt.result is None and attempt.overall_severity is None


def test_latest_valid_selection_and_immutable_history(source):
    first = submit(source, json.dumps(response()))
    first_path = source[0].parent / "attempts/attempt-001.json"
    first_bytes = first_path.read_bytes()
    submit(source, "{bad")
    assert latest_valid_manual_attempt(source[0]) == first
    third_data = response()
    third_data["zero_rationale"] = "Another synthetic response."
    third = submit(source, json.dumps(third_data))
    assert third.attempt_index == 3
    assert latest_valid_manual_attempt(source[0]) == third
    assert first_path.read_bytes() == first_bytes
    with pytest.raises(FileExistsError):
        write_new(first_path, first)


def test_wrong_expected_fingerprint_fails_before_write(source):
    raw_path = source[0].parent / "response.txt"
    raw_path.write_text("{}")
    with pytest.raises(ValueError, match="fingerprint"):
        import_manual_response(source[0], raw_path, expected_request_sha256="0" * 64, judge=PROVENANCE)
    assert not (source[0].parent / "attempts").exists()


@pytest.mark.parametrize("field", ["request_sha256", "request_file_sha256", "raw_response_sha256",
                                  "raw_response", "result", "overall_severity", "technical_status"])
def test_tampered_attempt_rejected(source, field):
    attempt = submit(source, json.dumps(response()))
    data = attempt.model_dump()
    data[field] = ({"result": None, "overall_severity": 3, "technical_status": "failed",
                    "raw_response": "{}"}.get(field, "0" * 64))
    with pytest.raises(ValidationError):
        ManualJudgeAttempt.model_validate(data)


@pytest.mark.parametrize("change", ["text", "scenario", "format"])
def test_history_rejects_different_external_request(source, change):
    submit(source, json.dumps(response()))
    path, request, _ = source
    data = request.model_dump()
    if change == "text":
        data["transcript"][1]["text"] += " Changed"
    elif change == "scenario":
        data["scenario_id"] = "DIFFERENT"
    path.write_bytes(json.dumps(data).encode("utf-8"))
    with pytest.raises(ValueError, match="different request/transcript"):
        load_manual_history(path)


def test_existing_configs_unchanged():
    assert JudgeConfig(mode="live", provider="test", model="test").sampling is None
    assert JudgeConfig(mode="fixture", provider="fixture", model="demo-test").mode == "fixture"
    with pytest.raises(ValidationError):
        JudgeConfig(mode="manual", provider="ChatGPT", model="test")


def test_maximum_aggregation_across_findings(source):
    data = response()
    data["zero_rationale"] = None
    data["findings"] = [dict(
        assistant_turn_id="A1", relational_proposition="Synthetic proposition",
        severity=severity, mechanisms=["accepting_framing"], evidence=[source[1].transcript[1].text],
        rationale="Synthetic validation", severity_3_basis=None,
    ) for severity in (2, 1)]
    assert submit(source, json.dumps(data)).overall_severity == 2


def test_failed_attempt_cannot_claim_result_or_severity(source):
    attempt = submit(source, "{bad")
    for update in ({"result": response()}, {"overall_severity": 0}, {"failure_reason": "invalid_response"}):
        with pytest.raises(ValidationError):
            ManualJudgeAttempt.model_validate({**attempt.model_dump(), **update})


def test_history_rejects_missing_attempt_and_duplicate_ids(source):
    first = submit(source, "{bad")
    second = submit(source, "{bad")
    directory = source[0].parent / "attempts"
    path = directory / "attempt-002.json"
    data = second.model_dump(mode="json")
    data["attempt_id"] = str(first.attempt_id)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_manual_history(source[0])
    path.write_text(second.model_dump_json(), encoding="utf-8")
    (directory / "attempt-001.json").unlink()
    with pytest.raises(ValueError, match="contiguous"):
        load_manual_history(source[0])


@pytest.mark.parametrize("home", ["C:/Users/synthetic", r"C:\Users\synthetic", "/Users/synthetic", "/home/synthetic"])
def test_verification_rejects_absolute_provenance_paths(source, home):
    from psych_eval.manual_verification import ManualImportVerification, SourceExtraction, build_manual_verification
    from hashlib import sha256

    raw = json.dumps(response())
    submit(source, raw)
    report = build_manual_verification(
        source[0].parent, source[0].parent / "attempts/attempt-001.json",
        source[0].parent / "synthetic-response.txt", source_sha256=sha256(raw.encode()).hexdigest(),
        extraction=SourceExtraction(start_byte_inclusive=0, end_byte_exclusive=len(raw.encode())),
    )
    for field in ("source", "raw_response_path", "artifact_path"):
        with pytest.raises(ValidationError):
            ManualImportVerification.model_validate({**report.model_dump(), field: home + "/input.txt"})


def test_manual_import_and_report_are_portable_under_home_shaped_checkout(source, tmp_path):
    from hashlib import sha256
    import shutil
    from psych_eval.manual_verification import SourceExtraction, build_manual_verification, save_manual_verification

    root = tmp_path / "Users" / "synthetic" / "Documents" / "checkout"
    directory = root / "manual_judge" / "synthetic"
    directory.mkdir(parents=True)
    request_path = directory / "judge-request.json"
    shutil.copyfile(source[0], request_path)
    raw = json.dumps(response())
    attempt = submit((request_path, source[1], source[2]), raw)
    attempt_path = directory / "attempts/attempt-001.json"
    report = build_manual_verification(root, attempt_path, directory / "synthetic-response.txt",
        source_sha256=sha256(raw.encode()).hexdigest(),
        extraction=SourceExtraction(start_byte_inclusive=0, end_byte_exclusive=len(raw.encode())))
    report_path = directory / "verification.json"
    save_manual_verification(report_path, report)
    assert report.source == "local_manual_input"
    assert report.artifact_path == "manual_judge/synthetic/attempts/attempt-001.json"
    assert report.raw_response_path == "manual_judge/synthetic/synthetic-response.txt"
    assert report.normalized_result == attempt.result
    for text in (report_path.read_text(), attempt.model_dump_json()):
        assert "Documents" not in text and str(tmp_path) not in text
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    assert latest_valid_manual_attempt(moved / "manual_judge/synthetic/judge-request.json") == attempt
    with pytest.raises(ValueError):
        build_manual_verification(directory / "attempts", attempt_path, directory / "synthetic-response.txt",
            source_sha256=report.source_sha256, extraction=report.extraction)


def test_committed_manual_artifacts_have_no_absolute_home_provenance():
    import re
    pattern = re.compile(r"[A-Za-z]:[\\/]+(?:Users|Documents)[\\/]|/(?:Users|home)/")
    for path in (ROOT / "manual_judge").rglob("*"):
        if path.is_file() and path.suffix in {".json", ".md", ".txt"}:
            assert not pattern.search(path.read_text(encoding="utf-8")), path.relative_to(ROOT)
