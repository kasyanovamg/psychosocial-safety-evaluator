"""Durable, non-sensitive Streamlit navigation and refresh behavior."""

from pathlib import Path
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlparse

import pytest

pytest.importorskip("streamlit", reason="Install the ui extra to run application smoke tests")
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "streamlit_app.py"
DEMO_RUN = ROOT / "demo/runs/relational-sycophancy-reference-v1/run.json"
PATH_BY_ROUTE = {
    ("home", None, False): "",
    ("demo", None, False): "demo",
    ("demo", None, True): "demo-scenario",
    ("run", "configure", False): "run",
    ("run", "review", False): "run-review",
    ("saved", None, False): "saved",
    ("results", None, False): "results",
    ("results", None, True): "scenario",
    ("rejudge", "configure", False): "rejudge",
    ("rejudge", "review", False): "rejudge-review",
}


@pytest.fixture(autouse=True)
def isolated_navigation(monkeypatch, tmp_path):
    calls = []

    def blocked_execution(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("navigation and refresh must never execute inference")

    def blocked_network(*args, **kwargs):
        raise AssertionError("navigation must not access the network")

    monkeypatch.setenv("PSYCH_EVAL_RUN_REGISTRY", str(tmp_path / "run-registry.json"))
    monkeypatch.setattr("psych_eval.integrations.workflow.execute_evaluation", blocked_execution)
    monkeypatch.setattr("psych_eval.integrations.workflow.execute_rejudge", blocked_execution)
    monkeypatch.setattr(socket.socket, "connect", blocked_network)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_network)
    monkeypatch.setattr(socket, "getaddrinfo", blocked_network)
    return calls


def retain_page(app: AppTest, url_path: str) -> AppTest:
    app._page_hash = next(
        page_hash for page_hash, page in app._registered_pages.items()
        if page["url_pathname"] == url_path
    )
    return app


def app_at(query: dict[str, str] | None = None) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    if not query:
        return app
    view = query.get("view", "home")
    url_path = PATH_BY_ROUTE[(view, query.get("step"), "scenario" in query)]
    retain_page(app, url_path)
    app.query_params.update(query)
    return app.run()


def query_of(app: AppTest) -> dict[str, str]:
    return {
        key: value[-1] if isinstance(value, list) else value
        for key, value in app.query_params.items()
    }


def route_links(app: AppTest) -> list[dict[str, str]]:
    links = []

    class LinkParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "a" and values.get("data-psych-route-link") == "true":
                links.append(values)

    for element in app.get("html"):
        LinkParser().feed(element.proto.body)
    return links


def link_by_label(app: AppTest, label: str):
    return next(link for link in route_links(app) if link["data-route-label"] == label)


def follow_link(app: AppTest, label: str) -> AppTest:
    """Model the native page link; AppTest cannot click page-link elements."""
    link = link_by_label(app, label)
    parsed = urlparse(link["href"])
    retain_page(app, parsed.path.strip("/"))
    app.query_params.clear()
    app.query_params.update(dict(parse_qsl(parsed.query)))
    return app.run()


def refreshed(app: AppTest) -> AppTest:
    return app_at(query_of(app))


def history_destination(app: AppTest, url_path: str, query: dict[str, str]) -> AppTest:
    """Model the pathname/query pair delivered by browser Back or Forward."""
    retain_page(app, url_path)
    app.query_params.clear()
    app.query_params.update(query)
    return app.run()


def open_saved_result() -> AppTest:
    app = app_at()
    follow_link(app, "Open saved run")
    app.text_input(key="saved_run_path_input").set_value(str(DEMO_RUN)).run()
    return follow_link(app, "Open saved results")


def test_home_and_top_level_routes_are_url_backed():
    home = app_at()
    assert home.header[0].value == "Choose a workflow"
    assert query_of(home) == {}

    follow_link(home, "Run evaluation")
    assert home.header[0].value == "Configure evaluation"
    assert query_of(home) == {"view": "run", "step": "configure"}

    saved = app_at({"view": "saved"})
    assert saved.header[0].value == "Open saved run"
    assert query_of(saved) == {"view": "saved"}


def test_demo_refresh_and_browser_history_restore_report_without_inference(isolated_navigation):
    app = app_at({"view": "demo"})
    assert app.header[0].value == "Demo results"
    assert query_of(app) == {"view": "demo"}
    assert not any(button.key in {"start_rejudge", "results_new_run"} for button in app.button)

    again = refreshed(app)
    assert again.header[0].value == "Demo results"
    assert isolated_navigation == []

    follow_link(again, "View details")
    detail_query = query_of(again)
    assert detail_query == {"view": "demo", "scenario": "RS-001"}
    assert again.header[0].value == "RS-001 — Excluded by Friends"

    detail_refresh = refreshed(again)
    assert detail_refresh.header[0].value == "RS-001 — Excluded by Friends"
    detail_refresh.query_params.clear()
    detail_refresh.query_params.update({"view": "demo"})
    detail_refresh.run()
    assert detail_refresh.header[0].value == "Demo results"
    assert isolated_navigation == []


def test_demo_detail_parent_action_restores_demo_report():
    app = app_at({"view": "demo", "scenario": "RS-001"})
    follow_link(app, "Back to demo results")
    assert app.header[0].value == "Demo results"
    assert query_of(app) == {"view": "demo"}


def test_distinct_path_entries_restore_demo_back_and_forward_without_normalization(
    isolated_navigation,
):
    app = app_at()
    follow_link(app, "View demo results")
    follow_link(app, "View details")
    assert query_of(app) == {"view": "demo", "scenario": "RS-001"}

    history_destination(app, "demo", {"view": "demo"})
    assert app.header[0].value == "Demo results"
    assert query_of(app) == {"view": "demo"}
    history_destination(app, "", {})
    assert app.header[0].value == "Choose a workflow"
    assert query_of(app) == {}

    history_destination(app, "demo", {"view": "demo"})
    assert app.header[0].value == "Demo results"
    history_destination(app, "demo-scenario", {"view": "demo", "scenario": "RS-001"})
    assert app.header[0].value == "RS-001 — Excluded by Friends"
    assert isolated_navigation == []


def test_saved_result_and_scenario_refresh_use_opaque_identity(isolated_navigation):
    app = open_saved_result()
    query = query_of(app)
    assert query["view"] == "results"
    assert query["origin"] == "saved"
    assert len(query["run"]) == 24
    assert str(DEMO_RUN) not in str(query)

    report_refresh = refreshed(app)
    assert report_refresh.header[0].value == "Evaluation results"
    follow_link(report_refresh, "View details")
    assert query_of(report_refresh)["scenario"] == "RS-001"

    detail_refresh = refreshed(report_refresh)
    assert detail_refresh.header[0].value == "RS-001 — Excluded by Friends"
    follow_link(detail_refresh, "Back to saved results")
    assert detail_refresh.header[0].value == "Evaluation results"
    assert "scenario" not in query_of(detail_refresh)
    assert isolated_navigation == []


def test_saved_history_restores_detail_report_picker_and_home(isolated_navigation):
    app = open_saved_result()
    report_query = query_of(app)
    follow_link(app, "View details")
    detail_query = query_of(app)

    history_destination(app, "results", report_query)
    assert app.header[0].value == "Evaluation results"
    history_destination(app, "saved", {"view": "saved"})
    assert app.header[0].value == "Open saved run"
    history_destination(app, "", {})
    assert app.header[0].value == "Choose a workflow"

    history_destination(app, "saved", {"view": "saved"})
    assert app.header[0].value == "Open saved run"
    history_destination(app, "results", report_query)
    assert app.header[0].value == "Evaluation results"
    history_destination(app, "scenario", detail_query)
    assert app.header[0].value == "RS-001 — Excluded by Friends"
    assert isolated_navigation == []


def test_missing_saved_run_has_recovery_instead_of_home_redirect():
    app = app_at({"view": "results", "run": "a" * 24, "origin": "saved"})
    assert app.header[0].value == "Saved run unavailable"
    assert link_by_label(app, "Choose another saved run")
    assert link_by_label(app, "Home")
    assert query_of(app)["run"] == "a" * 24
    assert not any(header.value == "Choose a workflow" for header in app.header)


def test_review_refresh_cannot_replay_or_retain_execution_permission(isolated_navigation):
    app = app_at({"view": "run", "step": "review"})
    assert app.header[0].value == "Review required again"
    assert link_by_label(app, "Edit configuration")
    assert not any(button.key == "run_evaluation" for button in app.button)
    assert isolated_navigation == []


def test_changed_yaml_invalidates_an_existing_review(tmp_path, isolated_navigation):
    config = tmp_path / "runtime.fixture.yaml"
    config.write_bytes((ROOT / "runtime.fixture.yaml").read_bytes())
    app = app_at({"view": "run", "step": "configure"})
    app.text_input(key="runtime_config_path").set_value(str(config)).run()
    app.button(key="review_evaluation").click().run()
    assert app.header[0].value == "Review run"
    assert app.button(key="run_evaluation")

    config.write_text(config.read_text() + "\n# changed after review\n")
    app.run()
    assert app.header[0].value == "Review run"
    assert any("no longer the reviewed version" in item.value for item in app.warning)
    assert not any(button.key == "run_evaluation" for button in app.button)
    assert isolated_navigation == []


def test_rejudge_configure_route_refreshes_without_target_or_judge_calls(isolated_navigation):
    app = open_saved_result()
    follow_link(app, "Rejudge saved transcripts")
    assert app.header[0].value == "Rejudge saved transcripts"
    assert query_of(app)["view"] == "rejudge"

    review_query = query_of(app)
    review_query["step"] = "review"
    again = app_at(review_query)
    assert again.header[0].value == "Rejudge saved transcripts"
    assert query_of(again)["step"] == "review"
    assert any("review was session-only" in item.value for item in again.info)
    assert isolated_navigation == []


def test_query_state_is_allowlisted_and_never_contains_credentials(monkeypatch):
    secret = "SHOULD_NEVER_SURVIVE_IN_URL"
    monkeypatch.setenv("OPENAI_JUDGE_API_KEY", secret)
    app = app_at()
    link = link_by_label(app, "View demo results")
    assert dict(parse_qsl(urlparse(link["href"]).query)) == {"view": "demo"}
    assert secret not in link["href"]


def test_invalid_scenario_has_contextual_recovery():
    app = app_at({"view": "demo", "scenario": "RS-999"})
    assert app.header[0].value == "Scenario unavailable"
    assert link_by_label(app, "Back to demo results")
