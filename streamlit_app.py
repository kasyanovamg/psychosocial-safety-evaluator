"""Thin local inspection page: persisted artifacts in, presentation only out."""

from html import escape
from pathlib import Path

import streamlit as st

from psych_eval.presentation import ArtifactLoadError, load_evaluation_view


DEMO_DIRECTORY = Path(__file__).resolve().parent / "demo" / "artifacts" / "RS-001"


def literal_text(text: str, *, evidence: bool = False) -> None:
    """Preserve whitespace and literal markup; Streamlit text/code trim it.

    Only escaped text enters this fixed HTML wrapper. No highlighting, scripts,
    links, or artifact-supplied markup/styles are interpreted.
    """
    style = "white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6;"
    if evidence:
        style += " background: #F3F5F7; border-left: 3px solid #405B72; padding: 12px 16px; border-radius: 4px;"
    st.html(f'<div style="{style}">{escape(text)}</div>')


def main() -> None:
    st.set_page_config(page_title="Psychosocial Safety Evaluator", layout="centered")
    st.title("Psychosocial Safety Evaluator")
    st.caption("AI BEHAVIOR EVALUATION · LOCAL ARTIFACT INSPECTION")
    try:
        view = load_evaluation_view(
            DEMO_DIRECTORY / "transcript.json", DEMO_DIRECTORY / "evaluation.json",
        )
    except ArtifactLoadError as exc:
        st.error(str(exc))
        st.stop()

    banner = st.warning if view.provenance_kind in ("fixture", "mixed") else st.info
    banner(f"**{view.banner_title}**\n\n{view.banner_text}")
    st.header("Relational Sycophancy Evaluation")
    st.subheader(view.scenario_heading)
    st.caption(
        f"{view.construct} · Scenario v{view.scenario_version} · "
        f"Execution: {view.execution_status} · Evaluation: {view.evaluation_status}"
    )

    with st.container(border=True):
        st.metric("Overall Severity", view.severity_display)
        st.caption("Classification of AI conversational behavior against the versioned rubric.")

    st.subheader("Evaluation summary")
    st.write(f"{view.finding_count} findings across {view.flagged_turn_count} assistant turns.")
    if view.mechanisms:
        st.write("Recorded mechanisms: " + ", ".join(view.mechanisms) + ".")
    if view.zero_rationale is not None:
        literal_text(view.zero_rationale)
    if view.cannot_assess_reason is not None:
        literal_text(view.cannot_assess_reason)

    st.divider()
    st.header("Findings")
    st.caption("Ordered by conversation turn. Match each A-number with the conversation below.")
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

    st.divider()
    st.header("Conversation")
    st.caption("Full persisted transcript. Flag labels refer to the findings above.")
    for turn in view.turns:
        with st.chat_message(turn.role):
            st.markdown(f"**{turn.role_label} · {turn.turn_id}**")
            for finding in turn.findings:
                st.caption(f"Flagged · Severity {finding.severity_display}")
            literal_text(turn.content)

    st.divider()
    with st.expander("Evaluation details / Reproducibility"):
        target_column, judge_column = st.columns(2)
        for column, label, provenance in (
            (target_column, "Target", view.target), (judge_column, "Judge", view.judge),
        ):
            with column:
                st.subheader(label)
                st.text(f"Execution: {provenance.execution}")
                st.text(f"Provider: {provenance.provider}")
                st.text(f"Model: {provenance.model}")
        st.markdown("**Artifact identity**")
        st.text(f"Scenario: {view.scenario_id} · Version {view.scenario_version}")
        st.text(f"Evaluation ID: {view.evaluation_id}")
        st.text(f"Transcript / run ID: {view.transcript_run_id}")
        st.text(f"Execution: {view.execution_status} · Evaluation: {view.evaluation_status}")
        st.text(f"Rubric: {view.rubric_version} · Evaluator: {view.evaluator_version}")
        st.text(f"Transcript schema: {view.transcript_schema_version} · Evaluation schema: {view.evaluation_schema_version}")
        st.markdown("**Recorded target configuration**")
        st.text(f"Temperature: {view.sampling_temperature} · Maximum output tokens: {view.sampling_max_output_tokens}")
        st.text(f"Maximum retries: {view.max_retries} · Retries used: {view.retry_count}")
        st.text(f"Recovery: {view.recovery} · Persistence: {view.persistence}")
        st.caption("The evaluation JSON retains the exact structured judge input and raw response.")

    st.caption(
        "This tool evaluates specified conversational behavior against a versioned rubric. "
        "It does not diagnose users, measure psychological harm, or provide compliance certification."
    )


if __name__ == "__main__":
    main()
