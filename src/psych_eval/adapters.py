"""Public V1 adapter API. Re-exports preserve the original class identities.

Implement Target and/or Judge structurally; no inheritance or registration object
is required. Register independent factories in psych_eval.targets/psych_eval.judges.
"""

from psych_eval.judge import (
    Finding, Judge, JudgeConfig, JudgeError, JudgeInput, JudgeResult, JudgeSamplingConfig,
    JudgeTurn, ReasoningConfig,
)
from psych_eval.judge_payload import (
    DEFAULT_JUDGE_PROMPT_VERSION, JUDGE_PROMPT_VERSION, RUBRIC_VERSION,
    SUPPORTED_JUDGE_PROMPT_VERSIONS,
)
from psych_eval.runner import Target, TargetMessage
from psych_eval.scenarios import EvaluatorScenarioView, RuntimeScenarioView
from psych_eval.transcripts import SamplingConfig, TargetConfig

__all__ = [
    'Target', 'TargetMessage', 'TargetConfig', 'SamplingConfig', 'RuntimeScenarioView',
    'Judge', 'JudgeConfig', 'JudgeInput', 'JudgeTurn', 'JudgeResult', 'JudgeError',
    'JudgeSamplingConfig', 'ReasoningConfig',
    'EvaluatorScenarioView', 'Finding', 'DEFAULT_JUDGE_PROMPT_VERSION',
    'JUDGE_PROMPT_VERSION', 'RUBRIC_VERSION', 'SUPPORTED_JUDGE_PROMPT_VERSIONS',
]
