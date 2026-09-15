"""Application composition for reviewable local runs over existing engine APIs."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from psych_eval.integrations.runtime import (
    IntegrationConfigError, RuntimeConfig, configure_integrations, load_runtime_config,
)
from psych_eval.judge import JudgeConfig
from psych_eval.runs import RunArtifact, load_run
from psych_eval.selection import ResolvedSelection, SelectionRequest, resolve_selection
from psych_eval.suite import SuiteProgress, execute_suite
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
