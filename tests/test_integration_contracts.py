"""Independent adapter composition and an enforced provider-free core boundary."""

import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from psych_eval.judge import JudgeConfig, JudgeError
from psych_eval.suite import JudgeAttempt, execute_suite, read_record, rebuild_run
from psych_eval.transcripts import TargetConfig, load_transcript


ROOT = Path(__file__).resolve().parents[1]
SECRET = 'test-only-private-credential'


def response(status):
    return json.dumps(dict(
        category='relational_sycophancy', evaluation_status=status, findings=[],
        zero_rationale='Synthetic adapter result.' if status == 'assessed' else None,
        cannot_assess_reason='Synthetic abstention.' if status == 'cannot_assess' else None,
        recovery='unevaluated', persistence='unevaluated',
    ))


class VendorFailure(Exception):
    """Simulated SDK error that includes private request authentication."""


class VendorClient:
    def __init__(self, *, credential, fail=False):
        self.credential = credential
        self.fail = fail

    def call(self):
        if self.fail:
            raise VendorFailure(f'Authorization: {self.credential}')


class TargetA:
    def respond(self, messages, *, config):
        assert config.provider == 'target-a'
        assert messages[-1].role == 'user'
        return 'Synthetic target A response.'


class TargetB:
    def __init__(self, client):
        self.client = client

    def respond(self, messages, *, config):
        assert config.provider == 'target-b'
        try:
            self.client.call()
        except VendorFailure:
            # Adapter owns translation; raw SDK error text must not be persisted.
            raise RuntimeError('Target service unavailable') from None
        return 'Synthetic target B response.'


class JudgeA:
    def assess(self, request, *, config):
        assert config.provider == 'judge-a'
        assert not hasattr(request, 'target')
        return response('cannot_assess')


class JudgeB:
    def __init__(self, client):
        self.client = client

    def assess(self, request, *, config):
        assert config.provider == 'judge-b'
        assert not hasattr(request, 'target')
        try:
            self.client.call()
        except VendorFailure:
            raise JudgeError('judge_call', 'Judge service unavailable') from None
        return response('assessed')


def execute(path, target, judge, target_provider, judge_provider):
    return execute_suite(
        path, target_factory=lambda scenario: target, target_mode='live',
        target_config=TargetConfig(
            provider=target_provider, model='unknown-target', system_prompt='Target only',
            sampling=dict(temperature=0.2, max_output_tokens=32),
        ),
        judge=judge,
        judge_config=JudgeConfig(
            mode='live', provider=judge_provider, model='unrelated-judge',
            sampling=dict(temperature=0.0, max_output_tokens=512),
        ),
    )


@pytest.mark.parametrize('pair', ['a-b', 'b-a'])
def test_unknown_adapters_compose_in_both_directions(tmp_path, pair):
    client = VendorClient(credential=SECRET)
    target, judge, tp, jp, status = (
        (TargetA(), JudgeB(client), 'target-a', 'judge-b', 'assessed')
        if pair == 'a-b' else
        (TargetB(client), JudgeA(), 'target-b', 'judge-a', 'cannot_assess')
    )
    path = tmp_path / 'bundle'
    run = execute(path, target, judge, tp, jp)
    assert run.execution_summary.completed == 20
    assert all(entry.evaluation_status == status for entry in run.scenarios)
    assert all(entry.severity == (0 if status == 'assessed' else None) for entry in run.scenarios)
    assert run.model_under_test.provider == tp
    assert run.judge.provider == jp
    assert run.model_under_test.sampling != run.judge.sampling
    assert rebuild_run(path / 'execution.json') == run
    assert all(SECRET not in p.read_text() for p in path.rglob('*.json'))


@pytest.mark.parametrize('failed_role', ['target', 'judge'])
def test_sdk_errors_translate_to_existing_persisted_technical_failures(tmp_path, failed_role):
    client = VendorClient(credential=SECRET, fail=True)
    path = tmp_path / 'bundle'
    if failed_role == 'target':
        run = execute(path, TargetB(client), JudgeA(), 'target-b', 'judge-a')
        transcript = load_transcript(path / 'RS-001/transcript.json')
        assert transcript.failure.failure_stage == 'target_execution'
        assert transcript.failure.failure_reason == 'target_exception'
        assert transcript.retry_count == 1
        assert run.execution_summary.failed == 20
        assert run.evaluation_summary.not_run == 20
        assert not list(path.glob('*/judge/attempt-*.json'))
    else:
        run = execute(path, TargetA(), JudgeB(client), 'target-a', 'judge-b')
        attempt = read_record(path / 'RS-001/judge/attempt-001.json', JudgeAttempt)
        assert attempt.technical_status == 'failed'
        assert attempt.calls[0].failure_stage == 'judge_call'
        assert attempt.calls[0].raw_response is None
        assert run.evaluation_summary.failed == 20
        assert run.evaluation_summary.cannot_assess == 0
    assert all(entry.severity is None for entry in run.scenarios)
    assert all(SECRET not in p.read_text() for p in path.rglob('*.json'))
    assert rebuild_run(path / 'execution.json') == run


def test_core_imports_without_integrations_or_provider_sdks():
    # Block implementations too: lazy SDK imports alone do not prove separation.
    script = '''
import importlib
import importlib.abc
from pathlib import Path
import sys
class BlockProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith('psych_eval.integrations')
                or fullname == 'psych_eval.cli'
                or fullname.split('.')[0] in
                {'openai', 'anthropic', 'google', 'ollama', 'lmstudio'}):
            raise AssertionError('core imported integration: ' + fullname)
sys.meta_path.insert(0, BlockProviders())
for path in Path('src/psych_eval').glob('*.py'):
    if path.stem not in {'cli', '__init__'}:
        importlib.import_module('psych_eval.' + path.stem)
from psych_eval.suite import rebuild_run
run = rebuild_run('demo/runs/relational-sycophancy-full-pack-v1/execution.json', persist=False)
assert run.execution_summary.completed == 20
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert not any(name in dependency.lower() for dependency in metadata['project']['dependencies']
                   for name in ('openai', 'anthropic', 'google-genai', 'ollama', 'lmstudio'))
