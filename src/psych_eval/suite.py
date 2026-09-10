"""Local suite execution, append-only inference records, and provider-free rebuilds.

The immutable execution manifest is the source of identity/configuration. Target
records and judge attempts are source artifacts; evaluations and run.json are
rebuildable projections. The newest valid evaluation is active independently of
the latest attempt's technical status; failed reruns remain diagnostic history.
"""

import argparse
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from psych_eval.evaluations import load_evaluation
from psych_eval.evaluator import evaluate_transcript, evaluation_from_response
from psych_eval.judge import Judge, JudgeConfig, JudgeError, JudgeInput, JudgeResult, validate_judge_result
from psych_eval.runner import Target, run_scenario
from psych_eval.runs import ArtifactRef, RunArtifact, ScenarioResult, SuiteConfig, _resolve_ref
from psych_eval.scenarios import EvaluatorScenarioView, NonblankString, RuntimeScenarioView, load_scenario
from psych_eval.transcripts import SamplingConfig, TargetConfig, Transcript, load_transcript


PACK_IDS = tuple(f"RS-{number:03}" for number in range(1, 21))
ROOT = Path(__file__).resolve().parents[2]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, serialize_by_alias=True)


class PlannedScenario(Record):
    scenario: EvaluatorScenarioView
    title: NonblankString
    target_ref: ArtifactRef


class ExecutionManifest(Record):
    schema_version: Literal["1.0"] = "1.0"
    implementation_version: Literal["suite-v1"] = "suite-v1"
    run_id: UUID
    created_at: AwareDatetime
    suite: SuiteConfig
    target_mode: Literal["fixture", "live"]
    target: TargetConfig
    judge: JudgeConfig
    # No textual provider prompt/sampling exists for fixture replay.
    judge_prompt_version: NonblankString | None = None
    judge_sampling: SamplingConfig | None = None
    target_max_retries: int = Field(ge=0)
    judge_max_retries: int = Field(ge=0)
    scenarios: list[PlannedScenario]

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if tuple(item.scenario.scenario_id for item in self.scenarios) != PACK_IDS:
            raise ValueError("execution manifest requires ordered RS-001 through RS-020")
        if self.suite.scenario_count != len(self.scenarios):
            raise ValueError("suite count must match plan")
        if len({item.target_ref for item in self.scenarios}) != len(self.scenarios):
            raise ValueError("duplicate target reference")
        if (self.target_mode == "fixture") != (self.target.provider == "fixture"):
            raise ValueError("target mode and provider provenance disagree")
        if self.judge_prompt_version != self.judge.prompt_version or self.judge_sampling != self.judge.sampling:
            raise ValueError("judge provenance must match the configuration passed to assess")
        return self


class TargetExecution(Record):
    schema_version: Literal["1.0"] = "1.0"
    suite_run_id: UUID
    target_mode: Literal["fixture", "live"]
    started_at: AwareDatetime
    finished_at: AwareDatetime
    scenario: RuntimeScenarioView
    transcript_ref: ArtifactRef
    transcript: Transcript

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("target timestamps out of order")
        for key in ("scenario_id", "scenario_version", "construct_name", "max_turns"):
            if getattr(self.scenario, key) != getattr(self.transcript, key):
                raise ValueError("target scenario must match transcript")
        if self.scenario.max_turns != len(self.scenario.user_turns):
            raise ValueError("invalid runtime turn count")
        users = [turn.content for turn in self.transcript.turns if turn.role == "user"]
        if users != self.scenario.user_turns[:len(users)]:
            raise ValueError("transcript user turns must match runtime input")
        if (self.target_mode == "fixture") != (self.transcript.target.provider == "fixture"):
            raise ValueError("target mode and transcript provider disagree")
        return self


class JudgeCall(Record):
    """One technical try within one intended judgment, not a statistical sample."""

    retry_index: int = Field(ge=0)
    started_at: AwareDatetime
    finished_at: AwareDatetime
    raw_response: str | None
    failure_stage: Literal["judge_input", "judge_call", "judge_schema"] | None
    failure_detail: NonblankString | None
    result: JudgeResult | None

    @model_validator(mode="after")
    def validate_call(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("judge timestamps out of order")
        if (self.failure_stage is None) != (self.failure_detail is None):
            raise ValueError("failure stage and detail must occur together")
        if self.failure_stage is None and self.raw_response is None:
            raise ValueError("successful call requires raw response")
        if (self.failure_stage is None) != (self.result is not None):
            raise ValueError("parsed result is required only for successful calls")
        return self


class JudgeAttempt(Record):
    schema_version: Literal["1.0"] = "1.0"
    suite_run_id: UUID
    attempt_id: UUID
    attempt_index: int = Field(ge=1)
    operation: Literal["initial", "judge_rerun"]
    transcript_ref: ArtifactRef
    request: JudgeInput
    judge: JudgeConfig
    judge_prompt_version: NonblankString | None
    judge_sampling: SamplingConfig | None
    max_retries: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    technical_status: Literal["completed", "failed"]
    calls: list[JudgeCall] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if self.request.transcript.execution_status != "completed":
            raise ValueError("suite judges only completed transcripts")
        if self.operation != ("initial" if self.attempt_index == 1 else "judge_rerun"):
            raise ValueError("operation must match attempt index")
        if self.retry_count != len(self.calls) - 1 or self.retry_count > self.max_retries:
            raise ValueError("invalid technical retry count")
        for index, call in enumerate(self.calls):
            if call.retry_index != index:
                raise ValueError("technical retries must be contiguous")
            if index and call.started_at < self.calls[index - 1].finished_at:
                raise ValueError("technical call timestamps overlap")
            if call.failure_stage is None:
                if validate_judge_result(call.raw_response, self.request.transcript) != call.result:
                    raise ValueError("parsed result must match saved raw response")
                if index != len(self.calls) - 1:
                    raise ValueError("cannot retry a successful judge call")
            elif call.failure_stage == "judge_schema" and call.raw_response is not None:
                try:
                    validate_judge_result(call.raw_response, self.request.transcript)
                except JudgeError:
                    pass
                else:
                    raise ValueError("schema failure carries a valid response")
        failed = self.calls[-1].failure_stage is not None
        if failed != (self.technical_status == "failed"):
            raise ValueError("technical status must match terminal call")
        if failed and self.retry_count != self.max_retries:
            raise ValueError("terminal judge failure requires exhausted retries")
        return self

    def evaluation(self):
        if self.technical_status == "failed":
            return None
        return evaluation_from_response(
            self.request, self.calls[-1].raw_response, self.judge,
            evaluation_id=self.attempt_id,
        )


def read_record(path: Path, model):
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except JudgeError as exc:
        raise ValueError(f"Invalid saved judge attempt: {path}: {exc}") from exc


def write_new(path: Path, value: BaseModel) -> None:
    """Exclusive creation: existing inference artifacts are never overwritten."""
    validated = type(value).model_validate(value.model_dump())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(validated.model_dump_json(indent=2) + "\n")


def write_derived(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        write_new(temporary, value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def discover_pack(directory: Path | None = None):
    directory = directory or ROOT / "scenarios/v1/relational_sycophancy"
    paths = sorted(Path(directory).glob("*.yaml"))
    if tuple(path.stem for path in paths) != PACK_IDS:
        raise ValueError("canonical pack requires exactly RS-001 through RS-020")
    scenarios = [load_scenario(path) for path in paths]
    for path, scenario in zip(paths, scenarios):
        if (scenario.scenario_id != path.stem or scenario.scenario_version != "1.0"
                or scenario.design_metadata.benchmark_split != "development"):
            raise ValueError("canonical scenario identity/version/split mismatch")
    return scenarios


def _target_record(root: Path, manifest: ExecutionManifest, plan: PlannedScenario):
    record = read_record(_resolve_ref(root, plan.target_ref), TargetExecution)
    if (record.suite_run_id != manifest.run_id or record.target_mode != manifest.target_mode
            or record.transcript.target != manifest.target
            or record.transcript.max_retries != manifest.target_max_retries):
        raise ValueError("target record run/configuration mismatch")
    expected = EvaluatorScenarioView(
        scenario_id=record.scenario.scenario_id, scenario_version=record.scenario.scenario_version,
        construct=record.scenario.construct_name,
    )
    if expected != plan.scenario:
        raise ValueError("target record scenario mismatch")
    transcript = load_transcript(_resolve_ref(root, record.transcript_ref))
    if transcript != record.transcript:
        raise ValueError("persisted transcript does not match target record")
    return record


def judge_saved_transcript(manifest_path: str | Path, scenario_id: str, judge: Judge) -> JudgeAttempt:
    """Intentional rerun appends a new attempt, never calls the target.

    The operation uses the manifest's independent judge config. Technical retries
    stay inside this attempt; changing judge configuration requires a new manifest.
    """
    manifest_path = Path(manifest_path)
    manifest = read_record(manifest_path, ExecutionManifest)
    root = manifest_path.parent
    plan = next((item for item in manifest.scenarios if item.scenario.scenario_id == scenario_id), None)
    if plan is None:
        raise ValueError("scenario not in execution manifest")
    record = _target_record(root, manifest, plan)
    if record.transcript.execution_status != "completed":
        raise ValueError("cannot judge incomplete target execution")
    request = JudgeInput(rubric_version="1.0", scenario=plan.scenario, transcript=record.transcript)
    directory = _resolve_ref(root, f"{scenario_id}/judge")
    directory.mkdir(parents=True, exist_ok=True)
    existing = _attempts(root, manifest, plan, record)
    index = len(existing) + 1
    path = directory / f"attempt-{index:03}.json"
    # Reserve BEFORE inference, so concurrent writers fail without another call.
    with path.open("x", encoding="utf-8", newline="\n") as output:
        calls = []
        for retry in range(manifest.judge_max_retries + 1):
            started = now()
            raw, stage, detail = None, None, None
            try:
                evaluation = evaluate_transcript(record.transcript, plan.scenario, judge, manifest.judge)
                raw = evaluation.raw_judge_response
            except JudgeError as exc:
                raw, stage, detail = exc.raw_response, exc.failure_stage, str(exc)
            calls.append(JudgeCall(
                retry_index=retry, started_at=started, finished_at=now(),
                raw_response=raw, failure_stage=stage, failure_detail=detail,
                result=validate_judge_result(raw, record.transcript) if stage is None else None,
            ))
            if stage is None:
                break
        attempt = JudgeAttempt(
            suite_run_id=manifest.run_id, attempt_id=uuid4(), attempt_index=index,
            operation="initial" if index == 1 else "judge_rerun",
            transcript_ref=record.transcript_ref, request=request, judge=manifest.judge,
            judge_prompt_version=manifest.judge_prompt_version, judge_sampling=manifest.judge_sampling,
            max_retries=manifest.judge_max_retries, retry_count=len(calls) - 1,
            technical_status="completed" if stage is None else "failed", calls=calls,
        )
        output.write(attempt.model_dump_json(indent=2) + "\n")
    return attempt


def _attempts(root: Path, manifest: ExecutionManifest, plan: PlannedScenario, record: TargetExecution):
    directory = _resolve_ref(root, f"{plan.scenario.scenario_id}/judge")
    paths = sorted(directory.glob("*.json"))
    attempts = []
    for index, path in enumerate(paths, 1):
        path = _resolve_ref(root, path.relative_to(root.resolve()).as_posix())
        attempt = read_record(path, JudgeAttempt)
        if path.name != f"attempt-{index:03}.json" or attempt.attempt_index != index:
            raise ValueError("judge attempt files/indices must be contiguous")
        if (attempt.suite_run_id != manifest.run_id or attempt.judge != manifest.judge
                or attempt.judge_prompt_version != manifest.judge_prompt_version
                or attempt.judge_sampling != manifest.judge_sampling
                or attempt.max_retries != manifest.judge_max_retries):
            raise ValueError("judge attempt run/configuration mismatch")
        if (attempt.transcript_ref != record.transcript_ref
                or attempt.request.transcript != record.transcript
                or attempt.request.scenario != plan.scenario):
            raise ValueError("judge attempt references wrong transcript/scenario")
        attempts.append(attempt)
    if len({attempt.attempt_id for attempt in attempts}) != len(attempts):
        raise ValueError("duplicate judge attempt ID")
    return attempts


def rebuild_run(manifest_path: str | Path, *, persist: bool = True, verify_derived: bool = False) -> RunArtifact:
    """Reparse raw saved responses, rebuild evaluations and aggregates with no inference.

    Every historic attempt is checked within the same transcript/config lineage.
    The newest technically completed attempt supplies the active evaluation,
    including a valid cannot_assess result. Failed attempts remain diagnostic
    history and do not invalidate an earlier valid evaluation.
    """
    manifest_path = Path(manifest_path)
    if manifest_path.name != "execution.json":
        raise ValueError("execution manifest must be named execution.json")
    root = manifest_path.parent
    manifest = read_record(manifest_path, ExecutionManifest)
    sources, derived, attempt_ids, transcript_refs = [], [], set(), set()
    for plan in manifest.scenarios:
        record = _target_record(root, manifest, plan)
        if record.transcript_ref in transcript_refs:
            raise ValueError("duplicate transcript reference")
        transcript_refs.add(record.transcript_ref)
        attempts = _attempts(root, manifest, plan, record)
        if (record.transcript.execution_status == "completed") != bool(attempts):
            raise ValueError("completed target requires judge attempt; incomplete target cannot have one")
        for attempt in attempts:
            if attempt.attempt_id in attempt_ids:
                raise ValueError("duplicate judge attempt ID")
            attempt_ids.add(attempt.attempt_id)
        latest_attempt = attempts[-1] if attempts else None
        latest_valid_attempt = next(
            (attempt for attempt in reversed(attempts) if attempt.technical_status == "completed"), None,
        )
        evaluation = latest_valid_attempt.evaluation() if latest_valid_attempt else None
        ref = f"{plan.scenario.scenario_id}/evaluations/attempt-{latest_valid_attempt.attempt_index:03}.json" if evaluation else None
        if evaluation:
            path = _resolve_ref(root, ref)
            if verify_derived and load_evaluation(path) != evaluation:
                raise ValueError("derived evaluation disagrees with immutable attempt")
            derived.append((path, evaluation))
        sources.append(ScenarioResult(
            scenario=plan.scenario, title=plan.title, transcript=record.transcript,
            transcript_ref=record.transcript_ref, evaluation=evaluation, evaluation_ref=ref,
            evaluation_status=evaluation.evaluation_status if evaluation else ("failed" if latest_attempt else "not_run"),
        ))
    run = RunArtifact.create(
        run_id=manifest.run_id, created_at=manifest.created_at, construct="relational_sycophancy",
        suite_id=manifest.suite.suite_id, suite_version=manifest.suite.suite_version,
        model_under_test=manifest.target, judge=manifest.judge, rubric_version="1.0",
        evaluator_version="1.0", scenarios=sources, execution_manifest_ref="execution.json",
    )
    if persist:
        for path, evaluation in derived:
            write_derived(path, evaluation)
        write_derived(root / "run.json", run)
    return run


def execute_suite(
    directory: str | Path, *, target_factory: Callable[[RuntimeScenarioView], Target],
    target_config: TargetConfig, target_mode: Literal["fixture", "live"],
    judge: Judge, judge_config: JudgeConfig, target_max_retries: int = 1,
    judge_max_retries: int = 0,
) -> RunArtifact:
    """One target sample per scenario; intentional target rerun uses a NEW bundle.

    Adapter errors become per-scenario technical failures. Persistence/validation
    errors fail closed (never fabricate successful artifacts on storage failure).
    No provider selection or fallback occurs here: both adapters are explicit.
    """
    scenarios = discover_pack()
    manifest = ExecutionManifest(
        run_id=uuid4(), created_at=now(),
        suite=SuiteConfig(suite_id="relational-sycophancy-development", suite_version="1.0", scenario_count=20),
        target_mode=target_mode, target=target_config, judge=judge_config,
        judge_prompt_version=judge_config.prompt_version, judge_sampling=judge_config.sampling,
        target_max_retries=target_max_retries, judge_max_retries=judge_max_retries,
        scenarios=[PlannedScenario(scenario=s.to_evaluator_view(), title=s.title,
                                   target_ref=f"{s.scenario_id}/target_call.json") for s in scenarios],
    )
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_new(directory / "execution.json", manifest)
    for scenario, plan in zip(scenarios, manifest.scenarios):
        runtime = scenario.to_runtime_view()
        started = now()
        # Lazy construction keeps factory/setup exceptions within runner retries.
        class LazyTarget:
            adapter = None

            def respond(self, messages, *, config):
                if self.adapter is None:
                    self.adapter = target_factory(runtime)
                return self.adapter.respond(messages, config=config)

        transcript = run_scenario(runtime, LazyTarget(), manifest.target, max_retries=target_max_retries)
        ref = f"{scenario.scenario_id}/transcript.json"
        write_new(directory / ref, transcript)
        record = TargetExecution(
            suite_run_id=manifest.run_id, target_mode=manifest.target_mode,
            started_at=started, finished_at=now(), scenario=runtime,
            transcript_ref=ref, transcript=transcript,
        )
        write_new(directory / plan.target_ref, record)
        if transcript.execution_status == "completed":
            judge_saved_transcript(directory / "execution.json", scenario.scenario_id, judge)
    return rebuild_run(directory / "execution.json")


def execute_fixture_pack(directory: str | Path) -> RunArtifact:
    from psych_eval.pack_fixtures import PackJudge, pack_target

    judge = PackJudge()
    config = pack_target(discover_pack()[0].to_runtime_view()).config
    return execute_suite(directory, target_factory=pack_target, target_config=config,
                         target_mode="fixture", judge=judge, judge_config=judge.config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("fixture", "rebuild", "judge-rerun"))
    parser.add_argument("path", type=Path, help="New bundle directory, or existing execution.json")
    parser.add_argument("--scenario", help="Scenario ID for an intentional fixture judge rerun")
    args = parser.parse_args()
    if args.operation == "fixture":
        run = execute_fixture_pack(args.path)
    elif args.operation == "rebuild":
        run = rebuild_run(args.path)
    else:
        from psych_eval.pack_fixtures import PackJudge

        judge_saved_transcript(args.path, args.scenario, PackJudge())
        run = rebuild_run(args.path)
    print(run.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
