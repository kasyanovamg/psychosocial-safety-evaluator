"""Append-only manual judge imports, without target execution or API provenance.

The frozen request is the transcript snapshot. Exact UTF-8 request text and its
file hash are retained in addition to the canonical semantic fingerprint.
"""

from hashlib import sha256
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from psych_eval.evaluations import overall_severity, select_latest_valid_attempt
from psych_eval.judge import JudgeError, JudgeInput, JudgeResult, request_fingerprint, validate_judge_result
from psych_eval.scenarios import NonblankString
from psych_eval.suite import Record, now, read_record, write_new


class ManualJudgeProvenance(Record):
    mode: Literal["manual"] = "manual"
    interface: NonblankString
    displayed_model: NonblankString


def _validated_response(raw: str, request: JudgeInput):
    try:
        return validate_judge_result(raw, request), None, None, None
    except JudgeError as exc:
        cause = exc.__cause__
        invalid_json = isinstance(cause, ValidationError) and any(
            error["type"] == "json_invalid" for error in cause.errors()
        )
        return None, exc.failure_stage, "invalid_json" if invalid_json else "invalid_response", str(exc)


class ManualJudgeAttempt(Record):
    schema_version: Literal["manual-1.0"] = "manual-1.0"
    attempt_id: UUID
    attempt_index: int = Field(ge=1)
    recorded_at: AwareDatetime
    # Only supplied when genuinely known; recorded_at is never execution time.
    model_executed_at: AwareDatetime | None = Field(default=None, exclude_if=lambda value: value is None)
    judge: ManualJudgeProvenance
    request_json: NonblankString
    request: JudgeInput
    request_sha256: str
    request_file_sha256: str
    raw_response: str
    raw_response_sha256: str
    technical_status: Literal["completed", "failed"]
    failure_stage: Literal["judge_schema"] | None
    failure_reason: Literal["invalid_json", "invalid_response"] | None
    failure_detail: NonblankString | None
    result: JudgeResult | None
    overall_severity: int | None = Field(ge=0, le=3)

    @model_validator(mode="after")
    def validate_reproducibility(self) -> Self:
        if JudgeInput.model_validate_json(self.request_json) != self.request:
            raise ValueError("request snapshot must match exact request JSON")
        if (request_fingerprint(self.request) != self.request_sha256
                or sha256(self.request_json.encode("utf-8")).hexdigest() != self.request_file_sha256):
            raise ValueError("request fingerprint mismatch")
        if sha256(self.raw_response.encode("utf-8")).hexdigest() != self.raw_response_sha256:
            raise ValueError("raw response fingerprint mismatch")
        result, stage, reason, detail = _validated_response(self.raw_response, self.request)
        if (self.result, self.failure_stage, self.failure_reason) != (result, stage, reason):
            raise ValueError("stored validation must match raw response")
        # Error wording may change between validator versions; classification may not.
        if (self.failure_detail is None) != (detail is None):
            raise ValueError("failure detail must accompany validation failure")
        if self.technical_status != ("completed" if result is not None else "failed"):
            raise ValueError("technical status must match validation")
        expected = overall_severity(result) if result is not None else None
        if self.overall_severity != expected:
            raise ValueError("severity must match deterministic aggregation")
        return self


def load_manual_attempt(path: str | Path) -> ManualJudgeAttempt:
    return read_record(Path(path), ManualJudgeAttempt)


def load_manual_history(request_path: str | Path) -> list[ManualJudgeAttempt]:
    """Revalidate all source records against the exact external frozen request."""
    request_path = Path(request_path)
    request_bytes = request_path.read_bytes()
    request = JudgeInput.model_validate_json(request_bytes)
    attempts = []
    for index, path in enumerate(sorted((request_path.parent / "attempts").glob("*.json")), 1):
        attempt = load_manual_attempt(path)
        if path.name != f"attempt-{index:03}.json" or attempt.attempt_index != index:
            raise ValueError("manual attempt files/indices must be contiguous")
        if (attempt.request != request or attempt.request_json.encode("utf-8") != request_bytes
                or attempt.request_sha256 != request_fingerprint(request)):
            raise ValueError("manual attempt references a different request/transcript")
        attempts.append(attempt)
    if len({attempt.attempt_id for attempt in attempts}) != len(attempts):
        raise ValueError("duplicate manual attempt ID")
    return attempts


def latest_valid_manual_attempt(request_path: str | Path) -> ManualJudgeAttempt | None:
    """Provider-free active evaluation projection, sharing the suite selection rule."""
    return select_latest_valid_attempt(load_manual_history(request_path))


def import_manual_response(
    request_path: str | Path, raw_response_path: str | Path, *,
    expected_request_sha256: str, judge: ManualJudgeProvenance,
    model_executed_at: AwareDatetime | None = None,
) -> ManualJudgeAttempt:
    """Import a supplied response once; never repair text, retry, or invoke a model.

    The caller must supply the fingerprint recorded for the request shown to the
    judge. This prevents accidental attachment to another request; it is not proof
    of what a human actually pasted. Each explicit invocation appends an attempt.
    """
    request_path = Path(request_path)
    request_json = request_path.read_bytes().decode("utf-8")
    request = JudgeInput.model_validate_json(request_json)
    if request_fingerprint(request) != expected_request_sha256:
        raise ValueError("request does not match supplied judge-request fingerprint")
    history = load_manual_history(request_path)
    raw = Path(raw_response_path).read_bytes().decode("utf-8")
    result, stage, reason, detail = _validated_response(raw, request)
    attempt = ManualJudgeAttempt(
        attempt_id=uuid4(), attempt_index=len(history) + 1, recorded_at=now(),
        model_executed_at=model_executed_at, judge=judge,
        request_json=request_json, request=request, request_sha256=expected_request_sha256,
        request_file_sha256=sha256(request_json.encode("utf-8")).hexdigest(),
        raw_response=raw, raw_response_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        technical_status="completed" if result is not None else "failed",
        failure_stage=stage, failure_reason=reason, failure_detail=detail,
        result=result, overall_severity=overall_severity(result) if result is not None else None,
    )
    # Detect source changes during import before the exclusive write.
    if request_path.read_bytes() != request_json.encode("utf-8"):
        raise ValueError("request changed during import")
    write_new(request_path.parent / "attempts" / f"attempt-{attempt.attempt_index:03}.json", attempt)
    return attempt
