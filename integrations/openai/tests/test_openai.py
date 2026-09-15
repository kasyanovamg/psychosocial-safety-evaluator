"""Mocked SDK transport and real entry-point composition; never paid API calls."""

from importlib.metadata import distribution, entry_points
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import openai
from openai.types.responses import Response
import pytest
import yaml

from psych_eval.cli import execute_configured_suite
from psych_eval.evaluator import evaluate_transcript
from psych_eval.integrations.pack_fixtures import PackJudge, pack_target
from psych_eval.integrations.runtime import resolve_factory
from psych_eval.judge import JudgeConfig, JudgeError, JudgeResult, assemble_judge_input
from psych_eval.runner import TargetMessage, run_scenario
from psych_eval.scenarios import load_scenario
from psych_eval.suite import rebuild_run
from psych_eval.adapters import TargetConfig
from psych_eval.testing import (
    assert_target_contract, assert_target_failure, assert_judge_contract, assert_judge_failure,
)
from psych_eval_openai.judge import build_judge
from psych_eval_openai.target import build_target

ROOT = Path(__file__).resolve().parents[3]
SECRET = 'test-private-openai-key'
RAW = JudgeResult(category='relational_sycophancy', evaluation_status='assessed',
                  findings=[], zero_rationale='Synthetic test.', cannot_assess_reason=None,
                  recovery='unevaluated', persistence='unevaluated').model_dump_json()


def reply(text='  Exact response.\n', *, status='completed', refusal=False, usage=True):
    return Response.model_validate(dict(
        id='resp_test', created_at=1.0, model='returned-model-snapshot', object='response',
        status=status, parallel_tool_calls=False, tool_choice='none', tools=[],
        output=[dict(id='msg_test', type='message', role='assistant', status='completed',
                     content=[dict(type='refusal', refusal=text) if refusal else
                              dict(type='output_text', text=text, annotations=[], logprobs=[])])],
        usage=dict(input_tokens=10, output_tokens=5, total_tokens=15,
                   input_tokens_details=dict(cached_tokens=0, cache_write_tokens=0),
                   output_tokens_details=dict(reasoning_tokens=0)) if usage else None,
    ))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('network forbidden in reference adapter tests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    for name in ('OPENAI_API_KEY', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID'):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sdk(monkeypatch):
    clients = []
    def construct(**kwargs):
        client = SimpleNamespace(responses=Mock(), options=kwargs)
        client.responses.create.return_value = reply()
        clients.append(client)
        return client
    constructor = Mock(side_effect=construct)
    monkeypatch.setattr('psych_eval_openai._transport.openai.OpenAI', constructor)
    return constructor, clients


@pytest.fixture
def configs():
    return (
        TargetConfig(provider='openai', model='target-model', system_prompt='System\nexact.',
                     sampling=dict(temperature=0.2, max_output_tokens=123)),
        JudgeConfig(mode='live', provider='openai', model='judge-model', prompt_version='0.1',
                    sampling=dict(temperature=0.0, max_output_tokens=456)),
    )


@pytest.fixture
def scenario():
    return load_scenario(ROOT / 'scenarios/v1/relational_sycophancy/RS-001.yaml')


@pytest.fixture
def transcript(scenario):
    target = pack_target(scenario.to_runtime_view())
    return run_scenario(scenario.to_runtime_view(), target, target.config)


@pytest.mark.parametrize('usage', [True, False])
def test_target_exact_history_and_text_without_sdk_metadata(sdk, configs, usage):
    config, _ = configs
    factory = build_target(config=config, options={'api_key': SECRET})
    adapter = factory(None)
    sdk[1][0].responses.create.return_value = reply(usage=usage)
    messages = tuple(TargetMessage(role=r, content=t) for r,t in
                     [('user', ' Q1\n'), ('assistant', ' A1 '), ('user', 'Q2')])
    assert adapter.respond(messages, config=config) == '  Exact response.\n'
    sent = sdk[1][0].responses.create.call_args.kwargs
    assert sent == dict(model='target-model', input=[
        {'role':'system', 'content':'System\nexact.'},
        *[{'role':m.role, 'content':m.content} for m in messages]],
        temperature=0.2, max_output_tokens=123, store=False, truncation='disabled')
    assert factory(None) is not adapter
    assert adapter.config == config and 'returned-model-snapshot' not in config.model_dump_json()
    assert SECRET not in json.dumps(sent)


def test_judge_exact_request_schema_and_raw_text(sdk, configs, transcript, scenario):
    _, config = configs
    judge = build_judge(config=config, options={'api_key': SECRET})
    sdk[1][0].responses.create.return_value = reply(RAW)
    request = assemble_judge_input(transcript, scenario.to_evaluator_view())
    assert_judge_contract(judge, config, transcript, scenario.to_evaluator_view(),
                          raw_response=RAW, secrets=(SECRET,),
                          transport_calls=lambda: sdk[1][0].responses.create.call_count)
    sent = sdk[1][0].responses.create.call_args.kwargs
    assert sent['input'] == [{'role':'user', 'content':request.model_dump_json()}]
    assert sent['instructions'] == request.instructions
    assert sent['text']['format'] == dict(type='json_schema', name='psych_eval_judge_result',
                                          strict=True, schema=JudgeResult.model_json_schema())
    assert sent['model'] == config.model and sent['max_output_tokens'] == 456
    assert SECRET not in json.dumps(sent)
    assert judge.config == config


def test_auth_independent_clients_and_sdk_retries_disabled(sdk, configs, monkeypatch):
    monkeypatch.setenv('TARGET_KEY', SECRET)
    monkeypatch.setenv('OPENAI_API_KEY', 'judge-private')
    build_target(config=configs[0], options={'api_key_env':'TARGET_KEY', 'organization':'private-org', 'timeout':12.0})
    build_judge(config=configs[1], options={})
    assert len(sdk[1]) == 2
    assert sdk[1][0].options['api_key'] == SECRET
    assert sdk[1][1].options['api_key'] == 'judge-private'
    assert sdk[1][0].options['organization'] == 'private-org'
    assert all(c.options['max_retries'] == 0 for c in sdk[1])
    assert all(c.options['base_url'] == 'https://api.openai.com/v1' for c in sdk[1])


@pytest.mark.parametrize('options', [{}, {'api_key': ''}, {'api_key':SECRET, 'max_retries':4},
                                    {'api_key':SECRET, 'temperature':0.1}, {'api_key':SECRET, 'timeout':-1}])
def test_bad_options_safe_and_no_client_created(sdk, configs, options):
    with pytest.raises(ValueError, match='Invalid OpenAI credentials or runtime options') as caught:
        build_target(config=configs[0], options=options)
    assert SECRET not in str(caught.value)
    sdk[0].assert_not_called()


@pytest.mark.parametrize('kind', ['auth','rate','timeout','network','request','unknown'])
@pytest.mark.parametrize('role', ['target','judge'])
def test_provider_errors_safely_use_core_failure_semantics(sdk, configs, transcript, scenario, kind, role):
    request = httpx2.Request('POST', 'https://api.openai.com/v1/responses', headers={'Authorization':SECRET})
    response = httpx2.Response(400, request=request)
    errors = {
        'auth':openai.AuthenticationError(SECRET, response=response, body={'secret':SECRET}),
        'rate':openai.RateLimitError(SECRET, response=response, body=None),
        'timeout':openai.APITimeoutError(request),
        'network':openai.APIConnectionError(message=SECRET, request=request),
        'request':openai.BadRequestError(SECRET, response=response, body=None),
        'unknown':RuntimeError(SECRET),
    }
    if role == 'target':
        target = build_target(config=configs[0], options={'api_key':SECRET})(None)
        sdk[1][0].responses.create.side_effect = errors[kind]
        assert_target_failure(target, configs[0], scenario.to_runtime_view(),
                              transport_calls=lambda: sdk[1][0].responses.create.call_count, secrets=(SECRET,))
    else:
        judge = build_judge(config=configs[1], options={'api_key':SECRET})
        sdk[1][0].responses.create.side_effect = errors[kind]
        assert_judge_failure(judge, configs[1], transcript, scenario.to_evaluator_view(),
                             stage='judge_call', secrets=(SECRET,),
                             transport_calls=lambda: sdk[1][0].responses.create.call_count)


@pytest.mark.parametrize('status,refusal,text', [('incomplete',False,RAW), ('failed',False,RAW),
                                               ('completed',True,'Refused'), ('completed',False,'')])
def test_judge_invalid_envelopes_are_technical(sdk, configs, transcript, scenario, status, refusal, text):
    judge = build_judge(config=configs[1], options={'api_key':SECRET})
    sdk[1][0].responses.create.return_value = reply(text,status=status,refusal=refusal)
    with pytest.raises(JudgeError) as caught:
        evaluate_transcript(transcript, scenario.to_evaluator_view(), judge, configs[1])
    assert caught.value.failure_stage == 'judge_call'


def test_target_refusal_is_transcript_text(sdk, configs, scenario):
    target = build_target(config=configs[0], options={'api_key':SECRET})(None)
    sdk[1][0].responses.create.return_value = reply('I cannot help with that.',refusal=True)
    assert_target_contract(target, configs[0], scenario.to_runtime_view(),
                           ['I cannot help with that.'] * scenario.max_turns, secrets=(SECRET,),
                           transport_calls=lambda: sdk[1][0].responses.create.call_count)


@pytest.mark.parametrize('raw', ['{broken', '{}', RAW.replace('Synthetic test.', '')])
def test_raw_invalid_json_is_validated_by_core(sdk, configs, transcript, scenario, raw):
    judge = build_judge(config=configs[1], options={'api_key':SECRET})
    sdk[1][0].responses.create.return_value = reply(raw)
    assert_judge_failure(judge, configs[1], transcript, scenario.to_evaluator_view(),
                         stage='judge_schema', raw_response=raw, secrets=(SECRET,),
                         transport_calls=lambda: sdk[1][0].responses.create.call_count)


def test_entry_points_come_from_independent_distribution():
    metadata = distribution('psych-eval-openai')
    assert any(dep.startswith('openai') for dep in metadata.requires)
    assert any(dep.startswith('psychosocial-safety-evaluator') for dep in metadata.requires)
    assert resolve_factory('openai','target') is build_target
    assert resolve_factory('openai','judge') is build_judge
    assert all(any(e.name == 'openai' for e in entry_points(group=group))
               for group in ('psych_eval.targets','psych_eval.judges'))


@pytest.mark.parametrize('pair', ['openai-fixture','fixture-openai','openai-openai'])
def test_normal_discovery_composition_and_persistence(sdk, configs, tmp_path, pair):
    # Use exact fixture text so strict fixture judging can accept the mock target.
    fixture_text = yaml.safe_load((ROOT / 'fixtures/demo_targets/relational_sycophancy/RS-001.yaml').read_text())['assistant_responses']
    def create(**kwargs):
        if 'text' in kwargs:
            return reply(RAW)
        users = [m['content'] for m in kwargs['input'] if m['role']=='user']
        from psych_eval.integrations.pack_fixtures import PLACEHOLDER
        first = load_scenario(ROOT / 'scenarios/v1/relational_sycophancy/RS-001.yaml').user_turns[0]
        return reply(fixture_text[len(users)-1] if users[0] == first else PLACEHOLDER)
    original = sdk[0].side_effect
    def construct(**kwargs):
        client = original(**kwargs)
        client.responses.create.side_effect = create
        return client
    sdk[0].side_effect = construct
    fixture_config = pack_target(load_scenario(ROOT / 'scenarios/v1/relational_sycophancy/RS-001.yaml').to_runtime_view()).config
    target_openai, judge_openai = pair.startswith('openai'), pair.endswith('openai')
    data = dict(
        target=dict(integration='openai' if target_openai else 'fixture',
                    config=(configs[0] if target_openai else fixture_config).model_dump(),
                    options={'api_key':SECRET} if target_openai else {}),
        judge=dict(integration='openai' if judge_openai else 'fixture',
                   config=(configs[1] if judge_openai else PackJudge().config).model_dump(),
                   options={'api_key':'judge-private'} if judge_openai else {}))
    path = tmp_path / 'runtime.yaml'
    path.write_text(yaml.safe_dump(data))
    bundle = tmp_path / 'bundle'
    run = execute_configured_suite(bundle,path)
    assert run.execution_summary.completed == 20
    assert run.evaluation_summary.assessed == (20 if judge_openai else 1)
    assert run.evaluation_summary.failed == 0
    assert rebuild_run(bundle / 'execution.json') == run
    for p in bundle.rglob('*.json'):
        assert SECRET not in p.read_text() and 'judge-private' not in p.read_text()
        assert 'returned-model-snapshot' not in p.read_text()


def test_actual_sdk_serializes_request_through_mock_transport(configs, transcript, scenario):
    from psych_eval_openai.judge import OpenAIJudge
    from psych_eval_openai.target import OpenAITarget

    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        assert request.headers['authorization'] == f'Bearer {SECRET}'
        return httpx2.Response(200, json=reply(RAW).model_dump(mode='json'))
    with openai.OpenAI(api_key=SECRET, max_retries=0,
                       http_client=httpx2.Client(transport=httpx2.MockTransport(handler))) as client:
        target = OpenAITarget(client, configs[0])
        assert target.respond((TargetMessage(role='user', content='Exact user'),), config=configs[0]) == RAW
        judge = OpenAIJudge(client, configs[1])
        request = assemble_judge_input(transcript, scenario.to_evaluator_view())
        assert judge.assess(request, config=configs[1]) == RAW
    assert captured[0]['input'][1]['content'] == 'Exact user'
    assert captured[1]['input'][0]['content'] == request.model_dump_json()
    assert captured[1]['text']['format']['schema'] == JudgeResult.model_json_schema()
    assert SECRET not in json.dumps(captured)


def test_judge_semantic_cannot_assess_and_optional_sampling(sdk, configs, transcript, scenario):
    config = JudgeConfig(mode='live', provider='openai', model='judge-model')
    judge = build_judge(config=config, options={'api_key':SECRET})
    raw = json.loads(RAW)
    raw.update(evaluation_status='cannot_assess', zero_rationale=None, cannot_assess_reason='Insufficient evidence.')
    sdk[1][0].responses.create.return_value = reply(json.dumps(raw),usage=False)
    assert_judge_contract(judge, config, transcript, scenario.to_evaluator_view(),
                          raw_response=json.dumps(raw), status='cannot_assess', severity=None,
                          transport_calls=lambda: sdk[1][0].responses.create.call_count)
    assert 'temperature' not in sdk[1][0].responses.create.call_args.kwargs
    assert 'max_output_tokens' not in sdk[1][0].responses.create.call_args.kwargs


def test_public_provenance_mismatch_fails_before_sdk(sdk, configs):
    with pytest.raises(ValueError, match='provider=openai'):
        build_target(config=configs[0].model_copy(update={'provider':'elsewhere'}),options={'api_key':SECRET})
    with pytest.raises(ValueError, match='provider=openai'):
        build_judge(config=configs[1].model_copy(update={'provider':'elsewhere'}),options={'api_key':SECRET})
    sdk[0].assert_not_called()


def test_explicit_key_overrides_environment(sdk, configs, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','environment-key')
    build_target(config=configs[0],options={'api_key':SECRET})
    assert sdk[1][0].options['api_key'] == SECRET
