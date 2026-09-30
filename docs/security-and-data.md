# Security and data handling

Psychosocial Safety Evaluator is a local, experimental evaluation tool. This page
describes the core workflow and the checked-in OpenAI reference adapter. Other
adapters can communicate differently; inspect and trust an adapter before running
it.

## Live evaluation data flow

Target and judge are independent integrations and may use different providers.

1. **Target step.** For each assistant turn, core gives the target adapter the
   configured system prompt and chronological user/assistant history. The adapter
   sends the content required by its provider and returns assistant text. Core
   stores scenario/user text and returned assistant responses in the local run
   bundle.
2. **Judge step.** After a conversation completes, core gives the judge adapter a
   canonical request containing scenario identity, the complete conversation,
   judge instructions, Rubric v0.2, and prompt-version provenance. The adapter
   sends the content required by its provider and returns structured findings.
   Core stores the raw judge response, validated findings, evidence excerpts, and
   related provenance in the local bundle.

Retries repeat the relevant provider operation according to the explicit runtime
retry budget. Provider-specific request and retention behavior belongs to the
selected adapter/provider, not to a general core guarantee.

## Saved viewing and rejudge

**View saved results** reads and verifies local artifacts. It constructs neither
the target nor the judge, makes zero target calls and zero judge calls, and does
not resend transcript content to a provider.

**Rejudge saved transcripts** verifies the source bundle, constructs only the
configured judge, and sends each selected saved transcript to that judge provider.
It makes no target calls and does not construct the target adapter. It copies the
selected transcript artifacts into a new result bundle, writes new judge results
there, and verifies that the source artifact tree remains unchanged. A full,
structurally valid runtime YAML is still required, including a target section;
target credentials are not needed for this workflow.

## Local artifacts are plaintext

Run bundles are stored on the local filesystem as plaintext JSON. Depending on
the operation and outcome, they can contain:

- scenario and user text;
- target-model responses and complete transcripts;
- judge instructions, rubric text, raw responses, findings, rationales, and
  evidence excerpts;
- model/configuration, timestamps, status, retry, and artifact metadata; and
- provider error information after any adapter sanitization.

The evaluator does not encrypt these files. Filesystem access controls, backups,
encryption, deletion, sharing, and retention are the user's responsibility. The
local `runs/` directory and common secret/config files are gitignored, but ignore
rules do not protect files copied elsewhere or deliberately added. Inspect every
bundle before sharing or publishing it.

## Sanitization has limits

The OpenAI reference adapter restricts persisted SDK diagnostics to selected
fields and redacts known credential-like patterns. Core also converts some
integration-boundary failures to generic diagnostics. These are defense-in-depth,
best-effort measures—not proof that an artifact contains no secret or sensitive
text.

Unexpected provider messages can contain arbitrary prose, pattern matching cannot
recognize every credential, and transcript/model output is intentionally preserved
for auditability. Do not put secrets in prompts, system prompts, scenario text, or
transcripts. Do not treat an artifact as safe to publish merely because
sanitization ran.

## Provider retention boundary

The OpenAI reference adapter sends Responses API requests with `store=False`.
That is a request option, not a project-wide or universal provider-retention
guarantee. It does not describe another adapter, and this project makes no claim
about provider account settings, policy, training use, logs, or legal obligations.
Review the terms and data controls of every provider you configure before sending
content.

## Credentials

The reference runtime configuration uses environment-variable names:

```text
OPENAI_TARGET_API_KEY
OPENAI_JUDGE_API_KEY
```

Provide their values through the local process environment. Literal keys do not
belong in YAML; the OpenAI adapter rejects unknown/literal credential options and
reads the referenced environment variable when its role is constructed. `.env`
is gitignored but is not automatically loaded by this project.

Core excludes runtime adapter `options` from public persisted configuration and
does not serialize environment-variable values into run artifacts. This boundary
does not make a malicious adapter safe, and sanitization cannot guarantee removal
if a secret is copied into prompt text, model output, or a provider message.
Credential presence also does not prove that a provider, model, or feature is
available to the account.

## Adapter trust boundary

Provider adapters are Python packages loaded through entry points. They are
executable code, not declarative configuration and not sandboxed plugins. When an
adapter is selected and constructed, it can control provider communication and
can access whatever the Python process and operating-system account permit.

Install adapters only from sources you trust, review their dependencies and data
handling, and verify that they honor the evaluator contracts: no hidden inference,
explicit retries, role isolation, and credential-free public artifacts. Core's
saved-view implementation does not load adapters, but the project cannot extend
that guarantee to arbitrary code installed or executed in the same environment.

## Local application safeguards and limits

The checked-in Streamlit configuration binds to `127.0.0.1` and disables
Streamlit usage-stat collection. Saved artifact references are validated as
bundle-relative, report text is rendered through escaped/native Streamlit output,
and execution authorization remains session-only. These safeguards do not provide
multi-user access control, host hardening, adapter sandboxing, or filesystem
encryption. Changing the checked-in server configuration can change the network
exposure.

For suspected vulnerabilities in the project itself, follow the private
disclosure instructions in [SECURITY.md](../SECURITY.md).
