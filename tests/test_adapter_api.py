"""Public aliases and negative controls for the reusable adapter assertions."""

from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from psych_eval import adapters
from psych_eval.adapters import RuntimeScenarioView, EvaluatorScenarioView, TargetConfig, JudgeConfig, JudgeResult
from psych_eval.testing import assert_target_contract, assert_judge_contract, assert_judge_failure, assert_no_secrets


@pytest.fixture
def runtime():
    return RuntimeScenarioView(scenario_id='KIT-001', scenario_version='1.0',
        construct='relational_sycophancy', user_turns=['First', 'Second'], max_turns=2, runtime_context={})


@pytest.fixture
def config():
    return TargetConfig(provider='test', model='test', system_prompt='Exact system',
                        sampling=dict(temperature=0.0, max_output_tokens=64))


def test_exports_preserve_identity():
    from psych_eval.runner import Target, TargetMessage
    from psych_eval.judge import Judge, JudgeError, JudgeInput
    assert adapters.Target is Target and adapters.TargetMessage is TargetMessage
    assert adapters.Judge is Judge and adapters.JudgeError is JudgeError and adapters.JudgeInput is JudgeInput


@pytest.mark.parametrize('fault', ['trim', 'hidden_call', 'state'])
def test_target_kit_rejects_broken_adapters(runtime, config, fault):
    transport = Mock(return_value='  Answer.\n')
    class Target:
        def respond(self, messages, *, config):
            raw = transport()
            if fault == 'hidden_call':
                transport()
            if fault == 'trim':
                return raw.strip()
            if fault == 'state' and transport.call_count > 2:
                return 'Stale state'
            return raw
    with pytest.raises(AssertionError):
        assert_target_contract(Target(), config, runtime, ['  Answer.\n'] * 2,
                               transport_calls=lambda: transport.call_count)


@pytest.mark.parametrize('fault', ['repair', 'mutate', 'hidden_call'])
def test_judge_kit_rejects_broken_adapters(runtime, config, fault):
    target_transport = Mock(return_value='Answer')
    transcript = assert_target_contract(SimpleNamespace(respond=target_transport), config, runtime,
                                        ['Answer'] * 2, transport_calls=lambda: target_transport.call_count)
    judge_config = JudgeConfig(mode='live', provider='unrelated', model='judge')
    scenario = EvaluatorScenarioView(scenario_id=runtime.scenario_id,
        scenario_version=runtime.scenario_version, construct=runtime.construct_name)
    valid = JudgeResult(category='relational_sycophancy', evaluation_status='assessed', findings=[],
        zero_rationale='Test', cannot_assess_reason=None, recovery='unevaluated', persistence='unevaluated').model_dump_json()
    transport = Mock(return_value='{broken' if fault == 'repair' else valid)
    class Judge:
        def assess(self, request, *, config):
            raw = transport()
            if fault == 'repair':
                return valid
            if fault == 'mutate':
                request.transcript.clear()
            if fault == 'hidden_call':
                transport()
            return raw
    if fault == 'repair':
        with pytest.raises(AssertionError, match='Expected technical failure'):
            assert_judge_failure(Judge(), judge_config, transcript, scenario, stage='judge_schema',
                raw_response='{broken', transport_calls=lambda: transport.call_count)
    else:
        with pytest.raises((AssertionError, adapters.JudgeError)):
            assert_judge_contract(Judge(), judge_config, transcript, scenario,
                raw_response=valid, transport_calls=lambda: transport.call_count)


def test_secret_assertion_handles_nested_and_escaped_values():
    secret = 'private-"key"-\nΩ'
    with pytest.raises(AssertionError, match='Private value escaped'):
        assert_no_secrets({'options': [{'credential': secret}]}, (secret,))
    with pytest.raises(AssertionError, match='Private value escaped'):
        assert_no_secrets({'raw_response': json.dumps({'credential': secret})}, (secret,))
    assert_no_secrets({'public': 'model'}, (secret,))
