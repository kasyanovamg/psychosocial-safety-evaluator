"""Runtime-only selection and independent role factories, outside evaluator core."""

from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from psych_eval.judge import Judge, JudgeConfig, JudgeError
from psych_eval.judge_payload import INSTRUCTIONS_BY_VERSION
from psych_eval.runner import Target
from psych_eval.scenarios import RuntimeScenarioView
from psych_eval.selection import SelectionRequest
from psych_eval.transcripts import TargetConfig


class IntegrationConfigError(ValueError):
    """Safe configuration diagnostic; never includes adapter exception text."""


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    integration: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    # Runtime input only. Never serialize these values or include them in repr.
    options: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)


class TargetSelection(Selection):
    config: TargetConfig


class JudgeSelection(Selection):
    config: JudgeConfig


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    target: TargetSelection
    judge: JudgeSelection
    target_max_retries: int = Field(default=1, ge=0)
    judge_max_retries: int = Field(default=0, ge=0)
    scenario_selection: SelectionRequest = Field(default_factory=SelectionRequest)


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    try:
        runtime = RuntimeConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError, yaml.YAMLError):
        # YAML and validation errors may echo credentials from the source file.
        raise IntegrationConfigError('Invalid runtime config; check target/judge selections, configs, and options') from None
    if (runtime.judge.config.prompt_version is not None
            and runtime.judge.config.prompt_version not in INSTRUCTIONS_BY_VERSION):
        raise IntegrationConfigError('Unsupported judge prompt_version')
    return runtime


def _fixture_target(*, config, options):
    from psych_eval.integrations.pack_fixtures import pack_target
    from psych_eval.suite import discover_pack

    if options or config != pack_target(discover_pack()[0].to_runtime_view()).config:
        raise IntegrationConfigError('Fixture target requires its exact public config and empty options')
    return pack_target


def _fixture_judge(*, config, options):
    from psych_eval.integrations.pack_fixtures import PackJudge

    judge = PackJudge()
    if options or config != judge.config:
        raise IntegrationConfigError('Fixture judge requires its exact public config and empty options')
    return judge


def integration_names() -> dict[str, list[str]]:
    """Enumerate names by role from metadata only; never import adapter code."""
    try:
        return {
            role: sorted({'fixture', *(entry.name for entry in entry_points(group=f'psych_eval.{role}s'))})
            for role in ('target', 'judge')
        }
    except Exception:
        raise IntegrationConfigError('Unable to read installed integration entry points') from None


def resolve_factory(name: str, role: Literal['target', 'judge']) -> Callable:
    """Load only the selected role. Installed entry points are trusted Python code."""
    if role not in ('target', 'judge'):
        raise IntegrationConfigError('Integration role must be target or judge')
    if name == 'fixture':
        return _fixture_target if role == 'target' else _fixture_judge
    try:
        candidates = list(entry_points(group=f'psych_eval.{role}s', name=name))
        other_role = 'judge' if role == 'target' else 'target'
        other = list(entry_points(group=f'psych_eval.{other_role}s', name=name)) if not candidates else []
    except Exception:
        raise IntegrationConfigError('Unable to read installed integration entry points') from None
    if not candidates:
        if other:
            raise IntegrationConfigError(f'Integration {name!r} does not support role {role!r}')
        raise IntegrationConfigError(f'Unknown {role} integration {name!r}')
    if len(candidates) != 1:
        raise IntegrationConfigError(f'Ambiguous {role} integration {name!r}; multiple entry points installed')
    try:
        factory = candidates[0].load()
        if not callable(factory):
            raise TypeError
    except Exception:
        raise IntegrationConfigError(f'Cannot load {role} integration {name!r}') from None
    return factory


def _construct(factory, selection, role):
    try:
        # Factories can consume/mutate their options without affecting the caller.
        selected = selection.model_copy(deep=True)
        adapter = factory(config=selected.config, options=selected.options)
        valid = callable(adapter) if role == 'target' else callable(getattr(adapter, 'assess', None))
    except Exception:
        raise IntegrationConfigError(f'Cannot configure {role} integration {selection.integration!r}') from None
    if not valid:
        expected = 'a scenario-to-target factory' if role == 'target' else 'a Judge with assess()'
        raise IntegrationConfigError(f'{role.title()} integration must return {expected}')
    return adapter


class _TargetBoundary:
    def __init__(self, factory, scenario):
        self.factory, self.scenario, self.adapter = factory, scenario, None

    def respond(self, messages, *, config):
        try:
            if self.adapter is None:
                self.adapter = self.factory(self.scenario)
            return self.adapter.respond(messages, config=config)
        except Exception:
            # Core persists exception strings. Do not let SDK/setup errors escape.
            raise RuntimeError('Target integration call failed') from None


class _JudgeBoundary:
    def __init__(self, adapter):
        self.adapter = adapter

    def assess(self, request, *, config):
        try:
            return self.adapter.assess(request, config=config)
        except JudgeError as exc:
            raise JudgeError(exc.failure_stage, 'Judge integration call failed') from None
        except Exception:
            raise JudgeError('judge_call', 'Judge integration call failed') from None


def configure_integrations(runtime: RuntimeConfig) -> tuple[Callable[[RuntimeScenarioView], Target], Judge]:
    """Resolve both roles before constructing either; never select a fallback."""
    target_builder = resolve_factory(runtime.target.integration, 'target')
    judge_builder = resolve_factory(runtime.judge.integration, 'judge')
    factory = _construct(target_builder, runtime.target, 'target')
    judge = _construct(judge_builder, runtime.judge, 'judge')
    return lambda scenario: _TargetBoundary(factory, scenario), _JudgeBoundary(judge)


def configure_judge(runtime: RuntimeConfig) -> Judge:
    """Construct only the selected judge; saved-transcript workflows need no target."""
    judge_builder = resolve_factory(runtime.judge.integration, 'judge')
    return _JudgeBoundary(_construct(judge_builder, runtime.judge, 'judge'))
