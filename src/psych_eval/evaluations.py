"""Canonical evaluation artifact and independently verifiable JSON persistence."""

from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from psych_eval.judge import (
    JudgeConfig, JudgeError, JudgeInput, SavedJudgeInput, JudgeResult, request_transcript, validate_judge_result,
)
from psych_eval.transcripts import Transcript
from psych_eval.scenarios import NonblankString


def overall_severity(result: JudgeResult) -> int | None:
    """The shared deterministic aggregation, independent of transport."""
    return (max((finding.severity for finding in result.findings), default=0)
            if result.evaluation_status == "assessed" else None)


def select_latest_valid_attempt(attempts):
    """Ordered validated history; technical failures never replace valid results."""
    return next((attempt for attempt in reversed(attempts)
                 if attempt.technical_status == "completed"), None)


class Evaluation(JudgeResult):
    """Normalized result fields plus aggregation and exact judge input/output.

    judge_input is the complete structured request actually supplied
    to assess. raw_judge_response preserves the returned JSON string verbatim.
    """

    schema_version: Literal["1.0"]
    evaluation_id: UUID
    transcript_run_id: UUID
    scenario_id: NonblankString
    scenario_version: NonblankString
    execution_status: Literal["completed", "partial", "failed"]
    overall_severity: int | None = Field(ge=0, le=3)
    judge: JudgeConfig
    rubric_version: Literal["0.2", "1.0"]
    evaluator_version: Literal["1.0"]
    judge_input: SavedJudgeInput
    raw_judge_response: NonblankString
    transcript_snapshot: Transcript | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def source_transcript(self) -> Transcript:
        return request_transcript(self.judge_input, self.transcript_snapshot)

    @model_validator(mode="after")
    def validate_reproducibility(self) -> Self:
        transcript = self.source_transcript
        if (isinstance(self.judge_input, JudgeInput)
                and self.judge.prompt_version not in (None, self.judge_input.judge_prompt_version)):
            raise ValueError("judge prompt_version must match request")
        if (
            self.transcript_run_id != transcript.run_id
            or self.scenario_id != transcript.scenario_id
            or self.scenario_version != transcript.scenario_version
            or self.category != transcript.construct_name
            or self.execution_status != transcript.execution_status
            or self.rubric_version != self.judge_input.rubric_version
        ):
            raise ValueError("evaluation identity must match judge_input")
        try:
            validated = validate_judge_result(self.raw_judge_response, transcript)
        except JudgeError as exc:
            raise ValueError(f"invalid persisted judge response: {exc}") from exc
        normalized = {name: getattr(self, name) for name in JudgeResult.model_fields}
        if JudgeResult.model_validate(normalized) != validated:
            raise ValueError("normalized result must match raw_judge_response")
        expected = overall_severity(self)
        if self.overall_severity != expected:
            raise ValueError("overall_severity must equal deterministic finding maximum or null")
        return self


def save_evaluation(path: str | Path, evaluation: Evaluation) -> None:
    """Revalidate mutable nested data before writing; no judge or target calls."""
    path = Path(path)
    try:
        validated = Evaluation.model_validate(evaluation.model_dump())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(validated.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        exc.add_note(f"Evaluation file: {path}")
        raise


def load_evaluation(path: str | Path) -> Evaluation:
    """Validate a self-contained artifact without executing either model."""
    path = Path(path)
    try:
        return Evaluation.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        exc.add_note(f"Evaluation file: {path}")
        raise
