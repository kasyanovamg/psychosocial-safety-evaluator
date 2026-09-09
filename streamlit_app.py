"""Thin local inspection page: persisted artifacts in, presentation only out."""

from html import escape
from inspect import signature
from pathlib import Path

import streamlit as st

from psych_eval.presentation import ArtifactLoadError, EvaluationView
from psych_eval.run_presentation import RunView, load_run_view


DEMO_RUN = Path(__file__).resolve().parent / "demo/runs/relational-sycophancy-demo-v1/run.json"


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
    st.caption("Models, providers, prompts, and credentials are configured locally. See README.md in the repository for model-under-test and judge configuration. This build supports fixture artifacts only; live providers and credential loading are not implemented.")
    st.subheader("Evaluation coverage")
    st.write(f"**{run.construct} — Available**")
    st.caption("Additional psychosocial evaluations — Planned")
    st.divider()
    st.info(run.disclosure)
    st.button("View demo evaluation", key="view_demo", type="primary", on_click=navigate, args=("results",))


def results(run: RunView) -> None:
    st.button("Back to configuration", on_click=navigate, args=("landing",))
    st.header("Evaluation results", anchor=destination_anchor())
    st.subheader(run.construct)
    st.caption(f"Suite {run.suite_id} · Version {run.suite_version}")
    st.info(run.disclosure)
    configuration(run)
    planned, assessed, material, severe = st.columns(4)
    planned.metric("Planned scenarios", run.planned)
    assessed.metric("Assessed scenarios", run.assessed)
    material.metric("Material or higher", run.material_or_higher)
    severe.metric("Severe", run.severe)
    st.caption("Severity and mechanism counts include only completed, assessed scenarios. Counts describe this saved run, not an overall model safety score.")
    st.subheader("Severity distribution")
    st.table([{"Severity": item.label, "Scenarios": item.scenario_count} for item in run.severity_distribution])
    st.subheader("Observed mechanisms")
    st.table([{"Mechanism": item.label, "Findings": item.finding_count, "Scenarios": item.scenario_count} for item in run.mechanisms])
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


def main() -> None:
    st.set_page_config(page_title="Psychosocial Safety Evaluator", layout="centered")
    page = st.session_state.get("inspection_page", "landing")
    st.title("Psychosocial Safety Evaluator", anchor=destination_anchor() if page == "landing" else None)
    selected = st.session_state.get("inspection_scenario") if page == "detail" else None
    try:
        run = load_run_view(DEMO_RUN, scenario_id=selected)
    except ArtifactLoadError as exc:
        st.error(str(exc))
        st.stop()
    if page == "detail" and run.detail is not None:
        scenario_detail(run, run.detail)
    elif page == "results":
        results(run)
    else:
        landing(run)
    st.divider()
    st.caption("This tool evaluates specified conversational behavior against a versioned rubric. It does not diagnose users, measure psychological harm, or provide compliance certification.")
    finish_navigation()


if __name__ == "__main__":
    main()
