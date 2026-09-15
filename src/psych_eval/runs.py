"""Portable suite manifests and deterministic aggregation of saved results."""

import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self, get_args
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from psych_eval.evaluations import Evaluation, load_evaluation
from psych_eval.judge import JudgeConfig
from psych_eval.scenarios import Construct, EvaluatorScenarioView, FailureMode, NonblankString
from psych_eval.selection import ResolvedSelection
from psych_eval.transcripts import TargetConfig, Transcript, load_transcript


MECHANISMS = get_args(FailureMode)
SEVERITIES = ("0", "1", "2", "3")
Count = Annotated[int, Field(ge=0)]
EvaluationStatus = Literal["assessed", "cannot_assess", "not_run", "failed"]
ExecutionStatus = Literal["completed", "partial", "failed", "not_run"]


def _safe_ref(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value or value != value.strip() or path.is_absolute()
        or any(part in (".", "..", "") for part in value.split("/"))
        or any(character in value for character in ("\\", ":"))
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or path.as_posix() != value
    ):
        raise ValueError("artifact reference must be a safe relative POSIX path")
    return value


ArtifactRef = Annotated[str, AfterValidator(_safe_ref)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, serialize_by_alias=True)


class SuiteConfig(_StrictModel):
    suite_id: NonblankString
    suite_version: NonblankString
    scenario_count: int = Field(gt=0)


class ExecutionSummary(_StrictModel):
    planned: Count
    completed: Count
    partial: Count
    failed: Count
    not_run: Count


class EvaluationSummary(_StrictModel):
    assessed: Count
    cannot_assess: Count
    not_run: Count
    failed: Count


class MechanismCount(_StrictModel):
    finding_count: Count
    scenario_count: Count


class RunResults(_StrictModel):
    severity_distribution: dict[Literal["0", "1", "2", "3"], Count]
    material_or_higher: Count
    severe: Count
    mechanisms: dict[FailureMode, MechanismCount]

    @model_validator(mode="after")
    def validate_keys(self) -> Self:
        if set(self.severity_distribution) != set(SEVERITIES):
            raise ValueError("severity_distribution must contain exactly 0, 1, 2, 3")
        if set(self.mechanisms) != set(MECHANISMS):
            raise ValueError("mechanisms must contain every allowed mechanism")
        return self


class ScenarioIndexEntry(_StrictModel):
    scenario_id: NonblankString
    scenario_version: NonblankString
    title: NonblankString | None
    execution_status: ExecutionStatus
    evaluation_status: EvaluationStatus
    severity: int | None = Field(ge=0, le=3)
    finding_count: Count | None
    mechanism_finding_counts: dict[FailureMode, Count]
    transcript_run_id: UUID | None
    evaluation_id: UUID | None
    transcript_ref: ArtifactRef | None
    evaluation_ref: ArtifactRef | None

    @model_validator(mode="after")
    def validate_entry(self) -> Self:
        has_transcript = self.execution_status != "not_run"
        has_evaluation = self.evaluation_status in ("assessed", "cannot_assess")
        if (self.transcript_ref is not None) != has_transcript or (self.transcript_run_id is not None) != has_transcript:
            raise ValueError("execution status requires matching transcript reference and identity")
        if (self.evaluation_ref is not None) != has_evaluation or (self.evaluation_id is not None) != has_evaluation:
            raise ValueError("evaluation status requires matching evaluation reference and identity")
        if not has_transcript and self.evaluation_status != "not_run":
            raise ValueError("unexecuted scenarios must have evaluation_status not_run")
        eligible = self.execution_status == "completed" and self.evaluation_status == "assessed"
        if (self.severity is not None) != eligible:
            raise ValueError("severity is required only for completed + assessed scenarios")
        if (self.finding_count is not None) != (self.evaluation_status == "assessed"):
            raise ValueError("finding_count is required only for assessed scenarios")
        if set(self.mechanism_finding_counts) != set(MECHANISMS):
            raise ValueError("mechanism_finding_counts must contain every allowed mechanism")
        count = self.finding_count or 0
        if any(value > count for value in self.mechanism_finding_counts.values()):
            raise ValueError("mechanism finding counts cannot exceed finding_count")
        if sum(self.mechanism_finding_counts.values()) < count:
            raise ValueError("every finding must contain at least one mechanism")
        if eligible and ((self.severity == 0) != (count == 0)):
            raise ValueError("severity zero requires no findings; positive severity requires findings")
        return self


class Coverage(_StrictModel):
    """Persisted coverage facts derived from selection and scenario statuses."""

    full_pack_total: int = Field(gt=0)
    selected_count: Count
    executed_count: Count
    completed_count: Count
    valid_assessed_count: Count
    technical_failure_count: Count
    cannot_assess_count: Count
    executed_scenario_ids: list[NonblankString]
    completed_scenario_ids: list[NonblankString]
    valid_assessed_scenario_ids: list[NonblankString]
    technical_failure_scenario_ids: list[NonblankString]
    cannot_assess_scenario_ids: list[NonblankString]
    selection_complete: bool
    pack_coverage_complete: bool
    assessment_coverage_complete: bool
    coverage_label: Literal["full", "partial"]


def derive_coverage(selection: ResolvedSelection, entries: list[ScenarioIndexEntry]) -> Coverage:
    """Derive canonical coverage without treating unselected scenarios as results."""
    selected = selection.selected_scenario_ids
    executed = [entry.scenario_id for entry in entries if entry.execution_status != "not_run"]
    completed = [entry.scenario_id for entry in entries if entry.execution_status == "completed"]
    assessed = [
        entry.scenario_id for entry in entries
        if entry.execution_status == "completed" and entry.evaluation_status == "assessed"
    ]
    cannot_assess = [
        entry.scenario_id for entry in entries
        if entry.execution_status == "completed" and entry.evaluation_status == "cannot_assess"
    ]
    technical_failures = [
        entry.scenario_id for entry in entries
        if entry.execution_status in ("partial", "failed")
        or (entry.execution_status == "completed" and entry.evaluation_status == "failed")
    ]
    pack_complete = selected == selection.full_pack_scenario_ids
    assessment_complete = assessed == selection.full_pack_scenario_ids
    return Coverage(
        full_pack_total=selection.full_pack_total,
        selected_count=len(selected), executed_count=len(executed), completed_count=len(completed),
        valid_assessed_count=len(assessed), technical_failure_count=len(technical_failures),
        cannot_assess_count=len(cannot_assess),
        executed_scenario_ids=executed, completed_scenario_ids=completed,
        valid_assessed_scenario_ids=assessed, technical_failure_scenario_ids=technical_failures,
        cannot_assess_scenario_ids=cannot_assess,
        selection_complete=executed == selected,
        pack_coverage_complete=pack_complete,
        assessment_coverage_complete=assessment_complete,
        coverage_label="full" if assessment_complete else "partial",
    )


class ScenarioResult(_StrictModel):
    """Builder input only; full detailed artifacts are never stored in run.json.

    Missing evaluations require an explicit failed/not_run status. The canonical
    evaluation contract has no technical-failure artifact, so no such file is invented.
    """

    scenario: EvaluatorScenarioView
    title: NonblankString | None = None
    transcript: Transcript | None = None
    evaluation: Evaluation | None = None
    evaluation_status: EvaluationStatus
    transcript_ref: ArtifactRef | None = None
    evaluation_ref: ArtifactRef | None = None


def _summaries(entries: list[ScenarioIndexEntry]) -> tuple[ExecutionSummary, EvaluationSummary, RunResults]:
    execution = ExecutionSummary(
        planned=len(entries),
        **{status: sum(entry.execution_status == status for entry in entries)
           for status in get_args(ExecutionStatus)},
    )
    evaluation = EvaluationSummary(
        **{status: sum(entry.evaluation_status == status for entry in entries)
           for status in get_args(EvaluationStatus)},
    )
    eligible = [entry for entry in entries if entry.severity is not None]
    distribution = {severity: sum(entry.severity == int(severity) for entry in eligible) for severity in SEVERITIES}
    results = RunResults(
        severity_distribution=distribution,
        material_or_higher=distribution["2"] + distribution["3"], severe=distribution["3"],
        mechanisms={
            mechanism: MechanismCount(
                finding_count=sum(entry.mechanism_finding_counts[mechanism] for entry in eligible),
                scenario_count=sum(entry.mechanism_finding_counts[mechanism] > 0 for entry in eligible),
            )
            for mechanism in MECHANISMS
        },
    )
    return execution, evaluation, results


class RunArtifact(_StrictModel):
    schema_version: Literal["1.0", "1.1"]
    run_id: UUID
    created_at: AwareDatetime
    construct_name: Construct = Field(alias="construct")
    suite: SuiteConfig
    model_under_test: TargetConfig
    judge: JudgeConfig
    rubric_version: Literal["0.2", "1.0"]
    evaluator_version: Literal["1.0"]
    execution_summary: ExecutionSummary
    evaluation_summary: EvaluationSummary
    results: RunResults
    scenarios: list[ScenarioIndexEntry] = Field(min_length=1)
    execution_manifest_ref: ArtifactRef | None = Field(default=None, exclude_if=lambda value: value is None)
    selection: ResolvedSelection | None = Field(default=None, exclude_if=lambda value: value is None)
    coverage: Coverage | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if self.suite.scenario_count != len(self.scenarios):
            raise ValueError("suite scenario_count must match scenario index")
        for field in ("scenario_id", "transcript_run_id", "evaluation_id"):
            values = [getattr(entry, field) for entry in self.scenarios if getattr(entry, field) is not None]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {field} in run")
        refs = [ref for entry in self.scenarios for ref in (entry.transcript_ref, entry.evaluation_ref) if ref is not None]
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate artifact references in run")
        expected = _summaries(self.scenarios)
        if (self.execution_summary, self.evaluation_summary, self.results) != expected:
            raise ValueError("stored aggregate counts must match scenario index")
        if self.schema_version == "1.0":
            if self.selection is not None or self.coverage is not None:
                raise ValueError("run schema 1.0 must not contain selection metadata")
        else:
            if self.selection is None or self.coverage is None:
                raise ValueError("run schema 1.1 requires selection and coverage metadata")
            if self.construct_name != self.selection.category:
                raise ValueError("selection category must match run construct")
            if [entry.scenario_id for entry in self.scenarios] != self.selection.selected_scenario_ids:
                raise ValueError("run scenario index must match selected scenario IDs")
            if self.coverage != derive_coverage(self.selection, self.scenarios):
                raise ValueError("stored coverage must match selection and scenario index")
        return self

    @classmethod
    def create(
        cls, *, run_id: UUID, created_at: AwareDatetime, construct: Construct,
        suite_id: str, suite_version: str, model_under_test: TargetConfig,
        judge: JudgeConfig, rubric_version: Literal["0.2", "1.0"], evaluator_version: Literal["1.0"],
        scenarios: list[ScenarioResult],
        execution_manifest_ref: str | None = None,
        selection: ResolvedSelection | None = None,
    ) -> Self:
        """Pure construction: caller supplies identity/time and expected configuration.

        Every supplied canonical artifact is revalidated, including nested mutable
        data. No files are opened, models executed, or timestamps generated here.
        """
        target = TargetConfig.model_validate(model_under_test.model_dump())
        judge = JudgeConfig.model_validate(judge.model_dump())
        entries = []
        for source in scenarios:
            source = ScenarioResult.model_validate(source.model_dump())
            scenario, transcript, evaluation = source.scenario, source.transcript, source.evaluation
            if scenario.construct_name != construct:
                raise ValueError("scenario construct must match run construct")
            if transcript is None:
                if source.transcript_ref is not None or evaluation is not None:
                    raise ValueError("missing transcript cannot have a reference or evaluation")
            else:
                if (
                    scenario.scenario_id != transcript.scenario_id
                    or scenario.scenario_version != transcript.scenario_version
                    or scenario.construct_name != transcript.construct_name
                ):
                    raise ValueError("scenario identity/version/construct must match transcript")
                if transcript.target != target:
                    raise ValueError("target configuration must match model_under_test")
            if evaluation is not None:
                if transcript != evaluation.source_transcript:
                    raise ValueError("evaluation must reference the exact transcript and run identity")
                if source.evaluation_status != evaluation.evaluation_status:
                    raise ValueError("scenario evaluation_status must match evaluation artifact")
                if evaluation.judge != judge:
                    raise ValueError("judge configuration must match run judge")
                if evaluation.rubric_version != rubric_version:
                    raise ValueError("rubric_version must match run")
                if evaluation.evaluator_version != evaluator_version:
                    raise ValueError("evaluator_version must match run")
            elif source.evaluation_status not in ("not_run", "failed"):
                raise ValueError("assessed/cannot_assess requires an evaluation artifact")
            findings = evaluation.findings if evaluation is not None and evaluation.evaluation_status == "assessed" else None
            eligible = transcript is not None and transcript.execution_status == "completed" and findings is not None
            entries.append(ScenarioIndexEntry(
                scenario_id=scenario.scenario_id, scenario_version=scenario.scenario_version, title=source.title,
                execution_status=transcript.execution_status if transcript is not None else "not_run",
                evaluation_status=source.evaluation_status,
                severity=evaluation.overall_severity if eligible else None,
                finding_count=len(findings) if findings is not None else None,
                mechanism_finding_counts={
                    mechanism: sum(mechanism in finding.mechanisms for finding in (findings or []))
                    for mechanism in MECHANISMS
                },
                transcript_run_id=transcript.run_id if transcript is not None else None,
                evaluation_id=evaluation.evaluation_id if evaluation is not None else None,
                transcript_ref=source.transcript_ref, evaluation_ref=source.evaluation_ref,
            ))
        execution, evaluation_summary, results = _summaries(entries)
        coverage = derive_coverage(selection, entries) if selection is not None else None
        return cls(
            schema_version="1.1" if selection is not None else "1.0",
            run_id=run_id, created_at=created_at, construct=construct,
            suite=SuiteConfig(suite_id=suite_id, suite_version=suite_version, scenario_count=len(entries)),
            model_under_test=target, judge=judge, rubric_version=rubric_version, evaluator_version=evaluator_version,
            execution_summary=execution, evaluation_summary=evaluation_summary, results=results, scenarios=entries,
            execution_manifest_ref=execution_manifest_ref,
            selection=selection, coverage=coverage,
        )


def save_run(path: str | Path, run: RunArtifact) -> None:
    """Validate before saving readable UTF-8 JSON, matching detail persistence."""
    path = Path(path)
    try:
        validated = RunArtifact.model_validate(run.model_dump())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(validated.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        exc.add_note(f"Run file: {path}")
        raise


def _resolve_ref(directory: Path, ref: str) -> Path:
    root = directory.resolve()
    resolved = (root / ref).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("artifact reference resolves outside the run bundle")
    return resolved


def _parse_versioned_run(text: str) -> RunArtifact:
    """Dispatch explicitly so unsupported future schemas are never guessed."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("run artifact must be a JSON object")
    version = raw.get("schema_version")
    readers = {
        "1.0": RunArtifact.model_validate_json,
        "1.1": RunArtifact.model_validate_json,
    }
    reader = readers.get(version)
    if reader is None:
        raise ValueError(f"unsupported run schema_version {version!r}")
    return reader(text)


def load_run(path: str | Path, *, verify_references: bool = False) -> RunArtifact:
    """Validate the manifest without opening details by default.

    verify_references additionally resolves safe bundle-relative references and
    rebuilds from canonical detail artifacts to detect stale indexes/configuration.
    Stored values are rejected on disagreement, never silently repaired.
    """
    path = Path(path)
    try:
        run = _parse_versioned_run(path.read_text(encoding="utf-8"))
        if verify_references:
            sources = []
            resolved_refs = set()
            for entry in run.scenarios:
                paths = []
                for ref in (entry.transcript_ref, entry.evaluation_ref):
                    resolved = _resolve_ref(path.parent, ref) if ref is not None else None
                    if resolved is not None:
                        if resolved in resolved_refs:
                            raise ValueError("duplicate resolved artifact references in run")
                        resolved_refs.add(resolved)
                    paths.append(resolved)
                sources.append(ScenarioResult(
                    scenario=EvaluatorScenarioView(scenario_id=entry.scenario_id, scenario_version=entry.scenario_version,
                                                   construct=run.construct_name),
                    title=entry.title, evaluation_status=entry.evaluation_status,
                    transcript=load_transcript(paths[0]) if paths[0] is not None else None,
                    evaluation=load_evaluation(paths[1]) if paths[1] is not None else None,
                    transcript_ref=entry.transcript_ref, evaluation_ref=entry.evaluation_ref,
                ))
            rebuilt = RunArtifact.create(
                run_id=run.run_id, created_at=run.created_at, construct=run.construct_name,
                suite_id=run.suite.suite_id, suite_version=run.suite.suite_version,
                model_under_test=run.model_under_test, judge=run.judge,
                rubric_version=run.rubric_version, evaluator_version=run.evaluator_version, scenarios=sources,
                execution_manifest_ref=run.execution_manifest_ref,
                selection=run.selection,
            )
            if rebuilt != run:
                raise ValueError("run index/aggregates do not match referenced artifacts")
            if run.execution_manifest_ref is not None:
                from psych_eval.suite import rebuild_run

                verified = rebuild_run(
                    _resolve_ref(path.parent, run.execution_manifest_ref), persist=False,
                    verify_derived=True,
                )
                if verified != run:
                    raise ValueError("run does not match saved execution/attempt artifacts")
        return run
    except (OSError, ValueError) as exc:
        exc.add_note(f"Run file: {path}")
        raise
