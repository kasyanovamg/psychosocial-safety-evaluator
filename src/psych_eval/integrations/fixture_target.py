"""Explicit, local demo execution through the ordinary target contract."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from psych_eval.runner import TargetMessage
from psych_eval.scenarios import NonblankString, RuntimeScenarioView
from psych_eval.transcripts import SamplingConfig, TargetConfig


class FixtureArtifact(BaseModel):
    """Versioned source data; strings are validated without rewriting them."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["1.0"]
    scenario_id: NonblankString
    scenario_version: NonblankString
    fixture_target_id: NonblankString
    fixture_version: NonblankString
    assistant_responses: list[NonblankString]


class FixtureTarget:
    """Prevalidated fixture target, with no cursor or retained conversation state.

    Construct explicitly against the runtime scenario before calling the runner.
    ``config`` supplies unmistakable fixture provenance. Its system prompt and
    sampling values satisfy the shared contract but do not affect fixture text.
    """

    def __init__(self, fixture: FixtureArtifact, scenario: RuntimeScenarioView):
        if not isinstance(scenario, RuntimeScenarioView):
            raise TypeError("FixtureTarget requires an explicit RuntimeScenarioView")
        scenario = RuntimeScenarioView.model_validate(scenario.model_dump())
        fixture = FixtureArtifact.model_validate(fixture.model_dump())
        if scenario.max_turns != len(scenario.user_turns):
            raise ValueError("max_turns must equal the number of user_turns")
        if fixture.scenario_id != scenario.scenario_id:
            raise ValueError("fixture scenario_id must match the loaded scenario")
        if fixture.scenario_version != scenario.scenario_version:
            raise ValueError("fixture scenario_version must match the loaded scenario")
        if len(fixture.assistant_responses) != scenario.max_turns:
            raise ValueError("fixture response count must equal scenario max_turns")

        # Snapshot mutable source lists; replay depends only on supplied history.
        self._responses = tuple(fixture.assistant_responses)
        self._user_turns = tuple(scenario.user_turns)
        self._config = TargetConfig(
            provider="fixture",
            model=f"demo-{fixture.fixture_target_id}-v{fixture.fixture_version}",
            system_prompt="",
            sampling=SamplingConfig(temperature=0.0, max_output_tokens=256),
        )

    @property
    def config(self) -> TargetConfig:
        """Shared target configuration carrying the versioned demo identity."""
        return self._config

    @classmethod
    def from_file(
        cls, path: str | Path, scenario: RuntimeScenarioView,
    ) -> "FixtureTarget":
        """Load and validate all fixture data before conversation execution."""
        path = Path(path)
        try:
            with path.open(encoding="utf-8") as source:
                fixture = FixtureArtifact.model_validate(yaml.safe_load(source))
            return cls(fixture, scenario)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            exc.add_note(f"Fixture file: {path}")
            raise

    def respond(
        self, messages: tuple[TargetMessage, ...], *, config: TargetConfig,
    ) -> str:
        if config.provider != self.config.provider or config.model != self.config.model:
            raise ValueError("FixtureTarget requires its fixture/demo provenance")
        if not messages or len(messages) % 2 != 1:
            raise ValueError("fixture history must end with an unanswered user turn")
        response_index = len(messages) // 2
        if response_index >= len(self._responses):
            raise ValueError("fixture history exceeds the available responses")
        for index, message in enumerate(messages):
            role = "user" if index % 2 == 0 else "assistant"
            texts = self._user_turns if role == "user" else self._responses
            if message.role != role or message.content != texts[index // 2]:
                raise ValueError("fixture history must match the validated scenario and responses")
        return self._responses[response_index]
