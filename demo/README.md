# Demo and compatibility artifacts

The directories under `demo/` have two distinct roles.

## Current reference demo

`runs/relational-sycophancy-reference-v1/` is the current V1 public reference
demo and the bundle opened by the Streamlit application. It is a preserved Full
controlled OpenAI evaluation over synthetic project scenarios—not a collection
of real-user conversations. The bundle is immutable and digest-pinned; see
[REFERENCE_RUN.md](REFERENCE_RUN.md) for its configuration and provenance.

## Legacy compatibility fixtures

The following older synthetic fixture bundles are retained because tests verify
historical schema loading, artifact rebuilding, presentation, and compatibility:

- `runs/relational-sycophancy-demo-v1/` — compact one-scenario fixture run;
- `runs/relational-sycophancy-full-pack-v1/` — 20-scenario fixture run; and
- `artifacts/RS-001/` — legacy standalone transcript/evaluation artifacts.

These fixtures are test infrastructure. They are not the current public demo,
current evaluation results, a benchmark result, or evidence about current model
behavior.
