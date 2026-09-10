"""Strict execution artifacts and JSON persistence, independent of evaluation."""

from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from psych_eval.scenarios import Construct, NonblankString


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, serialize_by_alias=True,
    )


class SamplingConfig(_StrictModel):
    """Explicit generation settings supplied to the target on every attempt."""

    temperature: float = Field(ge=0, allow_inf_nan=False)
    max_output_tokens: int = Field(gt=0)


class TargetConfig(_StrictModel):
    provider: NonblankString
    model: NonblankString
    system_prompt: str
    sampling: SamplingConfig


class TranscriptTurn(_StrictModel):
    turn_id: NonblankString
    role: Literal["user", "assistant"]
    content: NonblankString


class ExecutionFailure(_StrictModel):
    failure_stage: Literal["target_execution"]
    failure_reason: Literal["target_exception", "invalid_response"]
    detail: NonblankString
    # Additional attempts on the terminally failing turn, excluding its first call.
    retry_count: int = Field(ge=0)


class Transcript(_StrictModel):
    schema_version: Literal["1.0"]
    run_id: UUID
    scenario_id: NonblankString
    scenario_version: NonblankString
    construct_name: Construct = Field(alias="construct")
    execution_status: Literal["completed", "partial", "failed"]
    max_turns: int = Field(gt=0)
    turns: list[TranscriptTurn]
    target: TargetConfig
    max_retries: int = Field(ge=0)
    # Total additional target calls across all attempted user turns.
    retry_count: int = Field(ge=0)
    failure: ExecutionFailure | None

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        for index, turn in enumerate(self.turns):
            role = "user" if index % 2 == 0 else "assistant"
            turn_id = f"{'U' if role == 'user' else 'A'}{index // 2 + 1}"
            if turn.role != role or turn.turn_id != turn_id:
                raise ValueError(f"turns must alternate in order; expected {turn_id} ({role})")

        count = len(self.turns)
        if self.execution_status == "completed":
            if count != 2 * self.max_turns or self.failure is not None:
                raise ValueError("completed execution requires every response and no failure")
        else:
            if self.failure is None or count % 2 != 1 or count >= 2 * self.max_turns:
                raise ValueError("unsuccessful execution requires failure and an unanswered user turn")
            if self.execution_status == "failed" and count != 1:
                raise ValueError("failed execution must have no assistant responses")
            if self.execution_status == "partial" and count < 3:
                raise ValueError("partial execution requires a prior assistant response")
            if self.failure.retry_count != self.max_retries:
                raise ValueError("terminal failure requires exhausted retries")
            if self.failure.retry_count > self.retry_count:
                raise ValueError("failure retry_count cannot exceed total retry_count")

        attempted_turns = (count + 1) // 2
        if self.retry_count > attempted_turns * self.max_retries:
            raise ValueError("retry_count exceeds the configured retry budget")
        return self


def save_transcript(path: str | Path, transcript: Transcript) -> None:
    """Validate and save a readable UTF-8 artifact; never invoke a target."""
    # Revalidate nested mutable data before writing, too.
    validated = Transcript.model_validate(transcript.model_dump())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(validated.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")


def load_transcript(path: str | Path) -> Transcript:
    """Reload and validate a saved artifact without execution dependencies."""
    path = Path(path)
    try:
        return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        exc.add_note(f"Transcript file: {path}")
        raise
