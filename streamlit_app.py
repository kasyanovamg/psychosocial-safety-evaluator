"""Local configure, select, run, and persisted-artifact report workflow."""

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from inspect import signature
from pathlib import Path
import json
import os
import re
from urllib.parse import urlencode

import streamlit as st

from psych_eval.presentation import ArtifactLoadError, EvaluationView
from psych_eval.run_presentation import RunView, load_run_view
from psych_eval.integrations.runtime import IntegrationConfigError, load_runtime_config
from psych_eval.judge_payload import DEFAULT_JUDGE_PROMPT_VERSION, RUBRIC_VERSION
from psych_eval.selection import SelectionRequest, resolve_selection, selection_catalog
from psych_eval.suite import SuiteProgress, scenario_catalog
from psych_eval.integrations.workflow import (
    EvaluationReview, RejudgeReview, completed_saved_scenarios,
    default_rejudge_destination, execute_evaluation, execute_rejudge,
    prepare_evaluation, prepare_rejudge,
)


DEMO_RUN = Path(os.environ.get(
    "PSYCH_EVAL_RUN",
    str(Path(__file__).resolve().parent / "demo/runs/relational-sycophancy-reference-v1/run.json"),
))
VALIDATION_STATUS = "Experimental"
RUN_REGISTRY = Path(os.environ.get(
    "PSYCH_EVAL_RUN_REGISTRY",
    str(Path(__file__).resolve().parent / ".streamlit/run-registry.json"),
))
RUN_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{24}$")
NAV_PAGES: dict[str, st.Page] = {}
NAV_PATHS = {
    "home": "/",
    "demo": "/demo",
    "demo_detail": "/demo-scenario",
    "configure": "/run",
    "review": "/run-review",
    "saved": "/saved",
    "saved_view": "/saved-view",
    "saved_rejudge": "/saved-rejudge",
    "results": "/results",
    "result_detail": "/scenario",
    "rejudge_configure": "/rejudge",
    "rejudge_review": "/rejudge-review",
}

SELECTION_LABELS = {
    "quick": "Quick check",
    "development": "Development check",
    "full": "Full evaluation",
    "custom": "Custom",
}
SEVERITY_LABELS = {
    "0": "🟢 0 — None",
    "1": "🟡 1 — Mild",
    "2": "🟠 2 — Material",
    "3": "🔴 3 — Severe",
}


def literal_text(text: str, *, evidence: bool = False) -> None:
    """Preserve whitespace and literal markup; Streamlit text/code trim it.

    Only escaped text enters this fixed HTML wrapper. No highlighting, scripts,
    links, or artifact-supplied markup/styles are interpreted.
    """
    style = "white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6;"
    if evidence:
        style += " background: #F3F5F7; border-left: 3px solid #405B72; padding: 12px 16px; border-radius: 4px;"
    st.html(f'<div style="{style}">{escape(text)}</div>')


def _route_query(
    view: str, *, run_token: str | None = None, scenario_id: str | None = None,
    step: str | None = None, origin: str | None = None,
) -> dict[str, str]:
    """Return the complete non-sensitive URL state for a logical destination."""
    query = {} if view == "home" else {"view": view}
    if run_token is not None:
        query["run"] = run_token
    if scenario_id is not None:
        query["scenario"] = scenario_id
    if step is not None:
        query["step"] = step
    if origin in {"run", "saved"}:
        query["origin"] = origin
    return query


def _switch_route(
    page: str, view: str, *, run_token: str | None = None,
    scenario_id: str | None = None, step: str | None = None,
    origin: str | None = None,
) -> None:
    """Use a pathname transition so browser history triggers a Streamlit rerun."""
    if not NAV_PAGES:
        NAV_PAGES.update(_build_pages())
    st.switch_page(
        NAV_PAGES[page],
        query_params=_route_query(
            view, run_token=run_token, scenario_id=scenario_id,
            step=step, origin=origin,
        ),
    )


def route_link(
    label: str, page: str, view: str, *, run_token: str | None = None,
    scenario_id: str | None = None, step: str | None = None,
    origin: str | None = None, use_container_width: bool = False,
) -> None:
    """Render a same-tab link whose pathname and safe context form one history entry."""
    query = urlencode(_route_query(
        view, run_token=run_token, scenario_id=scenario_id,
        step=step, origin=origin,
    ))
    href = NAV_PATHS[page] + (f"?{query}" if query else "")
    width = "width: 100%;" if use_container_width else "width: fit-content;"
    st.html(
        '<a data-psych-route-link="true" '
        f'data-route-label="{escape(label, quote=True)}" '
        f'href="{escape(href, quote=True)}" target="_self" '
        'style="display:flex;align-items:center;justify-content:center;'
        f'{width}min-height:2.5rem;padding:.25rem .75rem;box-sizing:border-box;'
        'border:1px solid rgba(49,51,63,.2);border-radius:.5rem;'
        'color:inherit;text-decoration:none;font-weight:400;line-height:1.6;">'
        f'{escape(label)}</a>'
    )


def _read_registry() -> dict[str, str]:
    try:
        value = json.loads(RUN_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        token: path for token, path in value.items()
        if RUN_TOKEN_PATTERN.fullmatch(token)
        and isinstance(path, str) and Path(path).is_absolute()
    }


def register_saved_run(path: str | Path) -> str:
    """Persist a path locally and return a URL-safe opaque identifier for it."""
    resolved = Path(path).expanduser().resolve()
    token = sha256(str(resolved).encode("utf-8")).hexdigest()[:24]
    registry = _read_registry()
    if registry.get(token) != str(resolved):
        registry[token] = str(resolved)
        RUN_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        temporary = RUN_REGISTRY.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(RUN_REGISTRY)
    return token


def resolve_saved_run(token: str | None) -> Path | None:
    if token is None or RUN_TOKEN_PATTERN.fullmatch(token) is None:
        return None
    value = _read_registry().get(token)
    return Path(value) if value is not None else None


def _result_route(scenario_id: str | None = None) -> None:
    context = st.session_state.get("result_context")
    if context == "demo":
        _switch_route(
            "demo_detail" if scenario_id is not None else "demo",
            "demo", scenario_id=scenario_id,
        )
    run_path = st.session_state.get("completed_run_path")
    if run_path is None:
        _switch_route("saved", "saved")
    token = register_saved_run(run_path)
    st.session_state.completed_run_token = token
    _switch_route(
        "result_detail" if scenario_id is not None else "results",
        "results", run_token=token, scenario_id=scenario_id,
        origin=context if context in {"run", "saved"} else "saved",
    )


def navigate(page: str, scenario_id: str | None = None) -> None:
    st.session_state.inspection_page = page
    st.session_state.inspection_scenario = scenario_id
    st.session_state.inspection_navigation = st.session_state.get("inspection_navigation", 0) + 1
    st.session_state.inspection_arrival_pending = True
    if page == "home":
        _switch_route("home", "home")
    elif page == "open_saved":
        _switch_route("saved", "saved")
    elif page == "configure":
        _switch_route("configure", "run", step="configure")
    elif page == "review":
        _switch_route("review", "run", step="review")
    elif page == "results":
        _result_route()
    elif page == "detail":
        _result_route(scenario_id)
    elif page in {"rejudge_configure", "rejudge_review"}:
        run_path = st.session_state.get("completed_run_path")
        if run_path is not None:
            token = register_saved_run(run_path)
            _switch_route(
                page, "rejudge", run_token=token,
                step="review" if page == "rejudge_review" else "configure",
            )


def start_demo() -> None:
    st.session_state.completed_run_path = str(DEMO_RUN)
    st.session_state.result_context = "demo"
    navigate("results")


def start_run() -> None:
    st.session_state.result_context = "run"
    navigate("configure")


def return_home() -> None:
    st.session_state.inspection_page = "home"
    st.session_state.inspection_scenario = None
    st.session_state.pop("evaluation_review", None)
    st.session_state.pop("review_output_directory", None)
    st.session_state.pop("rejudge_review", None)
    st.session_state.inspection_navigation = st.session_state.get("inspection_navigation", 0) + 1
    st.session_state.inspection_arrival_pending = True
    _switch_route("home", "home")


def destination_anchor() -> str:
    # A fresh ID prevents a navigation effect from finding the previous page's
    # heading while Streamlit is still replacing the DOM during a rerun.
    return f"inspection-destination-{st.session_state.get('inspection_navigation', 0)}"


def finish_navigation() -> None:
    """Scroll/focus once per navigation, after the destination has been emitted."""
    if not st.session_state.pop("inspection_arrival_pending", False):
        return
    # Only an internally generated anchor enters this script, never artifact text.
    script = """<script>
    (() => {
        const doc = window.parent.document;
        let frames = 0;
        function arrive() {
            const heading = doc.getElementById("ANCHOR");
            if (!heading) {
                if (++frames < 120) requestAnimationFrame(arrive);
                return;
            }
            heading.style.scrollMarginTop = "4rem";
            heading.setAttribute("tabindex", "-1");
            heading.scrollIntoView({block: "start", behavior: "instant"});
            heading.focus({preventScroll: true});
        }
        requestAnimationFrame(arrive);
    })();
    </script>""".replace("ANCHOR", destination_anchor())
    if "unsafe_allow_javascript" in signature(st.html).parameters:
        st.html(script, unsafe_allow_javascript=True)
    else:
        # Compatibility with the declared Streamlit >=1.50 range, before
        # st.html supported JavaScript. Keep the bridge out of the tab order.
        from streamlit.components.v1 import html

        html(script, height=0, tab_index=-1)


def configuration(run: RunView, *, example: bool = False) -> None:
    st.subheader("Reference configuration" if example else "Evaluation configuration")
    left, right = st.columns(2)
    for column, label, provenance, explanation in (
        (left, "Target", run.model_under_test, "Model being evaluated"),
        (right, "Judge", run.judge, "Model assessing the conversations"),
    ):
        with column, st.container(border=True):
            st.markdown(f"**{label}**")
            st.caption(explanation)
            st.write(f"Provider: {provenance.provider}")
            st.write(f"Model: {provenance.model}")
            st.write("Source: Saved run artifact" if example else f"Execution: {provenance.execution}")


def technical_details(run: RunView, detail: EvaluationView | None = None) -> None:
    with st.expander("Technical details & reproducibility", expanded=False):
        st.caption(run.disclosure)
        st.text(f"Run ID: {run.run_id}")
        st.text(f"Created: {run.created_at}")
        st.text(f"Suite: {run.suite_id} · Version {run.suite_version}")
        for label, provenance in (("Model under test", run.model_under_test), ("Judge model", run.judge)):
            st.markdown(f"**{label}**")
            st.text(f"Execution: {provenance.execution} · Provider: {provenance.provider}")
            literal_text(provenance.model)
        st.text(f"Rubric: {run.rubric_version} · Evaluator: {run.evaluator_version}")
        st.text(
            f"Target temperature: {run.sampling_temperature} · "
            f"Maximum output tokens: {run.sampling_max_output_tokens}"
        )
        st.text(
            f"Judge prompt: {run.judge_prompt_version or 'Unspecified'} · "
            f"Temperature: {run.judge_temperature if run.judge_temperature is not None else 'Not set'} · "
            f"Reasoning effort: {run.judge_reasoning_effort or 'Not set'} · "
            f"Maximum output tokens: {run.judge_max_output_tokens or 'Not set'}"
        )
        st.text("Execution counts: " + ", ".join(f"{label}: {count}" for label, count in run.execution_counts))
        st.text("Evaluation counts: " + ", ".join(f"{label}: {count}" for label, count in run.evaluation_counts))
        if run.coverage is not None:
            coverage = run.coverage
            st.text(
                f"Selection: {coverage.selection_mode} · Selected: "
                f"{coverage.selected_count} / {coverage.full_pack_total} · "
                f"Assessed: {coverage.valid_assessed_count}"
            )
        if detail is not None:
            st.text(f"Scenario: {detail.scenario_id} · Version {detail.scenario_version}")
            st.text(f"Evaluation ID: {detail.evaluation_id}")
            st.text(f"Transcript / run ID: {detail.transcript_run_id}")
            st.text(f"Execution: {detail.execution_status} · Evaluation: {detail.evaluation_status}")
            st.text(f"Maximum retries: {detail.max_retries} · Retries used: {detail.retry_count}")
            st.text(f"Recovery: {detail.recovery} · Persistence: {detail.persistence}")
        st.caption("Loaded from a canonical run bundle with all referenced artifacts verified. Detailed JSON retains exact judge inputs and outputs.")


def evaluation_problems(run: RunView) -> list[tuple[str, int]]:
    """Expose canonical failure counts without deriving new evaluation semantics."""
    execution = dict(run.execution_counts)
    evaluation = dict(run.evaluation_counts)
    problems = (
        ("Could not assess", evaluation.get("Cannot assess", 0)),
        ("Evaluation failures", evaluation.get("Failed", 0)),
        ("Execution failures", execution.get("Failed", 0)),
        ("Not run", evaluation.get("Not run", 0)),
    )
    return [(label, count) for label, count in problems if count]


def breadcrumb(*parts: str) -> None:
    st.caption(" / ".join(parts))


def results(run: RunView, *, demo: bool) -> None:
    context = st.session_state.get("result_context", "saved")
    if demo:
        breadcrumb("Home", "Demo")
        route_link("Home", "home", "home")
    else:
        parent = "Run evaluation" if context == "run" else "Saved runs"
        breadcrumb("Home", parent, run.run_id)
        token = register_saved_run(st.session_state.completed_run_path)
        home, new_run, rejudge = st.columns(3)
        with home:
            if context == "saved":
                route_link(
                    "Back to Saved runs", "saved", "saved", use_container_width=True,
                )
            else:
                route_link("Home", "home", "home", use_container_width=True)
        with new_run:
            route_link(
                "Run evaluation", "configure", "run", step="configure",
                use_container_width=True,
            )
        with rejudge:
            route_link(
                "Rejudge saved transcripts", "rejudge_configure", "rejudge",
                run_token=token, step="configure", use_container_width=True,
            )
    st.header("Demo results" if demo else "Evaluation results", anchor=destination_anchor())
    if demo:
        st.info(
            "Saved example run · 20 scenarios · Viewing and exploring these artifacts makes no model calls."
        )
        st.caption(
            "Validation status: Experimental · Manually reviewed development reference; not a statistical accuracy estimate."
        )
    st.subheader(run.construct)
    st.caption(f"Suite {run.suite_id} · Version {run.suite_version}")
    st.caption(f"Rubric v{run.rubric_version}")
    if demo:
        st.caption(
            f"Judge prompt v{run.judge_prompt_version or 'Unspecified'} · "
            f"Reasoning effort: {run.judge_reasoning_effort or 'Not set'}"
        )
    st.info(run.disclosure)
    configuration(run, example=demo)
    st.metric("Scenarios evaluated", run.assessed)
    problems = evaluation_problems(run)
    if problems:
        st.warning("Some scenarios could not be fully evaluated.")
        st.write(" · ".join(f"{label}: {count}" for label, count in problems))
    st.subheader("Severity distribution")
    st.caption(
        "Severity reflects Relational Sycophancy in the model's responses, "
        "not the seriousness of the scenario itself."
    )
    st.table([{"Severity": severity_label(item.label), "Scenarios": item.scenario_count}
              for item in run.severity_distribution])
    st.subheader("Observed mechanisms")
    st.table([{"Mechanism": item.label, "Findings": item.finding_count, "Scenarios": item.scenario_count} for item in run.mechanisms])
    if run.technical_failures:
        st.subheader("Technical failures")
        for failure in run.technical_failures:
            attempt = f" · Judge attempt {failure.judge_attempt_index}" if failure.judge_attempt_index else ""
            st.error(f"{failure.scenario_id} · {failure.stage}{attempt}")
            literal_text(failure.detail)
    st.divider()
    st.header("Scenario results")
    for row in run.scenarios:
        with st.container(border=True):
            st.subheader(row.heading)
            findings = f"{row.finding_count} findings" if row.finding_count is not None else "No assessed finding count"
            st.write(f"{scenario_severity_label(row.severity_display)} · {findings}")
            st.caption(f"Execution: {row.execution_status} · Evaluation: {row.evaluation_status}")
            if row.has_details:
                if demo:
                    route_link(
                        "View details", "demo_detail", "demo", scenario_id=row.scenario_id,
                    )
                else:
                    route_link(
                        "View details", "result_detail", "results",
                        run_token=register_saved_run(st.session_state.completed_run_path),
                        scenario_id=row.scenario_id, origin=context,
                    )
            else:
                st.button("View details", key=f"details_{row.scenario_id}", disabled=True)
    technical_details(run)


def scenario_detail(run: RunView, view: EvaluationView, *, demo: bool) -> None:
    context = st.session_state.get("result_context", "saved")
    parent = "Demo" if demo else ("Run evaluation" if context == "run" else "Saved runs")
    back_label = (
        "Back to demo results" if demo else
        ("Back to run results" if context == "run" else "Back to saved results")
    )
    breadcrumb("Home", parent, run.run_id, view.scenario_heading)
    if demo:
        route_link(back_label, "demo", "demo")
    else:
        route_link(
            back_label, "results", "results",
            run_token=register_saved_run(st.session_state.completed_run_path), origin=context,
        )
    st.header(view.scenario_heading, anchor=destination_anchor())
    st.caption(f"{view.construct} · Scenario v{view.scenario_version} · Execution: {view.execution_status} · Evaluation: {view.evaluation_status}")
    st.info(
        "Saved example result · Viewing this artifact makes no model calls."
        if demo else run.disclosure
    )
    severity, count = st.columns(2)
    severity.metric("Scenario severity", scenario_severity_label(view.severity_display))
    count.metric("Findings", view.finding_count)
    if view.execution_status != "Completed":
        st.caption("This incomplete execution is excluded from the run’s severity and mechanism distribution.")
    if view.zero_rationale is not None:
        literal_text(view.zero_rationale)
    if view.cannot_assess_reason is not None:
        literal_text(view.cannot_assess_reason)
    st.header("Findings")
    st.caption(
        "Findings identify individual assistant responses. Scenario severity reflects the highest "
        "severity finding in that conversation."
    )
    for finding in view.findings:
        with st.container(border=True):
            st.subheader(f"{finding.turn_id} · {severity_label(finding.severity_display)}")
            st.markdown("**Mechanisms**")
            st.text(" · ".join(finding.mechanisms))
            st.markdown("**Relational proposition**")
            literal_text(finding.relational_proposition)
            st.markdown("**Evidence · exact assistant excerpts**")
            for evidence in finding.evidence:
                literal_text(evidence, evidence=True)
            st.markdown("**Rationale**")
            literal_text(finding.rationale)
            if finding.severity_3_basis is not None:
                st.caption(f"Severity-3 basis: {finding.severity_3_basis}")
    if not view.findings:
        st.caption("No positive findings are recorded in this artifact.")
    with st.expander("View full conversation", expanded=False):
        for turn in view.turns:
            with st.chat_message(turn.role):
                st.markdown(f"**{turn.role_label} · {turn.turn_id}**")
                for finding in turn.findings:
                    st.caption(f"Flagged · {severity_label(finding.severity_display)}")
                literal_text(turn.content)
    technical_details(run, view)


def product_introduction(*, home: bool) -> None:
    if not home:
        st.write(
            "A local, open-source harness for evaluating conversational AI through controlled multi-turn scenarios."
        )
        st.markdown("**V1 evaluation: Relational Sycophancy**")
        st.write(
            "Relational sycophancy happens when an AI treats a user's uncertain interpretation of another "
            "person, relationship, motive, or social situation as more certain than the evidence supports."
        )
        st.write(f"**Validation status: {VALIDATION_STATUS}**")
        return

    st.write(
        "Evaluate how a chatbot responds to uncertain interpretations of people and relationships."
    )
    st.write(
        "Relational sycophancy happens when an AI treats a user's uncertain interpretation of another "
        "person, relationship, motive, or social situation as more certain than the evidence supports."
    )
    st.markdown(
        '**Healthy empathy:** “I can see why that hurt.”  \n'
        '**Relational sycophancy:** “You’re right — they clearly don’t respect you.”'
    )
    st.write("Empathy can acknowledge a feeling without endorsing an uncertain conclusion.")
    st.write("Unsupported endorsement can strengthen assumptions that shape relationship decisions.")
    st.caption(
        f"{VALIDATION_STATUS} evaluator · Findings require interpretation; this is not a global safety score."
    )


def landing() -> None:
    breadcrumb("Home")
    st.header("Choose a workflow", anchor=destination_anchor())
    cards = (
        ("run", "Run evaluation", "Use your local model configuration to generate and evaluate new conversations.",
         "Run evaluation", "configure", "run", {"step": "configure"}),
        ("demo", "View demo", "Explore a real evaluation across all 20 V1 scenarios. No model calls.",
         "View demo", "demo", "demo", {}),
        ("saved", "Saved runs", "View saved reports or rejudge existing conversations with your current judge.",
         "Saved runs", "saved", "saved", {}),
    )
    # Keep the original minimum footprint, but let the tallest card size the row.
    # Fixed-height Streamlit containers introduce scrollports that clip headings.
    st.html("""
        <style>
        .st-key-landing_card_run,
        .st-key-landing_card_demo,
        .st-key-landing_card_saved { min-height: 250px; }
        </style>
    """)
    for column, card in zip(st.columns(3), cards):
        slug, title, description, label, page, view, route_options = card
        with column, st.container(
            border=True, height="stretch", key=f"landing_card_{slug}",
            vertical_alignment="distribute",
        ):
            # Distribute these two groups: natural-height copy above, action below.
            with st.container(border=False):
                st.subheader(title)
                st.caption(description)
            route_link(
                label, page, view, use_container_width=True, **route_options,
            )
    st.caption(
        "V1 reference: target gpt-4o-mini · judge gpt-5.6-terra · Rubric v0.2 · "
        "judge prompt v0.3 · reasoning effort medium."
    )


def severity_label(value: str) -> str:
    return SEVERITY_LABELS.get(str(value).strip().split(maxsplit=1)[0], str(value))


def scenario_severity_label(value: str) -> str:
    key = str(value).strip().split(maxsplit=1)[0]
    label = severity_label(value)
    return f"{label} Relational Sycophancy" if key in SEVERITY_LABELS else label


def execution_label(provider: str, mode: str | None = None) -> str:
    return "Fixture" if provider == "fixture" or mode == "fixture" else "Live API"


def resolved_model_cards(
    target, judge, *, target_integration: str, judge_integration: str,
) -> None:
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown("**TARGET — LOADED FROM CONFIGURATION**")
        st.caption("Model being evaluated")
        st.write(f"Integration: {target_integration}")
        st.write(f"Provider: {target.provider}")
        st.write(f"Model: {target.model}")
        st.write(f"System prompt: {'Configured' if target.system_prompt else 'Not set'}")
        st.write(f"Temperature: {target.sampling.temperature}")
        st.write(f"Maximum output tokens: {target.sampling.max_output_tokens}")
        st.write(f"Execution: {execution_label(target.provider)}")
    with right, st.container(border=True):
        sampling = judge.sampling
        reasoning = (
            sampling.reasoning.effort
            if sampling is not None and sampling.reasoning is not None else "Not set"
        )
        st.markdown("**JUDGE — LOADED FROM CONFIGURATION**")
        st.caption("Model assessing the conversations")
        st.write(f"Integration: {judge_integration}")
        st.write(f"Provider: {judge.provider}")
        st.write(f"Model: {judge.model}")
        st.write(f"Judge prompt version: {judge.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION}")
        st.write(f"Rubric version: {RUBRIC_VERSION}")
        st.write(
            f"Temperature: "
            f"{sampling.temperature if sampling is not None and sampling.temperature is not None else 'Not set'}"
        )
        st.write(f"Reasoning effort: {reasoning}")
        st.write(
            f"Maximum output tokens: {sampling.max_output_tokens if sampling is not None else 'Not set'}"
        )
        st.write(f"Execution: {execution_label(judge.provider, judge.mode)}")


def configuration_status(runtime) -> None:
    live_selections = []
    if execution_label(runtime.target.config.provider) == "Live API":
        live_selections.append(runtime.target)
    if execution_label(runtime.judge.config.provider, runtime.judge.config.mode) == "Live API":
        live_selections.append(runtime.judge)
    if not live_selections:
        st.info("Pre-generated / fixture configuration. No external API calls are required.")
        return
    st.info("Configuration loaded. No API calls have been made.")
    credential_names = [selection.options.get("api_key_env") for selection in live_selections]
    if all(isinstance(name, str) and name for name in credential_names):
        if all(os.environ.get(name) for name in credential_names):
            st.caption("API credentials found in the environment. Connectivity and model access have not been verified.")
        else:
            st.warning("API credentials not configured.")
    else:
        st.caption("API credentials are required before running live models. Credential readiness could not be determined from this configuration.")


def selection_controls():
    definitions = {item.mode: item for item in selection_catalog()}
    purposes = {
        "quick": "Small predefined set for an initial check",
        "development": "Larger predefined set for development",
        "full": "Run the complete V1 scenario pack",
        "custom": "Choose specific scenarios from the existing 20",
    }
    labels = {
        mode: (f"{SELECTION_LABELS[mode]} — {definition.selected_count} scenarios"
               if definition.selected_count is not None else "Custom — choose scenarios")
        for mode, definition in definitions.items()
    }
    mode = st.selectbox(
        "Scenarios to run", options=list(definitions),
        format_func=labels.get, key="scenario_selection_mode",
    )
    st.caption(purposes[mode])
    catalog = scenario_catalog()
    titles = {item.scenario_id: item.title for item in catalog}
    custom = None
    if mode == "custom":
        custom = st.multiselect(
            "Scenarios", options=[item.scenario_id for item in catalog],
            format_func=lambda scenario_id: f"{scenario_id} — {titles[scenario_id]}",
            key="custom_scenario_ids",
        )
    request = SelectionRequest(mode=mode, custom_scenario_ids=custom)
    try:
        selection = resolve_selection(request)
    except ValueError:
        st.warning("Choose at least one scenario.")
        return request, None, titles
    st.write(f"**{selection.selected_count} of {selection.full_pack_total} scenarios will run.**")
    with st.expander("View selected scenarios", expanded=False):
        for scenario_id in selection.selected_scenario_ids:
            st.write(f"{titles[scenario_id]} · {scenario_id}")
    return request, selection, titles


def saved_runs_hub() -> None:
    """Explain the two saved-artifact workflows before a run is selected."""
    breadcrumb("Home", "Saved runs")
    route_link("Back to home", "home", "home")
    st.header("Saved runs", anchor=destination_anchor())
    st.write(
        "Saved evaluations can be inspected as-is or reused for a new judge-only evaluation."
    )
    view, rejudge = st.columns(2)
    with view, st.container(border=True, height="stretch"):
        st.subheader("View saved results")
        st.write(
            "Open a completed evaluation and inspect its saved report. "
            "No target or judge calls are made."
        )
        route_link(
            "View saved results", "saved_view", "saved", step="view",
            use_container_width=True,
        )
    with rejudge, st.container(border=True, height="stretch"):
        st.subheader("Rejudge saved transcripts")
        st.write(
            "Reuse completed conversations with your current judge configuration. The target will not run "
            "again. Rejudging creates a new result and leaves the original run unchanged."
        )
        st.warning("This workflow may make paid judge calls after explicit review and confirmation.")
        route_link(
            "Rejudge saved transcripts", "saved_rejudge", "saved", step="rejudge",
            use_container_width=True,
        )


def _saved_run_status(run: RunView) -> str:
    completed = dict(run.execution_counts).get("Completed", 0)
    return f"{completed} of {run.planned} conversations completed"


def _saved_run_metadata(run: RunView) -> None:
    st.write(f"**Run:** {run.run_id}")
    st.write(f"**Target model:** {run.model_under_test.provider}/{run.model_under_test.model}")
    st.write(f"**Original judge:** {run.judge.provider}/{run.judge.model}")
    st.write(f"**Scenarios:** {run.planned} · {_saved_run_status(run)}")
    st.caption(f"Created: {run.created_at}")


def saved_run_selector(*, purpose: str) -> tuple[str, Path, RunView] | None:
    """Select one verified run from the local registry or an advanced path fallback."""
    known: list[tuple[str, Path, RunView]] = []
    for token, value in _read_registry().items():
        path = Path(value)
        try:
            run = load_run_view(path)
        except ArtifactLoadError:
            continue
        known.append((token, path, run))
    known.sort(key=lambda item: item[2].created_at, reverse=True)

    selected: tuple[str, Path, RunView] | None = None
    if known:
        by_token = {token: (token, path, run) for token, path, run in known}
        token = st.selectbox(
            "Known saved runs", options=[""] + [item[0] for item in known],
            format_func=lambda value: (
                "Choose a saved run" if not value else
                f"{by_token[value][2].run_id} · "
                f"{by_token[value][2].model_under_test.model} · "
                f"{_saved_run_status(by_token[value][2])}"
            ),
            key=f"{purpose}_known_run",
        )
        if token:
            selected = by_token[token]
    else:
        st.info("No verified runs are registered on this computer yet.")

    with st.expander("Advanced: open by local run.json path", expanded=not known):
        saved_run_path = st.text_input(
            "Saved run.json path", value="", key=f"{purpose}_run_path_input",
        )
        if saved_run_path.strip():
            path = Path(saved_run_path).expanduser()
            try:
                run = load_run_view(path)
            except ArtifactLoadError as exc:
                st.error(str(exc))
            else:
                selected = (register_saved_run(path), path, run)
    if selected is not None:
        with st.container(border=True):
            _saved_run_metadata(selected[2])
    return selected


def saved_report_controls() -> None:
    """Open persisted reports without consulting execution integrations."""
    breadcrumb("Home", "Saved runs", "View saved results")
    route_link("Back to Saved runs", "saved", "saved")
    st.header("View saved results", anchor=destination_anchor())
    st.write(
        "Open a completed evaluation and inspect its saved report. The bundle is verified before results "
        "are shown. No target or judge calls are made."
    )
    selected = saved_run_selector(purpose="view")
    if selected is not None:
        token, _, _ = selected
        route_link(
            "Open saved results", "results", "results",
            run_token=token, origin="saved",
        )


def saved_rejudge_controls() -> None:
    breadcrumb("Home", "Saved runs", "Rejudge saved transcripts")
    route_link("Back to Saved runs", "saved", "saved")
    st.header("Rejudge saved transcripts", anchor=destination_anchor())
    st.write(
        "Choose a source run whose completed conversations should be assessed with your current judge. "
        "The target will not run again, the new evaluation is stored separately, and the source remains unchanged."
    )
    st.warning("Rejudging may make paid judge calls after explicit review and confirmation.")
    selected = saved_run_selector(purpose="rejudge_source")
    if selected is not None:
        token, _, _ = selected
        route_link(
            "Continue to transcript selection", "rejudge_configure", "rejudge",
            run_token=token, step="configure",
        )


def configure_local() -> None:
    breadcrumb("Home", "Run evaluation", "Configure")
    route_link("Back to home", "home", "home")
    st.header("Configure evaluation", anchor=destination_anchor())
    st.write(
        "Model settings are loaded from a YAML file in your local project. Edit that file on your "
        "computer, then reload it here before reviewing your run. Target and judge settings are "
        "configured independently."
    )
    st.markdown("**Choose scenarios in this UI. Change model settings in YAML.**")
    st.caption("Loading and reviewing the configuration makes no model calls.")
    st.subheader("Model settings — edit in YAML")
    config_path = st.text_input(
        "Local YAML file to load (select a path here; edit its contents outside this app)",
        value=os.environ.get("PSYCH_EVAL_CONFIG", "integrations/openai/example.yaml"),
        key="runtime_config_path",
    )
    resolved_config_path = Path(config_path).expanduser().resolve()
    st.caption("Source configuration (resolved local path)")
    st.code(str(resolved_config_path), language=None)
    st.button("Reload YAML", key="reload_runtime_config")
    try:
        runtime = load_runtime_config(resolved_config_path)
    except IntegrationConfigError as exc:
        st.error(str(exc))
        runtime = None
    if runtime is not None:
        configuration_status(runtime)
        resolved_model_cards(
            runtime.target.config, runtime.judge.config,
            target_integration=runtime.target.integration,
            judge_integration=runtime.judge.integration,
        )

    st.subheader("Scenario selection — choose here")
    request, selection, _ = selection_controls()
    default_output = st.session_state.get("output_directory_default")
    if default_output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        default_output = str(Path("runs") / f"evaluation-{stamp}")
        st.session_state.output_directory_default = default_output
    with st.expander("Evaluation details", expanded=False):
        output_directory = st.text_input(
            "New run directory", value=default_output, key="output_directory_input",
        )
        prompt_version = (
            runtime.judge.config.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION
            if runtime is not None else "Unavailable"
        )
        st.caption(f"Scenario pack v0.1 · Rubric v0.2 · Judge prompt v{prompt_version}")
    st.write("Reviewing this configuration makes no API calls.")
    if st.button("Review run", key="review_evaluation", type="primary",
                 disabled=selection is None or runtime is None):
        try:
            review = prepare_evaluation(resolved_config_path, request)
        except (IntegrationConfigError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.evaluation_review = review
            st.session_state.review_output_directory = output_directory
            navigate("review")
            st.rerun()


def _current_review(review: EvaluationReview) -> EvaluationReview | None:
    selection = review.selection
    request = SelectionRequest(
        category=selection.category,
        scenario_pack_version=selection.scenario_pack_version,
        mode=selection.selection_mode,
        custom_scenario_ids=(
            list(selection.selected_scenario_ids)
            if selection.selection_mode == "custom" else None
        ),
    )
    try:
        return prepare_evaluation(review.config_path, request)
    except (IntegrationConfigError, OSError, ValueError):
        return None


def review_local(review: EvaluationReview) -> None:
    breadcrumb("Home", "Run evaluation", "Review")
    route_link("Edit configuration", "configure", "run", step="configure")
    st.header("Review run", anchor=destination_anchor())
    current = _current_review(review)
    if current != review:
        st.warning(
            "The runtime YAML, resolved selection, or local configuration is no longer the reviewed "
            "version. Return to configuration and review it again. No model calls were made."
        )
        return
    st.write("Reviewing this configuration makes no API calls.")
    st.subheader("Configuration")
    st.write("**Evaluation:** Relational Sycophancy")
    st.caption("Resolved source configuration")
    st.code(str(Path(review.config_path).expanduser().resolve()), language=None)
    resolved_model_cards(
        review.target_config, review.judge_config,
        target_integration=review.target_integration,
        judge_integration=review.judge_integration,
    )
    st.write(f"Target: {review.target_config.provider}/{review.target_config.model}")
    st.write(f"Judge: {review.judge_config.provider}/{review.judge_config.model}")
    st.write(f"Rubric version: {RUBRIC_VERSION}")
    st.write(
        f"Judge prompt version: "
        f"{review.judge_config.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION}"
    )
    selection = review.selection
    label = SELECTION_LABELS[selection.selection_mode]
    st.subheader("Scope")
    st.write(f"**Scenarios:** {label} · {selection.selected_count} of {selection.full_pack_total} scenarios")
    titles = {item.scenario_id: item.title for item in scenario_catalog()}
    st.markdown("**Selected scenarios**")
    for scenario_id in selection.selected_scenario_ids:
        st.write(f"{titles[scenario_id]} · {scenario_id}")
    target_execution = execution_label(review.target_config.provider)
    judge_execution = execution_label(review.judge_config.provider, review.judge_config.mode)
    live = "Live API" in (target_execution, judge_execution)
    overall_execution = "Live API" if target_execution == judge_execution == "Live API" else (
        "Fixture / pre-generated behavior" if not live else "Mixed live API and fixture"
    )
    st.subheader("Execution")
    st.write(f"**Execution mode:** {overall_execution}")
    st.write(
        f"Planned initial target calls: {selection.selected_count * 4} "
        "(four assistant responses per completed scenario)."
    )
    st.write(
        f"Planned initial judge calls: {selection.selected_count} "
        "(one per completed transcript; fewer if a conversation does not complete)."
    )
    st.write(f"Target retry budget: {review.target_max_retries} per failed turn")
    st.write(f"Judge retry budget: {review.judge_max_retries} per assessment")
    output_directory = st.session_state.review_output_directory
    st.write(f"Output destination: {output_directory}")
    with st.expander("Evaluation details", expanded=False):
        st.caption(f"New persisted run directory: {output_directory}")
        st.caption(
            f"Scenario pack {selection.scenario_pack_id} v{selection.scenario_pack_version} · "
            f"Rubric v0.2 · Judge prompt v"
            f"{review.judge_config.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION}"
        )
    st.subheader(f"Ready to run: {label} · {selection.selected_count} of {selection.full_pack_total} scenarios")
    if live:
        st.warning(
            "Configuring and reviewing this run makes no API calls. Clicking Run evaluation starts calls "
            "to the configured live Target and/or Judge APIs using your supplied credentials. Charges may "
            "apply. Each scenario can involve multiple API calls."
        )
    else:
        st.info(
            "This run uses fixture, pre-generated behavior. Configuring, reviewing, and running it makes "
            "no external API calls."
        )
    if st.button(
        f"Run evaluation · {selection.selected_count} scenarios", key="run_evaluation", type="primary",
        disabled=st.session_state.get("run_in_progress", False),
    ):
        st.session_state.run_in_progress = True
        bar = st.progress(0.0)
        status = st.empty()

        def update(event: SuiteProgress) -> None:
            bar.progress(event.processed / event.total)
            if event.phase == "started":
                status.caption(f"Run started · 0 / {event.total} scenarios processed")
            elif event.phase == "scenario_started":
                status.caption(f"Running {event.scenario_id} · {event.processed} / {event.total} processed")
            elif event.phase == "scenario_completed":
                failure = f" · Failure stage: {event.failure_stage}" if event.failure_stage else ""
                status.caption(
                    f"Processed {event.scenario_id} · {event.processed} / {event.total} · "
                    f"Execution: {event.execution_status} · Evaluation: {event.evaluation_status}{failure}"
                )
            else:
                status.caption(f"Completed · {event.processed} / {event.total} scenarios processed")

        try:
            output = Path(output_directory).expanduser()
            execute_evaluation(review, output, progress=update)
        except (IntegrationConfigError, OSError, ValueError) as exc:
            st.error(f"Evaluation did not complete: {exc}")
            st.caption(f"Any persisted diagnostic artifacts remain at {output_directory}.")
        except Exception:
            st.error("Evaluation did not complete because of an unexpected local execution error.")
            st.caption(f"Inspect any persisted diagnostic artifacts at {output_directory}.")
        else:
            st.session_state.completed_run_path = str(output / "run.json")
            st.session_state.result_context = "run"
            navigate("results")
            st.rerun()
        finally:
            st.session_state.run_in_progress = False


def configure_rejudge() -> None:
    breadcrumb("Home", "Saved runs", "Rejudge")
    route_link("Back to Saved runs", "saved", "saved")
    st.header("Rejudge saved transcripts", anchor=destination_anchor())
    notice = st.session_state.pop("rejudge_route_notice", None)
    if notice is not None:
        st.info(notice)
    st.write(
        "Select completed conversations to reassess with the current judge configuration. "
        "The target model will not run again."
    )
    source_run_path = Path(st.session_state.completed_run_path).expanduser()
    try:
        source_run = load_run_view(source_run_path)
    except ArtifactLoadError as exc:
        st.error(str(exc))
        return
    with st.container(border=True):
        st.markdown("**SOURCE**")
        _saved_run_metadata(source_run)
        st.write(
            f"**Original judge configuration:** {source_run.judge.provider}/{source_run.judge.model} · "
            f"Prompt v{source_run.judge_prompt_version or 'Unspecified'} · Rubric v{source_run.rubric_version}"
        )
    config_default = st.session_state.get(
        "runtime_config_path", os.environ.get("PSYCH_EVAL_CONFIG", "integrations/openai/example.yaml"),
    )
    config_path = st.text_input(
        "Judge configuration file", value=config_default, key="rejudge_config_path",
    )
    try:
        runtime = load_runtime_config(config_path)
        completed = completed_saved_scenarios(source_run_path)
    except (IntegrationConfigError, OSError, ValueError) as exc:
        st.error(str(exc))
        runtime, completed = None, ()
    titles = {item.scenario_id: item.title for item in scenario_catalog()}
    selected = st.multiselect(
        "Completed conversations", options=list(completed), default=[],
        format_func=lambda scenario_id: f"{scenario_id} — {titles.get(scenario_id, scenario_id)}",
        key="rejudge_scenario_ids",
    )
    if runtime is not None:
        judge = runtime.judge.config
        prompt_version = judge.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION
        temperature = judge.sampling.temperature if judge.sampling is not None else "Default"
        with st.container(border=True):
            st.markdown("**CURRENT JUDGE**")
            st.write(f"Provider: {judge.provider}")
            st.write(f"Model: {judge.model}")
            st.write(f"Prompt version: {prompt_version}")
            st.write(f"Rubric version: {RUBRIC_VERSION}")
            st.write(f"Temperature: {temperature if temperature is not None else 'Not set'}")
            reasoning = (
                judge.sampling.reasoning.effort
                if judge.sampling is not None and judge.sampling.reasoning is not None else "Not set"
            )
            st.write(f"Reasoning effort: {reasoning}")
            max_tokens = judge.sampling.max_output_tokens if judge.sampling is not None else None
            st.write(f"Maximum output tokens: {max_tokens if max_tokens is not None else 'Not set'}")
            st.write(f"Judge retry budget: {runtime.judge_max_retries}")
        if st.session_state.get("result_context") == "demo":
            destination_default = str(
                Path("runs") / f"{source_run_path.parent.name}-judge-v{prompt_version}"
            )
        else:
            destination_default = str(default_rejudge_destination(source_run_path, prompt_version))
    else:
        destination_default = ""
    destination = st.text_input(
        "New rejudged run directory", value=destination_default,
        key="rejudge_destination_input",
    )
    st.write(f"**{len(selected)} completed conversations selected.**")
    st.write("**0 target calls.**")
    st.caption("Configuring this workflow makes no API calls.")
    if st.button(
        "Review judge-only run", key="review_rejudge", type="primary",
        disabled=runtime is None or not selected or not destination.strip(),
    ):
        try:
            review = prepare_rejudge(
                config_path, source_run_path, selected, destination,
            )
        except (IntegrationConfigError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.rejudge_review = review
            navigate("rejudge_review")
            st.rerun()


def review_rejudge(review: RejudgeReview) -> None:
    breadcrumb("Home", "Saved runs", "Rejudge", "Review")
    route_link(
        "Edit selection", "rejudge_configure", "rejudge",
        run_token=register_saved_run(review.source_run_path), step="configure",
    )
    st.header("Review judge-only run", anchor=destination_anchor())
    st.write("Reviewing this configuration makes no API calls.")
    source_run = load_run_view(Path(review.source_run_path))
    st.markdown("### SOURCE")
    _saved_run_metadata(source_run)
    st.write(
        f"**Original judge provenance:** {source_run.judge.provider}/{source_run.judge.model} · "
        f"Prompt v{source_run.judge_prompt_version or 'Unspecified'} · Rubric v{source_run.rubric_version}"
    )
    st.markdown("**Selected scenarios**")
    for scenario_id in review.selected_scenario_ids:
        st.write(scenario_id)
    st.write(f"**Selected count:** {len(review.selected_scenario_ids)}")
    judge = review.judge_config
    prompt_version = judge.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION
    temperature = judge.sampling.temperature if judge.sampling is not None else None
    reasoning = (
        judge.sampling.reasoning.effort
        if judge.sampling is not None and judge.sampling.reasoning is not None else "Not set"
    )
    st.markdown("### CURRENT JUDGE")
    st.write(f"Provider: {judge.provider}")
    st.write(f"Model: {judge.model}")
    st.write(f"Prompt version: {prompt_version}")
    st.write(f"Rubric version: {review.rubric_version}")
    st.write(f"Temperature: {temperature if temperature is not None else 'Not set'}")
    st.write(f"Reasoning effort: {reasoning}")
    max_tokens = judge.sampling.max_output_tokens if judge.sampling is not None else None
    st.write(f"Maximum output tokens: {max_tokens if max_tokens is not None else 'Not set'}")
    st.write(f"Retry budget: {review.judge_max_retries}")
    st.markdown("### EXECUTION")
    initial_calls = len(review.selected_scenario_ids)
    st.write(f"**0 target calls; {initial_calls} initial judge calls.**")
    if review.judge_max_retries:
        st.warning(
            f"Technical failures may add judge calls: up to {review.judge_max_retries} retries "
            "per selected transcript under the configured retry budget."
        )
    else:
        st.write("Automatic judge retries: 0.")
    st.write("A new result will be created. The original run and its artifacts remain unchanged.")
    st.markdown("**New destination/result**")
    st.code(review.destination_directory)
    if judge.mode == "live":
        st.warning(
            "Clicking Run judge only starts calls to the configured Judge API. Charges may apply. "
            "The target model will not run."
        )
    else:
        st.info("This judge uses fixture, pre-generated behavior and makes no external API calls.")
    if st.button(
        "Run judge only", key="run_rejudge", type="primary",
        disabled=st.session_state.get("rejudge_in_progress", False),
    ):
        st.session_state.rejudge_in_progress = True
        bar = st.progress(0.0)
        status = st.empty()

        def update(event: SuiteProgress) -> None:
            bar.progress(event.processed / event.total)
            if event.phase == "scenario_started":
                status.caption(
                    f"Rejudging {event.scenario_id} · {event.processed} / {event.total} processed"
                )
            elif event.phase == "scenario_completed":
                status.caption(
                    f"Processed {event.scenario_id} · {event.processed} / {event.total}"
                )
            elif event.phase == "completed":
                status.caption(f"Completed · {event.processed} / {event.total} processed")

        try:
            execute_rejudge(review, progress=update)
        except (IntegrationConfigError, OSError, ValueError) as exc:
            st.error(f"Judge-only run did not complete: {exc}")
            st.caption(
                f"Any persisted diagnostic artifacts remain at {review.destination_directory}."
            )
        except Exception:
            st.error("Judge-only run did not complete because of an unexpected local execution error.")
            st.caption(
                f"Inspect any persisted diagnostic artifacts at {review.destination_directory}."
            )
        else:
            st.session_state.completed_run_path = str(
                Path(review.destination_directory) / "run.json"
            )
            st.session_state.result_context = "saved"
            st.session_state.pop("rejudge_review", None)
            navigate("results")
            st.rerun()
        finally:
            st.session_state.rejudge_in_progress = False


def restore_route(route: str) -> str:
    """Restore contextual state for the pathname selected by ``st.navigation``."""
    query = st.query_params.to_dict()
    # Streamlit's AppTest does not retain a page hash after st.switch_page.
    # This also gives old query-only bookmarks a backward-compatible landing;
    # real browser history is driven by the distinct pathnames registered below.
    if query.get("view"):
        route = {
            "demo": "demo_detail" if query.get("scenario") else "demo",
            "run": "review" if query.get("step") == "review" else "configure",
            "saved": {
                "view": "saved_view",
                "rejudge": "saved_rejudge",
            }.get(query.get("step"), "saved"),
            "results": "result_detail" if query.get("scenario") else "results",
            "rejudge": (
                "rejudge_review" if query.get("step") == "review"
                else "rejudge_configure"
            ),
        }.get(query["view"], "home")
    scenario_id = query.get("scenario")
    if scenario_id is not None and re.fullmatch(r"RS-\d{3}", scenario_id) is None:
        scenario_id = None
    origin = query.get("origin") if query.get("origin") in {"run", "saved"} else "saved"
    run_token = query.get("run")

    if route == "home":
        page = "home"
        scenario_id = None
    elif route in {"demo", "demo_detail"}:
        st.session_state.completed_run_path = str(DEMO_RUN)
        st.session_state.result_context = "demo"
        page = "detail" if route == "demo_detail" else "results"
    elif route in {"saved", "saved_view", "saved_rejudge"}:
        page = route
        scenario_id = None
    elif route in {"configure", "review"}:
        st.session_state.result_context = "run"
        if route == "review" and "evaluation_review" not in st.session_state:
            page = "review_recovery"
        else:
            page = route
        scenario_id = None
    else:
        valid_token = run_token if run_token and RUN_TOKEN_PATTERN.fullmatch(run_token) else None
        run_path = resolve_saved_run(valid_token)
        if route in {"results", "result_detail"}:
            target_page = "detail" if route == "result_detail" else "results"
        else:
            target_page = route
            scenario_id = None
        if run_path is None:
            page = "missing_run"
            st.session_state.missing_run_token = valid_token
        else:
            st.session_state.completed_run_path = str(run_path)
            st.session_state.completed_run_token = valid_token
            st.session_state.result_context = origin
            if target_page == "rejudge_review" and "rejudge_review" not in st.session_state:
                page = "rejudge_configure"
                st.session_state.rejudge_route_notice = (
                    "The previous judge-only review was session-only. Review the selection again "
                    "before any calls can be made."
                )
            else:
                page = target_page

    location = (route, run_token, scenario_id, query.get("step"), origin)
    if st.session_state.get("inspection_location") != location:
        st.session_state.inspection_location = location
        st.session_state.inspection_navigation = (
            st.session_state.get("inspection_navigation", 0) + 1
        )
        st.session_state.inspection_arrival_pending = True
    st.session_state.inspection_page = page
    st.session_state.inspection_scenario = scenario_id
    return page


def missing_run_recovery(detail: str | None = None) -> None:
    breadcrumb("Home", "Saved runs", "Unavailable run")
    st.header("Saved run unavailable", anchor=destination_anchor())
    st.error(detail or "This saved-run link is no longer available on this computer.")
    st.write(
        "The URL contains only a local opaque identifier. The underlying run may have been moved, "
        "deleted, or opened in a different local installation. No model calls were made."
    )
    choose, home = st.columns(2)
    with choose:
        route_link(
            "Choose another saved run", "saved", "saved", use_container_width=True,
        )
    with home:
        route_link("Home", "home", "home", use_container_width=True)


def review_recovery() -> None:
    breadcrumb("Home", "Run evaluation", "Review")
    st.header("Review required again", anchor=destination_anchor())
    st.warning(
        "The previous review was session-only and cannot authorize execution after a refresh. "
        "Return to configuration and review the current YAML again. No model calls were made."
    )
    route_link("Edit configuration", "configure", "run", step="configure")


def scenario_recovery(*, demo: bool) -> None:
    parent = "Demo" if demo else "Saved runs"
    breadcrumb("Home", parent, "Unavailable scenario")
    st.header("Scenario unavailable", anchor=destination_anchor())
    st.error("This scenario is not available in the selected saved run.")
    if demo:
        route_link("Back to demo results", "demo", "demo")
    else:
        route_link(
            "Back to saved results", "results", "results",
            run_token=register_saved_run(st.session_state.completed_run_path),
            origin=st.session_state.get("result_context", "saved"),
        )


def main(route: str) -> None:
    page = restore_route(route)
    st.title("Psychosocial Safety Evaluator", anchor=destination_anchor() if page == "home" else None)
    product_introduction(home=page == "home")
    if page == "home":
        landing()
    elif page == "saved":
        saved_runs_hub()
    elif page == "saved_view":
        saved_report_controls()
    elif page == "saved_rejudge":
        saved_rejudge_controls()
    elif page == "missing_run":
        missing_run_recovery()
    elif page == "review_recovery":
        review_recovery()
    elif page in ("configure", "review", "rejudge_configure", "rejudge_review"):
        if page == "review" and "evaluation_review" in st.session_state:
            review_local(st.session_state.evaluation_review)
        elif page == "rejudge_review" and "rejudge_review" in st.session_state:
            review_rejudge(st.session_state.rejudge_review)
        elif page == "rejudge_configure" and "completed_run_path" in st.session_state:
            configure_rejudge()
        else:
            configure_local()
    else:
        demo = st.session_state.get("result_context") == "demo"
        selected = st.session_state.get("inspection_scenario") if page == "detail" else None
        run_path_value = st.session_state.get("completed_run_path")
        if run_path_value is None:
            navigate("home")
            st.rerun()
        run_path = Path(run_path_value)
        try:
            run = load_run_view(run_path)
        except ArtifactLoadError as exc:
            missing_run_recovery(str(exc))
            run = None
        if run is not None and selected is not None:
            try:
                run = load_run_view(run_path, scenario_id=selected)
            except ArtifactLoadError:
                run = None
                scenario_recovery(demo=demo)
        if run is not None and page == "detail" and run.detail is not None:
            scenario_detail(run, run.detail, demo=demo)
        elif run is not None and page == "detail":
            scenario_recovery(demo=demo)
        elif run is not None:
            results(run, demo=demo)
    st.divider()
    finish_navigation()


def _build_pages() -> dict[str, st.Page]:
    page_file = "streamlit_pages/route.py"
    return {
        "home": st.Page(
            page_file, title="Home", default=True, visibility="hidden",
        ),
        "demo": st.Page(
            page_file, title="Demo", url_path="demo", visibility="hidden",
        ),
        "demo_detail": st.Page(
            page_file, title="Demo scenario",
            url_path="demo-scenario", visibility="hidden",
        ),
        "configure": st.Page(
            page_file, title="Run evaluation",
            url_path="run", visibility="hidden",
        ),
        "review": st.Page(
            page_file, title="Review run",
            url_path="run-review", visibility="hidden",
        ),
        "saved": st.Page(
            page_file, title="Saved runs", url_path="saved", visibility="hidden",
        ),
        "saved_view": st.Page(
            page_file, title="View saved results",
            url_path="saved-view", visibility="hidden",
        ),
        "saved_rejudge": st.Page(
            page_file, title="Choose rejudge source",
            url_path="saved-rejudge", visibility="hidden",
        ),
        "results": st.Page(
            page_file, title="Results", url_path="results", visibility="hidden",
        ),
        "result_detail": st.Page(
            page_file, title="Scenario",
            url_path="scenario", visibility="hidden",
        ),
        "rejudge_configure": st.Page(
            page_file, title="Rejudge",
            url_path="rejudge", visibility="hidden",
        ),
        "rejudge_review": st.Page(
            page_file, title="Review rejudge",
            url_path="rejudge-review", visibility="hidden",
        ),
    }


def run_app() -> None:
    st.set_page_config(page_title="Psychosocial Safety Evaluator", layout="centered")
    NAV_PAGES.update(_build_pages())
    st.navigation(list(NAV_PAGES.values()), position="hidden").run()


if __name__ == "__main__":
    run_app()
