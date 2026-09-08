"""Sequential execution of fixed runtime scenarios against a synchronous target."""

from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from psych_eval.scenarios import RuntimeScenarioView
from psych_eval.transcripts import (
    ExecutionFailure,
    TargetConfig,
    Transcript,
    TranscriptTurn,
)


class TargetMessage(BaseModel):
    """Conversation text only, with no authoring data or artifact identifiers."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    role: Literal["user", "assistant"]
    content: str


class Target(Protocol):
    def respond(
        self, messages: tuple[TargetMessage, ...], *, config: TargetConfig,
    ) -> str:
        """Return assistant text or raise a technical exception.

        The target must use the supplied history and configuration without
        retaining conversation history between calls. The system prompt is
        supplied separately in config; it is not a user/assistant transcript turn.
        """
        ...


def run_scenario(
    scenario: RuntimeScenarioView,
    target: Target,
    config: TargetConfig,
    *,
    max_retries: int = 1,
) -> Transcript:
    """Run one isolated conversation; retry technical failures without sleeping.

    max_retries counts additional calls per user turn. Exceptions raised by
    respond, non-string results, and blank strings are technical failures.
    Nonblank strings, including refusals, are recorded exactly without trimming.
    Process-control exceptions such as KeyboardInterrupt propagate.
    """
    if not isinstance(scenario, RuntimeScenarioView):
        raise TypeError("run_scenario requires an explicit RuntimeScenarioView")
    scenario = RuntimeScenarioView.model_validate(scenario.model_dump())
    if scenario.max_turns != len(scenario.user_turns):
        raise ValueError("max_turns must equal the number of user_turns")
    if type(max_retries) is not int or max_retries < 0:
        raise ValueError("max_retries must be a non-negative integer")
    config = TargetConfig.model_validate(config.model_dump())

    run_id = uuid4()
    turns: list[TranscriptTurn] = []
    retry_count = 0
    failure = None
    status = "completed"

    for number, user_text in enumerate(scenario.user_turns, start=1):
        turns.append(TranscriptTurn(turn_id=f"U{number}", role="user", content=user_text))
        messages = tuple(TargetMessage(role=turn.role, content=turn.content) for turn in turns)
        for attempt in range(max_retries + 1):
            if attempt:
                retry_count += 1
            try:
                response = target.respond(messages, config=config)
            except Exception as exc:
                reason = "target_exception"
                detail = f"{type(exc).__name__}: {exc}"
            else:
                if isinstance(response, str) and response.strip():
                    turns.append(TranscriptTurn(
                        turn_id=f"A{number}", role="assistant", content=response,
                    ))
                    break
                reason = "invalid_response"
                detail = (
                    "Target returned an empty or whitespace-only string"
                    if isinstance(response, str)
                    else f"Target must return str; received {type(response).__name__}"
                )
            if attempt == max_retries:
                failure = ExecutionFailure(
                    failure_stage="target_execution",
                    failure_reason=reason,
                    detail=detail,
                    retry_count=attempt,
                )

        if failure is not None:
            status = "partial" if number > 1 else "failed"
            break

    return Transcript(
        schema_version="1.0",
        run_id=run_id,
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.scenario_version,
        construct=scenario.construct_name,
        execution_status=status,
        max_turns=scenario.max_turns,
        turns=turns,
        target=config,
        max_retries=max_retries,
        retry_count=retry_count,
        failure=failure,
    )
