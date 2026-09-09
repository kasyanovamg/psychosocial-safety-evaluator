"""Strongly verified run presentation without inference or directory scanning."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from unittest.mock import Mock

import pytest

from psych_eval.presentation import ArtifactLoadError
from psych_eval.run_presentation import load_run_view, present_run
from psych_eval.runs import load_run


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "demo/runs/relational-sycophancy-demo-v1"


@pytest.fixture(autouse=True)
def no_execution_or_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("run presentation must not perform inference or network calls")

    for name in ("psych_eval.fixture_target.FixtureTarget.respond", "psych_eval.judge.FixtureJudge.assess",
                 "psych_eval.runner.run_scenario", "psych_eval.evaluator.evaluate_transcript"):
        monkeypatch.setattr(name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def test_canonical_run_view_copies_rollups_and_index_with_strong_verification(monkeypatch):
    recorded = Mock(wraps=load_run)
    monkeypatch.setattr("psych_eval.run_presentation.load_run", recorded)
    detail_loader = Mock(side_effect=AssertionError("overview must not build detail views"))
    monkeypatch.setattr("psych_eval.run_presentation.load_evaluation_view", detail_loader)
    path = BUNDLE / "run.json"
    view = load_run_view(path)
    recorded.assert_called_once_with(path, verify_references=True)
    detail_loader.assert_not_called()
    canonical = load_run(path)
    assert view.planned == canonical.execution_summary.planned
    assert view.assessed == canonical.evaluation_summary.assessed
    assert [item.scenario_count for item in view.severity_distribution] == [0, 0, 0, 1]
    assert [item.label for item in view.severity_distribution] == ["0 — None", "1 — Mild", "2 — Material", "3 — Severe"]
    assert view.material_or_higher == canonical.results.material_or_higher
    assert view.severe == canonical.results.severe
    assert [(item.finding_count, item.scenario_count) for item in view.mechanisms] == [(2, 1), (2, 1), (0, 0), (1, 1)]
    assert len(view.scenarios) == 1
    assert view.scenarios[0].heading == "RS-001 — Excluded by Friends"
    assert view.scenarios[0].finding_count == 4
    assert view.scenarios[0].severity_display == "3 — Severe"
    assert view.detail is None
    assert view.model_under_test.is_fixture and view.judge.is_fixture
    assert view.provenance_kind == "fixture"
    with pytest.raises(FrozenInstanceError):
        view.severe = 0


def test_pure_projection_opens_no_detail_files(monkeypatch):
    run = load_run(BUNDLE / "run.json")
    monkeypatch.setattr(Path, "open", Mock(side_effect=AssertionError("No reads during projection")))
    assert present_run(run).scenarios[0].scenario_id == "RS-001"


def test_selected_details_follow_manifest_refs_and_title(tmp_path, monkeypatch):
    from psych_eval.presentation import load_evaluation_view

    bundle = tmp_path / "relocated"
    shutil.copytree(BUNDLE, bundle)
    (bundle / "evaluations/RS-001.json").rename(bundle / "evaluations/renamed.json")
    data = json.loads((bundle / "run.json").read_text())
    data["scenarios"][0].update(title="Title from the run index", evaluation_ref="evaluations/renamed.json")
    (bundle / "run.json").write_text(json.dumps(data))
    recorded = Mock(wraps=load_evaluation_view)
    monkeypatch.setattr("psych_eval.run_presentation.load_evaluation_view", recorded)
    view = load_run_view(bundle / "run.json", scenario_id="RS-001")
    recorded.assert_called_once_with(bundle / "transcripts/RS-001.json", bundle / "evaluations/renamed.json")
    assert view.scenarios[0].heading == view.detail.scenario_heading == "RS-001 — Title from the run index"
    assert [finding.turn_id for finding in view.detail.findings] == ["A1", "A2", "A3", "A4"]
    assert [turn.turn_id for turn in view.detail.turns] == ["U1", "A1", "U2", "A2", "U3", "A3", "U4", "A4"]


@pytest.mark.parametrize("invalid", ["run", "transcript", "evaluation", "missing", "mismatch"])
def test_invalid_bundle_fails_before_exposing_overview(tmp_path, invalid):
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    if invalid in ("run", "transcript", "evaluation"):
        path = {"run": bundle / "run.json", "transcript": bundle / "transcripts/RS-001.json",
                "evaluation": bundle / "evaluations/RS-001.json"}[invalid]
        path.write_text("{broken")
    elif invalid == "missing":
        (bundle / "evaluations/RS-001.json").unlink()
    else:
        path = bundle / "transcripts/RS-001.json"
        data = json.loads(path.read_text())
        data["turns"][0]["content"] += " changed"
        path.write_text(json.dumps(data))
    with pytest.raises(ArtifactLoadError, match="Unable to load evaluation run"):
        load_run_view(bundle / "run.json")


def test_unknown_selection_fails_closed():
    with pytest.raises(ArtifactLoadError, match="not in the validated run index"):
        load_run_view(BUNDLE / "run.json", scenario_id="RS-999")


def test_nonassessed_row_has_explicit_state_and_no_detail_button(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    data = json.loads((bundle / "run.json").read_text())
    data["evaluation_summary"].update(assessed=0, failed=1)
    data["results"]["severity_distribution"]["3"] = 0
    data["results"].update(material_or_higher=0, severe=0)
    for counts in data["results"]["mechanisms"].values():
        counts.update(finding_count=0, scenario_count=0)
    data["scenarios"][0].update(evaluation_status="failed", evaluation_ref=None, evaluation_id=None,
                                 severity=None, finding_count=None,
                                 mechanism_finding_counts={key: 0 for key in data["results"]["mechanisms"]})
    (bundle / "run.json").write_text(json.dumps(data))
    view = load_run_view(bundle / "run.json")
    assert view.scenarios[0].severity_display == "Evaluation: Failed"
    assert view.scenarios[0].finding_count is None
    assert not view.scenarios[0].has_details
    with pytest.raises(ArtifactLoadError, match="No detailed evaluation"):
        load_run_view(bundle / "run.json", scenario_id="RS-001")


@pytest.mark.parametrize("target_fixture,judge_fixture,kind", [(True, True, "fixture"), (False, False, "live"), (True, False, "mixed"), (False, True, "mixed")])
def test_disclosure_follows_canonical_provenance(target_fixture, judge_fixture, kind):
    from psych_eval.runs import RunArtifact

    data = load_run(BUNDLE / "run.json").model_dump()
    if not target_fixture:
        data["model_under_test"].update(provider="example", model="example-model")
    if not judge_fixture:
        data["judge"].update(mode="live", provider="example", model="example-judge")
    view = present_run(RunArtifact.model_validate(data))
    assert view.provenance_kind == kind
    if kind != "fixture":
        assert "Fixture demo" not in view.disclosure


def test_fresh_process_presentation_has_no_network_or_provider_imports():
    script = r'''
import sys
def audit(event, args):
    if event.startswith("socket."):
        raise AssertionError("Network attempt")
    if event == "import" and args[0].split(".")[0] in {"openai", "anthropic", "dotenv"}:
        raise AssertionError("Provider SDK or environment loader import")
sys.addaudithook(audit)
from psych_eval.run_presentation import load_run_view
view = load_run_view("demo/runs/relational-sycophancy-demo-v1/run.json", scenario_id="RS-001")
assert view.severe == 1 and len(view.detail.findings) == 4
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
