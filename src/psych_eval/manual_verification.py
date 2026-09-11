"""Portable derived receipts; immutable manual attempts remain the authority."""

from hashlib import sha256
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from psych_eval.judge import JudgeResult
from psych_eval.manual_judge import ManualJudgeProvenance, load_manual_attempt, latest_valid_manual_attempt
from psych_eval.runs import ArtifactRef
from psych_eval.suite import Record, write_derived


class SourceExtraction(Record):
    start_byte_inclusive: int = Field(ge=0)
    end_byte_exclusive: int = Field(ge=0)
    normalization: Literal[False] = False

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_byte_exclusive < self.start_byte_inclusive:
            raise ValueError("source extraction range must be ordered")
        return self


class ManualImportVerification(Record):
    schema_version: Literal["manual-verification-1.0"] = "manual-verification-1.0"
    source: Literal["local_manual_input"] = "local_manual_input"
    # Historical source digest/range, not a claim that the source remains available.
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction: SourceExtraction
    raw_response_path: ArtifactRef
    artifact_path: ArtifactRef
    raw_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    literal_backslash_underscore: bool
    json_parsing: Literal["passed", "failed"]
    production_schema_semantic_validation: Literal["passed", "failed"]
    normalized_result: JudgeResult | None
    overall_severity: int | None = Field(ge=0, le=3)
    judge: ManualJudgeProvenance
    recorded_at: AwareDatetime
    failure_stage: Literal["judge_schema"] | None
    failure_reason: Literal["invalid_json", "invalid_response"] | None
    active_attempt_index: int | None = Field(ge=1)


def build_manual_verification(
    repository_root: str | Path, attempt_path: str | Path, raw_response_path: str | Path, *,
    source_sha256: str, extraction: SourceExtraction,
) -> ManualImportVerification:
    """Recheck saved IO and emit only safe POSIX references relative to the root.

    Paths resolve normally, including symlinks; references outside the repository
    are rejected. The original input location has no field in this schema.
    """
    root = Path(repository_root).resolve()
    attempt_path, raw_response_path = Path(attempt_path).resolve(), Path(raw_response_path).resolve()
    artifact_ref = attempt_path.relative_to(root).as_posix()
    raw_ref = raw_response_path.relative_to(root).as_posix()
    attempt = load_manual_attempt(attempt_path)
    raw = raw_response_path.read_bytes()
    if raw != attempt.raw_response.encode("utf-8"):
        raise ValueError("raw response file must match saved manual attempt")
    if len(raw) != extraction.end_byte_exclusive - extraction.start_byte_inclusive:
        raise ValueError("source extraction length must match preserved response")
    active = latest_valid_manual_attempt(attempt_path.parent.parent / "judge-request.json")
    return ManualImportVerification(
        source_sha256=source_sha256, extraction=extraction,
        raw_response_path=raw_ref, artifact_path=artifact_ref,
        raw_response_sha256=attempt.raw_response_sha256,
        artifact_sha256=sha256(attempt_path.read_bytes()).hexdigest(),
        request_fingerprint=attempt.request_sha256, request_file_sha256=attempt.request_file_sha256,
        literal_backslash_underscore=b"\\_" in raw,
        json_parsing="failed" if attempt.failure_reason == "invalid_json" else "passed",
        production_schema_semantic_validation="passed" if attempt.result is not None else "failed",
        normalized_result=attempt.result, overall_severity=attempt.overall_severity,
        judge=attempt.judge, recorded_at=attempt.recorded_at,
        failure_stage=attempt.failure_stage, failure_reason=attempt.failure_reason,
        active_attempt_index=active.attempt_index if active else None,
    )


def save_manual_verification(path: str | Path, report: ManualImportVerification) -> None:
    """Derived report writer revalidates typed portable references before saving."""
    write_derived(Path(path), report)
