"""Local configure, select, run, and persisted-artifact report workflow."""

from datetime import datetime, timezone
from html import escape
from inspect import signature
from pathlib import Path
import os

import streamlit as st

from psych_eval.presentation import ArtifactLoadError, EvaluationView
from psych_eval.run_presentation import RunView, load_run_view
from psych_eval.integrations.runtime import IntegrationConfigError, integration_names
from psych_eval.selection import SelectionRequest, resolve_selection, selection_catalog
from psych_eval.suite import SuiteProgress, scenario_catalog
from psych_eval.integrations.workflow import EvaluationReview, execute_evaluation, prepare_evaluation


DEMO_RUN = Path(os.environ.get(
    "PSYCH_EVAL_RUN", str(Path(__file__).resolve().parent / "demo/runs/relational-sycophancy-demo-v1/run.json"),
))
VALIDATION_STATUS = "Not yet validated"
RUN_MODE = "Run local evaluation"
DEMO_MODE = "View pre-generated demo"


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
    st.session_state.inspection_page = "configure" if local else "landing"
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


def configuration(run: RunView) -> None:
    st.subheader("Evaluation configuration")
    left, right = st.columns(2)
    for column, label, provenance, explanation in (
        (left, "Model under test", run.model_under_test, "The conversational model being evaluated."),
        (right, "Judge model", run.judge, "Independently assesses conversations against the rubric."),
    ):
        with column, st.container(border=True):
            st.markdown(f"**{label}**")
            st.caption(f"Provider: {provenance.provider} · {provenance.execution}")
            literal_text(provenance.model)
            st.caption(explanation)


def technical_details(run: RunView, detail: EvaluationView | None = None) -> None:
    with st.expander("Technical details & reproducibility", expanded=False):
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
        if detail is not None:
            st.text(f"Scenario: {detail.scenario_id} · Version {detail.scenario_version}")
            st.text(f"Evaluation ID: {detail.evaluation_id}")
            st.text(f"Transcript / run ID: {detail.transcript_run_id}")
            st.text(f"Execution: {detail.execution_status} · Evaluation: {detail.evaluation_status}")
            st.text(f"Maximum retries: {detail.max_retries} · Retries used: {detail.retry_count}")
            st.text(f"Recovery: {detail.recovery} · Persistence: {detail.persistence}")
        st.caption("Loaded from a canonical run bundle with all referenced artifacts verified. Detailed JSON retains exact judge inputs and outputs.")


def landing(run: RunView) -> None:
    st.subheader("Evaluate conversational AI for psychosocial safety risks through controlled multi-turn simulations.")
    st.write("Versioned scenarios probe a model’s conversational behavior. An independently configured judge applies a rubric, with findings linked to evidence and full transcripts.")
    st.caption("Controlled scenarios → model under test → transcript → judge → findings")
    st.divider()
    configuration(run)
    st.caption("This demo loads pre-generated artifacts. It does not configure credentials, initialize adapters, or run inference.")
    st.subheader("Evaluation coverage")
    st.write(f"**{run.construct} — Available**")
    st.caption(f"Scenario pack v0.1 · Rubric v0.2 · Judge prompt v0.1 · {VALIDATION_STATUS}")
    st.caption("Additional psychosocial evaluations — Planned")
    st.divider()
    st.info(run.disclosure)
    st.button("View demo evaluation", key="view_demo", type="primary", on_click=navigate, args=("results",))


def coverage_summary(run: RunView) -> None:
    st.subheader("Coverage")
    if run.coverage is None:
        st.warning("Coverage metadata unavailable for this historical artifact.")
        return
    coverage = run.coverage
    message = (
        f"{coverage.label} coverage · {coverage.selected_count} / {coverage.full_pack_total} scenarios selected · "
        f"{coverage.valid_assessed_count} / {coverage.selected_count} selected scenarios assessed"
    )
    (st.success if coverage.assessment_coverage_complete else st.warning)(message)
    st.write(
        f"Executed: {coverage.executed_count} · Technical failures: {coverage.technical_failure_count} · "
        f"Cannot assess: {coverage.cannot_assess_count}"
    )
    st.caption(
        f"Selection: {coverage.selection_mode}"
        + (f" v{coverage.selection_version}" if coverage.selection_version else "")
        + f" · Selection complete: {'Yes' if coverage.selection_complete else 'No'}"
        + f" · Complete pack selection: {'Yes' if coverage.pack_coverage_complete else 'No'}"
        + f" · Complete assessment coverage: {'Yes' if coverage.assessment_coverage_complete else 'No'}"
    )
    with st.expander("View exact selected scenarios", expanded=False):
        st.text("\n".join(coverage.selected_scenario_ids))


def results(run: RunView, *, demo: bool) -> None:
    st.button(
        "Back to demo" if demo else "Configure another evaluation",
        on_click=navigate, args=("landing" if demo else "configure",),
    )
    st.header("Evaluation results", anchor=destination_anchor())
    st.subheader(run.construct)
    st.caption(f"Suite {run.suite_id} · Version {run.suite_version}")
    st.caption(f"Rubric v{run.rubric_version} · {VALIDATION_STATUS}")
    st.info(run.disclosure)
    configuration(run)
    coverage_summary(run)
    planned, assessed, material, severe = st.columns(4)
    planned.metric("Planned scenarios", run.planned)
    assessed.metric("Assessed scenarios", run.assessed)
    material.metric("Material or higher", run.material_or_higher)
    severe.metric("Severe", run.severe)
    st.caption("Severity and mechanism counts include only completed, assessed scenarios. Counts describe this saved run, not an overall model safety score.")
    st.subheader("Severity distribution")
    st.caption("🟢 0 — None · 🟡 1 — Mild · 🟠 2 — Material · 🔴 3 — Severe")
    st.table([{"Severity": item.label, "Scenarios": item.scenario_count} for item in run.severity_distribution])
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
            st.write(f"{row.severity_display} · {findings}")
            st.caption(f"Execution: {row.execution_status} · Evaluation: {row.evaluation_status}")
            st.button("View details", key=f"details_{row.scenario_id}", disabled=not row.has_details,
                      on_click=navigate, args=("detail", row.scenario_id))
    technical_details(run)


def scenario_detail(run: RunView, view: EvaluationView) -> None:
    st.button("Back to run results", on_click=navigate, args=("results",))
    st.header(view.scenario_heading, anchor=destination_anchor())
    st.caption(f"{view.construct} · Scenario v{view.scenario_version} · Execution: {view.execution_status} · Evaluation: {view.evaluation_status}")
    st.info(run.disclosure)
    severity, count = st.columns(2)
    severity.metric("Scenario severity", view.severity_display)
    count.metric("Findings", view.finding_count)
    if view.execution_status != "Completed":
        st.caption("This incomplete execution is excluded from the run’s severity and mechanism distribution.")
    if view.zero_rationale is not None:
        literal_text(view.zero_rationale)
    if view.cannot_assess_reason is not None:
        literal_text(view.cannot_assess_reason)
    st.header("Findings")
    st.caption("In assistant-turn order. Turn IDs connect each finding to the full conversation below.")
    for finding in view.findings:
        with st.container(border=True):
            st.subheader(f"{finding.turn_id} · Severity {finding.severity_display}")
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
                    st.caption(f"Flagged · Severity {finding.severity_display}")
                literal_text(turn.content)
    technical_details(run, view)


def evaluation_metadata() -> None:
    st.subheader("Evaluation")
    st.write("**Relational Sycophancy**")
    st.caption(f"Scenario pack v0.1 · Rubric v0.2 · Judge prompt v0.1 · {VALIDATION_STATUS}")
    st.info("This evaluation has not yet completed formal human validation. Results support investigation and regression review, not certification or measurement of downstream harm.")


def selection_controls():
    definitions = {item.mode: item for item in selection_catalog()}
    purposes = {
        "quick": "Setup and smoke testing",
        "development": "Iterative development and regression checks",
        "full": "Complete current scenario pack",
        "custom": "Focused debugging with chosen scenarios",
    }
    labels = {
        mode: (
            f"{mode.capitalize()} — {definition.selected_count} scenarios"
            if definition.selected_count is not None else "Custom"
        )
        for mode, definition in definitions.items()
    }
    mode = st.selectbox(
        "Scenario coverage", options=list(definitions),
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
    except ValueError as exc:
        st.warning(str(exc))
        return request, None, titles
    st.write(f"**Selected: {selection.selected_count} / {selection.full_pack_total} scenarios**")
    version = f" · Subset v{selection.selection_version}" if selection.selection_version else ""
    st.caption(f"{mode.capitalize()}{version} · Canonical pack order")
    with st.expander("Preview exact run scope", expanded=False):
        for scenario_id in selection.selected_scenario_ids:
            st.write(f"{scenario_id} — {titles[scenario_id]}")
    return request, selection, titles


def saved_report_controls() -> None:
    """Open persisted reports without consulting execution integrations."""
    st.subheader("Open saved report")
    saved_run_path = st.text_input(
        "Saved run.json path", value="", key="saved_run_path_input",
    )
    if st.button("Open saved report", key="open_saved_report", disabled=not saved_run_path.strip()):
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
    evaluation_metadata()
    saved_report_controls()
    st.divider()
    try:
        names = integration_names()
    except IntegrationConfigError as exc:
        st.error(str(exc))
        return
    st.subheader("Target and judge configuration")
    st.write("Use one existing runtime YAML file. Target and Judge remain independent slots; private adapter options and credentials stay in that local file or its environment.")
    left, right = st.columns(2)
    left.caption("Installed target integrations: " + ", ".join(names["target"]))
    right.caption("Installed judge integrations: " + ", ".join(names["judge"]))
    config_path = st.text_input(
        "Runtime config path", value=os.environ.get("PSYCH_EVAL_CONFIG", "runtime.fixture.yaml"),
        key="runtime_config_path",
    )
    default_output = st.session_state.get("output_directory_default")
    if default_output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        default_output = str(Path("runs") / f"evaluation-{stamp}")
        st.session_state.output_directory_default = default_output
    output_directory = st.text_input(
        "New run directory", value=default_output, key="output_directory_input",
    )
    request, selection, _ = selection_controls()
    if st.button("Review evaluation", key="review_evaluation", type="primary", disabled=selection is None):
        try:
            review = prepare_evaluation(config_path, request)
        except (IntegrationConfigError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.evaluation_review = review
            st.session_state.review_output_directory = output_directory
            navigate("review")
            st.rerun()


def review_local(review: EvaluationReview) -> None:
    st.button("Back to configuration", on_click=navigate, args=("configure",))
    st.header("Review evaluation", anchor=destination_anchor())
    st.success("Runtime configuration and both adapter roles validated. No inference has run.")
    configuration_view = (
        ("Model under test", review.target_integration, review.target_config.provider, review.target_config.model),
        ("Judge model", review.judge_integration, review.judge_config.provider, review.judge_config.model),
    )
    left, right = st.columns(2)
    for column, values in zip((left, right), configuration_view):
        label, integration, provider, model = values
        with column, st.container(border=True):
            st.markdown(f"**{label}**")
            st.caption(f"Integration: {integration} · Provider: {provider}")
            literal_text(model)
    selection = review.selection
    evaluation_metadata()
    st.subheader("Run scope")
    st.write(f"**Coverage: {selection.selection_mode.capitalize()}**")
    st.write(f"**Selected: {selection.selected_count} / {selection.full_pack_total} scenarios**")
    st.caption(
        f"Scenario pack {selection.scenario_pack_id} v{selection.scenario_pack_version}"
        + (f" · Subset v{selection.selection_version}" if selection.selection_version else "")
    )
    st.text("\n".join(selection.selected_scenario_ids))
    output_directory = st.session_state.review_output_directory
    st.caption(f"New persisted run directory: {output_directory}")
    if st.button(
        "Run evaluation", key="run_evaluation", type="primary",
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
    mode = st.radio(
        "Workflow", options=(DEMO_MODE, RUN_MODE), horizontal=True,
        key="workflow_mode", on_change=change_workflow_mode,
    )
    demo = mode == DEMO_MODE
    page = st.session_state.get("inspection_page", "landing" if demo else "configure")
    st.title("Psychosocial Safety Evaluator", anchor=destination_anchor() if page == "landing" else None)
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
            scenario_detail(run, run.detail)
        elif page == "results":
            results(run, demo=demo)
        else:
            landing(run)
    st.divider()
    st.caption("This tool evaluates specified conversational behavior against a versioned rubric. It does not diagnose users, measure psychological harm, or provide compliance certification.")
    finish_navigation()


if __name__ == "__main__":
    main()
