# Psychosocial Safety Evaluator

Psychosocial Safety Evaluator is a local, open-source harness for evaluating
psychosocial behavior in conversational AI. V1 evaluates **Relational
Sycophancy**: unsupported reinforcement of a user's uncertain interpretation of
another person, relationship, or social situation.

The evaluator runs controlled multi-turn scenarios, keeps the target and judge
independently configurable, validates evidence against exact assistant text, and
stores reproducible local artifacts. Its Streamlit interface supports new runs,
zero-inference review of saved results, and judge-only reassessment of saved
transcripts.

> **Validation status: Experimental.** The judge is not yet formally validated
> against independent human annotations. Results are evaluation evidence, not a
> certification or global safety score.

## V1 at a glance

- Construct: Relational Sycophancy
- Scenario pack: 20 four-turn conversations
- Scope: Quick, Development, Full, or Custom
- Method: independent target generation and structured judge assessment
- Findings: assistant turn, relational proposition, mechanism, severity,
  verbatim evidence, and rationale
- Severity: maximum finding severity per assessed conversation; no average or
  composite score
- Storage: immutable source attempts plus rebuildable run/evaluation projections
- Interface: local Streamlit application; no deployment or hosted service

### Current reference configuration

The development reference currently uses:

| Role | Configuration |
| --- | --- |
| Target | `gpt-4o-mini`, temperature `0.0` |
| Judge | `gpt-5.6-terra`, reasoning effort `medium`, no temperature parameter |
| Rubric | v0.2 |
| Judge prompt | v0.3 |
| Judge retries | 0 evaluator retries; 0 OpenAI SDK retries |
| Status | Experimental, pending formal human validation |

Terra is **not** required by the architecture. Target and judge adapters are
selected independently, and other models/providers can implement the public
adapter contracts. The table records the current validated-for-development V1
reference configuration, not a universal recommendation.

## Install

Python 3.14 is required. From the repository root:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,ui]' -e './integrations/openai[dev]'
python -m pytest -q
```

If macOS marks virtualenv `.pth` files as hidden and Python 3.14 skips the
editable installs, use ordinary local installs instead:

```bash
python -m pip install --no-deps --no-build-isolation --force-reinstall . ./integrations/openai
```

No API key is needed to install, test, view the demo, or open saved results.

## OpenAI configuration

The OpenAI integration accepts environment-variable references only. Literal API
keys in YAML are rejected.

```bash
export OPENAI_TARGET_API_KEY='...'
export OPENAI_JUDGE_API_KEY='...'
```

The current example is [integrations/openai/example.yaml](integrations/openai/example.yaml):

```yaml
target:
  integration: openai
  config:
    provider: openai
    model: gpt-4o-mini
    system_prompt: ""
    sampling:
      temperature: 0.0
      max_output_tokens: 1024
  options:
    api_key_env: OPENAI_TARGET_API_KEY
judge:
  integration: openai
  config:
    mode: live
    provider: openai
    model: gpt-5.6-terra
    prompt_version: "0.3"
    sampling:
      max_output_tokens: 4096
      reasoning:
        effort: medium
  options:
    api_key_env: OPENAI_JUDGE_API_KEY
target_max_retries: 0
judge_max_retries: 0
```

Secrets stay in the process environment. Runtime-only integration options are
excluded from persisted artifacts.

## Start the local app

```bash
.venv/bin/python -m streamlit run streamlit_app.py
```

Open <http://127.0.0.1:8501>. The landing page offers three workflows.

### Run evaluation

1. Select a runtime YAML containing independent target and judge configuration.
2. Choose Quick, Development, Full, or Custom scope.
3. Review models, scenario count, retry settings, destination, and whether paid
   API calls will occur.
4. Click **Run evaluation**.
5. Explore the persisted results, findings, evidence, and transcripts.

Selection sizes are:

- Quick: 3 scenarios (`RS-002`, `RS-008`, `RS-013`)
- Development: 10 predefined scenarios
- Full: all 20 scenarios
- Custom: any selected scenarios, normalized to canonical pack order

Configuration and review make no API calls. A live run begins only after the
explicit final Run action.

### View demo results

The demo opens the preserved real Full 20-scenario reference run at
`demo/runs/relational-sycophancy-reference-v1/run.json`. It records the reference
configuration above and is loaded with full reference verification. Viewing it,
opening scenarios, and expanding transcripts make **zero inference calls**.

The packaged bundle is an immutable byte-for-byte copy of the local development
run `runs/evaluation-20260924-165559-judge-v0.3`. See
[demo/REFERENCE_RUN.md](demo/REFERENCE_RUN.md) for provenance and its pinned tree
digest. The original local run is not modified by the application.

### Open saved run

Choose **Open saved run**, enter a local `run.json` path, and open it. The app
validates the run and every referenced artifact before displaying results. It
does not initialize adapters or call a model.

### Rejudge saved transcripts

Every saved-results page exposes **Rejudge saved transcripts**. Select completed
conversations and a judge runtime config, review the call count, then create a
new fork bundle. Rejudging:

- makes zero target calls;
- makes one judge operation per selected transcript, subject only to the explicit
  configured retry budget;
- preserves the source run byte-for-byte;
- stores new prompt/model/sampling provenance in the fork; and
- never silently appends a changed configuration to the historical source run.

## Artifacts and reproducibility

A current run bundle contains:

```text
<run>/
  execution.json
  run.json
  RS-001/                     repeated for selected scenarios
    transcript.json
    target_call.json
    judge/
      attempt-001.json
      attempt-002.json        optional intentional rerun
    evaluations/
      attempt-001.json
```

`execution.json`, target records, transcripts, and judge attempts are source
artifacts. Evaluation files and `run.json` are rebuildable projections. Attempt
files are created exclusively and never overwritten. Failed calls retain
sanitized diagnostics; valid earlier evaluations remain available when a later
rerun fails.

Saved-result viewing is read-only and zero-inference. Rebuilding projections also
uses saved artifacts only:

```bash
.venv/bin/python -m psych_eval.suite rebuild path/to/execution.json
```

Local run bundles may contain conversation text, full prompts, raw judge output,
and provider diagnostics. `/runs/` is gitignored. Review any bundle deliberately
before selecting it as a public example.

## Validation status and limitations

The methodology freezes Rubric v0.2, judge prompt v0.3, strict structured output,
exact turn-local evidence validation, and deterministic severity aggregation.
That makes runs auditable; it does not establish judge validity.

The current Full reference run was manually reviewed during development. That
review identified **one likely false negative, RS-004**. This is a qualitative
observation from one run—not a statistical accuracy estimate, sensitivity
measurement, or formal benchmark result.

Planned validation work should use independently annotated transcripts, blinded
comparison, explicit disagreement adjudication, construct-level error analysis,
and a preregistered decision rule before reporting accuracy-like metrics. Until
then:

- do not treat zero findings as proof of psychosocial safety;
- do not compare models using this single saved run as a benchmark;
- do not interpret scenario severity as the seriousness of the user's situation;
- do not generalize beyond Relational Sycophancy; and
- keep human review in consequential decisions.

## Architecture and development

Core has no provider SDK dependency. Integrations register independent target and
judge factories through Python entry points. The OpenAI adapter uses the Responses
API, strict structured output, environment-only credentials, and SDK retries set
to zero. Adapter authors should read
[docs/adapter-development.md](docs/adapter-development.md).

Fixture targets and judgments remain as test infrastructure. They support
deterministic unit/integration tests but are no longer the user-facing demo.
Historical manual-judge samples in `manual_judge/` are archival development
evidence, not the current V1 workflow or formal validation data.

Run the complete suite with:

```bash
PYTHONPATH=src:integrations/openai/src .venv/bin/python -m pytest -q
```

## Security and privacy

- Never commit API keys or literal credentials.
- Use `api_key_env` references in runtime YAML.
- Keep local `.env`, secret configs, `/runs/`, caches, and build output ignored.
- Treat transcripts and raw responses as potentially sensitive.
- Provider errors are persisted only through a sanitized allowlist of diagnostic
  fields.

This repository is designed for local execution. It does not provide deployment,
multi-user access control, remote secret management, or a hosted data-retention
policy.
