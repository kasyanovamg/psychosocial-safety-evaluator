"""Command-line composition of explicit integrations and evaluator operations."""

import argparse
from pathlib import Path

from psych_eval.runs import RunArtifact
from psych_eval.suite import discover_pack, execute_suite, judge_saved_transcript, rebuild_run


def execute_fixture_pack(directory: str | Path) -> RunArtifact:
    from psych_eval.integrations.pack_fixtures import PackJudge, pack_target

    judge = PackJudge()
    config = pack_target(discover_pack()[0].to_runtime_view()).config
    return execute_suite(directory, target_factory=pack_target, target_config=config,
                         target_mode="fixture", judge=judge, judge_config=judge.config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("fixture", "rebuild", "judge-rerun"))
    parser.add_argument("path", type=Path, help="New bundle directory, or existing execution.json")
    parser.add_argument("--scenario", help="Scenario ID for an intentional fixture judge rerun")
    args = parser.parse_args()
    if args.operation == "fixture":
        run = execute_fixture_pack(args.path)
    elif args.operation == "rebuild":
        run = rebuild_run(args.path)
    else:
        from psych_eval.integrations.pack_fixtures import PackJudge

        judge_saved_transcript(args.path, args.scenario, PackJudge())
        run = rebuild_run(args.path)
    print(run.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
