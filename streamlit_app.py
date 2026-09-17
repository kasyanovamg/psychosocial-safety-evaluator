"""Local configure, select, run, and persisted-artifact report workflow."""

from datetime import datetime, timezone
from html import escape
from inspect import signature
from pathlib import Path
import os

import streamlit as st

from psych_eval.presentation import ArtifactLoadError, EvaluationView
from psych_eval.run_presentation import RunView, load_run_view
from psych_eval.integrations.runtime import IntegrationConfigError, load_runtime_config
from psych_eval.selection import SelectionRequest, resolve_selection, selection_catalog
from psych_eval.suite import SuiteProgress, scenario_catalog
from psych_eval.integrations.workflow import EvaluationReview, execute_evaluation, prepare_evaluation


DEMO_RUN = Path(os.environ.get(
    "PSYCH_EVAL_RUN", str(Path(__file__).resolve().parent / "demo/runs/relational-sycophancy-demo-v1/run.json"),
))
VALIDATION_STATUS = "Experimental"
RUN_MODE = "Configure evaluation"
DEMO_MODE = "Example results"

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


def navigate(page: str, scenario_id: str | None = None) -> None:
    st.session_state.inspection_page = page
    st.session_state.inspection_scenario = scenario_id
    st.session_state.inspection_navigation = st.session_state.get("inspection_navigation", 0) + 1
    st.session_state.inspection_arrival_pending = True


def change_workflow_mode() -> None:
    local = st.session_state.workflow_mode == RUN_MODE
    st.session_state.inspection_page = "configure" if local else "results"
    st.session_state.inspection_scenario = None
    st.session_state.pop("evaluation_review", None)
    st.session_state.pop("review_output_directory", None)
    st.session_state.pop("completed_run_path", None)


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
    st.subheader("Example models" if example else "Evaluation configuration")
    left, right = st.columns(2)
    for column, label, provenance, explanation in (
        (left, "Target", run.model_under_test, "Model being evaluated"),
        (right, "Judge", run.judge, "Model assessing the conversations"),
    ):
        with column, st.container(border=True):
            st.markdown(f"**{label}**")
            st.caption(explanation)
            if example:
                st.write("Pre-generated example")
            else:
                st.write(f"Provider: {provenance.provider}")
                st.write(f"Model: {provenance.model}")
                st.write(f"Execution: {provenance.execution}")


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
        st.text(f"Temperature: {run.sampling_temperature} · Maximum output tokens: {run.sampling_max_output_tokens}")
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


def results(run: RunView, *, demo: bool) -> None:
    if not demo:
        st.button("Configure another evaluation", on_click=navigate, args=("configure",))
    st.header("Example results" if demo else "Evaluation results", anchor=destination_anchor())
    if demo:
        st.caption("Pre-generated example · Viewing these results makes no API calls.")
    st.subheader(run.construct)
    st.caption(f"Suite {run.suite_id} · Version {run.suite_version}")
    st.caption(f"Rubric v{run.rubric_version}")
    if not demo:
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
            st.button("View details", key=f"details_{row.scenario_id}", disabled=not row.has_details,
                      on_click=navigate, args=("detail", row.scenario_id))
    technical_details(run)


def scenario_detail(run: RunView, view: EvaluationView, *, demo: bool) -> None:
    st.button("Back to run results", on_click=navigate, args=("results",))
    st.header(view.scenario_heading, anchor=destination_anchor())
    st.caption(f"{view.construct} · Scenario v{view.scenario_version} · Execution: {view.execution_status} · Evaluation: {view.evaluation_status}")
    st.info("Illustrative example results" if demo else run.disclosure)
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


def product_introduction() -> None:
    st.write(
        "Evaluate conversational AI for psychosocial safety risks through controlled multi-turn simulations."
    )
    st.markdown("**V1 evaluation: Relational Sycophancy**")
    st.write(
        "Relational Sycophancy is unsupported reinforcement of a user's interpretation "
        "of another person or relationship."
    )
    st.write(f"**Validation status: {VALIDATION_STATUS}**")


def severity_label(value: str) -> str:
    return SEVERITY_LABELS.get(str(value).strip().split(maxsplit=1)[0], str(value))


def scenario_severity_label(value: str) -> str:
    key = str(value).strip().split(maxsplit=1)[0]
    label = severity_label(value)
    return f"{label} Relational Sycophancy" if key in SEVERITY_LABELS else label


def execution_label(provider: str, mode: str | None = None) -> str:
    return "Fixture" if provider == "fixture" or mode == "fixture" else "Live API"


def resolved_model_cards(target, judge) -> None:
    cards = (
        ("TARGET", "Model being evaluated", target.provider, target.model, execution_label(target.provider)),
        ("JUDGE", "Model assessing the conversations", judge.provider, judge.model,
         execution_label(judge.provider, judge.mode)),
    )
    left, right = st.columns(2)
    for column, (label, role, provider, model, execution) in zip((left, right), cards):
        with column, st.container(border=True):
            st.markdown(f"**{label}**")
            st.caption(role)
            st.write(f"Provider: {provider}")
            st.write(f"Model: {model}")
            st.write(f"Execution: {execution}")


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


def saved_report_controls() -> None:
    """Open persisted reports without consulting execution integrations."""
    saved_run_path = st.text_input(
        "Saved run.json path", value="", key="saved_run_path_input",
    )
    if st.button("Open saved results", key="open_saved_report", disabled=not saved_run_path.strip()):
        try:
            load_run_view(Path(saved_run_path).expanduser())
        except ArtifactLoadError as exc:
            st.error(str(exc))
        else:
            st.session_state.completed_run_path = str(Path(saved_run_path).expanduser())
            navigate("results")
            st.rerun()


def configure_local() -> None:
    st.header("Configure evaluation", anchor=destination_anchor())
    config_path = st.text_input(
        "Model configuration file", value=os.environ.get("PSYCH_EVAL_CONFIG", "runtime.fixture.yaml"),
        key="runtime_config_path",
    )
    try:
        runtime = load_runtime_config(config_path)
    except IntegrationConfigError as exc:
        st.error(str(exc))
        runtime = None
    if runtime is not None:
        configuration_status(runtime)
        resolved_model_cards(runtime.target.config, runtime.judge.config)

    st.subheader("Scenarios to run")
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
        st.caption("Scenario pack v0.1 · Rubric v0.2 · Judge prompt v0.1")
    st.write("Reviewing this configuration makes no API calls.")
    if st.button("Review run", key="review_evaluation", type="primary",
                 disabled=selection is None or runtime is None):
        try:
            review = prepare_evaluation(config_path, request)
        except (IntegrationConfigError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.evaluation_review = review
            st.session_state.review_output_directory = output_directory
            navigate("review")
            st.rerun()
    with st.expander("Advanced · Open saved results", expanded=False):
        saved_report_controls()


def review_local(review: EvaluationReview) -> None:
    st.button("Edit configuration", on_click=navigate, args=("configure",))
    st.header("Review run", anchor=destination_anchor())
    st.write("Reviewing this configuration makes no API calls.")
    st.write("**Evaluation:** Relational Sycophancy")
    resolved_model_cards(review.target_config, review.judge_config)
    selection = review.selection
    label = SELECTION_LABELS[selection.selection_mode]
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
    st.write(f"**Execution mode:** {overall_execution}")
    output_directory = st.session_state.review_output_directory
    with st.expander("Evaluation details", expanded=False):
        st.caption(f"New persisted run directory: {output_directory}")
        st.caption(
            f"Scenario pack {selection.scenario_pack_id} v{selection.scenario_pack_version} · "
            "Rubric v0.2 · Judge prompt v0.1"
        )
    st.subheader(f"Ready to run: {label} · {selection.selected_count} of {selection.full_pack_total} scenarios")
    st.write(f"Target: {review.target_config.provider}/{review.target_config.model}")
    st.write(f"Judge: {review.judge_config.provider}/{review.judge_config.model}")
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
            navigate("results")
            st.rerun()
        finally:
            st.session_state.run_in_progress = False


def main() -> None:
    st.set_page_config(page_title="Psychosocial Safety Evaluator", layout="centered")
    page = st.session_state.get("inspection_page")
    st.title("Psychosocial Safety Evaluator", anchor=destination_anchor() if page is None else None)
    product_introduction()
    mode = st.radio(
        "Workflow", options=(DEMO_MODE, RUN_MODE), horizontal=True,
        key="workflow_mode", on_change=change_workflow_mode,
    )
    demo = mode == DEMO_MODE
    page = st.session_state.get("inspection_page", "results" if demo else "configure")
    if not demo and page in ("configure", "review"):
        if page == "review" and "evaluation_review" in st.session_state:
            review_local(st.session_state.evaluation_review)
        else:
            configure_local()
    else:
        selected = st.session_state.get("inspection_scenario") if page == "detail" else None
        run_path_value = str(DEMO_RUN) if demo else st.session_state.get("completed_run_path")
        if run_path_value is None:
            navigate("configure")
            st.rerun()
        run_path = Path(run_path_value)
        try:
            run = load_run_view(run_path, scenario_id=selected)
        except ArtifactLoadError as exc:
            st.error(str(exc))
            st.stop()
        if page == "detail" and run.detail is not None:
            scenario_detail(run, run.detail, demo=demo)
        else:
            results(run, demo=demo)
    st.divider()
    finish_navigation()


if __name__ == "__main__":
    main()
