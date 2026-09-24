"""Thin local workflow composition over canonical config, selection, and runs."""

from hashlib import sha256
from pathlib import Path

import pytest

from psych_eval.integrations.runtime import IntegrationConfigError
from psych_eval.integrations.runtime import load_runtime_config
from psych_eval.runs import load_run
from psych_eval.selection import SelectionRequest, resolve_selection
from psych_eval.integrations.workflow import (
    completed_saved_scenarios, execute_evaluation, execute_rejudge,
    prepare_evaluation, prepare_rejudge,
)
from psych_eval.judge import JudgeResult


ROOT = Path(__file__).resolve().parents[1]


def tree_digest(root):
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def fixture_source(tmp_path):
    directory = tmp_path / "source"
    review = prepare_evaluation(ROOT / "runtime.fixture.yaml", SelectionRequest(mode="quick"))
    execute_evaluation(review, directory)
    return directory


def test_checked_in_configs_select_prompt_versions_explicitly():
    fixture = load_runtime_config(ROOT / "runtime.fixture.yaml")
    openai = load_runtime_config(ROOT / "integrations/openai/example.yaml")

    assert fixture.judge.config.prompt_version == "0.1"
    assert openai.judge.config.prompt_version == "0.3"


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
    prompt_version: "0.2"
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


def test_rejudge_exposes_only_completed_transcripts_and_rejects_empty_selection(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    run_path = source / "run.json"
    assert completed_saved_scenarios(run_path) == ("RS-002", "RS-008", "RS-013")

    configured = []
    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_judge",
        lambda runtime: configured.append(runtime),
    )
    with pytest.raises(IntegrationConfigError, match="Choose at least one"):
        prepare_rejudge(ROOT / "runtime.fixture.yaml", run_path, [], tmp_path / "fork")
    assert configured == []
    assert not (tmp_path / "fork").exists()


def test_rejudge_excludes_saved_incomplete_transcripts(tmp_path):
    from psych_eval.integrations.pack_fixtures import PackJudge, pack_target
    from psych_eval.suite import discover_pack, execute_suite

    scenarios = {item.scenario_id: item for item in discover_pack()}
    target_config = pack_target(scenarios["RS-002"].to_runtime_view()).config

    class FailedTarget:
        def respond(self, messages, *, config):
            raise RuntimeError("synthetic target failure")

    def target_factory(runtime):
        return FailedTarget() if runtime.scenario_id == "RS-008" else pack_target(runtime)

    selection = resolve_selection(
        SelectionRequest(mode="custom", custom_scenario_ids=["RS-002", "RS-008"]),
    )
    directory = tmp_path / "mixed"
    execute_suite(
        directory, target_factory=target_factory, target_config=target_config,
        target_mode="fixture", judge=PackJudge(), judge_config=PackJudge().config,
        target_max_retries=0, judge_max_retries=0, selection=selection,
    )

    assert completed_saved_scenarios(directory / "run.json") == ("RS-002",)


def test_rejudge_three_selected_calls_only_judge_and_preserves_source(tmp_path, monkeypatch):
    from psych_eval.integrations.pack_fixtures import PackJudge

    source = fixture_source(tmp_path)
    before = tree_digest(source)
    destination = tmp_path / "fork"
    calls = []

    class CountingJudge:
        def assess(self, request, *, config):
            calls.append(request.scenario_id)
            return PackJudge().assess(request, config=config)

    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_judge",
        lambda runtime: CountingJudge(),
    )
    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_integrations",
        lambda runtime: (_ for _ in ()).throw(AssertionError("target must not be configured")),
    )
    review = prepare_rejudge(
        ROOT / "runtime.fixture.yaml", source / "run.json",
        ["RS-002", "RS-008", "RS-013"], destination,
    )
    assert calls == [] and not destination.exists()

    result = execute_rejudge(review)

    assert calls == ["RS-002", "RS-008", "RS-013"]
    assert tree_digest(source) == before
    assert result == load_run(destination / "run.json", verify_references=True)
    assert [entry.scenario_id for entry in result.scenarios] == list(calls)
    assert all((destination / scenario / "judge/attempt-001.json").is_file() for scenario in calls)
    assert all((destination / scenario / "target_call.json").read_bytes()
               == (source / scenario / "target_call.json").read_bytes() for scenario in calls)
    assert all((destination / scenario / "transcript.json").read_bytes()
               == (source / scenario / "transcript.json").read_bytes() for scenario in calls)

    calls.clear()
    assert load_run(destination / "run.json", verify_references=True) == result
    assert calls == []


def test_rejudge_selection_controls_calls_and_records_current_prompt(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    config = tmp_path / "v03.yaml"
    text = (ROOT / "runtime.fixture.yaml").read_text()
    text = text.replace("mode: fixture", "mode: live").replace(
        "provider: fixture\n    model: demo-relational-sycophancy-judge-v1",
        "provider: test\n    model: judge-model",
    ).replace('prompt_version: "0.1"', 'prompt_version: "0.3"')
    config.write_text(text)
    calls = []

    class ZeroJudge:
        def assess(self, request, *, config):
            calls.append(request.scenario_id)
            return JudgeResult(
                category="relational_sycophancy", evaluation_status="assessed",
                findings=[], zero_rationale="Synthetic static result.",
                cannot_assess_reason=None, recovery="unevaluated", persistence="unevaluated",
            ).model_dump_json()

    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_judge", lambda runtime: ZeroJudge(),
    )
    destination = tmp_path / "v03-fork"
    review = prepare_rejudge(
        config, source / "run.json", ["RS-002", "RS-013"], destination,
    )
    assert calls == []
    result = execute_rejudge(review)

    assert calls == ["RS-002", "RS-013"]
    assert result.judge.prompt_version == "0.3"
    assert result.rubric_version == "0.2"
    assert [entry.scenario_id for entry in result.scenarios] == ["RS-002", "RS-013"]


def test_rejudge_destination_collision_refuses_before_judge_construction(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    destination = tmp_path / "existing"
    destination.mkdir()
    configured = []
    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_judge",
        lambda runtime: configured.append(runtime),
    )

    with pytest.raises(IntegrationConfigError, match="already exists"):
        prepare_rejudge(
            ROOT / "runtime.fixture.yaml", source / "run.json", ["RS-002"], destination,
        )
    assert configured == []

    destination.rmdir()
    review = prepare_rejudge(
        ROOT / "runtime.fixture.yaml", source / "run.json", ["RS-002"], destination,
    )
    assert len(configured) == 1
    destination.mkdir()
    with pytest.raises(IntegrationConfigError, match="already exists"):
        execute_rejudge(review)
    assert len(configured) == 1

    with pytest.raises(IntegrationConfigError, match="outside the source"):
        prepare_rejudge(
            ROOT / "runtime.fixture.yaml", source / "run.json", ["RS-002"],
            source / "nested-fork",
        )
