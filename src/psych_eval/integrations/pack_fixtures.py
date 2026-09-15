"""Infrastructure fixtures, never benchmark labels or an automatic live fallback."""

import json
from importlib.resources import as_file, files
from pathlib import Path

from psych_eval.integrations.fixture_target import FixtureArtifact, FixtureTarget
from psych_eval.integrations.fixture_judge import FixtureJudge
from psych_eval.judge import JudgeConfig, JudgeError, JudgeInput
from psych_eval.scenarios import RuntimeScenarioView


ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDER = "[Infrastructure fixture] Placeholder response for orchestration testing."


def _fixture_resource(relative_path: str):
    try:
        return files("psych_eval._fixture_data").joinpath(relative_path)
    except ModuleNotFoundError:
        return ROOT / "fixtures" / relative_path


def pack_target(scenario: RuntimeScenarioView) -> FixtureTarget:
    if scenario.scenario_id == "RS-001":
        resource = _fixture_resource("demo_targets/relational_sycophancy/RS-001.yaml")
        with as_file(resource) as path:
            return FixtureTarget.from_file(path, scenario)
    return FixtureTarget(FixtureArtifact(
        schema_version="1.0", scenario_id=scenario.scenario_id,
        scenario_version=scenario.scenario_version,
        fixture_target_id="relational-sycophancy", fixture_version="1",
        assistant_responses=[PLACEHOLDER] * scenario.max_turns,
    ), scenario)


class PackJudge:
    """Replay RS-001; decline substantive assessment of generic placeholders."""

    def __init__(self):
        resource = _fixture_resource("demo_judges/relational_sycophancy/RS-001.yaml")
        with as_file(resource) as path:
            self.replay = FixtureJudge.from_file(path)
        self.config = self.replay.config

    def assess(self, request: JudgeInput, *, config: JudgeConfig) -> str:
        if config != self.config:
            raise JudgeError("judge_input", "PackJudge requires explicit fixture configuration")
        if request.scenario_id == "RS-001":
            return self.replay.assess(request, config=config)
        if any(turn.text != PLACEHOLDER for turn in request.transcript if turn.role == "assistant"):
            raise JudgeError("judge_input", "PackJudge only accepts infrastructure placeholder responses")
        return json.dumps({
            "category": "relational_sycophancy", "evaluation_status": "cannot_assess",
            "findings": [], "zero_rationale": None,
            "cannot_assess_reason": "Infrastructure fixture only; placeholder responses are not benchmark evidence or validated model behavior.",
            "recovery": "unevaluated", "persistence": "unevaluated",
        })
