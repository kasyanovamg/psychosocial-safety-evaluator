"""Application composition for reviewable local runs over existing engine APIs."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import shutil

from psych_eval.integrations.runtime import (
    IntegrationConfigError, RuntimeConfig, configure_integrations, configure_judge,
    load_runtime_config,
)
from psych_eval.judge import JudgeConfig
from psych_eval.judge_payload import DEFAULT_JUDGE_PROMPT_VERSION, INSTRUCTIONS_BY_VERSION, RUBRIC_VERSION
from psych_eval.runs import RunArtifact, load_run
from psych_eval.selection import ResolvedSelection, SelectionRequest, resolve_selection
from psych_eval.suite import (
    ExecutionManifest, SuiteProgress, TargetExecution, execute_suite,
    judge_saved_transcript, read_record, rebuild_run, write_new,
)
from psych_eval.transcripts import TargetConfig


@dataclass(frozen=True)
class EvaluationReview:
    """Public, secret-free values safe to retain in UI session state."""

    config_path: str
    config_digest: str
    target_integration: str
    target_config: TargetConfig
    judge_integration: str
    judge_config: JudgeConfig
    target_max_retries: int
    judge_max_retries: int
    selection: ResolvedSelection


@dataclass(frozen=True)
class RejudgeReview:
    """Secret-free, immutable review of an intentional judge-only operation."""

    config_path: str
    config_digest: str
    source_run_path: str
    source_digest: str
    destination_directory: str
    judge_integration: str
    judge_config: JudgeConfig
    judge_max_retries: int
    rubric_version: str
    completed_scenario_ids: tuple[str, ...]
    selected_scenario_ids: tuple[str, ...]


def _request_from_selection(selection: ResolvedSelection) -> SelectionRequest:
    return SelectionRequest(
        category=selection.category,
        scenario_pack_version=selection.scenario_pack_version,
        mode=selection.selection_mode,
        custom_scenario_ids=(
            list(selection.selected_scenario_ids)
            if selection.selection_mode == "custom" else None
        ),
    )


def _load(
    config_path: str | Path, request: SelectionRequest,
) -> tuple[RuntimeConfig, ResolvedSelection, str]:
    path = Path(config_path).expanduser()
    runtime = load_runtime_config(path)
    selection = resolve_selection(request)
    try:
        digest = sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise IntegrationConfigError("Unable to read runtime config") from None
    return runtime.model_copy(
        update={"scenario_selection": request.model_copy(deep=True)}, deep=True,
    ), selection, digest


def _review(
    path: str | Path, runtime: RuntimeConfig,
    selection: ResolvedSelection, digest: str,
) -> EvaluationReview:
    return EvaluationReview(
        config_path=str(Path(path).expanduser()), config_digest=digest,
        target_integration=runtime.target.integration,
        target_config=runtime.target.config.model_copy(deep=True),
        judge_integration=runtime.judge.integration,
        judge_config=runtime.judge.config.model_copy(deep=True),
        target_max_retries=runtime.target_max_retries,
        judge_max_retries=runtime.judge_max_retries,
        selection=selection.model_copy(deep=True),
    )


def prepare_evaluation(
    config_path: str | Path, request: SelectionRequest,
) -> EvaluationReview:
    """Validate selection and both configured adapters without running inference."""
    runtime, selection, digest = _load(config_path, request)
    configure_integrations(runtime)
    return _review(config_path, runtime, selection, digest)


def execute_evaluation(
    review: EvaluationReview, directory: str | Path, *,
    progress: Callable[[SuiteProgress], None] | None = None,
) -> RunArtifact:
    """Execute exactly the reviewed configuration, then reload persisted truth."""
    request = _request_from_selection(review.selection)
    runtime, selection, digest = _load(review.config_path, request)
    current = _review(review.config_path, runtime, selection, digest)
    if current != review:
        raise IntegrationConfigError("Runtime config or resolved selection changed; review it again")
    target_factory, judge = configure_integrations(runtime)
    directory = Path(directory).expanduser()
    execute_suite(
        directory, target_factory=target_factory, target_config=runtime.target.config,
        target_mode="fixture" if runtime.target.config.provider == "fixture" else "live",
        judge=judge, judge_config=runtime.judge.config,
        target_max_retries=runtime.target_max_retries,
        judge_max_retries=runtime.judge_max_retries,
        selection=selection, progress=progress,
    )
    return load_run(directory / "run.json", verify_references=True)


def _tree_digest(directory: Path) -> str:
    digest = sha256()
    try:
        paths = sorted(path for path in directory.rglob("*") if path.is_file())
        for path in paths:
            digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    except OSError:
        raise IntegrationConfigError("Unable to read saved run artifacts") from None
    return digest.hexdigest()


def _bundle_path(root: Path, reference: str) -> Path:
    root = root.resolve()
    path = (root / reference).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise IntegrationConfigError("Saved run contains an unsafe artifact reference") from None
    return path


def completed_saved_scenarios(run_path: str | Path) -> tuple[str, ...]:
    """Return completed saved transcript IDs without constructing integrations."""
    path = Path(run_path).expanduser()
    run = load_run(path, verify_references=True)
    return tuple(
        entry.scenario_id for entry in run.scenarios
        if entry.execution_status == "completed" and entry.transcript_ref is not None
    )


def default_rejudge_destination(run_path: str | Path, prompt_version: str) -> Path:
    source = Path(run_path).expanduser().parent
    return source.with_name(f"{source.name}-judge-v{prompt_version}")


def _rejudge_review(
    config_path: str | Path, source_run_path: str | Path,
    selected_scenario_ids: tuple[str, ...], destination: str | Path,
    *, configure: bool,
) -> tuple[RejudgeReview, RuntimeConfig]:
    config_path = Path(config_path).expanduser()
    source_run_path = Path(source_run_path).expanduser()
    destination = Path(destination).expanduser()
    runtime = load_runtime_config(config_path)
    try:
        config_digest = sha256(config_path.read_bytes()).hexdigest()
    except OSError:
        raise IntegrationConfigError("Unable to read runtime config") from None
    if destination.exists():
        raise IntegrationConfigError("Rejudge destination already exists; choose a new directory")
    try:
        destination.resolve().relative_to(source_run_path.parent.resolve())
    except ValueError:
        pass
    else:
        raise IntegrationConfigError("Rejudge destination must be outside the source run directory")
    try:
        completed = completed_saved_scenarios(source_run_path)
        run = load_run(source_run_path, verify_references=True)
    except (OSError, ValueError):
        raise IntegrationConfigError("Unable to load a valid saved run for rejudging") from None
    if run.rubric_version != RUBRIC_VERSION or run.execution_manifest_ref is None:
        raise IntegrationConfigError("Saved run does not support canonical transcript rejudging")
    selected = tuple(selected_scenario_ids)
    if not selected:
        raise IntegrationConfigError("Choose at least one completed conversation to rejudge")
    if len(selected) != len(set(selected)) or any(item not in completed for item in selected):
        raise IntegrationConfigError("Rejudge selection must contain unique completed conversations")
    selected = tuple(item for item in completed if item in set(selected))
    prompt_version = runtime.judge.config.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION
    if prompt_version not in INSTRUCTIONS_BY_VERSION:
        raise IntegrationConfigError("Unsupported judge prompt_version")
    if configure:
        configure_judge(runtime)
    review = RejudgeReview(
        config_path=str(config_path), config_digest=config_digest,
        source_run_path=str(source_run_path), source_digest=_tree_digest(source_run_path.parent),
        destination_directory=str(destination), judge_integration=runtime.judge.integration,
        judge_config=runtime.judge.config.model_copy(deep=True),
        judge_max_retries=runtime.judge_max_retries, rubric_version=run.rubric_version,
        completed_scenario_ids=completed, selected_scenario_ids=selected,
    )
    return review, runtime


def prepare_rejudge(
    config_path: str | Path, source_run_path: str | Path,
    selected_scenario_ids: list[str] | tuple[str, ...], destination: str | Path,
) -> RejudgeReview:
    """Review a saved-transcript rejudge without making inference calls."""
    review, _ = _rejudge_review(
        config_path, source_run_path, tuple(selected_scenario_ids), destination, configure=True,
    )
    return review


def _create_rejudge_fork(review: RejudgeReview, runtime: RuntimeConfig) -> Path:
    source_run_path = Path(review.source_run_path)
    source_root = source_run_path.parent
    destination = Path(review.destination_directory)
    source_run = load_run(source_run_path, verify_references=True)
    manifest_path = _bundle_path(source_root, source_run.execution_manifest_ref)
    source_manifest = read_record(manifest_path, ExecutionManifest)
    plan_by_id = {plan.scenario.scenario_id: plan for plan in source_manifest.scenarios}
    plans = [plan_by_id[scenario_id] for scenario_id in review.selected_scenario_ids]
    selection = resolve_selection(SelectionRequest(
        mode="custom", custom_scenario_ids=list(review.selected_scenario_ids),
    ))
    prompt_version = runtime.judge.config.prompt_version or DEFAULT_JUDGE_PROMPT_VERSION
    candidate = source_manifest.model_copy(update={
        "suite": source_manifest.suite.model_copy(update={"scenario_count": len(plans)}),
        "judge": runtime.judge.config,
        "rubric_version": RUBRIC_VERSION,
        "judge_prompt_version": prompt_version,
        "judge_sampling": runtime.judge.config.sampling,
        "judge_max_retries": runtime.judge_max_retries,
        "scenarios": plans,
        "selection": selection,
    })
    manifest = ExecutionManifest.model_validate(candidate.model_dump())
    destination.mkdir(parents=True, exist_ok=False)
    write_new(destination / "execution.json", manifest)
    for plan in plans:
        source_target = _bundle_path(source_root, plan.target_ref)
        record = read_record(source_target, TargetExecution)
        source_transcript = _bundle_path(source_root, record.transcript_ref)
        for source in (source_target, source_transcript):
            relative = source.relative_to(source_root.resolve())
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if target.read_bytes() != source.read_bytes():
                raise OSError("Copied source artifact failed byte verification")
    return destination


def execute_rejudge(
    review: RejudgeReview, *, progress: Callable[[SuiteProgress], None] | None = None,
) -> RunArtifact:
    """Fork selected transcripts and invoke only the reviewed judge configuration."""
    current, runtime = _rejudge_review(
        review.config_path, review.source_run_path, review.selected_scenario_ids,
        review.destination_directory, configure=False,
    )
    if current != review:
        raise IntegrationConfigError("Runtime config, source run, or selection changed; review it again")
    destination = Path(review.destination_directory)
    source_root = Path(review.source_run_path).parent
    # Collision and source-integrity checks precede adapter construction and inference.
    if destination.exists():
        raise IntegrationConfigError("Rejudge destination already exists; no judge calls were made")
    if _tree_digest(source_root) != review.source_digest:
        raise IntegrationConfigError("Source run changed; review it again")
    judge = configure_judge(runtime)
    _create_rejudge_fork(review, runtime)
    total = len(review.selected_scenario_ids)
    if progress is not None:
        progress(SuiteProgress(phase="started", processed=0, total=total))
    for index, scenario_id in enumerate(review.selected_scenario_ids, 1):
        if progress is not None:
            progress(SuiteProgress(
                phase="scenario_started", processed=index - 1, total=total,
                scenario_id=scenario_id,
            ))
        attempt = judge_saved_transcript(destination / "execution.json", scenario_id, judge)
        evaluation = attempt.evaluation()
        if progress is not None:
            progress(SuiteProgress(
                phase="scenario_completed", processed=index, total=total,
                scenario_id=scenario_id, execution_status="completed",
                evaluation_status=(evaluation.evaluation_status if evaluation else "failed"),
                failure_stage=(attempt.calls[-1].failure_stage if evaluation is None else None),
            ))
    run = rebuild_run(destination / "execution.json")
    if _tree_digest(source_root) != review.source_digest:
        raise IntegrationConfigError("Source run changed during rejudging")
    if progress is not None:
        progress(SuiteProgress(phase="completed", processed=total, total=total))
    return load_run(destination / "run.json", verify_references=True)
