# Psychosocial Safety Evaluator

Psychosocial Safety Evaluator is intended to become an open-source evaluation harness for testing psychosocial behavior in conversational AI. The first V1 evaluation construct will be relational sycophancy.

Current status: strict scenario loading, sequential target execution, and transcript persistence are implemented. RS-001 can run locally using an explicit fixture target. Live providers, judging, scoring, and the dashboard are not implemented.

## Local development

Python 3.14 is required. From the repository root, on macOS/Linux:

```sh
python3.14 --version
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python --version
python -c "import psych_eval"
python -m pytest
```

`.env.example` documents the future OpenAI API key configuration. No key is needed for setup or tests, and this bootstrap does not load `.env` files. Never commit credentials.

## Zero-cost RS-001 fixture execution

From the repository root, with the virtual environment active:

```python
from psych_eval.fixture_target import FixtureTarget
from psych_eval.runner import run_scenario
from psych_eval.scenarios import load_scenario
from psych_eval.transcripts import load_transcript, save_transcript

scenario = load_scenario(
    "scenarios/v1/relational_sycophancy/RS-001.yaml"
).to_runtime_view()
target = FixtureTarget.from_file(
    "fixtures/demo_targets/relational_sycophancy/RS-001.yaml", scenario
)
transcript = run_scenario(scenario, target, target.config)
assert transcript.execution_status == "completed"
save_transcript("/tmp/RS-001.fixture.transcript.json", transcript)
assert load_transcript("/tmp/RS-001.fixture.transcript.json") == transcript
```

`fixture` and `live` are execution modes; only fixture execution exists here.
Constructing `FixtureTarget` explicitly selects that mode, with no environment
inspection, API calls, or fallback selection. The existing transcript schema
records `provider="fixture"` as a provenance marker and
`model="demo-relational-sycophancy-v1"` as the versioned demo identity. Separate
live mode/provider/model configuration is deferred until live execution exists.

The YAML fixture declares schema, scenario, and fixture versions plus four
ordered assistant responses. Construction rejects invalid data, unknown fields,
and scenario identity, version, or response-count mismatches before execution.
Responses intentionally illustrate progressively unsupported relational
interpretations for future evaluation; they are demo data, not model measurements
or advice. Text passes unchanged through the shared runner and persistence path.
The target derives its response position from the supplied history, so retries
and repeated runs do not consume responses. Run IDs remain unique per execution.

`target.config` provides the required shared system-prompt and sampling fields;
these do not alter predefined responses. Passing different provider/model
provenance is rejected. Loading a saved transcript never invokes a target.
