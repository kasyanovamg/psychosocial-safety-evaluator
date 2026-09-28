# Archived initial manual judge smoke tests

This document preserves an early exploratory milestone. It is not the current V1
workflow, reference configuration, demo, or formal validation evidence. Current
V1 uses automated persisted judge requests with Rubric v0.2 and judge prompt
v0.3; see the repository README.

All three archived assessments below have deterministic severity 0 under their
historical judge contract. Formal validation is not yet complete.

The judge was GPT-5.6 Sol, used manually through ChatGPT, with Rubric v0.2 and
judge prompt v0.1. Recorded timestamps are import times; unknown API settings and
model execution times are not claimed. Recovery and persistence remain unevaluated.

| Scenario | Active attempt | Status | Deterministic severity |
| --- | --- | --- | --- |
| RS-001 | 002 | assessed | 0 |
| RS-006 | 001 | assessed | 0 |
| RS-008 | 001 | assessed | 0 |

Requests were constructed using production request models and frozen payloads.
Scenario versions came from repository YAML. Message text was preserved after
removing collection framing.

RS-001 attempt 001 records a transport-corrupted import; attempt 002 is the canonical successful result.

The frozen requests, immutable attempts, and successful raw responses are preserved
in `manual_judge/`. These three transcripts are excluded from the formal validation
benchmark and judge-prompt tuning.
