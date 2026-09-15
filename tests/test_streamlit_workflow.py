"""Configure, review, explicit-run, and persisted-report UI smoke tests."""

from pathlib import Path
import socket

import pytest

pytest.importorskip("streamlit", reason="Install the ui extra to run application smoke tests")
from streamlit.testing.v1 import AppTest

from psych_eval.runs import load_run


ROOT = Path(__file__).resolve().parents[1]
DEMO_RUN = ROOT / "demo/runs/relational-sycophancy-demo-v1/run.json"
RUN_MODE = "Run local evaluation"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("local fixture workflow must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def open_local():
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=30).run()
    app.radio(key="workflow_mode").set_value(RUN_MODE).run()
    return app


def test_demo_is_default_artifact_only_mode():
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()

    assert app.radio(key="workflow_mode").value == "View pre-generated demo"
    assert not app.text_input
    assert not any(button.label in {"Review evaluation", "Run evaluation"} for button in app.button)
    assert any("does not configure credentials" in item.value for item in app.caption)


def test_local_configuration_uses_discovery_and_engine_selection_catalog():
    app = open_local()

    assert not app.exception and not app.error
    captions = [item.value for item in app.caption]
    target_integrations = next(item for item in captions if item.startswith("Installed target integrations:"))
    judge_integrations = next(item for item in captions if item.startswith("Installed judge integrations:"))
    assert "fixture" in target_integrations and "fixture" in judge_integrations
    assert any("Scenario pack v0.1 · Rubric v0.2 · Judge prompt v0.1" in item for item in captions)
    assert "**Relational Sycophancy**" in [item.value for item in app.markdown]
    assert not any("Additional psychosocial evaluations" in item for item in captions)
    assert app.selectbox(key="scenario_selection_mode").options == [
        "Quick — 3 scenarios", "Development — 10 scenarios", "Full — 20 scenarios", "Custom",
    ]
    assert "**Selected: 3 / 20 scenarios**" in [item.value for item in app.markdown]

    app.selectbox(key="scenario_selection_mode").set_value("custom").run()
    assert app.button(key="review_evaluation").disabled
    app.multiselect(key="custom_scenario_ids").set_value(["RS-013", "RS-002"]).run()
    assert not app.button(key="review_evaluation").disabled
    assert "**Selected: 2 / 20 scenarios**" in [item.value for item in app.markdown]
    scope = next(item for item in app.expander if item.label == "Preview exact run scope")
    assert [item.value for item in scope.markdown] == [
        "RS-002 — Delayed Reply", "RS-013 — Boundary Based on Impact",
    ]


def test_invalid_runtime_config_stays_on_review_gate_without_creating_output(tmp_path):
    output = tmp_path / "must-not-exist"
    app = open_local()
    app.text_input(key="runtime_config_path").set_value(str(tmp_path / "missing.yaml"))
    app.text_input(key="output_directory_input").set_value(str(output))
    app.button(key="review_evaluation").click().run()

    assert not app.exception
    assert app.error[0].value == "Invalid runtime config; check target/judge selections, configs, and options"
    assert not any(button.label == "Run evaluation" for button in app.button)
    assert not output.exists()


def test_saved_report_remains_available_when_integration_discovery_fails(monkeypatch):
    from psych_eval.runs import load_run as canonical_load_run

    def blocked(*args, **kwargs):
        raise AssertionError("report inspection must not initialize or call adapters")

    def broken_metadata(**kwargs):
        raise RuntimeError("unsafe optional-package metadata detail")

    verified = []

    def tracked_load(path, *, verify_references=False):
        verified.append(verify_references)
        return canonical_load_run(path, verify_references=verify_references)

    monkeypatch.setattr("psych_eval.integrations.runtime.entry_points", broken_metadata)
    monkeypatch.setattr("psych_eval.integrations.workflow.configure_integrations", blocked)
    monkeypatch.setattr("psych_eval.integrations.fixture_target.FixtureTarget.respond", blocked)
    monkeypatch.setattr("psych_eval.integrations.fixture_judge.FixtureJudge.assess", blocked)
    monkeypatch.setattr("psych_eval.run_presentation.load_run", tracked_load)

    app = open_local()
    assert not app.exception
    assert [item.value for item in app.error] == [
        "Unable to read installed integration entry points",
    ]
    assert app.text_input(key="saved_run_path_input")
    assert not any("unsafe optional-package metadata detail" in item.value for item in app.error)

    verified.clear()
    app.text_input(key="saved_run_path_input").set_value(str(DEMO_RUN)).run()
    app.button(key="open_saved_report").click().run()

    assert not app.exception and not app.error
    assert app.header[0].value == "Evaluation results"
    assert verified and all(verified)


def test_fixture_quick_workflow_runs_only_after_confirmation_and_loads_persisted_report(tmp_path):
    output = tmp_path / "quick-run"
    app = open_local()
    app.text_input(key="runtime_config_path").set_value(str(ROOT / "runtime.fixture.yaml"))
    app.text_input(key="output_directory_input").set_value(str(output))
    assert not output.exists()

    app.button(key="review_evaluation").click().run()
    assert not app.exception and not app.error and not output.exists()
    assert app.header[0].value == "Review evaluation"
    assert app.button(key="run_evaluation").label == "Run evaluation"
    assert "**Selected: 3 / 20 scenarios**" in [item.value for item in app.markdown]
    assert [item.value for item in app.text if item.value.startswith("RS-")] == [
        "RS-002\nRS-008\nRS-013",
    ]

    app.button(key="run_evaluation").click().run()
    assert not app.exception and not app.error
    assert app.header[0].value == "Evaluation results"
    run_path = output / "run.json"
    run = load_run(run_path, verify_references=True)
    assert run.selection.selection_mode == "quick"
    assert run.selection.selected_scenario_ids == ["RS-002", "RS-008", "RS-013"]
    assert [entry.scenario_id for entry in run.scenarios] == run.selection.selected_scenario_ids
    assert any("Partial coverage · 3 / 20 scenarios selected" in item.value for item in app.warning)
    original = run_path.read_bytes()
    app.run()
    assert run_path.read_bytes() == original

    reopened = open_local()
    reopened.text_input(key="saved_run_path_input").set_value(str(run_path)).run()
    reopened.button(key="open_saved_report").click().run()
    assert not reopened.exception and not reopened.error
    assert reopened.header[0].value == "Evaluation results"
    assert run_path.read_bytes() == original


def test_execution_failure_is_visible_without_a_success_report(tmp_path, monkeypatch):
    output = tmp_path / "failed-run"
    app = open_local()
    app.text_input(key="runtime_config_path").set_value(str(ROOT / "runtime.fixture.yaml"))
    app.text_input(key="output_directory_input").set_value(str(output))
    app.button(key="review_evaluation").click().run()

    def fail(*args, **kwargs):
        raise RuntimeError("private provider detail")

    monkeypatch.setattr("psych_eval.integrations.workflow.execute_evaluation", fail)
    app.button(key="run_evaluation").click().run()
    assert not app.exception
    assert app.error[0].value == "Evaluation did not complete because of an unexpected local execution error."
    assert "private provider detail" not in str(app)
    assert not any(header.value == "Evaluation results" for header in app.header)
    assert not (output / "run.json").exists()
