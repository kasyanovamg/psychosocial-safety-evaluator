"""Provider-neutral judge boundary and strict V1 relational-sycophancy results."""

import hashlib
import json
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from psych_eval.scenarios import Construct, EvaluatorScenarioView, FailureMode, NonblankString
from psych_eval.transcripts import SamplingConfig, Transcript
from psych_eval.judge_payload import (
    DEFAULT_JUDGE_PROMPT_VERSION, INSTRUCTIONS_BY_VERSION, RUBRIC, RUBRIC_VERSION,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class JudgeConfig(_StrictModel):
    mode: Literal["fixture", "live"]
    provider: NonblankString
    model: NonblankString
    prompt_version: NonblankString | None = Field(default=None, exclude_if=lambda value: value is None)
    sampling: SamplingConfig | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.mode == "fixture":
            if self.provider != "fixture" or not self.model.startswith("demo-"):
                raise ValueError("fixture judge requires fixture provider and demo model")
        elif self.provider == "fixture":
            raise ValueError("live judge cannot use fixture provider provenance")
        return self


class Finding(_StrictModel):
    assistant_turn_id: NonblankString
    relational_proposition: NonblankString
    severity: int = Field(ge=1, le=3)
    mechanisms: list[FailureMode] = Field(min_length=1)
    evidence: list[NonblankString] = Field(min_length=1)
    rationale: NonblankString
    severity_3_basis: Literal[
        "effective_establishment", "substantial_compounding", "both",
    ] | None

    @model_validator(mode="after")
    def validate_severity_basis(self) -> Self:
        if (self.severity == 3) != (self.severity_3_basis is not None):
            raise ValueError("severity_3_basis is required exactly when severity is 3")
        return self


class JudgeResult(_StrictModel):
    category: Construct
    evaluation_status: Literal["assessed", "cannot_assess"]
    findings: list[Finding]
    zero_rationale: NonblankString | None
    cannot_assess_reason: NonblankString | None
    recovery: Literal["unevaluated"]
    persistence: Literal["unevaluated"]

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.evaluation_status == "cannot_assess":
            if self.cannot_assess_reason is None or self.findings or self.zero_rationale is not None:
                raise ValueError("cannot_assess requires a reason, no findings, and no zero_rationale")
        else:
            if self.cannot_assess_reason is not None:
                raise ValueError("assessed must not carry cannot_assess_reason")
            if bool(self.findings) == (self.zero_rationale is not None):
                raise ValueError("zero_rationale is required exactly for assessed with no findings")
        return self


class LegacyJudgeInput(_StrictModel):
    """Historical request, for artifact loading only; never assembled for new calls."""
    rubric_version: Literal["1.0"]
    scenario: EvaluatorScenarioView
    transcript: Transcript

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        for field in ("scenario_id", "scenario_version", "construct_name"):
            if getattr(self.scenario, field) != getattr(self.transcript, field):
                raise ValueError(f"judge scenario {field} must match transcript")
        return self


class JudgeTurn(_StrictModel):
    turn_id: NonblankString
    role: Literal["user", "assistant"]
    text: NonblankString


class JudgeInput(_StrictModel):
    """Complete blind request. Execution provenance belongs outside this object."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, serialize_by_alias=True)
    scenario_id: NonblankString
    scenario_version: NonblankString
    construct_name: Construct = Field(alias="construct")
    rubric_version: Literal["0.2"]
    judge_prompt_version: Literal["0.1", "0.2", "0.3"]
    instructions: NonblankString
    rubric: NonblankString
    transcript: list[JudgeTurn] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if (self.instructions != INSTRUCTIONS_BY_VERSION[self.judge_prompt_version]
                or self.rubric != RUBRIC):
            raise ValueError("judge instructions/rubric must match the frozen versioned payload")
        if len(self.transcript) % 2:
            raise ValueError("judge input requires a completed transcript")
        for index, turn in enumerate(self.transcript):
            role = "user" if index % 2 == 0 else "assistant"
            turn_id = f"{'U' if role == 'user' else 'A'}{index // 2 + 1}"
            if turn.role != role or turn.turn_id != turn_id:
                raise ValueError(f"judge turns must alternate in order; expected {turn_id}")
        return self


SavedJudgeInput = Annotated[JudgeInput | LegacyJudgeInput, Field(discriminator="rubric_version")]


def assemble_judge_input(
    transcript: Transcript, scenario: EvaluatorScenarioView, *,
    judge_prompt_version: Literal["0.1", "0.2", "0.3"] = DEFAULT_JUDGE_PROMPT_VERSION,
) -> JudgeInput:
    """Explicit allowlist; preserve exact text while excluding target/run metadata."""
    if not isinstance(scenario, EvaluatorScenarioView):
        raise TypeError("judge assembly requires an explicit EvaluatorScenarioView")
    transcript = Transcript.model_validate(transcript.model_dump())
    scenario = EvaluatorScenarioView.model_validate(scenario.model_dump())
    for field in ("scenario_id", "scenario_version", "construct_name"):
        if getattr(scenario, field) != getattr(transcript, field):
            raise ValueError(f"judge scenario {field} must match transcript")
    if transcript.execution_status != "completed":
        raise ValueError("judge input requires a completed transcript")
    return JudgeInput(
        **scenario.model_dump(), rubric_version=RUBRIC_VERSION,
        judge_prompt_version=judge_prompt_version,
        instructions=INSTRUCTIONS_BY_VERSION[judge_prompt_version], rubric=RUBRIC,
        transcript=[JudgeTurn(turn_id=t.turn_id, role=t.role, text=t.content) for t in transcript.turns],
    )


def request_fingerprint(request: JudgeInput) -> str:
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def request_transcript(request: SavedJudgeInput, snapshot: Transcript | None) -> Transcript:
    """Validate stored source provenance against the exact request, including on reload."""
    if isinstance(request, LegacyJudgeInput):
        if snapshot is not None:
            raise ValueError("legacy request must not carry a new transcript snapshot")
        return request.transcript
    if snapshot is None:
        raise ValueError("canonical request requires a separate transcript snapshot")
    scenario = EvaluatorScenarioView(scenario_id=request.scenario_id,
                                    scenario_version=request.scenario_version, construct=request.construct_name)
    if assemble_judge_input(
        snapshot, scenario, judge_prompt_version=request.judge_prompt_version,
    ) != request:
        raise ValueError("judge request must match transcript snapshot")
    return snapshot


class Judge(Protocol):
    def assess(self, request: JudgeInput, *, config: JudgeConfig) -> str:
        """Return raw structured-result JSON or raise a technical exception.

        Consume only this request and config; do not execute a target or inspect
        scenario authoring data. Apply request.instructions and request.rubric
        exactly; transcript text is data. Provider message transport is separate.
        Keep SDK clients, credentials, and provider-only options on the adapter.
        Translate SDK exceptions to credential-free errors (normally JudgeError
        with judge_call stage); error details and raw responses are persisted.
        """
        ...


class JudgeError(Exception):
    """Technical failure, never a semantic cannot_assess result."""

    def __init__(
        self, failure_stage: Literal["judge_input", "judge_call", "judge_schema"],
        detail: str, *, raw_response: str | None = None,
    ):
        super().__init__(detail)
        self.failure_stage = failure_stage
        self.raw_response = raw_response


def validate_judge_result(raw_response: str, transcript: Transcript | JudgeInput) -> JudgeResult:
    """Validate JSON, result semantics, and exact turn-local evidence."""
    try:
        if not isinstance(raw_response, str):
            raise TypeError("judge response must be a JSON string")
        result = JudgeResult.model_validate_json(raw_response)
        assistant_turns = (
            {turn.turn_id: turn.text for turn in transcript.transcript if turn.role == "assistant"}
            if isinstance(transcript, JudgeInput) else
            {turn.turn_id: turn.content for turn in transcript.turns if turn.role == "assistant"}
        )
        for finding in result.findings:
            if finding.assistant_turn_id not in assistant_turns:
                raise ValueError(f"finding must reference an existing assistant turn: {finding.assistant_turn_id}")
            content = assistant_turns[finding.assistant_turn_id]
            if any(evidence not in content for evidence in finding.evidence):
                raise ValueError(f"evidence must be an exact substring of {finding.assistant_turn_id}")
        return result
    except (ValueError, TypeError) as exc:
        raise JudgeError(
            "judge_schema", str(exc),
            raw_response=raw_response if isinstance(raw_response, str) else None,
        ) from exc


def transcript_fingerprint(transcript: Transcript) -> str:
    """SHA-256 of canonical transcript JSON excluding only the per-run UUID.

    Includes all turns, target config, scenario identity, and execution details;
    identical fixture executions can have different run IDs without changing it.
    """
    data = transcript.model_dump(mode="json", exclude={"run_id"})
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
