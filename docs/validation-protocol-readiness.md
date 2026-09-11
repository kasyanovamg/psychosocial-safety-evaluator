# Formal judge validation readiness

Repository inspection found infrastructure for evaluation, but no frozen formal
judge-validation protocol or benchmark-results collection workflow. This inventory
does not choose protocol values, validation thresholds, or collect results.

## Implemented

- Frozen Rubric v0.2, prompt v0.1, canonical blind `JudgeInput`, and pinned payload
  fingerprints (`judge.py`, `judge_payload.py`, `test_judge_payload.py`).
- Strict JudgeResult parsing, semantic consistency, exact evidence and turn checks,
  deterministic maximum severity, and explicit `cannot_assess` handling.
- Immutable fixture/live suite attempts and manual ChatGPT imports with separate
  raw/normalized results, provenance, failed attempts, and latest-valid selection.
- Provider-free artifact reload/rebuild, summary severity/mechanism counts, and
  fixture presentation. These are infrastructure demonstrations, not measured
  agreement, reliability, or benchmark accuracy.
- Scenario schema supports `development` and `held_out` metadata, but the canonical
  20-scenario pack and discovery checks are development-only. A split field alone
  does not establish genuinely held-out story structures.
- Infrastructure tests cover request blindness, payload integrity, invalid outputs,
  evidence references, deterministic rebuilding, and legacy compatibility.
- The three manual smoke-test transcripts have persisted results and are explicitly
  excluded from formal validation evidence. The observed judge was GPT-5.6 Sol
  through ChatGPT; that observation does not freeze a future validation judge model.

## Must be defined before collecting benchmark results

| Area | Remaining protocol decisions / implementation |
| --- | --- |
| Benchmark | Size, sampling frame, genuinely held-out scenario/story structures, contamination controls, and exclusion of the three smoke-test transcripts. |
| Human reference | Two independent human raters, instructions and blinding, preserved independent labels, consensus/adjudication procedure and records. |
| Agreement | Human-human and judge-human comparisons; exact severity agreement and adjacent agreement definitions and denominators. |
| Errors | Major-error and severe-error definitions, directionality and denominators; no rates or thresholds are frozen here. |
| Statistics | Whether to retain weighted kappa and which weights; uncertainty/confidence intervals and appropriate analysis unit. No agreement/statistics implementation was found. |
| Repeatability | Judge rerun/stability policy, repeat counts, selection rules and reporting. Existing latest-valid infrastructure is not a statistical stability policy. |
| Sensitivity | Artifact-sensitivity perturbations and comparisons; subgroup/failure-mode analysis only where justified by the benchmark and sample sizes. Existing parser/metadata tests do not constitute this empirical study. |
| Gates | Preregistered pass/fail validation gates, decision rules and handling of inconclusive evidence. |
| Frozen configuration | Judge model and interface, rubric/prompt versions, exact request construction, and sampling/configuration policy including genuinely unavailable manual settings. |
| Missingness and failures | Treatment of `cannot_assess`, exclusions, technical failures, retries and their effects on metrics/denominators. |
| Collection artifacts | Benchmark manifest, independent rater annotations, adjudication records, analysis inputs and reproducible metric/report generation. |

No two-rater/adjudication schema, human/judge agreement computation, confidence
interval calculation, empirical stability/sensitivity runner, or validation-gate
implementation was found in the current repository. No additional externally
stored protocol was supplied for this task. Existing frozen methodology remains
unchanged; formal validation choices require the next task.
