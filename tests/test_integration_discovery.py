"""Discovery through real distribution metadata, independent runtime options, CLI."""

import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from psych_eval.cli import execute_configured_suite, execute_fixture_pack
from psych_eval.integrations.runtime import (
    IntegrationConfigError, load_runtime_config, resolve_factory,
)
from psych_eval.suite import JudgeAttempt, read_record
from psych_eval.transcripts import load_transcript


TARGET_SECRET = 'private-target-credential'
JUDGE_SECRET = 'private-judge-credential'


@pytest.fixture
def installed(tmp_path, monkeypatch):
    # A distribution installed on sys.path: exercise importlib.metadata itself.
    for name in ('vendor_a', 'vendor_b'):
        (tmp_path / f'{name}.py').write_text('''
import json
from psych_eval.adapters import JudgeError
target_calls = 0
judge_calls = 0

def target(*, config, options):
    assert config.provider == __name__
    assert options['credential'] == 'private-target-credential'
    if options.get('fail') == 'setup':
        raise ValueError(options['credential'])
    def factory(scenario):
        if options.get('fail') == 'factory':
            raise ValueError(options['credential'])
        class Target:
            def respond(self, messages, *, config):
                global target_calls
                target_calls += 1
                if options.get('fail') == 'call':
                    raise ValueError(options['credential'])
                return 'Independent response from ' + __name__
        return Target()
    return factory

def judge(*, config, options):
    assert config.provider == __name__
    assert options['credential'] == 'private-judge-credential'
    if options.get('fail') == 'setup':
        raise ValueError(options['credential'])
    class Judge:
        def assess(self, request, *, config):
            global judge_calls
            judge_calls += 1
            if options.get('fail') == 'call':
                raise ValueError(options['credential'])
            if options.get('fail') == 'normalized':
                raise JudgeError('judge_input', options['credential'], raw_response=options['credential'])
            return json.dumps(dict(
                category='relational_sycophancy', evaluation_status='assessed',
                findings=[], zero_rationale='Synthetic discovery test.', cannot_assess_reason=None,
                recovery='unevaluated', persistence='unevaluated'))
    return Judge()
''')
        metadata = tmp_path / f'{name}-1.0.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n')
        (metadata / 'entry_points.txt').write_text(
            f'[psych_eval.targets]\n{name} = {name}:target\n'
            + (f'target_only = {name}:target\n' if name == 'vendor_a' else '')
            + f'[psych_eval.judges]\n{name} = {name}:judge\n'
            + (f'judge_only = {name}:judge\n' if name == 'vendor_b' else '')
        )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in ('vendor_a', 'vendor_b'):
        sys.modules.pop(name, None)


def config_data(target='vendor_a', judge='vendor_b'):
    return dict(
        target=dict(integration=target, config=dict(
            provider=target, model='target-model', system_prompt='Target only',
            sampling=dict(temperature=0.2, max_output_tokens=32)),
            options=dict(credential=TARGET_SECRET, transport='target-private-transport')),
        judge=dict(integration=judge, config=dict(
            mode='live', provider=judge, model='judge-model',
            sampling=dict(temperature=0.0, max_output_tokens=512)),
            options=dict(credential=JUDGE_SECRET, transport='judge-private-transport')),
    )


def save_config(tmp_path, data):
    path = tmp_path / 'runtime.yaml'
    path.write_text(yaml.safe_dump(data))
    return path


def assert_private(bundle):
    for path in bundle.rglob('*.json'):
        text = path.read_text()
        for private in (TARGET_SECRET, JUDGE_SECRET, 'private-transport', '"options"', '"integration"'):
            assert private not in text


@pytest.mark.parametrize('target,judge', [('vendor_a', 'vendor_b'), ('vendor_b', 'vendor_a')])
def test_discovered_roles_compose_with_independent_options(installed, target, judge):
    path = save_config(installed, config_data(target, judge))
    config = load_runtime_config(path)
    assert TARGET_SECRET not in repr(config) + config.model_dump_json()
    assert JUDGE_SECRET not in repr(config) + config.model_dump_json()
    bundle = installed / 'bundle'
    run = execute_configured_suite(bundle, path)
    assert run.execution_summary.completed == run.evaluation_summary.assessed == 20
    assert run.model_under_test.provider == target
    assert run.judge.provider == judge
    assert run.model_under_test.sampling != run.judge.sampling
    transcript = load_transcript(bundle / 'RS-001/transcript.json')
    assert transcript.turns[1].content.endswith(target)
    assert_private(bundle)


@pytest.mark.parametrize('name,role,message', [
    ('target_only', 'judge', 'does not support role'),
    ('judge_only', 'target', 'does not support role'),
    ('missing', 'target', 'Unknown target integration'),
    ('missing', 'judge', 'Unknown judge integration'),
])
def test_invalid_selection_fails_before_bundle_creation(installed, name, role, message):
    data = config_data()
    data[role]['integration'] = name
    # Setup would fail if either builder ran before both selections resolved.
    data['target']['options']['fail'] = 'setup'
    data['judge']['options']['fail'] = 'setup'
    path = save_config(installed, data)
    with pytest.raises(IntegrationConfigError, match=message) as caught:
        execute_configured_suite(installed / 'bundle', path)
    assert TARGET_SECRET not in str(caught.value) and JUDGE_SECRET not in str(caught.value)
    assert not (installed / 'bundle').exists()


@pytest.mark.parametrize('role,stage', [
    ('target', 'setup'), ('judge', 'setup'), ('target', 'factory'),
    ('target', 'call'), ('judge', 'call'), ('judge', 'normalized'),
])
def test_credentials_never_escape_setup_or_persisted_failure_details(installed, role, stage):
    data = config_data()
    data[role]['options']['fail'] = stage
    path = save_config(installed, data)
    bundle = installed / 'bundle'
    if stage == 'setup':
        with pytest.raises(IntegrationConfigError) as caught:
            execute_configured_suite(bundle, path)
        assert TARGET_SECRET not in str(caught.value) and JUDGE_SECRET not in str(caught.value)
        assert not bundle.exists()
    else:
        run = execute_configured_suite(bundle, path)
        if role == 'target':
            assert run.execution_summary.failed == 20
            transcript = load_transcript(bundle / 'RS-001/transcript.json')
            assert transcript.failure.failure_reason == 'target_exception'
            assert transcript.retry_count == 1
        else:
            assert run.evaluation_summary.failed == 20
            assert run.evaluation_summary.cannot_assess == 0
            attempt = read_record(bundle / 'RS-001/judge/attempt-001.json', JudgeAttempt)
            assert attempt.calls[0].failure_stage == ('judge_input' if stage == 'normalized' else 'judge_call')
            assert attempt.calls[0].raw_response is None
        assert_private(bundle)


def test_duplicate_names_fail_clearly(installed):
    path = installed / 'vendor_b-1.0.dist-info/entry_points.txt'
    path.write_text(path.read_text().replace('[psych_eval.targets]', '[psych_eval.targets]\nvendor_a = vendor_b:target'))
    with pytest.raises(IntegrationConfigError, match='Ambiguous target integration'):
        resolve_factory('vendor_a', 'target')


@pytest.mark.parametrize('role,vendor', [('target', 'vendor_a'), ('judge', 'vendor_b')])
def test_load_errors_are_safe(installed, role, vendor):
    (installed / 'broken.py').write_text(f'raise RuntimeError({TARGET_SECRET!r})')
    path = installed / f'{vendor}-1.0.dist-info/entry_points.txt'
    path.write_text(path.read_text().replace(f'{vendor}:{role}', f'broken:{role}'))
    with pytest.raises(IntegrationConfigError, match=f'Cannot load {role}') as caught:
        resolve_factory(vendor, role)
    assert TARGET_SECRET not in str(caught.value)


@pytest.mark.parametrize('contents', [f'target: [{TARGET_SECRET}', f'password: {TARGET_SECRET}', '[]'])
def test_invalid_yaml_and_config_do_not_echo_secrets(tmp_path, contents):
    path = tmp_path / 'config.yaml'
    path.write_text(contents)
    with pytest.raises(IntegrationConfigError, match='Invalid runtime config; check target/judge selections, configs, and options') as caught:
        load_runtime_config(path)
    assert TARGET_SECRET not in str(caught.value)


def test_builtin_fixture_selection_matches_existing_command(tmp_path):
    from psych_eval.integrations.pack_fixtures import PackJudge, pack_target
    from psych_eval.suite import discover_pack

    data = dict(
        target=dict(integration='fixture', config=pack_target(discover_pack()[0].to_runtime_view()).config.model_dump()),
        judge=dict(integration='fixture', config=PackJudge().config.model_dump()),
    )
    configured = execute_configured_suite(tmp_path / 'configured', save_config(tmp_path, data))
    original = execute_fixture_pack(tmp_path / 'original')
    assert configured.results == original.results
    assert configured.execution_summary == original.execution_summary
    assert configured.evaluation_summary == original.evaluation_summary


def test_cli_selects_installed_packages_and_reports_safe_errors(installed, monkeypatch):
    import os

    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(installed), str(Path('src').resolve())]))
    config = save_config(installed, config_data())
    command = [sys.executable, '-m', 'psych_eval.suite', 'run', str(installed / 'bundle'), '--config', str(config)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['evaluation_summary']['assessed'] == 20
    data = config_data()
    data['judge']['options']['fail'] = 'setup'
    save_config(installed, data)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Cannot configure judge' in result.stderr
    assert TARGET_SECRET not in result.stderr and JUDGE_SECRET not in result.stderr
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('role', ['target', 'judge'])
def test_invalid_factory_result_fails_before_execution(installed, role):
    name = 'vendor_a' if role == 'target' else 'vendor_b'
    (installed / f'{name}.py').write_text(f'def {role}(*, config, options):\n    return None\n')
    config = save_config(installed, config_data())
    with pytest.raises(IntegrationConfigError, match='must return'):
        execute_configured_suite(installed / 'bundle', config)
    assert not (installed / 'bundle').exists()


def test_invalid_prompt_version_fails_before_loading_integrations(installed):
    data = config_data()
    data['judge']['config']['prompt_version'] = JUDGE_SECRET
    with pytest.raises(IntegrationConfigError, match='Unsupported judge prompt_version') as caught:
        execute_configured_suite(installed / 'bundle', save_config(installed, data))
    assert JUDGE_SECRET not in str(caught.value)
    assert not (installed / 'bundle').exists()
    assert 'vendor_a' not in sys.modules and 'vendor_b' not in sys.modules


def test_cli_lists_installed_names_by_role_without_loading_adapters(installed, monkeypatch, capsys):
    from importlib.metadata import EntryPoint
    from psych_eval.cli import main

    def forbidden_load(self):
        pytest.fail('listing must not load integration code')

    monkeypatch.setattr(EntryPoint, 'load', forbidden_load)
    # Duplicate registrations appear once; resolution still rejects ambiguity.
    path = installed / 'vendor_b-1.0.dist-info/entry_points.txt'
    path.write_text(path.read_text().replace('[psych_eval.targets]', '[psych_eval.targets]\nvendor_a = missing:target'))
    monkeypatch.setattr(sys, 'argv', ['psych_eval.suite', 'integrations'])
    main()
    targets, judges = capsys.readouterr().out.strip().split('\n\n')
    target_names, judge_names = targets.splitlines()[1:], judges.splitlines()[1:]
    assert targets.startswith('Targets:\n') and judges.startswith('Judges:\n')
    assert {'fixture', 'target_only', 'vendor_a', 'vendor_b'} <= set(target_names)
    assert {'fixture', 'judge_only', 'vendor_a', 'vendor_b'} <= set(judge_names)
    assert 'target_only' not in judge_names and 'judge_only' not in target_names
    assert target_names == sorted(set(target_names))
    assert judge_names == sorted(set(judge_names))
    assert 'vendor_a' not in sys.modules and 'vendor_b' not in sys.modules


def test_cli_lists_fixtures_without_external_packages(monkeypatch, capsys):
    from psych_eval.cli import main

    monkeypatch.setattr('psych_eval.integrations.runtime.entry_points', lambda **kwargs: ())
    monkeypatch.setattr(sys, 'argv', ['psych_eval.suite', 'integrations'])
    main()
    assert capsys.readouterr().out == 'Targets:\nfixture\n\nJudges:\nfixture\n'


@pytest.mark.parametrize('operation', ['listing', 'resolution'])
def test_metadata_errors_exclude_secrets(monkeypatch, capsys, operation):
    from psych_eval.cli import main

    def broken_metadata(**kwargs):
        raise RuntimeError(TARGET_SECRET + JUDGE_SECRET)

    monkeypatch.setattr('psych_eval.integrations.runtime.entry_points', broken_metadata)
    if operation == 'listing':
        monkeypatch.setattr(sys, 'argv', ['psych_eval.suite', 'integrations'])
        with pytest.raises(SystemExit) as caught:
            main()
        assert caught.value.code == 2
        output = capsys.readouterr()
        assert output.out == ''
        diagnostic = output.err
        assert 'Traceback' not in diagnostic
    else:
        with pytest.raises(IntegrationConfigError) as caught:
            resolve_factory('example', 'judge')
        diagnostic = str(caught.value)
    assert 'Unable to read installed integration entry points' in diagnostic
    assert TARGET_SECRET not in diagnostic and JUDGE_SECRET not in diagnostic


@pytest.mark.parametrize('arguments,message', [
    (['fixture'], 'path is required'),
    (['run', '--config', 'runtime.yaml'], 'path is required'),
    (['integrations', 'unused-path'], 'does not accept a path'),
])
def test_listing_optional_path_does_not_change_execution_arguments(monkeypatch, capsys, arguments, message):
    from psych_eval.cli import main

    monkeypatch.setattr(sys, 'argv', ['psych_eval.suite', *arguments])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize('target_name,judge_name', [('vendor_a', 'vendor_b'), ('vendor_b', 'vendor_a')])
def test_external_packages_use_public_api_and_contract_kit(installed, target_name, judge_name):
    import importlib
    from psych_eval.adapters import RuntimeScenarioView, EvaluatorScenarioView
    from psych_eval.testing import assert_target_contract, assert_judge_contract

    config = load_runtime_config(save_config(installed, config_data(target_name, judge_name)))
    scenario = RuntimeScenarioView(scenario_id='KIT-001', scenario_version='1.0',
        construct='relational_sycophancy', user_turns=[' First user. ', 'Second user.'],
        max_turns=2, runtime_context={})
    target = resolve_factory(target_name, 'target')(config=config.target.config, options=config.target.options)(scenario)
    judge = resolve_factory(judge_name, 'judge')(config=config.judge.config, options=config.judge.options)
    target_module, judge_module = importlib.import_module(target_name), importlib.import_module(judge_name)
    transcript = assert_target_contract(target, config.target.config, scenario,
        ['Independent response from ' + target_name] * 2,
        transport_calls=lambda: target_module.target_calls, secrets=(TARGET_SECRET, JUDGE_SECRET))
    raw = json.dumps(dict(category='relational_sycophancy', evaluation_status='assessed', findings=[],
        zero_rationale='Synthetic discovery test.', cannot_assess_reason=None,
        recovery='unevaluated', persistence='unevaluated'))
    assert_judge_contract(judge, config.judge.config, transcript,
        EvaluatorScenarioView(scenario_id='KIT-001', scenario_version='1.0', construct='relational_sycophancy'),
        raw_response=raw, transport_calls=lambda: judge_module.judge_calls,
        secrets=(TARGET_SECRET, JUDGE_SECRET))
