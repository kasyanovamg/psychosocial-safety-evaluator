"""SDK/pytest-free assertions for adapter authors using mocked transports.

These functions make adapter calls: NEVER pass live clients. transport_calls must
count the mocked transport's inference requests, not just adapter method entries.
Wire-format/settings checks remain the adapter author's responsibility.
"""

import json
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from psych_eval.adapters import (
    EvaluatorScenarioView, Judge, JudgeConfig, JudgeError, RuntimeScenarioView,
    Target, TargetConfig, TargetMessage,
)
from psych_eval.evaluator import evaluate_transcript
from psych_eval.judge import assemble_judge_input
from psych_eval.runner import run_scenario
from psych_eval.transcripts import Transcript

__all__ = ['assert_no_secrets', 'assert_target_contract', 'assert_target_failure',
           'assert_judge_contract', 'assert_judge_failure', 'assert_judge_attempt']


def assert_no_secrets(value, secrets: Sequence[str]) -> None:
    """Check JSON-visible values without echoing a secret in assertion messages."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    serialized = json.dumps(value, ensure_ascii=True)
    for secret in secrets:
        assert secret, 'Secret test markers must be nonempty'
        # Also cover JSON-escaped markers inside saved raw judge-response strings.
        for marker in (secret, json.dumps(secret, ensure_ascii=True)[1:-1]):
            assert json.dumps(marker, ensure_ascii=True)[1:-1] not in serialized, 'Private value escaped into public output'


def assert_target_contract(
    target: Target, config: TargetConfig, scenario: RuntimeScenarioView,
    responses: Sequence[str], *, transport_calls: Callable[[], int], secrets: Sequence[str] = (),
) -> Transcript:
    """Run the same mocked conversation twice; exact history/text/config, isolation.

    Configure the mock to return responses on BOTH runs, including refusal text
    when applicable. Returns the second canonical transcript for composition.
    """
    assert len(responses) == scenario.max_turns
    expected = []
    for user, assistant in zip(scenario.user_turns, responses):
        expected.extend([TargetMessage(role='user', content=user), TargetMessage(role='assistant', content=assistant)])
    observed = []

    class ObservedTarget:
        def respond(self, messages, *, config):
            observed.append((tuple(m.model_copy(deep=True) for m in messages), config.model_copy(deep=True)))
            return target.respond(messages, config=config)

    before = transport_calls()
    first = run_scenario(scenario, ObservedTarget(), config, max_retries=0)
    second = run_scenario(scenario, ObservedTarget(), config, max_retries=0)
    assert first.run_id != second.run_id
    for transcript in (first, second):
        assert transcript.execution_status == 'completed', 'Mocked target failed'
        assert transcript.retry_count == 0 and transcript.failure is None
        assert transcript.target == config
        assert [TargetMessage(role=t.role, content=t.content) for t in transcript.turns] == expected
        assert_no_secrets(transcript, secrets)
    assert len(observed) == 2 * scenario.max_turns
    for index, (messages, received_config) in enumerate(observed):
        assert list(messages) == expected[:2 * (index % scenario.max_turns) + 1]
        assert received_config == config
    assert transport_calls() - before == len(observed), 'Hidden or missing transport calls'
    return second


def assert_target_failure(
    target: Target, config: TargetConfig, scenario: RuntimeScenarioView, *,
    transport_calls: Callable[[], int], retries: int = 1, secrets: Sequence[str] = (),
) -> Transcript:
    """All mocked calls must raise safely; check first-turn failure and retries."""
    before = transport_calls()
    result = run_scenario(scenario, target, config, max_retries=retries)
    assert result.execution_status == 'failed'
    assert result.failure.failure_stage == 'target_execution'
    assert result.failure.failure_reason == 'target_exception'
    assert result.retry_count == retries
    assert transport_calls() - before == retries + 1, 'Hidden or missing transport calls'
    assert_no_secrets(result, secrets)
    return result


def assert_judge_contract(
    judge: Judge, config: JudgeConfig, transcript: Transcript, scenario: EvaluatorScenarioView, *,
    raw_response: str, transport_calls: Callable[[], int],
    status: str = 'assessed', severity: int | None = 0, secrets: Sequence[str] = (),
):
    """One exact canonical request and raw result; authoritative core validation."""
    expected = assemble_judge_input(transcript, scenario)
    seen = []

    class ObservedJudge:
        def assess(self, request, *, config):
            seen.append((request.model_copy(deep=True), config.model_copy(deep=True)))
            raw = judge.assess(request, config=config)
            assert request == expected, 'Judge mutated the canonical request'
            return raw

    before = transport_calls()
    result = evaluate_transcript(transcript, scenario, ObservedJudge(), config)
    assert seen == [(expected, config)]
    assert result.judge_input == expected and result.judge == config
    assert result.raw_judge_response == raw_response, 'Judge rewrote the mocked raw result'
    assert result.evaluation_status == status and result.overall_severity == severity
    assert transport_calls() - before == 1, 'Hidden or missing transport calls'
    assert_no_secrets(result, secrets)
    return result


def assert_judge_failure(
    judge: Judge, config: JudgeConfig, transcript: Transcript, scenario: EvaluatorScenarioView, *,
    stage: str, transport_calls: Callable[[], int], raw_response: str | None = None,
    secrets: Sequence[str] = (),
) -> JudgeError:
    """One mocked error/invalid result stays technical, without repair or retries."""
    before = transport_calls()
    try:
        evaluate_transcript(transcript, scenario, judge, config)
    except JudgeError as error:
        assert error.failure_stage == stage
        assert error.raw_response == raw_response
        assert_no_secrets({'detail': str(error), 'raw_response': error.raw_response}, secrets)
        assert transport_calls() - before == 1, 'Hidden or missing transport calls'
        return error
    raise AssertionError('Expected technical failure; judge returned an evaluation')


def assert_judge_attempt(attempt, *, transport_call_count: int, secrets: Sequence[str] = ()) -> None:
    """Check an existing persisted JudgeAttempt against the transport-call delta.

    Run through the normal suite/rerun API first. This avoids fabricating manifests
    or requiring repository scenario resources in an external package's test kit.
    """
    assert len(attempt.calls) == attempt.retry_count + 1 == transport_call_count
    assert [call.retry_index for call in attempt.calls] == list(range(transport_call_count))
    assert attempt.retry_count <= attempt.max_retries
    assert_no_secrets(attempt, secrets)
