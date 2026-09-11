# Initial held-out manual judge smoke tests

The initial held-out manual judge smoke test produced 3/3 severity-0 assessments under the frozen judge contract, with evidence-sensitive rationales across ambiguous, confirming, and counterevidence trajectories. These results are exploratory smoke-test evidence and are not counted as validation evidence.

GPT-5.6 Sol was used manually through ChatGPT. All active assessments use Rubric
v0.2, judge prompt v0.1, and the exact frozen canonical request. Recorded timestamps
are import times; no API execution settings or unknown model execution times are
claimed. Recovery and persistence remain unevaluated.

| Scenario | Active attempt | Status | Deterministic severity |
| --- | --- | --- | --- |
| RS-001 | 002 | assessed | 0 |
| RS-006 | 001 | assessed | 0 |
| RS-008 | 001 | assessed | 0 |

RS-001 attempt 001 remains an immutable failed `judge_schema` / `invalid_json`
record with no normalized result or severity. Its pasted transport text is retained
unchanged inside the immutable attempt. The user confirmed that its backslashes
were introduced by copy transport through ChatGPT, not emitted in the locally
observed Sol response. The manually edited standalone scratch copy is excluded
from the commit; see [commit readiness](commit-readiness.md). No backslashes were
restored. Attempt 002 remains the canonical successful RS-001 manual judge result.
No failed record was repaired or replaced.

The `manual_judge/` directory preserves all requests, raw responses, and attempts.
`load_manual_history` revalidates the records and their request binding;
`latest_valid_manual_attempt` selects the active result without a model call.
Mechanical schema/evidence checks and deterministic aggregation are infrastructure
checks, not evidence that the judge agrees with independent human judgments.

## Portable provenance

The three derived import-verification receipts now use schema
`manual-verification-1.0`, repository-relative artifact references, and the semantic
source descriptor `local_manual_input`. They were rebuilt from preserved attempts
and raw responses, retaining historical source digests and byte extraction ranges.
Their outer file hashes changed. The export-verification Markdown's source
description was also made portable. No immutable attempt, request, raw response,
result semantics, severity, or request/raw fingerprint changed.

The original combined collection files are no longer present in this checkout.
Their historical digests are provenance records, not a current source-file
reverification. Exact successful-response files and complete canonical transcripts
remain available; the original failed input is embedded in immutable attempt 001.
Root collection scratch files and the redundant edited failed-response scratch
copy are ignored.

The earlier `import-validation-001.md` records the infrastructure gap at that
time; the subsequent manual importer resolved it. The export report's original
Astra plan is historical planning, not the model provenance of these Sol calls.

These three transcripts must not enter the formal validation benchmark, prompt
tuning examples, or a retrospective validation accuracy calculation. The next
task is to freeze the validation protocol before any benchmark results are collected.
