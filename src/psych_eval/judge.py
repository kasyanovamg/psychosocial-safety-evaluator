"""Provider-neutral judge boundary and strict V1 relational-sycophancy results."""

import hashlib
import json
from pathlib import Path
from typing import Literal, Protocol, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from psych_eval.scenarios import Construct, EvaluatorScenarioView, FailureMode, NonblankString
from psych_eval.transcripts import Transcript


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class JudgeConfig(_StrictModel):
    mode: Literal["fixture", "live"]
    provider: NonblankString
    model: NonblankString

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


class JudgeInput(_StrictModel):
    rubric_version: Literal["1.0"]
    scenario: EvaluatorScenarioView
    transcript: Transcript

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        for field in ("scenario_id", "scenario_version", "construct_name"):
            if getattr(self.scenario, field) != getattr(self.transcript, field):
                raise ValueError(f"judge scenario {field} must match transcript")
        return self


class Judge(Protocol):
    def assess(self, request: JudgeInput, *, config: JudgeConfig) -> str:
        """Return raw structured-result JSON or raise a technical exception.

        Consume only this request and config; do not execute a target or inspect
        scenario authoring data. No provider prompt is implied by this interface.
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


def validate_judge_result(raw_response: str, transcript: Transcript) -> JudgeResult:
    """Validate JSON, result semantics, and exact turn-local evidence."""
    try:
        if not isinstance(raw_response, str):
            raise TypeError("judge response must be a JSON string")
        result = JudgeResult.model_validate_json(raw_response)
        assistant_turns = {turn.turn_id: turn.content for turn in transcript.turns if turn.role == "assistant"}
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


class FixtureJudgeArtifact(_StrictModel):
    schema_version: Literal["1.0"]
    scenario_id: NonblankString
    scenario_version: NonblankString
    fixture_judge_id: NonblankString
    fixture_version: NonblankString
    rubric_version: Literal["1.0"]
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_response: NonblankString


class FixtureJudge:
    """Explicit replay of a versioned demo result for one transcript content."""

    def __init__(self, artifact: FixtureJudgeArtifact):
        try:
            self._artifact = FixtureJudgeArtifact.model_validate(artifact.model_dump())
            JudgeResult.model_validate_json(self._artifact.raw_response)
        except ValueError as exc:
            raise JudgeError("judge_schema", str(exc), raw_response=artifact.raw_response) from exc
        self._config = JudgeConfig(
            mode="fixture", provider="fixture",
            model=f"demo-{artifact.fixture_judge_id}-v{artifact.fixture_version}",
        )

    @property
    def config(self) -> JudgeConfig:
        return self._config

    @classmethod
    def from_file(cls, path: str | Path) -> "FixtureJudge":
        path = Path(path)
        try:
            with path.open(encoding="utf-8") as source:
                artifact = FixtureJudgeArtifact.model_validate(yaml.safe_load(source))
            return cls(artifact)
        except OSError as exc:
            raise JudgeError("judge_input", f"Fixture judge file {path}: {exc}") from exc
        except (ValueError, yaml.YAMLError) as exc:
            raise JudgeError("judge_schema", f"Fixture judge file {path}: {exc}") from exc
        except JudgeError as exc:
            exc.add_note(f"Fixture judge file: {path}")
            raise

    def assess(self, request: JudgeInput, *, config: JudgeConfig) -> str:
        if config != self.config:
            raise JudgeError("judge_input", "FixtureJudge requires its fixture/demo provenance")
        artifact = self._artifact
        if (
            request.scenario.scenario_id != artifact.scenario_id
            or request.scenario.scenario_version != artifact.scenario_version
            or request.rubric_version != artifact.rubric_version
            or transcript_fingerprint(request.transcript) != artifact.transcript_sha256
        ):
            raise JudgeError("judge_input", "fixture judge requires the exact versioned fixture transcript")
        return artifact.raw_response
