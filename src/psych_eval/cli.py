"""Command-line composition of explicit integrations and evaluator operations."""

import argparse
from pathlib import Path

from psych_eval.runs import RunArtifact
from psych_eval.suite import discover_pack, execute_suite, judge_saved_transcript, rebuild_run


def execute_configured_suite(directory: str | Path, config_path: str | Path) -> RunArtifact:
    from psych_eval.integrations.runtime import configure_integrations, load_runtime_config

    runtime = load_runtime_config(config_path)
    target_factory, judge = configure_integrations(runtime)
    return execute_suite(
        directory, target_factory=target_factory, target_config=runtime.target.config,
        target_mode='fixture' if runtime.target.config.provider == 'fixture' else 'live',
        judge=judge, judge_config=runtime.judge.config,
        target_max_retries=runtime.target_max_retries, judge_max_retries=runtime.judge_max_retries,
    )


def execute_fixture_pack(directory: str | Path) -> RunArtifact:
    from psych_eval.integrations.pack_fixtures import PackJudge, pack_target

    judge = PackJudge()
    config = pack_target(discover_pack()[0].to_runtime_view()).config
    return execute_suite(directory, target_factory=pack_target, target_config=config,
                         target_mode="fixture", judge=judge, judge_config=judge.config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("run", "fixture", "rebuild", "judge-rerun", "integrations"))
    parser.add_argument("path", type=Path, nargs='?', help="New bundle directory, or existing execution.json (not used by integrations)")
    parser.add_argument("--scenario", help="Scenario ID for an intentional fixture judge rerun")
    parser.add_argument("--config", type=Path, help="Runtime YAML with independent target and judge selections (run only)")
    args = parser.parse_args()
    if (args.operation == 'run') != (args.config is not None):
        parser.error('--config is required for run and only supported by run')
    if args.operation == 'integrations':
        if args.path is not None or args.scenario is not None:
            parser.error('integrations does not accept a path or --scenario')
        from psych_eval.integrations.runtime import IntegrationConfigError, integration_names

        try:
            names = integration_names()
        except IntegrationConfigError as exc:
            parser.error(str(exc))
        print('Targets:\n' + '\n'.join(names['target']))
        print('\nJudges:\n' + '\n'.join(names['judge']))
        return
    if args.path is None:
        parser.error('path is required for run, fixture, rebuild, and judge-rerun')
    if args.operation == 'run':
        from psych_eval.integrations.runtime import IntegrationConfigError

        try:
            run = execute_configured_suite(args.path, args.config)
        except IntegrationConfigError as exc:
            parser.error(str(exc))
    elif args.operation == "fixture":
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
