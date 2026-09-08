"""Deterministic execution, failure, and persisted-artifact contract tests."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from psych_eval.runner import TargetMessage, run_scenario
from psych_eval.scenarios import load_scenario
from psych_eval.transcripts import (
    SamplingConfig,
    TargetConfig,
    Transcript,
    load_transcript,
    save_transcript,
)


SCENARIO_PATH = (
    Path(__file__).resolve().parents[1]
    / "scenarios/v1/relational_sycophancy/RS-001.yaml"
)
RESPONSES = ["  First reply.\n", "Second reply — unchanged.", "Third reply.", "Fourth reply."]


class ScriptedTarget:
    """Test-only target: one predefined response or exception per call."""

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.configs = []

    def respond(self, messages, *, config):
        self.calls.append(messages)
        self.configs.append(config)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def runtime():
    return load_scenario(SCENARIO_PATH).to_runtime_view()


@pytest.fixture
def config():
    return TargetConfig(
        provider="fake",
        model="deterministic-test-target",
        system_prompt="  Be helpful.\nPreserve uncertainty — exactly.\n",
        sampling=SamplingConfig(temperature=0.0, max_output_tokens=256),
    )


@pytest.fixture
def completed(runtime, config):
    return run_scenario(runtime, ScriptedTarget(RESPONSES), config)


def test_success_preserves_text_order_ids_and_configuration(runtime, config):
    target = ScriptedTarget(RESPONSES)
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "completed"
    assert result.failure is None
    assert result.retry_count == 0
    assert result.schema_version == "1.0"
    assert result.scenario_id == runtime.scenario_id
    assert result.scenario_version == runtime.scenario_version
    assert result.construct_name == runtime.construct_name
    assert len(result.turns) == 8
    assert [turn.turn_id for turn in result.turns] == [
        "U1", "A1", "U2", "A2", "U3", "A3", "U4", "A4",
    ]
    assert [turn.role for turn in result.turns] == ["user", "assistant"] * 4
    assert [turn.content for turn in result.turns[::2]] == runtime.user_turns
    assert [turn.content for turn in result.turns[1::2]] == RESPONSES
    assert result.target == config
    assert result.target.system_prompt == config.system_prompt
    assert target.configs == [config] * 4


def test_target_receives_increasing_exact_history(runtime, config):
    target = ScriptedTarget(RESPONSES)
    result = run_scenario(runtime, target, config)
    assert [len(call) for call in target.calls] == [1, 3, 5, 7]
    for index, call in enumerate(target.calls):
        assert [message.model_dump() for message in call] == [
            {"role": turn.role, "content": turn.content}
            for turn in result.turns[:2 * index + 1]
        ]


def test_new_run_has_fresh_history_and_unique_id(runtime, config):
    target = ScriptedTarget([*RESPONSES, "Another reply"])
    first = run_scenario(runtime, target, config)
    second_runtime = runtime.model_copy(deep=True)
    second_runtime.scenario_id = "RS-002"
    second_runtime.user_turns = ["Different scenario"]
    second_runtime.max_turns = 1
    second = run_scenario(second_runtime, target, config)
    assert first.run_id != second.run_id
    assert target.calls[4] == (TargetMessage(role="user", content="Different scenario"),)
    assert [turn.turn_id for turn in second.turns] == ["U1", "A1"]
    assert len(first.turns) == 8


def test_design_information_never_reaches_target(config):
    scenario = load_scenario(SCENARIO_PATH)
    scenario.design_metadata.risk_hypothesis = "HIDDEN_RISK_CANARY"
    scenario.design_metadata.expected_safe_behavior = "HIDDEN_BEHAVIOR_CANARY"
    target = ScriptedTarget(RESPONSES)
    run_scenario(scenario.to_runtime_view(), target, config)
    payload = json.dumps({
        "messages": [[message.model_dump() for message in call] for call in target.calls],
        "configs": [item.model_dump() for item in target.configs],
    })
    for hidden in (
        "design_metadata", "risk_hypothesis", "expected_safe_behavior",
        "intended_failure_modes", "difficulty", "benchmark_split",
        "HIDDEN_RISK_CANARY", "HIDDEN_BEHAVIOR_CANARY",
    ):
        assert hidden not in payload
    with pytest.raises(TypeError, match="RuntimeScenarioView"):
        run_scenario(scenario, target, config)
    assert len(target.calls) == 4


def test_partial_execution_preserves_successes_and_unanswered_user(runtime, config):
    target = ScriptedTarget([*RESPONSES[:2], RuntimeError("offline"), RuntimeError("offline")])
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "partial"
    assert [turn.turn_id for turn in result.turns] == ["U1", "A1", "U2", "A2", "U3"]
    assert [turn.content for turn in result.turns[1::2]] == RESPONSES[:2]
    assert result.turns[-1].content == runtime.user_turns[2]
    assert result.failure.failure_stage == "target_execution"
    assert result.failure.failure_reason == "target_exception"
    assert result.failure.detail == "RuntimeError: offline"
    assert result.failure.retry_count == result.retry_count == 1
    assert len(target.calls) == 4
    assert target.calls[2] == target.calls[3]


def test_immediate_failure_has_no_assistant_turn(runtime, config):
    target = ScriptedTarget([TimeoutError("timeout")] * 3)
    result = run_scenario(runtime, target, config, max_retries=2)
    assert result.execution_status == "failed"
    assert [(turn.turn_id, turn.role) for turn in result.turns] == [("U1", "user")]
    assert result.failure.detail == "TimeoutError: timeout"
    assert result.failure.retry_count == result.retry_count == 2
    assert len(target.calls) == 3
    assert target.calls[0] == target.calls[1] == target.calls[2]


@pytest.mark.parametrize("text", [
    "I can't help with that.", "You are absolutely right!", "  Valid reply.\n",
])
def test_nonblank_content_is_never_a_retry_signal(runtime, config, text):
    target = ScriptedTarget([text] * 4)
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "completed"
    assert [turn.content for turn in result.turns[1::2]] == [text] * 4
    assert result.retry_count == 0
    assert len(target.calls) == 4


@pytest.mark.parametrize("blank", ["", "   ", " \t\n"])
@pytest.mark.parametrize("prior_responses", [0, 2])
@pytest.mark.parametrize("max_retries", [0, 1, 2])
def test_blank_responses_exhaust_technical_retries(
    runtime, config, blank, prior_responses, max_retries,
):
    target = ScriptedTarget([
        *RESPONSES[:prior_responses], *([blank] * (max_retries + 1)),
    ])
    result = run_scenario(runtime, target, config, max_retries=max_retries)
    assert result.execution_status == ("partial" if prior_responses else "failed")
    assert result.failure.failure_stage == "target_execution"
    assert result.failure.failure_reason == "invalid_response"
    assert result.failure.detail == "Target returned an empty or whitespace-only string"
    assert result.failure.retry_count == result.retry_count == max_retries
    assert len(target.calls) == prior_responses + max_retries + 1
    assert all(call == target.calls[prior_responses] for call in target.calls[prior_responses:])
    assert len(result.turns) == 2 * prior_responses + 1
    assert [turn.content for turn in result.turns[1::2]] == RESPONSES[:prior_responses]
    assert result.turns[-1].turn_id == f"U{prior_responses + 1}"
    assert result.turns[-1].content == runtime.user_turns[prior_responses]


@pytest.mark.parametrize("blank", ["", "   ", " \t\n"])
def test_blank_response_can_recover_on_retry_without_changing_valid_text(runtime, config, blank):
    target = ScriptedTarget([blank, *RESPONSES])
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "completed"
    assert result.failure is None
    assert result.retry_count == 1
    assert len(target.calls) == 5
    assert target.calls[0] == target.calls[1]
    assert [turn.content for turn in result.turns[1::2]] == RESPONSES
    assert [turn.turn_id for turn in result.turns] == [
        "U1", "A1", "U2", "A2", "U3", "A3", "U4", "A4",
    ]


def test_transient_failures_retry_same_history_and_record_total(runtime, config):
    target = ScriptedTarget([
        TimeoutError("first"), RESPONSES[0],
        TimeoutError("second"), RESPONSES[1], *RESPONSES[2:],
    ])
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "completed"
    assert result.failure is None
    assert result.retry_count == 2
    assert len(result.turns) == 8
    assert target.calls[0] == target.calls[1]
    assert target.calls[2] == target.calls[3]


def test_failure_retry_count_is_per_turn_while_total_includes_earlier_retries(runtime, config):
    target = ScriptedTarget([
        TimeoutError("first"), RESPONSES[0],
        TimeoutError("second"), TimeoutError("second"),
    ])
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "partial"
    assert result.retry_count == 2
    assert result.failure.retry_count == 1


def test_zero_retries_makes_one_attempt(runtime, config):
    target = ScriptedTarget([RuntimeError("offline")])
    result = run_scenario(runtime, target, config, max_retries=0)
    assert result.execution_status == "failed"
    assert result.failure.retry_count == result.retry_count == 0
    assert len(target.calls) == 1


@pytest.mark.parametrize("invalid", [None, 123, {"content": "reply"}])
def test_non_string_return_is_a_technical_interface_failure(runtime, config, invalid):
    target = ScriptedTarget([invalid, invalid])
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "failed"
    assert result.failure.failure_reason == "invalid_response"
    assert len(result.turns) == 1
    assert result.retry_count == 1


def test_process_interrupts_are_not_swallowed(runtime, config):
    with pytest.raises(KeyboardInterrupt):
        run_scenario(runtime, ScriptedTarget([KeyboardInterrupt()]), config)


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "1"])
def test_invalid_retry_configuration_never_calls_target(runtime, config, invalid):
    target = ScriptedTarget([])
    with pytest.raises(ValueError, match="max_retries"):
        run_scenario(runtime, target, config, max_retries=invalid)
    assert target.calls == []


def test_runner_rejects_inconsistent_runtime_turn_count(runtime, config):
    runtime.max_turns = 1
    target = ScriptedTarget([])
    with pytest.raises(ValueError, match="max_turns"):
        run_scenario(runtime, target, config)
    assert target.calls == []


def test_target_input_is_immutable(runtime, config):
    class InspectingTarget:
        def respond(self, messages, *, config):
            assert isinstance(messages, tuple)
            with pytest.raises(ValidationError):
                messages[0].content = "changed"
            with pytest.raises(ValidationError):
                config.sampling.temperature = 1.0
            return "reply"

    result = run_scenario(runtime, InspectingTarget(), config)
    assert result.execution_status == "completed"
    assert result.turns[0].content == runtime.user_turns[0]


def test_persistence_round_trips_exactly_without_target_calls(tmp_path, runtime, config):
    target = ScriptedTarget(RESPONSES)
    result = run_scenario(runtime, target, config)
    path = tmp_path / "nested" / "transcript.json"
    save_transcript(path, result)
    first_bytes = path.read_bytes()
    reloaded = load_transcript(str(path))
    assert isinstance(reloaded, Transcript)
    assert reloaded == result
    assert reloaded.target.system_prompt == config.system_prompt
    assert [turn.content for turn in reloaded.turns[1::2]] == RESPONSES
    save_transcript(path, reloaded)
    assert path.read_bytes() == first_bytes
    assert first_bytes.endswith(b"\n")
    assert len(target.calls) == 4


@pytest.mark.parametrize("prior_responses", [0, 2])
def test_unsuccessful_artifacts_round_trip(tmp_path, runtime, config, prior_responses):
    target = ScriptedTarget([*RESPONSES[:prior_responses], RuntimeError("offline")])
    result = run_scenario(runtime, target, config, max_retries=0)
    path = tmp_path / "unsuccessful.json"
    save_transcript(path, result)
    assert load_transcript(path) == result


@pytest.mark.parametrize("contents", ["{broken", "{}", "[]", "null", ""])
def test_loader_rejects_malformed_artifacts(tmp_path, contents):
    path = tmp_path / "invalid.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_transcript(path)
    assert f"Transcript file: {path}" in caught.value.__notes__


@pytest.mark.parametrize("location", [(), ("turns", 0), ("target",), ("target", "sampling")])
def test_unknown_fields_are_rejected_at_every_level(tmp_path, completed, location):
    data = completed.model_dump(mode="json")
    nested = data
    for key in location:
        nested = nested[key]
    nested["unexpected"] = "value"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="unexpected"):
        load_transcript(path)


@pytest.mark.parametrize("location,value", [
    (("schema_version",), "2.0"),
    (("run_id",), "not-a-uuid"),
    (("construct",), "other_construct"),
    (("scenario_id",), " "),
    (("execution_status",), "judged"),
    (("execution_status",), "partial"),
    (("execution_status",), "failed"),
    (("max_turns",), 5),
    (("max_turns",), "4"),
    (("turns",), []),
    (("turns", 0, "turn_id"), "U2"),
    (("turns", 1, "role"), "system"),
    (("turns", 1, "turn_id"), "A2"),
    (("turns", 0, "content"), " "),
    (("turns", 1, "content"), ""),
    (("turns", 1, "content"), "   "),
    (("turns", 1, "content"), " \t\n"),
    (("turns", 1, "content"), 123),
    (("target", "system_prompt"), None),
    (("target", "sampling", "temperature"), -1.0),
    (("target", "sampling", "max_output_tokens"), 0),
    (("retry_count",), 5),
    (("max_retries",), -1),
])
def test_loader_rejects_invalid_contract_data(tmp_path, completed, location, value):
    data = completed.model_dump(mode="json")
    nested = data
    for key in location[:-1]:
        nested = nested[key]
    nested[location[-1]] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_transcript(path)


@pytest.mark.parametrize("field,value", [
    ("failure_stage", "judge"),
    ("failure_reason", "undesirable_answer"),
    ("retry_count", -1),
    ("retry_count", 0),
    ("unexpected", "value"),
])
def test_failure_contract_is_strict(runtime, config, field, value):
    target = ScriptedTarget([RuntimeError("offline")] * 2)
    data = run_scenario(runtime, target, config).model_dump(mode="json")
    data["failure"][field] = value
    with pytest.raises(ValidationError):
        Transcript.model_validate_json(json.dumps(data))


def test_save_revalidates_modified_turn_list(tmp_path, completed):
    completed.turns.clear()
    path = tmp_path / "invalid.json"
    with pytest.raises(ValidationError):
        save_transcript(path, completed)
    assert not path.exists()
