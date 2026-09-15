"""Thin local workflow composition over canonical config, selection, and runs."""

from pathlib import Path

import pytest

from psych_eval.integrations.runtime import IntegrationConfigError
from psych_eval.runs import load_run
from psych_eval.selection import SelectionRequest
from psych_eval.integrations.workflow import execute_evaluation, prepare_evaluation


ROOT = Path(__file__).resolve().parents[1]


def test_review_retains_only_public_configuration_and_resolved_scope(tmp_path, monkeypatch):
    secret = "private-token-must-never-escape"
    config = tmp_path / "runtime.yaml"
    config.write_text(f"""
target:
  integration: target-a
  config:
    provider: target-provider
    model: target-model
    system_prompt: target prompt
    sampling: {{temperature: 0.2, max_output_tokens: 64}}
  options:
    api_key: {secret}
judge:
  integration: judge-b
  config:
    mode: live
    provider: judge-provider
    model: judge-model
    prompt_version: "0.1"
  options:
    api_key: {secret}
target_max_retries: 2
judge_max_retries: 1
""", encoding="utf-8")
    monkeypatch.setattr("psych_eval.integrations.workflow.configure_integrations", lambda runtime: (None, None))

    review = prepare_evaluation(config, SelectionRequest(mode="quick"))

    assert review.target_integration == "target-a"
    assert review.target_config.provider == "target-provider"
    assert review.judge_integration == "judge-b"
    assert review.judge_config.provider == "judge-provider"
    assert review.target_max_retries == 2 and review.judge_max_retries == 1
    assert review.selection.selected_scenario_ids == ["RS-002", "RS-008", "RS-013"]
    assert secret not in repr(review)
    assert not hasattr(review, "target_options") and not hasattr(review, "judge_options")


def test_execute_runs_exact_reviewed_scope_reports_progress_and_reloads_persisted_run(tmp_path):
    review = prepare_evaluation(ROOT / "runtime.fixture.yaml", SelectionRequest(mode="quick"))
    events = []

    run = execute_evaluation(review, tmp_path / "bundle", progress=events.append)

    assert run == load_run(tmp_path / "bundle/run.json", verify_references=True)
    assert run.selection == review.selection
    assert [entry.scenario_id for entry in run.scenarios] == ["RS-002", "RS-008", "RS-013"]
    assert [event.phase for event in events] == [
        "started", "scenario_started", "scenario_completed",
        "scenario_started", "scenario_completed",
        "scenario_started", "scenario_completed", "completed",
    ]
    assert [(event.phase, event.processed, event.total) for event in events] == [
        ("started", 0, 3), ("scenario_started", 0, 3), ("scenario_completed", 1, 3),
        ("scenario_started", 1, 3), ("scenario_completed", 2, 3),
        ("scenario_started", 2, 3), ("scenario_completed", 3, 3), ("completed", 3, 3),
    ]
    assert all(event.execution_status == "completed" for event in events if event.phase == "scenario_completed")


def test_changed_config_must_be_reviewed_again_before_execution(tmp_path, monkeypatch):
    config = tmp_path / "runtime.yaml"
    config.write_bytes((ROOT / "runtime.fixture.yaml").read_bytes())
    review = prepare_evaluation(config, SelectionRequest(mode="quick"))
    config.write_text(config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_integrations",
        lambda runtime: (_ for _ in ()).throw(AssertionError("must fail before adapter construction")),
    )

    with pytest.raises(IntegrationConfigError, match="review it again"):
        execute_evaluation(review, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
