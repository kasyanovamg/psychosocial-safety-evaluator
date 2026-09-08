"""Small optional UI smoke tests using Streamlit's public application test API."""

import builtins
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
import socket

import pytest

pytest.importorskip("streamlit", reason="Install the ui extra to run application smoke tests")
from streamlit.testing.v1 import AppTest

from psych_eval.presentation import ArtifactLoadError, load_evaluation_view


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo/artifacts/RS-001"


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
        "psych_eval.fixture_target.FixtureTarget.respond", "psych_eval.judge.FixtureJudge.assess",
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


def test_page_renders_all_findings_evidence_transcript_and_provenance():
    view = load_evaluation_view(DEMO / "transcript.json", DEMO / "evaluation.json")
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()
    assert not app.exception
    assert not app.error
    assert app.title[0].value == "Psychosocial Safety Evaluator"
    assert app.metric[0].label == "Overall Severity"
    assert app.metric[0].value == "3 — Severe"
    assert "Fixture / Demo Data" in app.warning[0].value
    assert "RS-001 — Excluded by Friends" in [item.value for item in app.subheader]
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
    subheaders = [item.value for item in app.subheader]
    for finding in view.findings:
        assert f"{finding.turn_id} · Severity {finding.severity_display}" in subheaders
    texts = [item.value for item in app.text]
    assert f"Model: {view.target.model}" in texts
    assert f"Model: {view.judge.model}" in texts
    assert texts.count("Provider: fixture") == 2
    assert texts.count("Execution: Fixture") == 2
    assert not app.button
    assert not app.text_input


def test_literal_rendering_preserves_whitespace_and_never_interprets_artifact_markup(monkeypatch):
    view = load_evaluation_view(DEMO / "transcript.json", DEMO / "evaluation.json")
    exact = '\n  **Literal** <script>do_not_run()</script> <img src="https://invalid.test/x"> & café\t\n\n'
    finding = replace(view.findings[0], evidence=(exact,), rationale=exact, relational_proposition=exact)
    turn = replace(view.turns[0], content=exact)
    view = replace(view, findings=(finding, *view.findings[1:]), turns=(turn, *view.turns[1:]))
    monkeypatch.setattr("psych_eval.presentation.load_evaluation_view", lambda *args: view)
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()
    assert not app.exception
    assert literal_body(app.chat_message[0].get("html")[0]) == exact
    matching = [item for item in app.get("html") if literal_body(item) == exact]
    assert len(matching) == 4
    for item in matching:
        assert "<script>" not in item.proto.body
        assert "<img " not in item.proto.body
        assert "&lt;script&gt;" in item.proto.body


@pytest.mark.parametrize("message", [
    "Unable to load evaluation artifact. The saved evaluation did not pass schema validation.",
    "Unable to combine artifacts. The transcript does not match the evaluation's saved transcript and run identity.",
])
def test_page_stops_cleanly_on_invalid_or_mismatched_artifacts(monkeypatch, message):
    def invalid(*args, **kwargs):
        raise ArtifactLoadError(message)

    monkeypatch.setattr("psych_eval.presentation.load_evaluation_view", invalid)
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()
    assert not app.exception
    assert app.error[0].value == message
    assert not app.metric
    assert not app.chat_message
    assert not app.warning
