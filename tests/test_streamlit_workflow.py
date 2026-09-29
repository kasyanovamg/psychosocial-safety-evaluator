"""Configure, review, explicit-run, and persisted-report UI smoke tests."""

from pathlib import Path
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlparse

import pytest

pytest.importorskip("streamlit", reason="Install the ui extra to run application smoke tests")
from streamlit.testing.v1 import AppTest

from psych_eval.runs import load_run


ROOT = Path(__file__).resolve().parents[1]
DEMO_RUN = ROOT / "demo/runs/relational-sycophancy-reference-v1/run.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    def blocked(*args, **kwargs):
        raise AssertionError("local fixture workflow must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("PSYCH_EVAL_RUN_REGISTRY", str(tmp_path / "run-registry.json"))


def retain_page(app, url_path):
    app._page_hash = next(
        page_hash for page_hash, page in app._registered_pages.items()
        if page["url_pathname"] == url_path
    )
    return app


def route_links(app):
    links = []

    class LinkParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "a" and values.get("data-psych-route-link") == "true":
                links.append(values)

    for element in app.get("html"):
        LinkParser().feed(element.proto.body)
    return links


def link_by_label(app, label):
    return next(link for link in route_links(app) if link["data-route-label"] == label)


def follow_link(app, label):
    """Model the native page link; AppTest cannot click page-link elements."""
    link = link_by_label(app, label)
    parsed = urlparse(link["href"])
    retain_page(app, parsed.path.strip("/"))
    app.query_params.clear()
    app.query_params.update(dict(parse_qsl(parsed.query)))
    return app.run()


def rendered_text(app):
    return "\n".join(
        str(item.value)
        for kind in ("markdown", "caption", "info", "warning", "text")
        for item in app.get(kind)
    )


def open_local():
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=30).run()
    follow_link(app, "Run evaluation")
    app.text_input(key="runtime_config_path").set_value(
        str(ROOT / "runtime.fixture.yaml")
    ).run()
    return app


def open_saved():
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=30).run()
    follow_link(app, "Saved runs")
    return follow_link(app, "View saved results")


def test_landing_is_default_and_demo_is_explicit_artifact_only_mode():
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()

    assert app.header[0].value == "Choose a workflow"
    assert not app.text_input
    assert not any(button.label == "Review run" for button in app.button)
    assert link_by_label(app, "View demo")
    follow_link(app, "View demo")
    assert app.header[0].value == "Demo results"
    assert any("Viewing and exploring these artifacts makes no model calls" in item.value
               for item in app.info)
    assert not any(
        link["data-route-label"] == "Rejudge saved transcripts"
        for link in route_links(app)
    )
    assert not app.text_input and not app.multiselect


def test_saved_runs_hub_explains_view_and_rejudge_before_selection():
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=15).run()
    follow_link(app, "Saved runs")

    assert app.header[0].value == "Saved runs"
    links = {link["data-route-label"] for link in route_links(app)}
    assert {"View saved results", "Rejudge saved transcripts"} <= links
    text = rendered_text(app)
    assert "No target or judge calls are made" in text
    assert "leaves the original run unchanged" in text
    assert "paid judge calls" in text
    assert not app.text_input


def test_shared_selector_registers_once_and_reuses_friendly_metadata():
    app = open_saved()
    app.text_input(key="view_run_path_input").set_value(str(DEMO_RUN)).run()
    assert link_by_label(app, "Open saved results")
    assert str(DEMO_RUN) not in link_by_label(app, "Open saved results")["href"]

    follow_link(app, "Back to Saved runs")
    follow_link(app, "Rejudge saved transcripts")
    selector = app.selectbox(key="rejudge_source_known_run")
    assert len(selector.options) == 2
    selector.set_value(selector.options[1]).run()
    assert "gpt-4o-mini" in rendered_text(app)
    assert "gpt-5.6-terra" in rendered_text(app)
    follow_link(app, "Continue to transcript selection")
    assert app.header[0].value == "Rejudge saved transcripts"
    assert "**SOURCE**" in [item.value for item in app.markdown]
    assert "**CURRENT JUDGE**" in [item.value for item in app.markdown]
    assert app.multiselect(key="rejudge_scenario_ids").value == []
    assert "**0 target calls.**" in [item.value for item in app.markdown]


def test_configuration_shows_roles_and_engine_owned_selection_counts():
    app = open_local()

    assert not app.exception and not app.error
    captions = [item.value for item in app.caption]
    markdown = [item.value for item in app.markdown]
    assert "**TARGET — LOADED FROM CONFIGURATION**" in markdown
    assert "**JUDGE — LOADED FROM CONFIGURATION**" in markdown
    assert "Model being evaluated" in captions
    assert "Model assessing the conversations" in captions
    assert "Provider: fixture" in markdown
    assert "Model: demo-relational-sycophancy-v1" in markdown
    assert "Model: demo-relational-sycophancy-judge-v1" in markdown
    assert markdown.count("Execution: Fixture") == 2
    assert any("no external api calls are required" in item.value.lower() for item in app.info)
    assert any("Scenario pack v0.1 · Rubric v0.2 · Judge prompt v0.1" in item for item in captions)
    assert app.text_input(key="runtime_config_path").label == (
        "Local YAML file to load (select a path here; edit its contents outside this app)"
    )
    assert app.button(key="reload_runtime_config").label == "Reload YAML"
    assert str((ROOT / "runtime.fixture.yaml").resolve()) in [item.value for item in app.code]
    assert "Model settings are loaded from a YAML file in your local project." in rendered_text(app)
    assert "Choose scenarios in this UI. Change model settings in YAML." in rendered_text(app)
    assert "Loading and reviewing the configuration makes no model calls." in rendered_text(app)
    assert app.subheader[-1].value == "Scenario selection — choose here"
    assert "Judge prompt version: 0.1" in markdown
    assert "Rubric version: 0.2" in markdown
    assert app.selectbox(key="scenario_selection_mode").options == [
        "Quick check — 3 scenarios", "Development check — 10 scenarios",
        "Full evaluation — 20 scenarios", "Custom — choose scenarios",
    ]
    assert "**3 of 20 scenarios will run.**" in markdown
    assert not any(button.label == "Select all" for button in app.button)

    app.selectbox(key="scenario_selection_mode").set_value("custom").run()
    assert app.button(key="review_evaluation").disabled
    assert [item.value for item in app.warning] == ["Choose at least one scenario."]
    app.multiselect(key="custom_scenario_ids").set_value(["RS-013", "RS-002"]).run()
    assert not app.button(key="review_evaluation").disabled
    assert "**2 of 20 scenarios will run.**" in [item.value for item in app.markdown]
    scope = next(item for item in app.expander if item.label == "View selected scenarios")
    assert [item.value for item in scope.markdown] == [
        "Delayed Reply · RS-002", "Boundary Based on Impact · RS-013",
    ]


@pytest.mark.parametrize("mode,count", [
    ("quick", 3), ("development", 10), ("full", 20),
])
def test_predefined_selection_displays_actual_count(mode, count):
    app = open_local()
    app.selectbox(key="scenario_selection_mode").set_value(mode).run()
    assert f"**{count} of 20 scenarios will run.**" in [item.value for item in app.markdown]


def test_configure_and_review_do_not_call_models(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Configure and Review must not call a model")

    monkeypatch.setattr("psych_eval.integrations.fixture_target.FixtureTarget.respond", blocked)
    monkeypatch.setattr("psych_eval.integrations.fixture_judge.FixtureJudge.assess", blocked)

    app = open_local()
    app.button(key="review_evaluation").click().run()

    assert not app.exception and not app.error
    assert app.header[0].value == "Review run"
    assert {item.value for item in app.subheader} >= {
        "Configuration", "Scope", "Execution",
    }
    assert str((ROOT / "runtime.fixture.yaml").resolve()) in [item.value for item in app.code]
    markdown = [item.value for item in app.markdown]
    assert "Planned initial target calls: 12 (four assistant responses per completed scenario)." in markdown
    assert "Planned initial judge calls: 3 (one per completed transcript; fewer if a conversation does not complete)." in markdown
    assert "Target retry budget: 1 per failed turn" in markdown
    assert "Judge retry budget: 0 per assessment" in markdown
    assert "Reviewing this configuration makes no API calls." in [
        item.value for item in app.markdown
    ]


def test_openai_example_replaces_fixture_labels_in_configure_and_review(monkeypatch):
    """The active runtime file, not the bundled example run, owns live labels."""
    secret = "PRIVATE_OPENAI_KEY_MUST_NOT_RENDER"
    monkeypatch.setenv("OPENAI_TARGET_API_KEY", secret)
    monkeypatch.setenv("OPENAI_JUDGE_API_KEY", secret)

    configured = []

    def validate_without_inference(runtime):
        configured.append(runtime)
        return object(), object()

    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_integrations",
        validate_without_inference,
    )

    app = open_local()
    fixture_markdown = [item.value for item in app.markdown]
    assert fixture_markdown.count("Provider: fixture") == 2

    app.text_input(key="runtime_config_path").set_value(
        str(ROOT / "integrations/openai/example.yaml")
    ).run()
    configure_markdown = [item.value for item in app.markdown]
    assert configure_markdown.count("Provider: openai") == 2
    assert configure_markdown.count("Model: gpt-4o-mini") == 1
    assert configure_markdown.count("Model: gpt-5.6-terra") == 1
    assert "Provider: fixture" not in configure_markdown
    assert secret not in str(app)

    app.button(key="review_evaluation").click().run()
    review_markdown = [item.value for item in app.markdown]
    assert not app.exception and not app.error
    assert app.header[0].value == "Review run"
    assert review_markdown.count("Provider: openai") == 2
    assert review_markdown.count("Model: gpt-4o-mini") == 1
    assert review_markdown.count("Model: gpt-5.6-terra") == 1
    assert "Target: openai/gpt-4o-mini" in review_markdown
    assert "Judge: openai/gpt-5.6-terra" in review_markdown
    assert "Provider: fixture" not in review_markdown
    assert secret not in str(app)
    assert len(configured) == 2


def test_live_review_marks_the_api_boundary_and_dynamic_count(tmp_path, monkeypatch):
    config = tmp_path / "live.yaml"
    config.write_text("""\
target:
  integration: openai
  config:
    provider: openai
    model: target-model
    system_prompt: ""
    sampling:
      temperature: 0.0
      max_output_tokens: 256
  options:
    api_key_env: TEST_TARGET_API_KEY
judge:
  integration: openai
  config:
    mode: live
    provider: openai
    model: judge-model
    prompt_version: "0.2"
  options:
    api_key_env: TEST_JUDGE_API_KEY
target_max_retries: 0
judge_max_retries: 0
""")
    monkeypatch.setattr(
        "psych_eval.integrations.workflow.configure_integrations",
        lambda runtime: (object(), object()),
    )

    app = open_local()
    app.text_input(key="runtime_config_path").set_value(str(config)).run()
    configure_markdown = [item.value for item in app.markdown]
    assert "Model: target-model" in configure_markdown
    assert "Model: judge-model" in configure_markdown
    assert configure_markdown.count("Execution: Live API") == 2
    assert any("Configuration loaded. No API calls have been made" in item.value for item in app.info)
    assert [item.value for item in app.warning] == ["API credentials not configured."]
    assert "verified" not in str(app).lower()
    monkeypatch.setenv("TEST_TARGET_API_KEY", "test-only")
    monkeypatch.setenv("TEST_JUDGE_API_KEY", "test-only")
    app.run()
    assert any("API credentials found in the environment" in item.value for item in app.caption)
    assert any("have not been verified" in item.value for item in app.caption)
    app.selectbox(key="scenario_selection_mode").set_value("development").run()
    app.button(key="review_evaluation").click().run()

    assert not app.exception and not app.error
    markdown = [item.value for item in app.markdown]
    assert "**Execution mode:** Live API" in markdown
    assert "Target: openai/target-model" in markdown
    assert "Judge: openai/judge-model" in markdown
    assert app.button(key="run_evaluation").label == "Run evaluation · 10 scenarios"
    warning = app.warning[0].value
    assert "Configuring and reviewing this run makes no API calls" in warning
    assert "Clicking Run evaluation starts calls" in warning
    assert "Charges may apply" in warning


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


def test_saved_report_remains_available_without_initializing_integrations(monkeypatch):
    from psych_eval.runs import load_run as canonical_load_run

    def blocked(*args, **kwargs):
        raise AssertionError("report inspection must not initialize or call adapters")

    verified = []

    def tracked_load(path, *, verify_references=False):
        verified.append(verify_references)
        return canonical_load_run(path, verify_references=verify_references)

    monkeypatch.setattr("psych_eval.integrations.workflow.configure_integrations", blocked)
    monkeypatch.setattr("psych_eval.integrations.fixture_target.FixtureTarget.respond", blocked)
    monkeypatch.setattr("psych_eval.integrations.fixture_judge.FixtureJudge.assess", blocked)
    monkeypatch.setattr("psych_eval.run_presentation.load_run", tracked_load)

    app = open_saved()
    assert not app.exception and not app.error
    assert app.header[0].value == "View saved results"
    assert app.text_input(key="view_run_path_input")

    verified.clear()
    app.text_input(key="view_run_path_input").set_value(str(DEMO_RUN)).run()
    follow_link(app, "Open saved results")

    assert not app.exception and not app.error
    assert app.header[0].value == "Evaluation results"
    assert link_by_label(app, "Back to Saved runs")
    assert verified and all(verified)


def test_fixture_quick_workflow_runs_only_after_confirmation_and_loads_persisted_report(tmp_path, monkeypatch):
    from psych_eval.integrations.workflow import execute_evaluation as canonical_execute

    executions = []

    def tracked_execute(*args, **kwargs):
        executions.append((args, kwargs))
        return canonical_execute(*args, **kwargs)

    monkeypatch.setattr("psych_eval.integrations.workflow.execute_evaluation", tracked_execute)
    output = tmp_path / "quick-run"
    app = open_local()
    app.text_input(key="runtime_config_path").set_value(str(ROOT / "runtime.fixture.yaml"))
    app.text_input(key="output_directory_input").set_value(str(output))
    assert not output.exists()

    app.button(key="review_evaluation").click().run()
    retain_page(app, "run-review")
    assert not app.exception and not app.error and not output.exists()
    assert app.header[0].value == "Review run"
    assert app.button(key="run_evaluation").label == "Run evaluation · 3 scenarios"
    markdown = [item.value for item in app.markdown]
    assert "**Scenarios:** Quick check · 3 of 20 scenarios" in markdown
    assert "Delayed Reply · RS-002" in markdown
    assert "Counterevidence Arrives · RS-008" in markdown
    assert "Boundary Based on Impact · RS-013" in markdown
    assert "**Execution mode:** Fixture / pre-generated behavior" in markdown
    assert any("no external API calls" in item.value for item in app.info)

    app.button(key="run_evaluation").click().run()
    assert not app.exception and not app.error
    assert len(executions) == 1
    assert app.header[0].value == "Evaluation results"
    run_path = output / "run.json"
    run = load_run(run_path, verify_references=True)
    assert run.selection.selection_mode == "quick"
    assert run.selection.selected_scenario_ids == ["RS-002", "RS-008", "RS-013"]
    assert [entry.scenario_id for entry in run.scenarios] == run.selection.selected_scenario_ids
    assert {item.label: item.value for item in app.metric} == {
        "Scenarios evaluated": str(run.evaluation_summary.assessed),
    }
    assert app.warning and app.warning[0].value == "Some scenarios could not be fully evaluated."
    original = run_path.read_bytes()
    app.run()
    assert len(executions) == 1
    assert run_path.read_bytes() == original

    reopened = open_saved()
    reopened.text_input(key="view_run_path_input").set_value(str(run_path)).run()
    follow_link(reopened, "Open saved results")
    assert not reopened.exception and not reopened.error
    assert len(executions) == 1
    assert reopened.header[0].value == "Evaluation results"
    assert run_path.read_bytes() == original


def test_saved_transcript_rejudge_requires_review_calls_only_judge_and_does_not_repeat(
    tmp_path, monkeypatch,
):
    from psych_eval.integrations.workflow import execute_evaluation, prepare_evaluation
    from psych_eval.selection import SelectionRequest

    source = tmp_path / "source"
    source_review = prepare_evaluation(
        ROOT / "runtime.fixture.yaml", SelectionRequest(mode="quick"),
    )
    execute_evaluation(source_review, source)
    protected = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    judge_calls = []

    from psych_eval.integrations.pack_fixtures import PackJudge
    canonical_assess = PackJudge.assess

    def tracked_assess(self, request, *, config):
        judge_calls.append(request.scenario_id)
        return canonical_assess(self, request, config=config)

    def target_forbidden(*args, **kwargs):
        raise AssertionError("rejudge must not execute a target")

    monkeypatch.setattr(PackJudge, "assess", tracked_assess)
    monkeypatch.setattr(
        "psych_eval.integrations.fixture_target.FixtureTarget.respond", target_forbidden,
    )
    monkeypatch.setattr("psych_eval.integrations.runtime._fixture_target", target_forbidden)
    destination = tmp_path / "rejudged"
    retry_config = tmp_path / "runtime.fixture.retries.yaml"
    retry_config.write_text(
        (ROOT / "runtime.fixture.yaml").read_text().replace(
            "judge_max_retries: 0", "judge_max_retries: 2",
        )
    )
    app = open_saved()
    app.text_input(key="view_run_path_input").set_value(str(source / "run.json")).run()
    follow_link(app, "Open saved results")
    follow_link(app, "Rejudge saved transcripts")

    assert not app.exception and not app.error and judge_calls == []
    assert app.header[0].value == "Rejudge saved transcripts"
    assert app.multiselect(key="rejudge_scenario_ids").value == []
    assert app.button(key="review_rejudge").disabled
    app.text_input(key="rejudge_config_path").set_value(
        str(retry_config)
    ).run()
    app.multiselect(key="rejudge_scenario_ids").set_value(
        ["RS-002", "RS-008", "RS-013"],
    ).run()
    app.text_input(key="rejudge_destination_input").set_value(str(destination)).run()
    assert judge_calls == []
    app.button(key="review_rejudge").click().run()
    retain_page(app, "rejudge-review")

    assert not app.exception and not app.error and judge_calls == []
    assert app.header[0].value == "Review judge-only run"
    markdown = [item.value for item in app.markdown]
    assert "**0 target calls; 3 initial judge calls.**" in markdown
    assert "A new result will be created. The original run and its artifacts remain unchanged." in markdown
    assert "Retry budget: 2" in markdown
    assert any("up to 2 retries per selected transcript" in item.value for item in app.warning)
    assert app.button(key="run_rejudge").label == "Run judge only"
    app.button(key="run_rejudge").click().run()

    assert not app.exception and not app.error
    assert judge_calls == ["RS-002", "RS-008", "RS-013"]
    assert app.header[0].value == "Evaluation results"
    assert load_run(destination / "run.json", verify_references=True)
    assert protected == {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    app.run()
    assert judge_calls == ["RS-002", "RS-008", "RS-013"]

    reopened = open_saved()
    reopened.text_input(key="view_run_path_input").set_value(
        str(destination / "run.json")
    ).run()
    follow_link(reopened, "Open saved results")
    assert not reopened.exception and not reopened.error
    assert judge_calls == ["RS-002", "RS-008", "RS-013"]

def test_execution_failure_is_visible_without_a_success_report(tmp_path, monkeypatch):
    output = tmp_path / "failed-run"
    app = open_local()
    app.text_input(key="runtime_config_path").set_value(str(ROOT / "runtime.fixture.yaml"))
    app.text_input(key="output_directory_input").set_value(str(output))
    app.button(key="review_evaluation").click().run()
    retain_page(app, "run-review")

    def fail(*args, **kwargs):
        raise RuntimeError("private provider detail")

    monkeypatch.setattr("psych_eval.integrations.workflow.execute_evaluation", fail)
    app.button(key="run_evaluation").click().run()
    assert not app.exception
    assert app.error[0].value == "Evaluation did not complete because of an unexpected local execution error."
    assert "private provider detail" not in str(app)
    assert not any(header.value == "Evaluation results" for header in app.header)
    assert not (output / "run.json").exists()
