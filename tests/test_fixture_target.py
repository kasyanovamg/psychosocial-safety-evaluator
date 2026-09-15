"""Fixture validation and zero-network execution through the shared runner."""

import json
from pathlib import Path
import socket
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
import yaml

from psych_eval.integrations.fixture_target import FixtureArtifact, FixtureTarget
from psych_eval.runner import Target, TargetMessage, run_scenario
from psych_eval.scenarios import load_scenario
from psych_eval.transcripts import load_transcript, save_transcript
from psych_eval.testing import assert_target_contract


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "scenarios/v1/relational_sycophancy/RS-001.yaml"
FIXTURE_PATH = ROOT / "fixtures/demo_targets/relational_sycophancy/RS-001.yaml"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("fixture execution must not access the network")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def runtime():
    return load_scenario(SCENARIO_PATH).to_runtime_view()


@pytest.fixture
def fixture_data():
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def write_fixture(tmp_path, data):
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_canonical_fixture_loads_and_satisfies_target_protocol(runtime, fixture_data):
    fixture = FixtureTarget.from_file(FIXTURE_PATH, runtime)
    # Target is a structural Protocol, deliberately not runtime_checkable.
    target: Target = fixture
    terminal = Mock(wraps=target.respond)
    target.respond = terminal
    assert_target_contract(target, fixture.config, runtime, fixture_data['assistant_responses'],
                           transport_calls=lambda: terminal.call_count)
    assert fixture.config.provider == "fixture"
    assert fixture.config.model == "demo-relational-sycophancy-v1"


@pytest.mark.parametrize("field", [
    "scenario_id", "scenario_version", "fixture_target_id", "fixture_version",
])
@pytest.mark.parametrize("value", ["", " \t\n", 1, None])
def test_fixture_identifiers_are_strict_nonblank_strings(
    tmp_path, runtime, fixture_data, field, value,
):
    fixture_data[field] = value
    with pytest.raises(ValidationError, match=field):
        FixtureTarget.from_file(write_fixture(tmp_path, fixture_data), runtime)


@pytest.mark.parametrize("field,value", [
    ("unexpected", "value"),
    ("schema_version", "2.0"),
    ("schema_version", 1.0),
    ("assistant_responses", "one string is not a response sequence"),
    ("assistant_responses", {"A1": "a response"}),
])
def test_unknown_fields_and_invalid_schema_or_sequence_are_rejected(
    tmp_path, runtime, fixture_data, field, value,
):
    fixture_data[field] = value
    path = write_fixture(tmp_path, fixture_data)
    with pytest.raises(ValidationError, match=field) as caught:
        FixtureTarget.from_file(path, runtime)
    assert f"Fixture file: {path}" in caught.value.__notes__


@pytest.mark.parametrize("response", ["", "   ", " \t\n", 123, None, {"content": "reply"}])
def test_invalid_response_rejected_before_execution(tmp_path, runtime, fixture_data, response):
    fixture_data["assistant_responses"][2] = response
    with pytest.raises(ValidationError, match="assistant_responses"):
        FixtureTarget.from_file(write_fixture(tmp_path, fixture_data), runtime)


@pytest.mark.parametrize("count", [0, 3, 5])
def test_response_count_mismatch_rejected(tmp_path, runtime, fixture_data, count):
    fixture_data["assistant_responses"] = ["reply"] * count
    with pytest.raises(ValueError, match="response count"):
        FixtureTarget.from_file(write_fixture(tmp_path, fixture_data), runtime)


@pytest.mark.parametrize("field,value", [("scenario_id", "RS-002"), ("scenario_version", "2.0")])
def test_scenario_mismatch_rejected(tmp_path, runtime, fixture_data, field, value):
    fixture_data[field] = value
    with pytest.raises(ValueError, match=field):
        FixtureTarget.from_file(write_fixture(tmp_path, fixture_data), runtime)


def test_inconsistent_runtime_rejected(runtime):
    runtime.max_turns = 3
    with pytest.raises(ValueError, match="max_turns"):
        FixtureTarget.from_file(FIXTURE_PATH, runtime)


def test_rs001_end_to_end_round_trip_without_network_or_reexecution(
    tmp_path, runtime, fixture_data, monkeypatch,
):
    fixture = FixtureTarget.from_file(FIXTURE_PATH, runtime)
    target = Mock(wraps=fixture)
    result = run_scenario(runtime, target, fixture.config)
    assert result.execution_status == "completed"
    assert result.failure is None
    assert result.retry_count == 0
    assert len(result.turns) == 8
    assert [turn.turn_id for turn in result.turns] == [
        "U1", "A1", "U2", "A2", "U3", "A3", "U4", "A4",
    ]
    assert [turn.role for turn in result.turns] == ["user", "assistant"] * 4
    assert [turn.content for turn in result.turns[::2]] == runtime.user_turns
    assert [turn.content for turn in result.turns[1::2]] == fixture_data["assistant_responses"]
    assert target.respond.call_count == 4

    def forbidden_response(*args, **kwargs):
        pytest.fail("persistence must not invoke the target")

    monkeypatch.setattr(FixtureTarget, "respond", forbidden_response)
    path = tmp_path / "transcript.json"
    save_transcript(path, result)
    original_bytes = path.read_bytes()
    persisted = json.loads(original_bytes)
    assert persisted["target"]["provider"] == "fixture"
    assert persisted["target"]["model"] == "demo-relational-sycophancy-v1"
    assert persisted["target"] == fixture.config.model_dump()
    reloaded = load_transcript(path)
    assert reloaded == result
    save_transcript(path, reloaded)
    assert path.read_bytes() == original_bytes
    assert target.respond.call_count == 4


def test_exact_text_survives_loader_runner_and_persistence(tmp_path, runtime, fixture_data):
    responses = ["  First reply.\n", "Second reply — unchanged.", "\tThird.  ", "Fourth.\n\n"]
    fixture_data["assistant_responses"] = responses
    target = FixtureTarget.from_file(write_fixture(tmp_path, fixture_data), runtime)
    result = run_scenario(runtime, target, target.config)
    assert result.execution_status == "completed"
    assert [turn.content for turn in result.turns[1::2]] == responses
    path = tmp_path / "transcript.json"
    save_transcript(path, result)
    assert load_transcript(path) == result


def test_retries_and_repeated_runs_do_not_consume_responses(runtime, fixture_data):
    fixture = FixtureTarget.from_file(FIXTURE_PATH, runtime)

    class RetryOnce:
        calls = 0

        def respond(self, messages, *, config):
            self.calls += 1
            response = fixture.respond(messages, config=config)
            if self.calls == 1:
                raise TimeoutError("simulate a failure after retrieving the response")
            return response

    first = run_scenario(runtime, RetryOnce(), fixture.config)
    second = run_scenario(runtime, fixture, fixture.config)
    assert first.execution_status == second.execution_status == "completed"
    assert first.retry_count == 1
    assert second.retry_count == 0
    assert first.turns == second.turns
    assert first.run_id != second.run_id
    assert [turn.content for turn in first.turns[1::2]] == fixture_data["assistant_responses"]


@pytest.mark.parametrize("field,value", [("provider", "openai"), ("model", "live-model")])
def test_misleading_provenance_cannot_produce_fixture_output(runtime, field, value):
    target = FixtureTarget.from_file(FIXTURE_PATH, runtime)
    config = target.config.model_copy(update={field: value})
    result = run_scenario(runtime, target, config)
    assert result.execution_status == "failed"
    assert result.failure.failure_reason == "target_exception"
    assert "fixture/demo provenance" in result.failure.detail
    assert [turn.role for turn in result.turns] == ["user"]


def test_source_mutation_cannot_change_validated_target(runtime, fixture_data):
    artifact = FixtureArtifact.model_validate(fixture_data)
    target = FixtureTarget(artifact, runtime)
    artifact.assistant_responses.clear()
    result = run_scenario(runtime, target, target.config)
    assert result.execution_status == "completed"
    assert [turn.content for turn in result.turns[1::2]] == fixture_data["assistant_responses"]


def test_wrong_conversation_is_rejected(runtime):
    target = FixtureTarget.from_file(FIXTURE_PATH, runtime)
    runtime.user_turns[0] = "A different conversation"
    result = run_scenario(runtime, target, target.config)
    assert result.execution_status == "failed"
    assert "fixture history" in result.failure.detail
