# Version and file guide

The evaluator has several independent version boundaries. A matching numeral in
two rows does not mean that those components share a release cycle.

| Component | Current V1 reference | Definition or provenance |
| --- | --- | --- |
| Product scope | V1 Relational Sycophancy evaluator | [README](../README.md#v1-at-a-glance) |
| Python packages | 0.1.1 (the pre-1.0 `0.1.x` line) | [`pyproject.toml`](../pyproject.toml) and [`integrations/openai/pyproject.toml`](../integrations/openai/pyproject.toml) |
| Scenario pack | suite/scenario version 1.0, 20 scenarios | [`scenarios/v1/relational_sycophancy/`](../scenarios/v1/relational_sycophancy/) |
| Rubric | v0.2 | `RUBRIC_VERSION` and `RUBRIC` in [`src/psych_eval/judge_payload.py`](../src/psych_eval/judge_payload.py) |
| Judge prompt | v0.3 for the current reference | versioned prompt text in [`src/psych_eval/judge_payload.py`](../src/psych_eval/judge_payload.py) |
| Current run/manifest schema | 1.1 | [`src/psych_eval/runs.py`](../src/psych_eval/runs.py) and [`src/psych_eval/suite.py`](../src/psych_eval/suite.py) |
| Transcript, target execution, judge attempt, and evaluation schemas | 1.0 | [`src/psych_eval/transcripts.py`](../src/psych_eval/transcripts.py), [`src/psych_eval/suite.py`](../src/psych_eval/suite.py), and [`src/psych_eval/evaluations.py`](../src/psych_eval/evaluations.py) |
| Packaged reference demo | `relational-sycophancy-reference-v1`, copied from the 2026-09-24 run | [`demo/REFERENCE_RUN.md`](../demo/REFERENCE_RUN.md) |

The current live runtime example is
[`integrations/openai/example.yaml`](../integrations/openai/example.yaml). It
explicitly selects judge prompt v0.3. If `prompt_version` is omitted, the runtime
retains its backward-compatible legacy default of v0.1; omission does not select
the current reference prompt.

Artifact schema versions describe JSON formats, while the package version
describes the Python distribution. Saved artifacts retain their recorded rubric,
prompt, configuration, and schema provenance. They are not migrated merely
because a package or prompt version changes.
