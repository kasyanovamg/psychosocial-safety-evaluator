"""V1 scenario authoring contract, consumer views, and YAML loading."""

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonblankString = Annotated[str, AfterValidator(_nonblank)]
Construct = Literal["relational_sycophancy"]
FailureMode = Literal[
    "accepting_framing",
    "epistemic_endorsement",
    "escalation",
    "consequential_reinforcement",
]
# V1 deliberately permits no context entries.
EmptyRuntimeContext = Annotated[dict[str, object], Field(max_length=0)]
UserTurns = Annotated[list[NonblankString], Field(min_length=1)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, serialize_by_alias=True)


class DesignMetadata(_StrictModel):
    risk_hypothesis: NonblankString
    expected_safe_behavior: NonblankString
    intended_failure_modes: list[FailureMode]
    difficulty: Literal["easy", "medium", "hard"]
    benchmark_split: Literal["development", "held_out"]


class RuntimeScenarioView(_StrictModel):
    """Fixed, ordered user turns and identifiers for later execution."""

    scenario_id: NonblankString
    scenario_version: NonblankString
    # Alias avoids shadowing Pydantic's legacy BaseModel.construct method.
    construct_name: Construct = Field(alias="construct")
    user_turns: UserTurns
    max_turns: int
    runtime_context: EmptyRuntimeContext


class EvaluatorScenarioView(_StrictModel):
    """Identification only; no design hints or future judge configuration."""

    scenario_id: NonblankString
    scenario_version: NonblankString
    construct_name: Construct = Field(alias="construct")


class DesignScenarioView(_StrictModel):
    """Full authoring information, exclusively for developer/design use."""

    scenario_id: NonblankString
    scenario_version: NonblankString
    construct_name: Construct = Field(alias="construct")
    title: NonblankString
    scenario_family: NonblankString
    user_turns: UserTurns
    max_turns: int
    runtime_context: EmptyRuntimeContext
    design_metadata: DesignMetadata


class Scenario(DesignScenarioView):
    """Validated V1 source scenario. Pass explicit views to consumers."""

    @model_validator(mode="after")
    def validate_turn_count(self) -> Self:
        if self.max_turns != len(self.user_turns):
            raise ValueError("max_turns must equal the number of user_turns")
        return self

    def to_runtime_view(self) -> RuntimeScenarioView:
        return RuntimeScenarioView(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            construct=self.construct_name,
            user_turns=list(self.user_turns),
            max_turns=self.max_turns,
            runtime_context={},
        )

    def to_evaluator_view(self) -> EvaluatorScenarioView:
        return EvaluatorScenarioView(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            construct=self.construct_name,
        )

    def to_design_view(self) -> DesignScenarioView:
        return DesignScenarioView(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            construct=self.construct_name,
            title=self.title,
            scenario_family=self.scenario_family,
            user_turns=list(self.user_turns),
            max_turns=self.max_turns,
            runtime_context={},
            design_metadata=self.design_metadata.model_copy(deep=True),
        )


def load_scenario(path: str | Path) -> Scenario:
    """Safely parse UTF-8 YAML and validate it, preserving detailed errors.

    File, YAML, and validation exceptions retain their original types and gain
    a note identifying the source file.
    """
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as source:
            data = yaml.safe_load(source)
        return Scenario.model_validate(data)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        exc.add_note(f"Scenario file: {path}")
        raise
