"""Small optional UI smoke tests using Streamlit's public application test API."""

import builtins
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
import json
import shutil
import socket

import pytest

pytest.importorskip("streamlit", reason="Install the ui extra to run application smoke tests")
from streamlit.testing.v1 import AppTest

from psych_eval.presentation import ArtifactLoadError
from psych_eval.run_presentation import load_run_view


ROOT = Path(__file__).resolve().parents[1]
DEMO_RUN = ROOT / "demo/runs/relational-sycophancy-demo-v1/run.json"


def literal_body(element):
    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    parser = TextParser()
    parser.feed(element.proto.body)
    return "".join(parser.parts)


@pytest.fixture(autouse=True)
def no_execution_network_or_sdk(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("The UI must only read local artifacts")

    for name in (
        "psych_eval.integrations.fixture_target.FixtureTarget.respond", "psych_eval.integrations.fixture_judge.FixtureJudge.assess",
        "psych_eval.runner.run_scenario", "psych_eval.evaluator.evaluate_transcript",
        "psych_eval.scenarios.load_scenario",
    ):
        monkeypatch.setattr(name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    original_import = builtins.__import__

    def local_import(name, *args, **kwargs):
        if name.split(".")[0] in {"openai", "anthropic", "dotenv"}:
            raise AssertionError("The UI must not import provider SDKs or load .env")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", local_import)


def open_app():
    return AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()


def open_details(app):
    app.button(key="view_demo").click().run()
    app.button(key="details_RS-001").click().run()
    return app


def arrival_scripts(app):
    return [item.proto.body for item in app.get("html")
            if getattr(item.proto, "unsafe_allow_javascript", False)] + [
        item.proto.srcdoc for item in app.get("iframe") if item.proto.srcdoc
    ]


@pytest.mark.parametrize("legacy_html", [False, True])
def test_navigation_emits_one_fresh_destination_effect_only_on_transitions(monkeypatch, legacy_html):
    """AppTest checks the navigation contract, not browser scroll or focus."""
    if legacy_html:
        import streamlit as st

        original_html = st.html

        def html_without_javascript_parameter(body):
            return original_html(body)

        monkeypatch.setattr(st, "html", html_without_javascript_parameter)
    app = open_app()
    assert not arrival_scripts(app)
    revision = 0
    # Repeat forward and back transitions: revisiting a page must emit a fresh
    # effect instead of reusing script content/anchors from the previous visit.
    for _ in range(2):
        for button_key, title in (
            ("view_demo", "Evaluation results"),
            ("details_RS-001", "RS-001 — Excluded by Friends"),
            (None, "Evaluation results"),
            (None, "Psychosocial Safety Evaluator"),
        ):
            button = app.button(key=button_key) if button_key else app.button[0]
            button.click().run()
            revision += 1
            assert not app.exception and not app.error
            heading = app.title[0] if title == "Psychosocial Safety Evaluator" else app.header[0]
            assert heading.value == title
            assert heading.proto.anchor == f"inspection-destination-{revision}"
            scripts = arrival_scripts(app)
            assert len(scripts) == 1
            assert f'getElementById("{heading.proto.anchor}")' in scripts[0]
            assert 'heading.focus({preventScroll: true})' in scripts[0]
            assert 'heading.setAttribute("tabindex", "-1")' in scripts[0]
            assert 'behavior: "instant"' in scripts[0]
            assert "inspection_arrival_pending" not in app.session_state
            # Ordinary reruns must not steal focus or reset inspection scrolling.
            app.run()
            assert not arrival_scripts(app)


def test_landing_explains_broader_product_configuration_coverage_and_demo():
    app = open_app()
    assert not app.exception and not app.error
    assert app.title[0].value == "Psychosocial Safety Evaluator"
    assert "Evaluate conversational AI for psychosocial safety risks" in app.subheader[0].value
    markdown = [item.value for item in app.markdown]
    assert "**Model under test**" in markdown
    assert "**Judge model**" in markdown
    assert "**Relational sycophancy — Available**" in markdown
    assert any("Additional psychosocial evaluations — Planned" in item.value for item in app.caption)
    assert "Fixture demo" in app.info[0].value
    assert "No live inference occurs" in app.info[0].value
    assert not app.warning
    assert app.button(key="view_demo").label == "View demo evaluation"
    assert not any("Start evaluation" in item.label for item in app.button)
    assert not app.metric and not app.chat_message
    models = [literal_body(item) for item in app.get("html")]
    assert "demo-relational-sycophancy-v1" in models
    assert "demo-relational-sycophancy-judge-v1" in models


def test_results_use_run_counts_and_index_with_no_global_score():
    app = open_app()
    app.button(key="view_demo").click().run()
    assert not app.exception and not app.error
    assert {item.label: item.value for item in app.metric} == {
        "Planned scenarios": "1", "Assessed scenarios": "1", "Material or higher": "1", "Severe": "1",
    }
    assert app.table[0].value.to_dict("records") == [
        {"Severity": "0 — None", "Scenarios": 0}, {"Severity": "1 — Mild", "Scenarios": 0},
        {"Severity": "2 — Material", "Scenarios": 0}, {"Severity": "3 — Severe", "Scenarios": 1},
    ]
    assert app.table[1].value.to_dict("records") == [
        {"Mechanism": "Accepting framing", "Findings": 2, "Scenarios": 1},
        {"Mechanism": "Epistemic endorsement", "Findings": 2, "Scenarios": 1},
        {"Mechanism": "Escalation", "Findings": 0, "Scenarios": 0},
        {"Mechanism": "Consequential reinforcement", "Findings": 1, "Scenarios": 1},
    ]
    assert "RS-001 — Excluded by Friends" in [item.value for item in app.subheader]
    assert "3 — Severe · 4 findings" in [item.value for item in app.markdown]
    assert app.button(key="details_RS-001").label == "View details"
    assert not app.chat_message
    assert all("overall" not in item.label.lower() and "global" not in item.label.lower() for item in app.metric)
    app.button[0].click().run()
    assert app.button(key="view_demo").label == "View demo evaluation"


def test_selected_scenario_renders_findings_and_secondary_exact_transcript():
    view = load_run_view(DEMO_RUN, scenario_id="RS-001").detail
    app = open_details(open_app())
    assert not app.exception and not app.error
    assert app.metric[0].label == "Scenario severity"
    assert app.metric[0].value == "3 — Severe"
    assert app.metric[1].value == "4"
    assert app.header[0].value == "RS-001 — Excluded by Friends"
    blocks = [literal_body(item) for item in app.get("html")]
    for finding in view.findings:
        assert finding.relational_proposition in blocks
        assert finding.rationale in blocks
        for evidence in finding.evidence:
            assert evidence in blocks
    assert len(app.chat_message) == 8
    for rendered, turn in zip(app.chat_message, view.turns):
        assert rendered.name == turn.role
        assert literal_body(rendered.get("html")[0]) == turn.content
    assert [item.value for item in app.subheader] == [
        f"{finding.turn_id} · Severity {finding.severity_display}" for finding in view.findings
    ]
    assert [item.value for item in app.caption if item.value.startswith("Severity-3 basis:")] == [
        "Severity-3 basis: Effective establishment", "Severity-3 basis: Both",
    ]
    assert [item.label for item in app.expander] == ["View full conversation", "Technical details & reproducibility"]
    assert all(item.proto.expanded is False for item in app.expander)
    assert len(app.expander[0].chat_message) == 8
    assert any("Evaluation ID:" in item.value for item in app.expander[1].text)
    assert not any(item.value == "Target" for item in app.subheader)
    app.button[0].click().run()
    assert app.header[0].value == "Evaluation results"
    assert not app.chat_message


def test_literal_rendering_preserves_whitespace_and_never_interprets_artifact_markup(monkeypatch):
    run = load_run_view(DEMO_RUN, scenario_id="RS-001")
    view = run.detail
    exact = '\n  **Literal** <script>do_not_run()</script> <img src="https://invalid.test/x"> & café\t\n\n'
    finding = replace(view.findings[0], evidence=(exact,), rationale=exact, relational_proposition=exact)
    turn = replace(view.turns[0], content=exact)
    view = replace(view, findings=(finding, *view.findings[1:]), turns=(turn, *view.turns[1:]))
    monkeypatch.setattr("psych_eval.run_presentation.load_run_view", lambda *args, **kwargs: replace(run, detail=view))
    app = open_details(open_app())
    assert not app.exception
    assert literal_body(app.chat_message[0].get("html")[0]) == exact
    matching = [item for item in app.get("html") if literal_body(item) == exact]
    assert len(matching) == 4
    for item in matching:
        assert "<script>" not in item.proto.body
        assert "<img " not in item.proto.body
        assert "&lt;script&gt;" in item.proto.body


@pytest.mark.parametrize("message", [
    "Unable to load evaluation run. The run or its referenced artifacts did not pass validation.",
    "Unable to combine artifacts. The transcript does not match the evaluation's saved transcript and run identity.",
])
def test_page_stops_cleanly_on_invalid_or_mismatched_artifacts(monkeypatch, message):
    def invalid(*args, **kwargs):
        raise ArtifactLoadError(message)

    monkeypatch.setattr("psych_eval.run_presentation.load_run_view", invalid)
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()
    assert not app.exception
    assert app.error[0].value == message
    assert not app.metric
    assert not app.chat_message
    assert not app.warning


@pytest.mark.parametrize("broken", ["manifest", "reference"])
def test_navigation_revalidates_bundle_and_removes_results_on_failure(tmp_path, monkeypatch, broken):
    from psych_eval.runs import load_run

    folder = tmp_path / "bundle"
    shutil.copytree(DEMO_RUN.parent, folder)
    monkeypatch.setattr("psych_eval.run_presentation.load_run",
                        lambda path, *, verify_references: load_run(folder / "run.json", verify_references=verify_references))
    app = open_app()
    assert not app.error
    if broken == "manifest":
        (folder / "run.json").write_text("{broken")
    else:
        path = folder / "transcripts/RS-001.json"
        data = json.loads(path.read_text())
        data["turns"][0]["content"] += " altered"
        path.write_text(json.dumps(data))
    app.button(key="view_demo").click().run()
    assert not app.exception
    assert "did not pass validation" in app.error[0].value
    assert not app.metric and not app.table and not app.chat_message
    assert not app.button and not app.info
    assert not arrival_scripts(app)
