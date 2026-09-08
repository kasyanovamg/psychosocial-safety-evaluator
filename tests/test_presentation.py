"""Artifact integrity, presentation fidelity, and execution-free loading."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import socket
from uuid import uuid4

import pytest

from psych_eval.evaluations import load_evaluation
from psych_eval.presentation import (
    ArtifactLoadError, format_identifier, load_evaluation_view, severity_label,
)
from psych_eval.transcripts import load_transcript


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo/artifacts/RS-001"


@pytest.fixture(autouse=True)
def forbid_execution_and_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("artifact presentation must not execute models or access the network")

    for name in (
        "psych_eval.fixture_target.FixtureTarget.respond", "psych_eval.judge.FixtureJudge.assess",
        "psych_eval.runner.run_scenario", "psych_eval.evaluator.evaluate_transcript",
        "psych_eval.scenarios.load_scenario",
    ):
        monkeypatch.setattr(name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def artifacts():
    return (
        json.loads((DEMO / "transcript.json").read_text(encoding="utf-8")),
        json.loads((DEMO / "evaluation.json").read_text(encoding="utf-8")),
    )


def write_pair(tmp_path, artifacts):
    paths = (tmp_path / "transcript.json", tmp_path / "evaluation.json")
    for path, artifact in zip(paths, artifacts):
        path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    return paths


def sync_raw_result(evaluation):
    fields = ("category", "evaluation_status", "findings", "zero_rationale",
              "cannot_assess_reason", "recovery", "persistence")
    evaluation["raw_judge_response"] = json.dumps({key: evaluation[key] for key in fields})


def test_canonical_demo_artifacts_load_and_match():
    transcript = load_transcript(DEMO / "transcript.json")
    evaluation = load_evaluation(DEMO / "evaluation.json")
    assert evaluation.transcript_run_id == transcript.run_id
    assert evaluation.judge_input.transcript == transcript
    assert transcript.execution_status == "completed"
    assert evaluation.evaluation_status == "assessed"
    assert evaluation.overall_severity == 3


def test_demo_view_is_factual_and_preserves_provenance():
    view = load_evaluation_view(DEMO / "transcript.json", DEMO / "evaluation.json")
    assert view.scenario_heading == "RS-001 — Excluded by Friends"
    assert view.scenario_version == "1.0"
    assert view.construct == "Relational sycophancy"
    assert view.execution_status == "Completed"
    assert view.evaluation_status == "Assessed"
    assert view.overall_severity == 3
    assert view.severity_display == "3 — Severe"
    assert view.finding_count == view.flagged_turn_count == 4
    assert view.mechanisms == ("Accepting framing", "Epistemic endorsement", "Consequential reinforcement")
    assert view.target.is_fixture and view.judge.is_fixture
    assert view.target.provider == view.judge.provider == "fixture"
    assert view.target.model == "demo-relational-sycophancy-v1"
    assert view.judge.model == "demo-relational-sycophancy-judge-v1"
    assert view.provenance_kind == "fixture"
    assert view.banner_title == "Fixture / Demo Data"
    assert "predefined target and judge outputs" in view.banner_text
    assert view.recovery == view.persistence == "Unevaluated"
    assert view.sampling_temperature == 0.0
    assert view.sampling_max_output_tokens == 256


@pytest.mark.parametrize("number,label", [(0, "None detected"), (1, "Mild"), (2, "Material"), (3, "Severe")])
def test_severity_presentation_labels(number, label):
    assert severity_label(number) == label


@pytest.mark.parametrize("identifier,label", [
    ("accepting_framing", "Accepting framing"),
    ("epistemic_endorsement", "Epistemic endorsement"),
    ("escalation", "Escalation"),
    ("consequential_reinforcement", "Consequential reinforcement"),
    ("effective_establishment", "Effective establishment"),
    ("substantial_compounding", "Substantial compounding"),
    ("both", "Both"),
])
def test_machine_identifier_labels(identifier, label):
    assert format_identifier(identifier) == label


@pytest.mark.parametrize("change", ["run_id", "text", "target", "scenario_version"])
def test_unrelated_artifacts_rejected_even_with_same_run_id(artifacts, tmp_path, change):
    transcript, _ = artifacts
    if change == "run_id":
        transcript["run_id"] = str(uuid4())
    elif change == "text":
        transcript["turns"][0]["content"] += " Additional context."
    elif change == "target":
        transcript["target"]["model"] = "another-model"
    else:
        transcript["scenario_version"] = "2.0"
    with pytest.raises(ArtifactLoadError, match="does not match"):
        load_evaluation_view(*write_pair(tmp_path, artifacts))


def test_findings_sort_by_transcript_order_without_mutating_artifacts(artifacts, tmp_path):
    _, evaluation = artifacts
    evaluation["findings"].reverse()
    sync_raw_result(evaluation)
    paths = write_pair(tmp_path, artifacts)
    before = [path.read_bytes() for path in paths]
    view = load_evaluation_view(*paths)
    assert [finding.turn_id for finding in view.findings] == ["A1", "A2", "A3", "A4"]
    assert [path.read_bytes() for path in paths] == before
    for turn in view.turns:
        if turn.role == "user":
            assert turn.findings == ()
        else:
            assert len(turn.findings) == 1
            assert turn.findings[0].turn_id == turn.turn_id
    assert view.findings[0].severity_3_basis is None
    assert view.findings[2].severity_3_basis == "Effective establishment"


def test_multiple_findings_on_one_turn_are_preserved_without_new_turn_score(artifacts, tmp_path):
    _, evaluation = artifacts
    evaluation["findings"].append(dict(evaluation["findings"][0], severity=2))
    sync_raw_result(evaluation)
    view = load_evaluation_view(*write_pair(tmp_path, artifacts))
    assert view.finding_count == 5
    assert view.flagged_turn_count == 4
    assert [finding.severity for finding in view.turns[1].findings] == [1, 2]
    assert view.overall_severity == 3


def test_exact_persisted_text_evidence_and_rationale_preserved(artifacts, tmp_path):
    transcript, evaluation = artifacts
    suffix = "\n  **Literal markdown** <script>literal text</script> — café\t "
    for turn, snapshot in zip(transcript["turns"], evaluation["judge_input"]["transcript"]["turns"]):
        turn["content"] += suffix
        snapshot["content"] += suffix
    evaluation["findings"][0]["evidence"].append(suffix)
    evaluation["findings"][0]["rationale"] += suffix
    sync_raw_result(evaluation)
    view = load_evaluation_view(*write_pair(tmp_path, artifacts))
    assert [turn.content for turn in view.turns] == [turn["content"] for turn in transcript["turns"]]
    assert [turn.turn_id for turn in view.turns] == ["U1", "A1", "U2", "A2", "U3", "A3", "U4", "A4"]
    for finding, persisted in zip(view.findings, evaluation["findings"]):
        assert finding.evidence == tuple(persisted["evidence"])
        assert finding.rationale == persisted["rationale"]
        assert finding.relational_proposition == persisted["relational_proposition"]
    with pytest.raises(FrozenInstanceError):
        view.overall_severity = 0


@pytest.mark.parametrize("target_fixture,judge_fixture,kind", [
    (True, True, "fixture"), (False, False, "live"),
    (True, False, "mixed"), (False, True, "mixed"),
])
def test_banner_is_derived_from_both_provenances(artifacts, tmp_path, target_fixture, judge_fixture, kind):
    transcript, evaluation = artifacts
    if not target_fixture:
        transcript["target"].update(provider="example-provider", model="example-model")
        evaluation["judge_input"]["transcript"]["target"] = transcript["target"]
    if not judge_fixture:
        evaluation["judge"] = {"mode": "live", "provider": "example-provider", "model": "example-judge"}
    view = load_evaluation_view(*write_pair(tmp_path, artifacts))
    assert view.target.is_fixture == target_fixture
    assert view.judge.is_fixture == judge_fixture
    assert view.provenance_kind == kind
    if kind != "fixture":
        assert view.banner_title != "Fixture / Demo Data"
        assert "predefined target and judge outputs" not in view.banner_text
    if kind == "live":
        assert view.banner_title == "Live Evaluation"


@pytest.mark.parametrize("status,severity,reason", [
    ("assessed", 0, "No positive findings."),
    ("cannot_assess", None, "Insufficient context."),
])
def test_zero_and_cannot_assess_are_presented_distinctly(artifacts, tmp_path, status, severity, reason):
    _, evaluation = artifacts
    evaluation.update(findings=[], evaluation_status=status, overall_severity=severity,
                       zero_rationale=reason if status == "assessed" else None,
                       cannot_assess_reason=reason if status == "cannot_assess" else None)
    sync_raw_result(evaluation)
    view = load_evaluation_view(*write_pair(tmp_path, artifacts))
    assert view.overall_severity == severity
    assert view.severity_display == ("0 — None detected" if status == "assessed" else "Not assessed")
    assert view.findings == ()
    assert view.mechanisms == ()


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("contents", ["{broken", "{}", "null"])
def test_malformed_artifacts_fail_clearly(artifacts, tmp_path, which, contents):
    paths = write_pair(tmp_path, artifacts)
    paths[which].write_text(contents, encoding="utf-8")
    artifact_name = "transcript" if which == 0 else "evaluation"
    with pytest.raises(ArtifactLoadError, match=f"Unable to load {artifact_name} artifact.*schema validation"):
        load_evaluation_view(*paths)


@pytest.mark.parametrize("which", [0, 1])
def test_missing_artifacts_fail_clearly(artifacts, tmp_path, which):
    paths = write_pair(tmp_path, artifacts)
    paths[which].unlink()
    with pytest.raises(ArtifactLoadError, match="could not be read"):
        load_evaluation_view(*paths)


def test_invalid_stored_severity_is_not_repaired(artifacts, tmp_path):
    artifacts[1]["overall_severity"] = 1
    with pytest.raises(ArtifactLoadError, match="evaluation.*schema validation"):
        load_evaluation_view(*write_pair(tmp_path, artifacts))
