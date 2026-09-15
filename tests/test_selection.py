"""Versioned scenario selection, execution scoping, and coverage artifacts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from psych_eval.integrations.pack_fixtures import PackJudge, pack_target
from psych_eval.runs import RunArtifact, ScenarioResult, load_run
from psych_eval.selection import (
    PACK_SCENARIO_IDS, PREDEFINED_SELECTIONS, SelectionRequest, resolve_selection,
    selection_catalog,
)
from psych_eval.suite import ExecutionManifest, discover_pack, execute_suite, read_record


ROOT = Path(__file__).resolve().parents[1]
QUICK = ("RS-002", "RS-008", "RS-013")
DEVELOPMENT = (
    "RS-002", "RS-005", "RS-006", "RS-008", "RS-010",
    "RS-011", "RS-013", "RS-015", "RS-018", "RS-020",
)


class AssessingJudge:
    def __init__(self, *, fail=(), cannot_assess=()):
        self.config = PackJudge().config
        self.fail = set(fail)
        self.cannot_assess = set(cannot_assess)
        self.calls = []

    def assess(self, request, *, config):
        assert config == self.config
        self.calls.append(request.scenario_id)
        if request.scenario_id in self.fail:
            raise TimeoutError("test judge failure")
        semantic_failure = request.scenario_id in self.cannot_assess
        return json.dumps({
            "category": "relational_sycophancy",
            "evaluation_status": "cannot_assess" if semantic_failure else "assessed",
            "findings": [],
            "zero_rationale": None if semantic_failure else "No relational-sycophancy finding in test fixture.",
            "cannot_assess_reason": "Insufficient fixture evidence." if semantic_failure else None,
            "recovery": "unevaluated",
            "persistence": "unevaluated",
        })


def execute_selection(
    directory, selection, *, judge=None, target_factory=pack_target, progress=None,
):
    judge = judge or AssessingJudge()
    target = pack_target(discover_pack()[0].to_runtime_view()).config
    run = execute_suite(
        directory, target_factory=target_factory, target_config=target,
        target_mode="fixture", judge=judge, judge_config=judge.config,
        target_max_retries=0, judge_max_retries=0, selection=selection,
        progress=progress,
    )
    return run, judge


def test_predefined_membership_is_fixed_versioned_representative_and_not_first_n():
    quick = resolve_selection(mode="quick")
    development = resolve_selection(mode="development")
    assert PREDEFINED_SELECTIONS == {("quick", "0.1"): QUICK, ("development", "0.1"): DEVELOPMENT}
    assert tuple(quick.selected_scenario_ids) == QUICK
    assert tuple(development.selected_scenario_ids) == DEVELOPMENT
    assert quick.selection_version == development.selection_version == "0.1"
    assert QUICK != PACK_SCENARIO_IDS[:3]
    assert DEVELOPMENT != PACK_SCENARIO_IDS[:10]
    assert set(QUICK) <= set(DEVELOPMENT)


def test_full_and_predefined_resolution_are_deterministic():
    assert resolve_selection(mode="quick") == resolve_selection(SelectionRequest(mode="quick"))
    assert tuple(resolve_selection(mode="full").selected_scenario_ids) == PACK_SCENARIO_IDS
    assert resolve_selection(mode="full").selection_version is None


def test_selection_catalog_exposes_supported_modes_and_canonical_counts():
    assert [item.model_dump() for item in selection_catalog()] == [
        {"mode": "quick", "selected_count": 3, "full_pack_total": 20, "selection_version": "0.1"},
        {"mode": "development", "selected_count": 10, "full_pack_total": 20, "selection_version": "0.1"},
        {"mode": "full", "selected_count": 20, "full_pack_total": 20, "selection_version": None},
        {"mode": "custom", "selected_count": None, "full_pack_total": 20, "selection_version": None},
    ]


def test_custom_resolves_to_canonical_order():
    selection = resolve_selection(mode="custom", custom_scenario_ids=["RS-020", "RS-002", "RS-013"])
    assert selection.selected_scenario_ids == ["RS-002", "RS-013", "RS-020"]
    assert selection.selection_version is None


@pytest.mark.parametrize("ids,message", [
    (None, "at least one"),
    ([], "at least one"),
    (["RS-002", "RS-999"], "unknown scenario IDs"),
    (["RS-002", "RS-002"], "duplicate"),
])
def test_invalid_custom_selection_rejected(ids, message):
    with pytest.raises(ValueError, match=message):
        resolve_selection(mode="custom", custom_scenario_ids=ids)


def test_unsupported_pack_and_invalid_predefined_options_fail_clearly():
    with pytest.raises(ValueError, match="unsupported scenario pack version"):
        resolve_selection(scenario_pack_version="9.9", mode="full")
    with pytest.raises(ValueError, match="only valid for custom"):
        resolve_selection(mode="quick", custom_scenario_ids=["RS-002"])


def test_runner_executes_only_selected_scenarios_in_canonical_order(tmp_path):
    selection = resolve_selection(mode="quick")
    factory_calls = []

    def factory(runtime):
        factory_calls.append(runtime.scenario_id)
        return pack_target(runtime)

    run, judge = execute_selection(tmp_path / "quick", selection, target_factory=factory)
    assert factory_calls == list(QUICK)
    assert judge.calls == list(QUICK)
    assert [entry.scenario_id for entry in run.scenarios] == list(QUICK)
    assert sorted(path.name for path in (tmp_path / "quick").iterdir() if path.is_dir()) == list(QUICK)
    assert run.execution_summary.planned == 3


def test_progress_callback_exception_cannot_change_canonical_execution(tmp_path):
    selection = resolve_selection(mode="quick")
    baseline, _ = execute_selection(tmp_path / "baseline", selection)
    factory_calls = []
    observed = []

    def factory(runtime):
        factory_calls.append(runtime.scenario_id)
        return pack_target(runtime)

    def failing_progress(event):
        observed.append((event.phase, event.scenario_id))
        if event.phase == "scenario_started":
            raise RuntimeError("presentation observer failed")

    run, judge = execute_selection(
        tmp_path / "observed", selection, target_factory=factory,
        progress=failing_progress,
    )

    assert factory_calls == list(QUICK)
    assert judge.calls == list(QUICK)
    assert [item for item in observed if item[0] == "scenario_started"] == [
        ("scenario_started", scenario_id) for scenario_id in QUICK
    ]
    assert run.execution_summary == baseline.execution_summary
    assert run.evaluation_summary == baseline.evaluation_summary
    assert run.results == baseline.results
    assert run.coverage == baseline.coverage
    assert run.coverage.technical_failure_count == 0
    assert load_run(tmp_path / "observed/run.json", verify_references=True) == run


def test_runtime_config_keeps_scenario_selection_independent_from_adapters(tmp_path):
    import yaml

    from psych_eval.cli import execute_configured_suite
    from psych_eval.integrations.runtime import (
        JudgeSelection, RuntimeConfig, TargetSelection,
    )

    judge = PackJudge()
    target_config = pack_target(discover_pack()[0].to_runtime_view()).config
    runtime = RuntimeConfig(
        target=TargetSelection(integration="fixture", config=target_config),
        judge=JudgeSelection(integration="fixture", config=judge.config),
        scenario_selection=SelectionRequest(mode="quick"),
    )
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(yaml.safe_dump(runtime.model_dump(mode="json")), encoding="utf-8")
    run = execute_configured_suite(tmp_path / "configured", config_path)
    assert run.selection.selection_mode == "quick"
    assert run.coverage.selected_count == 3


@pytest.mark.parametrize("mode,count", [("quick", 3), ("development", 10)])
def test_completed_predefined_selection_has_partial_pack_coverage(tmp_path, mode, count):
    selection = resolve_selection(mode=mode)
    run, _ = execute_selection(tmp_path / mode, selection)
    assert run.schema_version == "1.1"
    assert run.selection == selection
    assert run.coverage.selected_count == run.coverage.executed_count == count
    assert run.coverage.completed_count == run.coverage.valid_assessed_count == count
    assert run.coverage.selection_complete
    assert not run.coverage.pack_coverage_complete
    assert not run.coverage.assessment_coverage_complete
    assert run.coverage.coverage_label == "partial"
    assert load_run(tmp_path / mode / "run.json", verify_references=True) == run


def test_full_success_is_full_coverage_and_custom_full_keeps_both_facts(tmp_path):
    full, _ = execute_selection(tmp_path / "full", resolve_selection(mode="full"))
    custom, _ = execute_selection(
        tmp_path / "custom-full",
        resolve_selection(mode="custom", custom_scenario_ids=list(reversed(PACK_SCENARIO_IDS))),
    )
    for run in (full, custom):
        assert run.coverage.full_pack_total == 20
        assert run.coverage.pack_coverage_complete
        assert run.coverage.assessment_coverage_complete
        assert run.coverage.coverage_label == "full"
    assert full.selection.selection_mode == "full"
    assert custom.selection.selection_mode == "custom"


def test_custom_subset_is_partial_and_unselected_scenarios_are_absent(tmp_path):
    selection = resolve_selection(
        mode="custom", custom_scenario_ids=["RS-018", "RS-004", "RS-013", "RS-002"],
    )
    run, _ = execute_selection(tmp_path / "custom", selection)
    assert run.coverage.selected_count == run.coverage.valid_assessed_count == 4
    assert run.coverage.coverage_label == "partial"
    assert run.coverage.technical_failure_scenario_ids == []
    assert set(PACK_SCENARIO_IDS) - set(selection.selected_scenario_ids) == {
        item for item in PACK_SCENARIO_IDS if item not in [entry.scenario_id for entry in run.scenarios]
    }


def test_full_failure_and_semantic_cannot_assess_are_distinct_coverage_states(tmp_path):
    judge = AssessingJudge(fail={"RS-020"}, cannot_assess={"RS-008"})
    run, _ = execute_selection(tmp_path / "partial-full", resolve_selection(mode="full"), judge=judge)
    assert run.coverage.selection_complete and run.coverage.pack_coverage_complete
    assert not run.coverage.assessment_coverage_complete
    assert run.coverage.coverage_label == "partial"
    assert run.coverage.valid_assessed_count == 18
    assert run.coverage.technical_failure_scenario_ids == ["RS-020"]
    assert run.coverage.cannot_assess_scenario_ids == ["RS-008"]
    assert run.evaluation_summary.failed == run.evaluation_summary.cannot_assess == 1


def test_target_failure_is_executed_and_technical_but_not_completed_or_assessed(tmp_path):
    selection = resolve_selection(mode="custom", custom_scenario_ids=["RS-002", "RS-008"])

    def factory(runtime):
        if runtime.scenario_id == "RS-002":
            class FailedTarget:
                def respond(self, messages, *, config):
                    raise TimeoutError("test target failure")

            return FailedTarget()
        return pack_target(runtime)

    run, judge = execute_selection(tmp_path / "target-failure", selection, target_factory=factory)
    assert run.coverage.executed_scenario_ids == ["RS-002", "RS-008"]
    assert run.coverage.completed_scenario_ids == ["RS-008"]
    assert run.coverage.valid_assessed_scenario_ids == ["RS-008"]
    assert run.coverage.technical_failure_scenario_ids == ["RS-002"]
    assert run.coverage.cannot_assess_scenario_ids == []
    assert judge.calls == ["RS-008"]


def test_selected_but_not_executed_is_not_a_failure_or_cannot_assess():
    legacy = load_run(ROOT / "demo/runs/relational-sycophancy-demo-v1/run.json")
    scenario = discover_pack()[1]
    selection = resolve_selection(mode="custom", custom_scenario_ids=[scenario.scenario_id])
    run = RunArtifact.create(
        run_id=legacy.run_id, created_at=legacy.created_at, construct=legacy.construct_name,
        suite_id="selection-test", suite_version="0.1",
        model_under_test=legacy.model_under_test, judge=legacy.judge,
        rubric_version=legacy.rubric_version, evaluator_version=legacy.evaluator_version,
        scenarios=[ScenarioResult(
            scenario=scenario.to_evaluator_view(), title=scenario.title,
            evaluation_status="not_run",
        )],
        selection=selection,
    )
    assert run.coverage.selected_count == 1 and run.coverage.executed_count == 0
    assert not run.coverage.selection_complete
    assert run.coverage.technical_failure_count == run.coverage.cannot_assess_count == 0


def test_manifest_and_run_persist_exact_selection_execution_and_coverage(tmp_path):
    selection = resolve_selection(mode="quick")
    run, _ = execute_selection(tmp_path / "bundle", selection)
    manifest = read_record(tmp_path / "bundle/execution.json", ExecutionManifest)
    assert manifest.schema_version == "1.1" and manifest.selection == selection
    saved = json.loads((tmp_path / "bundle/run.json").read_text(encoding="utf-8"))
    assert saved["selection"]["selection_mode"] == "quick"
    assert saved["selection"]["selection_version"] == "0.1"
    assert saved["selection"]["selected_scenario_ids"] == list(QUICK)
    assert saved["coverage"]["executed_scenario_ids"] == list(QUICK)
    assert saved["coverage"]["full_pack_total"] == 20
    assert run.coverage.executed_count == 3


def test_selection_and_coverage_tampering_is_rejected(tmp_path):
    run, _ = execute_selection(tmp_path / "bundle", resolve_selection(mode="quick"))
    data = run.model_dump()
    data["coverage"]["valid_assessed_count"] = 2
    with pytest.raises(ValidationError, match="stored coverage"):
        type(run).model_validate(data)


def test_historical_artifacts_remain_legacy_without_invented_selection_metadata():
    compact = load_run(ROOT / "demo/runs/relational-sycophancy-demo-v1/run.json")
    full = load_run(
        ROOT / "demo/runs/relational-sycophancy-full-pack-v1/run.json",
        verify_references=True,
    )
    for run in (compact, full):
        assert run.schema_version == "1.0"
        assert run.selection is None and run.coverage is None
    assert full.suite.suite_id == "relational-sycophancy-development"


def test_unknown_run_and_execution_schema_versions_are_not_guessed(tmp_path):
    run_data = json.loads(
        (ROOT / "demo/runs/relational-sycophancy-full-pack-v1/run.json").read_text(encoding="utf-8")
    )
    execution_data = json.loads(
        (ROOT / "demo/runs/relational-sycophancy-full-pack-v1/execution.json").read_text(encoding="utf-8")
    )
    run_data["schema_version"] = execution_data["schema_version"] = "99.0"
    run_path = tmp_path / "run.json"
    execution_path = tmp_path / "execution.json"
    run_path.write_text(json.dumps(run_data), encoding="utf-8")
    execution_path.write_text(json.dumps(execution_data), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported run schema_version"):
        load_run(run_path)
    with pytest.raises(ValueError, match="unsupported execution schema_version"):
        read_record(execution_path, ExecutionManifest)
