"""Explicit local replay through the ordinary judge contract."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from psych_eval.judge import (
    _StrictModel, JudgeConfig, JudgeError, JudgeInput, JudgeResult, request_fingerprint,
)
from psych_eval.judge_payload import DEFAULT_JUDGE_PROMPT_VERSION
from psych_eval.scenarios import NonblankString


class FixtureJudgeArtifact(_StrictModel):
    schema_version: Literal["1.0"]
    scenario_id: NonblankString
    scenario_version: NonblankString
    fixture_judge_id: NonblankString
    fixture_version: NonblankString
    rubric_version: Literal["0.2"]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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
            prompt_version=DEFAULT_JUDGE_PROMPT_VERSION,
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
            request.scenario_id != artifact.scenario_id
            or request.scenario_version != artifact.scenario_version
            or request.rubric_version != artifact.rubric_version
            or request_fingerprint(request) != artifact.request_sha256
        ):
            raise JudgeError("judge_input", "fixture judge requires the exact versioned fixture transcript")
        return artifact.raw_response
