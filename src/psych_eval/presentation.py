"""Read-only artifact presentation; no execution or evaluation decisions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from psych_eval.evaluations import load_evaluation
from psych_eval.transcripts import load_transcript


SEVERITY_LABELS = {0: "None", 1: "Mild", 2: "Material", 3: "Severe"}
# Public display labels only. The UI never loads the authoring scenario.
SCENARIO_TITLES = {("RS-001", "1.0"): "Excluded by Friends"}


def format_identifier(value: str) -> str:
    return value.replace("_", " ").capitalize()


def severity_label(severity: int) -> str:
    return SEVERITY_LABELS[severity]


class ArtifactLoadError(Exception):
    """A complete page cannot be rendered from these saved artifacts."""


@dataclass(frozen=True)
class FindingView:
    turn_id: str
    severity: int
    severity_display: str
    mechanisms: tuple[str, ...]
    relational_proposition: str
    evidence: tuple[str, ...]
    rationale: str
    severity_3_basis: str | None


@dataclass(frozen=True)
class TranscriptTurnView:
    turn_id: str
    role: Literal["user", "assistant"]
    role_label: str
    content: str
    findings: tuple[FindingView, ...]


@dataclass(frozen=True)
class ProvenanceView:
    is_fixture: bool
    execution: str
    provider: str
    model: str


@dataclass(frozen=True)
class EvaluationView:
    scenario_heading: str
    scenario_id: str
    scenario_version: str
    construct: str
    execution_status: str
    evaluation_status: str
    overall_severity: int | None
    severity_display: str
    finding_count: int
    flagged_turn_count: int
    mechanisms: tuple[str, ...]
    findings: tuple[FindingView, ...]
    turns: tuple[TranscriptTurnView, ...]
    zero_rationale: str | None
    cannot_assess_reason: str | None
    recovery: str
    persistence: str
    provenance_kind: Literal["fixture", "live", "mixed"]
    banner_title: str
    banner_text: str
    target: ProvenanceView
    judge: ProvenanceView
    evaluation_id: str
    transcript_run_id: str
    rubric_version: str
    evaluator_version: str
    transcript_schema_version: str
    evaluation_schema_version: str
    sampling_temperature: float
    sampling_max_output_tokens: int
    max_retries: int
    retry_count: int


def load_evaluation_view(
    transcript_path: str | Path, evaluation_path: str | Path,
) -> EvaluationView:
    """Validate both canonical artifacts and their full relationship, then format.

    Canonical persistence owns schema/evidence/aggregation validation. This layer
    only copies validated results and computes presentation counts/associations.
    """
    try:
        transcript = load_transcript(transcript_path)
    except OSError as exc:
        raise ArtifactLoadError("Unable to load transcript artifact. The saved file could not be read.") from exc
    except ValueError as exc:
        raise ArtifactLoadError("Unable to load transcript artifact. The saved transcript did not pass schema validation.") from exc
    try:
        evaluation = load_evaluation(evaluation_path)
    except OSError as exc:
        raise ArtifactLoadError("Unable to load evaluation artifact. The saved file could not be read.") from exc
    except ValueError as exc:
        raise ArtifactLoadError("Unable to load evaluation artifact. The saved evaluation did not pass schema validation.") from exc
    if (
        transcript.run_id != evaluation.transcript_run_id
        or transcript != evaluation.source_transcript
    ):
        raise ArtifactLoadError(
            "Unable to combine artifacts. The transcript does not match the evaluation's saved transcript and run identity."
        )

    turn_order = {turn.turn_id: index for index, turn in enumerate(transcript.turns)}
    findings = tuple(
        FindingView(
            turn_id=finding.assistant_turn_id,
            severity=finding.severity,
            severity_display=f"{finding.severity} — {severity_label(finding.severity)}",
            mechanisms=tuple(format_identifier(item) for item in finding.mechanisms),
            relational_proposition=finding.relational_proposition,
            evidence=tuple(finding.evidence),
            rationale=finding.rationale,
            severity_3_basis=(
                format_identifier(finding.severity_3_basis)
                if finding.severity_3_basis is not None else None
            ),
        )
        for finding in sorted(evaluation.findings, key=lambda item: turn_order[item.assistant_turn_id])
    )
    turns = tuple(
        TranscriptTurnView(
            turn_id=turn.turn_id, role=turn.role, role_label=turn.role.capitalize(),
            content=turn.content,
            findings=tuple(finding for finding in findings if finding.turn_id == turn.turn_id),
        )
        for turn in transcript.turns
    )
    target_fixture = transcript.target.provider == "fixture"
    judge_fixture = evaluation.judge.mode == "fixture" and evaluation.judge.provider == "fixture"
    if target_fixture and judge_fixture:
        provenance_kind = "fixture"
        banner_title = "Fixture / Demo Data"
        banner_text = (
            "This evaluation uses predefined target and judge outputs to exercise the complete "
            "evaluation pipeline without live model inference. It is not an independent measurement of model behavior."
        )
    elif not target_fixture and not judge_fixture:
        provenance_kind = "live"
        banner_title = "Live Evaluation"
        banner_text = "These saved artifacts identify non-fixture target and live judge providers. Viewing them performs no model inference."
    else:
        provenance_kind = "mixed"
        banner_title = "Mixed Provenance"
        fixture_component = "target" if target_fixture else "judge"
        banner_text = (
            f"The {fixture_component} output is predefined fixture data; the other component is recorded as non-fixture. "
            "This is not a fully live evaluation. Viewing saved artifacts performs no model inference."
        )
    title = SCENARIO_TITLES.get((transcript.scenario_id, transcript.scenario_version))
    return EvaluationView(
        scenario_heading=f"{transcript.scenario_id} — {title}" if title else transcript.scenario_id,
        scenario_id=transcript.scenario_id, scenario_version=transcript.scenario_version,
        construct=format_identifier(evaluation.category),
        execution_status=format_identifier(evaluation.execution_status),
        evaluation_status=format_identifier(evaluation.evaluation_status),
        overall_severity=evaluation.overall_severity,
        severity_display=(
            f"{evaluation.overall_severity} — {severity_label(evaluation.overall_severity)}"
            if evaluation.overall_severity is not None else "Not assessed"
        ),
        finding_count=len(findings), flagged_turn_count=len({finding.turn_id for finding in findings}),
        mechanisms=tuple(dict.fromkeys(mechanism for finding in findings for mechanism in finding.mechanisms)),
        findings=findings, turns=turns,
        zero_rationale=evaluation.zero_rationale, cannot_assess_reason=evaluation.cannot_assess_reason,
        recovery=format_identifier(evaluation.recovery), persistence=format_identifier(evaluation.persistence),
        provenance_kind=provenance_kind, banner_title=banner_title, banner_text=banner_text,
        target=ProvenanceView(target_fixture, "Fixture" if target_fixture else "Non-fixture",
                              transcript.target.provider, transcript.target.model),
        judge=ProvenanceView(judge_fixture, format_identifier(evaluation.judge.mode),
                             evaluation.judge.provider, evaluation.judge.model),
        evaluation_id=str(evaluation.evaluation_id), transcript_run_id=str(transcript.run_id),
        rubric_version=evaluation.rubric_version, evaluator_version=evaluation.evaluator_version,
        transcript_schema_version=transcript.schema_version, evaluation_schema_version=evaluation.schema_version,
        sampling_temperature=transcript.target.sampling.temperature,
        sampling_max_output_tokens=transcript.target.sampling.max_output_tokens,
        max_retries=transcript.max_retries, retry_count=transcript.retry_count,
    )
