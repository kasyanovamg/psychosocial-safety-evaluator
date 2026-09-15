# Adapter developer API (V1, pre-1.0)

The primary workflow is install → independently configure target and judge → run
an evaluation → inspect its report. An adapter translates one external system
interface into one evaluator role. Provider, adapter implementation, and Python
distribution are separate concepts: one distribution may register either role or
both, and a tested protocol implementation may support several endpoints.

## Public imports and factories

Import adapter-facing contracts from `psych_eval.adapters`:

```python
from psych_eval.adapters import (
    Target, TargetMessage, TargetConfig, SamplingConfig, RuntimeScenarioView,
    Judge, JudgeConfig, JudgeInput, JudgeTurn, JudgeResult, JudgeError,
    EvaluatorScenarioView, Finding, JUDGE_PROMPT_VERSION, RUBRIC_VERSION,
)
```

These are re-exports of the existing types, not new wrappers. Original import
paths continue to work. Implement the protocols structurally; subclassing is not
required. The public factory signatures are:

```python
# Return a callable that constructs a target for each isolated scenario.
def build_target(*, config: TargetConfig, options: dict):
    client = make_your_client(options)
    return lambda scenario: YourTarget(client, config)

# Return an independent judge; never construct or invoke a target here.
def build_judge(*, config: JudgeConfig, options: dict):
    return YourJudge(make_your_client(options), config)
```

`Target.respond(messages: tuple[TargetMessage, ...], *, config: TargetConfig) -> str`
receives exact chronological user/assistant history. The system prompt is in
`config.system_prompt`, not a transcript turn. Use only the supplied history for
inference; the runtime scenario passed to the factory must not be used to send
future user turns. Return exact nonblank assistant text, including genuine refusal
text. Empty/non-string output is a technical execution failure.

`Judge.assess(request: JudgeInput, *, config: JudgeConfig) -> str` receives the
canonical self-contained request: frozen instructions, rubric, scenario identity,
and chronological transcript. Preserve it without rewriting, hidden enrichment,
or mutation. Return the raw JSON text, not a parsed SDK object or a `JudgeResult`
instance. `JudgeResult.model_json_schema()` supplies the canonical result schema
where a transport supports structured outputs. Core validates schema, evidence,
semantic status, and severity; never repair invalid JSON or weaken that contract.

A judge refusal or API error is not semantic `cannot_assess`. Only a valid judge
result can express that status. Raise `JudgeError('judge_call', safe_detail)` for
call failures, or `judge_input` for invalid adapter input. Leave malformed returned
JSON to core's `judge_schema` path. Target exceptions likewise become technical
execution failures. Never include credentials, request headers, or SDK object
representations in exceptions.

## Configuration, isolation, and retries

`TargetConfig` contains public provider/model identity, system prompt, and required
`SamplingConfig(temperature=..., max_output_tokens=...)`. `JudgeConfig` has separate
provider/model identity, fixture/live mode, and optional sampling/prompt version.
The public config is persisted. Validate unsupported settings; never silently omit
an explicit temperature, instruction, or output limit while recording it as used.

The independent runtime `options` mappings are opaque and private to each factory.
Use them for credentials, credential environment-variable names, and other
runtime options. Do not copy them into canonical configs, prompts, or results.
Runtime options are excluded from serialization; the discovery boundary suppresses
adapter exception text. Direct Python callers still require safely translated
exceptions from your adapter. SDKs, clients, and authentication stay outside core.

No hidden inference calls, schema-repair calls, or SDK retries: core owns retries.
A target attempt operates against the supplied history, including on a retry.
Do not retain hidden conversation state between scenarios or calls. The judge
must make one inference call per `assess`; intentional reruns append immutable
attempts. Neither role may invoke the other.

Some browser/stateful chatbots cannot reconstruct supplied history, apply the
required controls, or retry without duplicate messages. They cannot honestly
implement the current Target contract. Canonical transcript import is an advanced
fallback for those systems; do not fake controls or add session behavior to core.

The frozen string-return contracts have no token-usage, returned-model-ID, or
adapter-version fields. Runtime endpoints/options are not persisted either. Do
not smuggle metadata into other artifact fields or claim full endpoint provenance.

## Registration and installation

In your independent package's `pyproject.toml`:

```toml
[project]
name = "my-company-psych-eval-adapter"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["psychosocial-safety-evaluator>=0.1.1,<0.2"]
# Add your own SDK dependency here or in a documented optional extra.

[project.entry-points."psych_eval.targets"]
company-chat = "company_adapter.target:build_target"

[project.entry-points."psych_eval.judges"]
company-judge = "company_adapter.judge:build_judge"
```

Register only the roles supported. Install into the evaluator's Python environment,
then run `python -m psych_eval.suite integrations`. Listing reads metadata without
loading SDKs; selected factories are loaded on demand. Unknown, wrong-role,
unloadable, and duplicate names fail without fallback. Reserve `fixture` for the
built-in replay implementation. Installed entry points execute trusted Python code.

Set `target.integration: company-chat` and `judge.integration: company-judge` in
runtime YAML, with each role's own `config` and `options`. Either can instead name
an unrelated installed integration. See the [runtime configuration example](../README.md#select-target-and-judge-integrations).

The public re-exports and kit are available starting in evaluator 0.1.1.
Declare a tested evaluator version range. During 0.x, incompatible public adapter
contract changes require a minor-version bump and compatibility notes; compatible
fixes can use patch versions. Test supported versions in CI. There is no runtime
contract negotiation or separate contracts distribution.

## Reusable contract assertions

`psych_eval.testing` ships in the evaluator distribution and requires neither
pytest nor any provider SDK to import. Use it from your own pytest/unittest tests.
**The helpers call adapters: always supply mocked transports, never live clients.**

| Helper | Mock setup and checks |
|---|---|
| `assert_target_contract` | Supply a response per user turn on each of two runs. Checks exact text, increasing history, public config/system prompt at the adapter boundary, independent run IDs, no stale state, and call counts. Include whitespace and refusal text. Returns the second transcript. |
| `assert_target_failure` | Make every transport call fail. Checks first-turn technical failure, core retry count, transport-call count, and safe failure artifacts. Default: one retry. |
| `assert_judge_contract` | Supply a valid raw result. Checks canonical request/config at the adapter boundary, no request mutation, exact raw result, expected status/severity, and one transport call. Returns the evaluation. |
| `assert_judge_failure` | Supply a safely translated transport error or malformed raw JSON. Checks expected stage/raw response, no repair into an evaluation, one transport call, and safe diagnostics. |
| `assert_judge_attempt` | Pass an actual saved suite `JudgeAttempt` plus the measured transport-call delta for that attempt. Checks contiguous retry indices/counts and private-value exclusion. |
| `assert_no_secrets` | Checks a public model or JSON-compatible value against nonempty synthetic secret markers, including escaped Unicode/quotes. |

The call counter must measure requests at your mocked SDK/HTTP transport boundary.
Counting only `respond`/`assess` entries would miss internal retries. Helpers compare
counter deltas, so earlier setup calls do not affect the expected count. Secret
markers should be synthetic credentials passed through real factory options.

For example, define provider-specific fixtures `target_under_test`, `target_config`,
and `transport_mock` that build your real adapter and return these assistant strings
on **both** runs. The transport mock may need to return SDK-native response objects:

```python
from psych_eval.adapters import RuntimeScenarioView
from psych_eval.testing import assert_target_contract


def test_target(target_under_test, target_config, transport_mock):
    scenario = RuntimeScenarioView(
        scenario_id="CONTRACT-001", scenario_version="1.0",
        construct="relational_sycophancy", user_turns=[" First question. ", "Second?"],
        max_turns=2, runtime_context={},
    )
    assert_target_contract(
        target_under_test, target_config, scenario,
        ["  Exact answer.\n", "I cannot help with that."],
        transport_calls=lambda: transport_mock.call_count,
        secrets=("test-private-key",),
    )
```

For a judge, use the returned transcript and an `EvaluatorScenarioView` with its
same identity. Call `assert_judge_contract(..., raw_response=expected_json,
transport_calls=counter)` for assessed severity zero, and pass
`status='cannot_assess', severity=None` for semantic abstention. For malformed JSON,
use `assert_judge_failure(..., stage='judge_schema', raw_response='{broken',
transport_calls=counter)`. For an SDK failure use `stage='judge_call'` and leave
`raw_response=None`.

Run `python -m pytest -q` in your adapter project after installing it and pytest.
The [OpenAI reference tests](../integrations/openai/tests/test_openai.py) consume
these same helpers while keeping SDK request-format assertions separate.

## What the kit does not prove

Boundary assertions cannot inspect arbitrary wire protocols, detect silently
dropped settings inside a service, prove model quality, or certify live endpoints.
Keep transport-specific tests for message roles/system authority, canonical payload
serialization, output-schema transmission, refusal envelopes, authentication,
unsupported settings, SDK retry disabling, and error translation. Mock actual SDK
responses, not an unrelated replacement adapter.

Core's existing discovery/composition tests continue to cover duplicate/invalid
entry points, missing packages/SDKs, and provider-independent imports. Your package
should additionally build/install its own wheel and resolve its real entry points;
do not synthesize metadata as a replacement for packaging verification. The core
suite's existing temporary external-package fixture exercises the public API and
kit in both cross-package pairings.

Write a dedicated adapter when preserving semantics requires a different external
interface, not just a different endpoint value. A name such as “OpenAI-compatible”
is not proof of system-message, structured-output, or retry equivalence. No
capability matrix or automatic protocol fallback is part of this API.
