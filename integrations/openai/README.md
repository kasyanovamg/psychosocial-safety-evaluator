# OpenAI reference integration

Independent target and judge factories using the OpenAI Responses API. Core does
not depend on this package. Requires Python 3.14, Pydantic `>=2.12,<3`, and
OpenAI SDK `>=3.8,<4`. No API calls run during installation, listing, or tests.

From the evaluator repository root:

```sh
.venv/bin/python -m pip install '.[dev]' './integrations/openai[dev]'
.venv/bin/python -m psych_eval.suite integrations
```

Both entry-point groups register `openai`: `psych_eval.targets` calls
`psych_eval_openai.target:build_target`, and `psych_eval.judges` calls
`psych_eval_openai.judge:build_judge`. Each builds an independent client.

Set `OPENAI_API_KEY` in your environment. For separate credentials, set
`OPENAI_TARGET_API_KEY` and `OPENAI_JUDGE_API_KEY` and use `api_key_env` as below.
Literal API keys are rejected; configuration accepts only an `api_key_env`
reference and reads the credential from that environment variable. Optional
runtime-only `organization`, `project`, and positive `timeout` (seconds, default
60) are supported. Organization/project also follow SDK environment defaults.
Unknown options are rejected; endpoint overrides and request-parameter overrides
are not supported. The endpoint is fixed to `https://api.openai.com/v1`.

The checked-in [`example.yaml`](example.yaml) contains this configuration. Each section is independently usable
with an unrelated integration in the other section. The example uses separate
credentials and the explicit public sampling settings required by core:

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

With one `OPENAI_API_KEY`, replace both `options` mappings with `{}`. A minimal
judge config may omit `sampling` and `prompt_version`; SDK model defaults then
apply to sampling. Judge sampling supports an optional nonnegative `temperature`,
required positive `max_output_tokens`, and optional strict `reasoning.effort`.
Target sampling remains unchanged and mandatory under the existing contract.
Use a model supporting Responses, your explicit sampling settings, and (for the
judge) strict structured outputs. Unsupported settings fail technically; there
is no model-specific rewriting, schema weakening, or JSON-mode fallback.

Optional live smoke command (**makes paid API calls for the 20-scenario pack**;
not part of tests; destination must not already exist):

```sh
.venv/bin/python -m psych_eval.suite run /tmp/psych-eval-openai-live --config integrations/openai/example.yaml
```

The target sends the system prompt followed by the exact chronological history,
with no server-side conversation state. Target refusals remain assistant text.
The judge sends the complete canonical `JudgeInput` JSON and its unchanged
instructions, plus the unmodified `JudgeResult` JSON schema. Core alone validates
JSON, evidence, semantic status, and severity. Provider refusals for the judge,
incomplete output, or malformed envelopes are technical call failures; malformed
returned JSON goes to core's `judge_schema` path.

SDK retries are disabled (`max_retries=0`); only evaluator retries apply.
Requests use `store=False` and `truncation='disabled'`. SDK errors expose only a
sanitized allowlist of provider diagnostics: exception type, integer HTTP status,
and OpenAI message/code/parameter fields. Headers, request objects, credentials,
and arbitrary exception text are not persisted. Unmarked integration exceptions
still become generic diagnostics. Exact returned model text is persisted, so
credentials must never be included in prompts.

Public artifacts retain `provider=openai`, configured model, sampling, and core
execution timestamps/status/retries. The frozen string-return contracts have no
usage, adapter-version, or returned-model-ID fields: these SDK metadata are
intentionally not persisted or smuggled into transcripts. No SDK objects escape.
Use a pinned model identifier when you need stable model provenance.

The reference tests reuse `psych_eval.testing` assertions; adapter-facing types are
imported from `psych_eval.adapters`. See the [adapter developer guide](../../docs/adapter-development.md).

Run mocked tests from the repository root after installation:

```sh
.venv/bin/python -m pytest -q integrations/openai/tests
.venv/bin/python -m pytest -q tests integrations/openai/tests
```

Core-only tests continue to run without this optional package. Scenario and
fixture resources required by the core wheel are packaged and tested separately
from this integration.

API references: [Responses](https://developers.openai.com/api/reference/python/resources/responses/methods/create),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
