"""Read-only run overview and selected scenario presentation."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from psych_eval.presentation import (
    ArtifactLoadError, EvaluationView, ProvenanceView, format_identifier,
    load_evaluation_view, severity_label,
)
from psych_eval.runs import RunArtifact, load_run


@dataclass(frozen=True)
class SeverityCountView:
    label: str
    scenario_count: int


@dataclass(frozen=True)
class MechanismView:
    label: str
    finding_count: int
    scenario_count: int


@dataclass(frozen=True)
class ScenarioRowView:
    scenario_id: str
    heading: str
    scenario_version: str
    severity_display: str
    finding_count: int | None
    execution_status: str
    evaluation_status: str
    has_details: bool


@dataclass(frozen=True)
class CoverageView:
    label: str
    selection_mode: str
    selection_version: str | None
    scenario_pack_id: str
    scenario_pack_version: str
    full_pack_total: int
    selected_count: int
    executed_count: int
    valid_assessed_count: int
    technical_failure_count: int
    cannot_assess_count: int
    selection_complete: bool
    pack_coverage_complete: bool
    assessment_coverage_complete: bool
    selected_scenario_ids: tuple[str, ...]


@dataclass(frozen=True)
class TechnicalFailureView:
    scenario_id: str
    stage: str
    detail: str
    judge_attempt_index: int | None


@dataclass(frozen=True)
class RunView:
    run_id: str
    created_at: str
    construct: str
    suite_id: str
    suite_version: str
    model_under_test: ProvenanceView
    judge: ProvenanceView
    rubric_version: str
    evaluator_version: str
    judge_prompt_version: str | None
    judge_temperature: float | None
    judge_reasoning_effort: str | None
    judge_max_output_tokens: int | None
    sampling_temperature: float
    sampling_max_output_tokens: int
    planned: int
    assessed: int
    execution_counts: tuple[tuple[str, int], ...]
    evaluation_counts: tuple[tuple[str, int], ...]
    severity_distribution: tuple[SeverityCountView, ...]
    material_or_higher: int
    severe: int
    mechanisms: tuple[MechanismView, ...]
    scenarios: tuple[ScenarioRowView, ...]
    provenance_kind: Literal["fixture", "live", "mixed"]
    disclosure: str
    coverage: CoverageView | None
    technical_failures: tuple[TechnicalFailureView, ...]
    detail: EvaluationView | None = None


def present_run(run: RunArtifact) -> RunView:
    """Project a validated run; counts come from its canonical summaries/index.

    This function opens no files and computes no scoring or aggregation.
    Evidence-backed UI callers should use load_run_view for reference verification.
    """
    target_fixture = run.model_under_test.provider == "fixture"
    judge_fixture = run.judge.mode == "fixture" and run.judge.provider == "fixture"
    if target_fixture and judge_fixture:
        kind = "fixture"
        disclosure = (
            "Fixture demo · Pre-generated model-under-test and judge artifacts. No live inference occurs. "
            "These results are illustrative, not an independent measurement of a production model."
            " Full-pack fixtures test infrastructure, not evaluator validity or benchmark truth."
        )
    elif target_fixture or judge_fixture:
        kind = "mixed"
        disclosure = "Mixed provenance · Includes predefined fixture output. This is not a fully live evaluation. Viewing these artifacts makes no model calls."
    else:
        kind = "live"
        disclosure = "Live evaluation artifacts · The saved configuration identifies non-fixture providers. Viewing these artifacts makes no model calls."
    rows = []
    for entry in run.scenarios:
        if entry.severity is not None:
            state = f"{entry.severity} — {severity_label(entry.severity)}"
        elif entry.execution_status != "completed":
            state = f"Execution: {format_identifier(entry.execution_status)}"
        else:
            state = f"Evaluation: {format_identifier(entry.evaluation_status)}"
        rows.append(ScenarioRowView(
            scenario_id=entry.scenario_id,
            heading=f"{entry.scenario_id} — {entry.title}" if entry.title else entry.scenario_id,
            scenario_version=entry.scenario_version, severity_display=state,
            finding_count=entry.finding_count,
            execution_status=format_identifier(entry.execution_status),
            evaluation_status=format_identifier(entry.evaluation_status),
            has_details=entry.transcript_ref is not None and entry.evaluation_ref is not None,
        ))
    coverage = None
    if run.selection is not None and run.coverage is not None:
        coverage = CoverageView(
            label=format_identifier(run.coverage.coverage_label),
            selection_mode=format_identifier(run.selection.selection_mode),
            selection_version=run.selection.selection_version,
            scenario_pack_id=run.selection.scenario_pack_id,
            scenario_pack_version=run.selection.scenario_pack_version,
            full_pack_total=run.coverage.full_pack_total,
            selected_count=run.coverage.selected_count,
            executed_count=run.coverage.executed_count,
            valid_assessed_count=run.coverage.valid_assessed_count,
            technical_failure_count=run.coverage.technical_failure_count,
            cannot_assess_count=run.coverage.cannot_assess_count,
            selection_complete=run.coverage.selection_complete,
            pack_coverage_complete=run.coverage.pack_coverage_complete,
            assessment_coverage_complete=run.coverage.assessment_coverage_complete,
            selected_scenario_ids=tuple(run.selection.selected_scenario_ids),
        )
    return RunView(
        run_id=str(run.run_id), created_at=run.created_at.isoformat(),
        construct=format_identifier(run.construct_name),
        suite_id=run.suite.suite_id, suite_version=run.suite.suite_version,
        model_under_test=ProvenanceView(target_fixture, "Fixture" if target_fixture else "Non-fixture",
                                       run.model_under_test.provider, run.model_under_test.model),
        judge=ProvenanceView(judge_fixture, format_identifier(run.judge.mode), run.judge.provider, run.judge.model),
        rubric_version=run.rubric_version, evaluator_version=run.evaluator_version,
        judge_prompt_version=run.judge.prompt_version,
        judge_temperature=(run.judge.sampling.temperature if run.judge.sampling else None),
        judge_reasoning_effort=(
            run.judge.sampling.reasoning.effort
            if run.judge.sampling and run.judge.sampling.reasoning else None
        ),
        judge_max_output_tokens=(
            run.judge.sampling.max_output_tokens if run.judge.sampling else None
        ),
        sampling_temperature=run.model_under_test.sampling.temperature,
        sampling_max_output_tokens=run.model_under_test.sampling.max_output_tokens,
        planned=run.execution_summary.planned, assessed=run.evaluation_summary.assessed,
        execution_counts=tuple((format_identifier(key), value) for key, value in run.execution_summary.model_dump().items()),
        evaluation_counts=tuple((format_identifier(key), value) for key, value in run.evaluation_summary.model_dump().items()),
        severity_distribution=tuple(
            SeverityCountView(f"{number} — {severity_label(number)}", run.results.severity_distribution[str(number)])
            for number in range(4)
        ),
        material_or_higher=run.results.material_or_higher, severe=run.results.severe,
        mechanisms=tuple(MechanismView(format_identifier(key), value.finding_count, value.scenario_count)
                         for key, value in run.results.mechanisms.items()),
        scenarios=tuple(rows), provenance_kind=kind, disclosure=disclosure,
        coverage=coverage, technical_failures=(),
    )


def load_run_view(path: str | Path, *, scenario_id: str | None = None) -> RunView:
    """Fail closed on the entire bundle before exposing overview or detail.

    Reference verification necessarily reads all referenced artifacts. Overview
    rows then come only from the index; only the selected scenario is transformed
    into an evidence/transcript view. No directory scanning or cached trust.
    """
    path = Path(path)
    try:
        run = load_run(path, verify_references=True)
        failures = ()
        if run.execution_manifest_ref is not None:
            from psych_eval.suite import load_technical_failures

            failures = tuple(TechnicalFailureView(
                scenario_id=item.scenario_id,
                stage=format_identifier(item.failure_stage),
                detail=item.detail,
                judge_attempt_index=item.judge_attempt_index,
            ) for item in load_technical_failures(path.parent / run.execution_manifest_ref))
    except OSError as exc:
        raise ArtifactLoadError("Unable to load evaluation run. The run or a referenced artifact could not be read.") from exc
    except ValueError as exc:
        raise ArtifactLoadError("Unable to load evaluation run. The run or its referenced artifacts did not pass validation.") from exc
    view = replace(present_run(run), technical_failures=failures)
    if scenario_id is None:
        return view
    entry = next((entry for entry in run.scenarios if entry.scenario_id == scenario_id), None)
    if entry is None:
        raise ArtifactLoadError("Unable to open scenario. It is not in the validated run index.")
    if entry.transcript_ref is None or entry.evaluation_ref is None:
        raise ArtifactLoadError("Unable to open scenario. No detailed evaluation artifact is recorded.")
    detail = load_evaluation_view(path.parent / entry.transcript_ref, path.parent / entry.evaluation_ref)
    heading = next(row.heading for row in view.scenarios if row.scenario_id == scenario_id)
    return replace(view, detail=replace(detail, scenario_heading=heading))
