"""Full-pack infrastructure execution, durable attempts, and inference-free rebuilds."""

import builtins
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import Mock
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from psych_eval.judge import JudgeConfig, transcript_fingerprint
from psych_eval.integrations.pack_fixtures import PackJudge, pack_target
from psych_eval.run_presentation import load_run_view
from psych_eval.runs import load_run
from psych_eval.selection import resolve_selection
from psych_eval.cli import execute_fixture_pack
from psych_eval.suite import (
    ExecutionManifest, JudgeAttempt, PACK_IDS, discover_pack,
    execute_suite, judge_saved_transcript, load_technical_failures, read_record,
    rebuild_run, write_new,
)
from psych_eval.transcripts import TargetConfig, load_transcript
from psych_eval.testing import assert_judge_attempt


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network_or_provider_sdk(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("suite must not access network/provider SDKs")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in {'openai', 'anthropic', 'dotenv'}:
            blocked()
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path / 'bundle'
    execute_fixture_pack(directory)
    return directory


def execute(directory, *, target_factory=pack_target, judge=None, **kwargs):
    judge = judge or PackJudge()
    config = pack_target(discover_pack()[0].to_runtime_view()).config
    return execute_suite(directory, target_factory=target_factory, target_config=config,
                         target_mode='fixture', judge=judge, judge_config=PackJudge().config, **kwargs)


def test_discovery_is_exact_order_and_does_not_modify_frozen_pack(tmp_path):
    paths = sorted((ROOT / 'scenarios/v1/relational_sycophancy').glob('*.yaml'))
    before = {p.name: sha256(p.read_bytes()).hexdigest() for p in paths}
    assert tuple(s.scenario_id for s in discover_pack()) == PACK_IDS
    execute_fixture_pack(tmp_path / 'bundle')
    assert {p.name: sha256(p.read_bytes()).hexdigest() for p in paths} == before


def test_discovery_rejects_missing_extra_and_misidentified_scenarios(tmp_path):
    import shutil

    pack = tmp_path / 'pack'
    shutil.copytree(ROOT / 'scenarios/v1/relational_sycophancy', pack)
    last = pack / 'RS-020.yaml'
    data = last.read_text(encoding="utf-8")
    last.unlink()
    with pytest.raises(ValueError, match='exactly'):
        discover_pack(pack)
    last.write_text(data.replace('scenario_id: RS-020', 'scenario_id: RS-019'), encoding="utf-8")
    with pytest.raises(ValueError, match='identity'):
        discover_pack(pack)
    last.write_text(data, encoding="utf-8")
    (pack / 'RS-021.yaml').write_text(data, encoding="utf-8")
    with pytest.raises(ValueError, match='exactly'):
        discover_pack(pack)


def test_full_pack_reload_summary_and_legacy_rs001_compatibility(bundle):
    run = load_run(bundle / 'run.json', verify_references=True)
    assert run.execution_summary.model_dump() == dict(planned=20, completed=20, partial=0, failed=0, not_run=0)
    assert run.evaluation_summary.model_dump() == dict(assessed=1, cannot_assess=19, not_run=0, failed=0)
    assert run.results.severity_distribution == {'0': 0, '1': 0, '2': 0, '3': 1}
    assert run.results.material_or_higher == run.results.severe == 1
    assert [(c.finding_count, c.scenario_count) for c in run.results.mechanisms.values()] == [(2, 1), (2, 1), (0, 0), (1, 1)]
    assert tuple(entry.scenario_id for entry in run.scenarios) == PACK_IDS
    assert len(list(bundle.glob('*/judge/attempt-*.json'))) == 20
    legacy = load_transcript(ROOT / 'demo/artifacts/RS-001/transcript.json')
    new = load_transcript(bundle / 'RS-001/transcript.json')
    assert transcript_fingerprint(new) == transcript_fingerprint(legacy)
    attempt = read_record(bundle / 'RS-001/judge/attempt-001.json', JudgeAttempt)
    assert [f.severity for f in attempt.evaluation().findings] == [1, 2, 3, 3]
    for entry in run.scenarios[1:]:
        assert entry.severity is None and entry.finding_count is None


def test_target_rerun_has_new_identity_and_deterministic_content(bundle, tmp_path):
    other = tmp_path / 'second'
    second = execute_fixture_pack(other)
    first = load_run(bundle / 'run.json', verify_references=True)
    assert first.run_id != second.run_id
    assert first.results == second.results
    for a, b in zip(first.scenarios, second.scenarios):
        assert a.transcript_run_id != b.transcript_run_id
        assert a.evaluation_id != b.evaluation_id
        ta = load_transcript(bundle / a.transcript_ref)
        tb = load_transcript(other / b.transcript_ref)
        assert transcript_fingerprint(ta) == transcript_fingerprint(tb)
    with pytest.raises(FileExistsError):
        execute_fixture_pack(bundle)


def test_transcript_and_provenance_are_durable_before_judge(tmp_path):
    directory = tmp_path / 'bundle'
    replay = PackJudge()
    seen = []

    class InspectingJudge:
        def assess(self, request, *, config):
            sid = request.scenario_id
            assert [t.content for t in load_transcript(directory / sid / 'transcript.json').turns] == [t.text for t in request.transcript]
            assert (directory / sid / 'target_call.json').is_file()
            assert not (directory / 'run.json').exists()
            seen.append(sid)
            return replay.assess(request, config=config)

    execute(directory, judge=InspectingJudge())
    assert tuple(seen) == PACK_IDS


@pytest.mark.parametrize('failure', ['target', 'partial', 'factory', 'judge_call', 'judge_schema'])
def test_failure_continuation_and_preserved_upstream_artifacts(tmp_path, failure):
    directory = tmp_path / 'bundle'
    replay = PackJudge()

    def target_factory(scenario):
        target = pack_target(scenario)
        if scenario.scenario_id != 'RS-003':
            return target
        if failure == 'factory':
            raise RuntimeError('setup failed')

        class FaultTarget:
            def respond(self, messages, *, config):
                if failure == 'target' or (failure == 'partial' and len(messages) > 1):
                    raise TimeoutError('target unavailable')
                return target.respond(messages, config=config)

        return FaultTarget()

    class FaultJudge:
        def assess(self, request, *, config):
            if request.scenario_id == 'RS-003':
                if failure == 'judge_call':
                    raise TimeoutError('judge unavailable')
                if failure == 'judge_schema':
                    return '{broken'
            return replay.assess(request, config=config)

    run = execute(directory, target_factory=target_factory, judge=FaultJudge())
    entry = run.scenarios[2]
    assert run.scenarios[-1].execution_status == 'completed'
    assert run.results.severity_distribution == {'0': 0, '1': 0, '2': 0, '3': 1}
    assert entry.severity is None
    transcript = load_transcript(directory / entry.transcript_ref)
    assert (directory / 'RS-003/target_call.json').exists()
    if failure in ('target', 'partial', 'factory'):
        expected = 'partial' if failure == 'partial' else 'failed'
        assert transcript.execution_status == entry.execution_status == expected
        assert entry.evaluation_status == 'not_run'
        assert run.execution_summary.completed == 19
        assert transcript.retry_count == 1
        assert not (directory / 'RS-003/judge').exists()
    else:
        assert transcript.execution_status == 'completed'
        assert entry.evaluation_status == 'failed'
        attempt = read_record(directory / 'RS-003/judge/attempt-001.json', JudgeAttempt)
        assert attempt.technical_status == 'failed'
        assert attempt.calls[0].failure_stage == failure
        assert attempt.calls[0].raw_response == ('{broken' if failure == 'judge_schema' else None)
        original = (directory / entry.transcript_ref).read_bytes()
        judge_saved_transcript(directory / 'execution.json', 'RS-003', replay)
        recovered = rebuild_run(directory / 'execution.json')
        assert recovered.scenarios[2].evaluation_status == 'cannot_assess'
        assert (directory / entry.transcript_ref).read_bytes() == original
        run = recovered
    assert load_run(directory / 'run.json', verify_references=True) == run


def test_technical_retries_stay_inside_one_attempt(tmp_path):
    replay = PackJudge()
    calls = {}

    class RetryJudge:
        def assess(self, request, *, config):
            sid = request.scenario_id
            calls[sid] = calls.get(sid, 0) + 1
            if sid == 'RS-002' and calls[sid] == 1:
                return '{broken'
            return replay.assess(request, config=config)

    directory = tmp_path / 'bundle'
    execute(directory, judge=RetryJudge(), judge_max_retries=1)
    attempt = read_record(directory / 'RS-002/judge/attempt-001.json', JudgeAttempt)
    assert_judge_attempt(attempt, transport_call_count=calls['RS-002'])
    assert attempt.retry_count == 1 and attempt.operation == 'initial'
    assert attempt.calls[0].failure_stage == 'judge_schema'
    assert attempt.calls[1].result.evaluation_status == 'cannot_assess'
    assert len(list((directory / 'RS-002/judge').glob('*.json'))) == 1
    assert load_run(directory / 'run.json', verify_references=True)


def test_judge_rerun_preserves_prior_assessment_and_failed_attempt_history(bundle, monkeypatch):
    original = {p: p.read_bytes() for p in bundle.glob('*/transcript.json')}
    first_path = bundle / 'RS-001/judge/attempt-001.json'
    first_bytes = first_path.read_bytes()
    first = read_record(first_path, JudgeAttempt)
    monkeypatch.setattr('psych_eval.suite.run_scenario', Mock(side_effect=AssertionError('target rerun')))
    second = judge_saved_transcript(bundle / 'execution.json', 'RS-001', PackJudge())
    assert second.attempt_index == 2 and second.operation == 'judge_rerun'
    assert second.attempt_id != first.attempt_id
    assert second.request == first.request
    with pytest.raises(FileExistsError):
        write_new(first_path, second)
    assert first_path.read_bytes() == first_bytes
    assert {p: p.read_bytes() for p in original} == original
    run = rebuild_run(bundle / 'execution.json')
    assert run.scenarios[0].evaluation_id == second.attempt_id
    prior_run = run
    evaluation_path = bundle / run.scenarios[0].evaluation_ref
    evaluation_bytes = evaluation_path.read_bytes()
    failing = Mock()
    failing.assess.side_effect = TimeoutError('rerun unavailable')
    third = judge_saved_transcript(bundle / 'execution.json', 'RS-001', failing)
    assert third.attempt_index == 3
    run = rebuild_run(bundle / 'execution.json')
    assert run == prior_run
    assert run.scenarios[0].evaluation_status == 'assessed'
    assert run.results.severe == 1
    assert evaluation_path.read_bytes() == evaluation_bytes
    saved_failure = read_record(bundle / 'RS-001/judge/attempt-003.json', JudgeAttempt)
    assert saved_failure == third
    assert saved_failure.technical_status == 'failed'
    assert saved_failure.calls[-1].failure_stage == 'judge_call'
    assert (bundle / 'RS-001/evaluations/attempt-001.json').exists()
    assert load_run(bundle / 'run.json', verify_references=True) == run


def test_report_exposes_target_failure_and_failed_judge_rerun_while_retaining_valid_result(tmp_path):
    directory = tmp_path / 'bundle'

    def factory(runtime):
        if runtime.scenario_id == 'RS-002':
            class FailedTarget:
                def respond(self, messages, *, config):
                    raise TimeoutError('target unavailable for diagnostic test')

            return FailedTarget()
        return pack_target(runtime)

    run = execute(
        directory, target_factory=factory,
        selection=resolve_selection(mode='custom', custom_scenario_ids=['RS-001', 'RS-002']),
    )
    assert run.scenarios[0].evaluation_status == 'assessed'
    failing = Mock()
    failing.assess.side_effect = TimeoutError('judge unavailable for diagnostic test')
    failed_rerun = judge_saved_transcript(directory / 'execution.json', 'RS-001', failing)
    assert failed_rerun.technical_status == 'failed'
    retained = rebuild_run(directory / 'execution.json')

    failures = load_technical_failures(directory / 'execution.json')
    assert [(item.scenario_id, item.failure_stage, item.judge_attempt_index) for item in failures] == [
        ('RS-001', 'judge_call', 2), ('RS-002', 'target_execution', None),
    ]
    view = load_run_view(directory / 'run.json')
    assert retained.scenarios[0].evaluation_status == 'assessed'
    assert view.scenarios[0].evaluation_status == 'Assessed'
    assert [(item.scenario_id, item.stage, item.judge_attempt_index) for item in view.technical_failures] == [
        ('RS-001', 'Judge call', 2), ('RS-002', 'Target execution', None),
    ]


def test_rebuild_restores_deleted_projections_without_inference(bundle, monkeypatch):
    original_run = (bundle / 'run.json').read_bytes()
    original_eval = (bundle / 'RS-001/evaluations/attempt-001.json').read_bytes()
    (bundle / 'run.json').unlink()
    for path in bundle.glob('*/evaluations/*.json'):
        path.unlink()
    for name in ('psych_eval.suite.run_scenario', 'psych_eval.suite.evaluate_transcript',
                 'psych_eval.integrations.pack_fixtures.PackJudge.assess', 'psych_eval.integrations.fixture_target.FixtureTarget.respond'):
        monkeypatch.setattr(name, Mock(side_effect=AssertionError('inference forbidden')))
    rebuild_run(bundle / 'execution.json')
    assert (bundle / 'run.json').read_bytes() == original_run
    assert (bundle / 'RS-001/evaluations/attempt-001.json').read_bytes() == original_eval
    load_run(bundle / 'run.json', verify_references=True)


@pytest.mark.parametrize('corruption', [
    'missing_target', 'missing_transcript', 'missing_attempt', 'missing_evaluation',
    'scenario_id', 'run_id', 'transcript_id', 'wrong_attempt_transcript', 'duplicate_attempt',
    'duplicate_scenario', 'counts', 'severity', 'schema', 'raw_result',
])
def test_reference_verification_fails_closed(bundle, corruption):
    files = {
        'missing_target': 'RS-002/target_call.json',
        'missing_transcript': 'RS-002/transcript.json',
        'missing_attempt': 'RS-002/judge/attempt-001.json',
        'missing_evaluation': 'RS-002/evaluations/attempt-001.json',
    }
    if corruption in files:
        (bundle / files[corruption]).unlink()
    elif corruption in ('counts', 'severity', 'duplicate_scenario'):
        path = bundle / 'run.json'
        data = json.loads(path.read_text(encoding="utf-8"))
        if corruption == 'counts':
            data['execution_summary']['completed'] = 19
        elif corruption == 'severity':
            data['results']['severe'] = 0
        else:
            data['scenarios'][1] = data['scenarios'][0]
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        path = bundle / 'RS-002/judge/attempt-001.json'
        data = json.loads(path.read_text(encoding="utf-8"))
        if corruption == 'scenario_id':
            data['request']['scenario_id'] = 'RS-003'
        elif corruption == 'run_id':
            data['suite_run_id'] = str(uuid4())
        elif corruption == 'transcript_id':
            data['transcript_snapshot']['run_id'] = str(uuid4())
        elif corruption == 'wrong_attempt_transcript':
            data['transcript_ref'] = 'RS-003/transcript.json'
        elif corruption == 'duplicate_attempt':
            data['attempt_id'] = json.loads((bundle / 'RS-001/judge/attempt-001.json').read_text(encoding="utf-8"))['attempt_id']
        elif corruption == 'raw_result':
            data['calls'][0]['result']['cannot_assess_reason'] = 'changed'
        else:
            data['unknown'] = True
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        load_run(bundle / 'run.json', verify_references=True)


def test_target_and_judge_configurations_are_independent_no_live_fallback(tmp_path):
    target = TargetConfig(provider='target-vendor', model='target-model', system_prompt='target-only',
                          sampling=dict(temperature=0.2, max_output_tokens=32))
    config = JudgeConfig(mode='live', provider='judge-vendor', model='judge-model')
    seen = []

    class LocalTarget:
        def respond(self, messages, *, config):
            assert config == target
            return 'Test double for independent adapter routing.'

    class FailingJudge:
        def assess(self, request, *, config):
            seen.append(config)
            raise TimeoutError('no live adapter/fallback')

    run = execute_suite(tmp_path / 'bundle', target_factory=lambda runtime: LocalTarget(),
                        target_mode='live', target_config=target, judge=FailingJudge(), judge_config=config)
    assert seen == [config] * 20
    assert run.evaluation_summary.failed == 20
    assert run.results.severity_distribution == dict.fromkeys(('0', '1', '2', '3'), 0)
    assert run.model_under_test.provider != run.judge.provider
    assert load_run(tmp_path / 'bundle/run.json', verify_references=True) == run


def test_full_pack_ui_overview_and_details(bundle, monkeypatch):
    monkeypatch.setenv('PSYCH_EVAL_RUN', str(bundle / 'run.json'))
    view = load_run_view(bundle / 'run.json', scenario_id='RS-020')
    assert view.planned == 20 and len(view.scenarios) == 20
    assert view.detail.cannot_assess_reason and len(view.detail.turns) == 8
    assert 'not evaluator validity' in view.disclosure
    app = AppTest.from_file(ROOT / 'streamlit_app.py', default_timeout=20).run()
    page_hashes = {
        page['url_pathname']: page_hash
        for page_hash, page in app._registered_pages.items()
    }
    app._page_hash = page_hashes['demo']
    app.query_params.update({'view': 'demo'})
    app.run()
    assert not app.exception and not app.error
    assert {metric.label: metric.value for metric in app.metric} == {
        'Scenarios evaluated': '1',
    }
    assert app.warning and app.warning[0].value == 'Some scenarios could not be fully evaluated.'
    assert sum(
        'data-route-label="View details"' in item.proto.body
        for item in app.get('html')
    ) == 20
    app._page_hash = page_hashes['demo-scenario']
    app.query_params.update({'view': 'demo', 'scenario': 'RS-001'})
    app.run()
    assert not app.exception and not app.error
    assert len(app.chat_message) == 8 and app.metric[1].value == '4'
    assert not app.expander[0].proto.expanded
    app.query_params.update({'view': 'demo', 'scenario': 'RS-020'})
    app.run()
    assert not app.exception and not app.error
    assert 'RS-020' in app.header[0].value and len(app.chat_message) == 8
    assert not app.expander[0].proto.expanded


def test_fresh_process_full_path_has_zero_network_or_sdk_calls(tmp_path):
    script = '''
import sys
def audit(event, args):
    if event.startswith('socket.'):
        raise AssertionError('network attempt')
    if event == 'import' and args[0].split('.')[0] in {'openai', 'anthropic', 'dotenv'}:
        raise AssertionError('provider SDK import')
sys.addaudithook(audit)
from pathlib import Path
from psych_eval.cli import execute_fixture_pack
from psych_eval.suite import rebuild_run
from psych_eval.runs import load_run
from psych_eval.run_presentation import load_run_view
path = Path(sys.argv[1]) / 'bundle'
run = execute_fixture_pack(path)
assert rebuild_run(path / 'execution.json') == run
assert load_run(path / 'run.json', verify_references=True) == run
assert load_run_view(path / 'run.json', scenario_id='RS-020').planned == 20
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reserved_attempt_fails_before_any_judge_call(bundle):
    # Interrupted or concurrent writers leave a reservation; never overwrite it.
    path = bundle / 'RS-001/judge/attempt-002.json'
    path.write_text('', encoding="utf-8")
    judge = Mock()
    with pytest.raises(ValueError):
        judge_saved_transcript(bundle / 'execution.json', 'RS-001', judge)
    judge.assess.assert_not_called()
    assert path.read_bytes() == b''


def test_judge_config_sampling_and_prompt_are_passed_and_preserved(tmp_path):
    from psych_eval.integrations.pack_fixtures import PLACEHOLDER
    from psych_eval.judge import JudgeResult, JudgeSamplingConfig

    target = pack_target(discover_pack()[0].to_runtime_view()).config
    config = JudgeConfig(mode='live', provider='judge-vendor', model='judge-model',
                         prompt_version='0.1',
                         sampling=JudgeSamplingConfig(
                             max_output_tokens=400, reasoning={'effort': 'medium'},
                         ))
    raw = JudgeResult(category='relational_sycophancy', evaluation_status='cannot_assess',
                      findings=[], zero_rationale=None, cannot_assess_reason='Routing test only.',
                      recovery='unevaluated', persistence='unevaluated').model_dump_json()
    judge = Mock()
    judge.assess.return_value = raw
    directory = tmp_path / 'bundle'
    execute_suite(directory, target_factory=pack_target, target_config=target, target_mode='fixture',
                  judge=judge, judge_config=config)
    assert all(call.kwargs['config'] == config for call in judge.assess.call_args_list)
    attempt = read_record(directory / 'RS-001/judge/attempt-001.json', JudgeAttempt)
    assert attempt.judge_sampling == config.sampling
    assert attempt.judge_sampling.temperature is None
    assert attempt.judge_sampling.reasoning.effort == 'medium'
    assert attempt.judge_prompt_version == '0.1'
    assert attempt.source_transcript.target.sampling != attempt.judge_sampling
    persisted = json.loads((directory / 'execution.json').read_text())
    assert persisted['judge']['sampling'] == {
        'max_output_tokens': 400, 'reasoning': {'effort': 'medium'},
    }
    assert persisted['judge_sampling'] == persisted['judge']['sampling']
    assert load_run(directory / 'run.json', verify_references=True)


@pytest.mark.parametrize('field,value', [
    ('attempt_index', 2), ('retry_count', 1), ('technical_status', 'failed'),
    ('max_retries', -1), ('operation', 'judge_rerun'),
])
def test_attempt_state_validation(bundle, field, value):
    data = json.loads((bundle / 'RS-001/judge/attempt-001.json').read_text(encoding="utf-8"))
    data[field] = value
    with pytest.raises(ValueError):
        JudgeAttempt.model_validate_json(json.dumps(data))


def test_technical_target_retry_is_not_a_new_transcript_sample(tmp_path):
    calls = {}

    def factory(runtime):
        replay = pack_target(runtime)

        class OnceFailing:
            def respond(self, messages, *, config):
                sid = runtime.scenario_id
                calls[sid] = calls.get(sid, 0) + 1
                if sid == 'RS-002' and calls[sid] == 1:
                    raise TimeoutError('transient')
                return replay.respond(messages, config=config)

        return OnceFailing()

    directory = tmp_path / 'bundle'
    run = execute(directory, target_factory=factory)
    transcript = load_transcript(directory / 'RS-002/transcript.json')
    assert transcript.retry_count == 1 and len(transcript.turns) == 8
    assert run.execution_summary.completed == 20
    assert len(list((directory / 'RS-002').glob('transcript*.json'))) == 1
    assert len(list((directory / 'RS-002/judge').glob('*.json'))) == 1


def test_attempt_symlink_outside_bundle_is_rejected(bundle, tmp_path):
    path = bundle / 'RS-001/judge/attempt-001.json'
    outside = tmp_path / 'outside.json'
    outside.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Runtime cannot create the symlink needed to test bundle escape: {exc}")
    with pytest.raises(ValueError, match='outside'):
        load_run(bundle / 'run.json', verify_references=True)


def test_relative_bundle_path_and_rebuild(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path('relative-bundle')
    run = execute_fixture_pack(path)
    assert rebuild_run(path / 'execution.json') == run
    assert load_run(path / 'run.json', verify_references=True) == run


def test_saved_review_bundle_loads_with_all_references():
    path = ROOT / 'demo/runs/relational-sycophancy-full-pack-v1/run.json'
    run = load_run(path, verify_references=True)
    assert run.execution_summary.completed == run.execution_summary.planned == 20
    assert run.evaluation_summary.cannot_assess == 19
    assert load_run_view(path, scenario_id='RS-001').detail.finding_count == 4


def test_failed_first_judgment_then_successful_assessment(tmp_path):
    directory = tmp_path / 'bundle'
    judge = Mock()
    judge.assess.side_effect = TimeoutError('initial judgment unavailable')
    initial = execute(directory, judge=judge)
    assert initial.scenarios[0].evaluation_status == 'failed'
    failed_path = directory / 'RS-001/judge/attempt-001.json'
    failed_bytes = failed_path.read_bytes()
    successful = judge_saved_transcript(directory / 'execution.json', 'RS-001', PackJudge())
    run = rebuild_run(directory / 'execution.json')
    assert successful.attempt_index == 2
    assert run.scenarios[0].evaluation_id == successful.attempt_id
    assert run.scenarios[0].evaluation_status == 'assessed'
    assert run.scenarios[0].severity == 3 and run.results.severe == 1
    assert failed_path.read_bytes() == failed_bytes
    assert load_run(directory / 'run.json', verify_references=True) == run


@pytest.mark.parametrize('status', ['assessed', 'cannot_assess'])
def test_newest_valid_evaluation_wins_regardless_of_prior_severity(bundle, status):
    first_run = load_run(bundle / 'run.json', verify_references=True)
    first_path = bundle / first_run.scenarios[0].evaluation_ref
    first_bytes = first_path.read_bytes()
    judge = Mock()
    judge.assess.return_value = json.dumps({
        'category': 'relational_sycophancy', 'evaluation_status': status,
        'findings': [], 'zero_rationale': 'Synthetic selection test.' if status == 'assessed' else None,
        'cannot_assess_reason': 'Synthetic selection test.' if status == 'cannot_assess' else None,
        'recovery': 'unevaluated', 'persistence': 'unevaluated',
    })
    newest = judge_saved_transcript(bundle / 'execution.json', 'RS-001', judge)
    run = rebuild_run(bundle / 'execution.json')
    assert run.scenarios[0].evaluation_id == newest.attempt_id
    assert run.scenarios[0].evaluation_ref == 'RS-001/evaluations/attempt-002.json'
    assert run.scenarios[0].evaluation_status == status
    assert run.scenarios[0].severity == (0 if status == 'assessed' else None)
    assert run.results.severe == 0
    assert run.results.severity_distribution['0'] == (1 if status == 'assessed' else 0)
    assert first_path.read_bytes() == first_bytes
    assert rebuild_run(bundle / 'execution.json') == run
    assert load_run(bundle / 'run.json', verify_references=True) == run


def test_all_judge_attempts_fail_without_fabricating_evaluation(tmp_path):
    directory = tmp_path / 'bundle'
    judge = Mock()
    judge.assess.side_effect = TimeoutError('judge unavailable')
    initial = execute(directory, judge=judge)
    newest = judge_saved_transcript(directory / 'execution.json', 'RS-001', judge)
    run = rebuild_run(directory / 'execution.json')
    assert newest.attempt_index == 2 and newest.technical_status == 'failed'
    assert run == initial
    assert run.evaluation_summary.failed == 20
    assert run.scenarios[0].evaluation_id is None
    assert run.scenarios[0].evaluation_ref is None
    assert run.scenarios[0].severity is None
    assert not (directory / 'RS-001/evaluations').exists()
    assert len(list((directory / 'RS-001/judge').glob('attempt-*.json'))) == 2
    assert run.results.severity_distribution == dict.fromkeys(('0', '1', '2', '3'), 0)
    assert load_run(directory / 'run.json', verify_references=True) == run
