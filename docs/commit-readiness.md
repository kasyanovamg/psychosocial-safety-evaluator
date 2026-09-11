# Commit readiness review

Portability work is complete. The known copy-transport discrepancy is understood
and accepted; no remaining commit blocker was identified. The proposed contents
below are ready to commit and push when authorized. No commit or push was performed.

## Portability and verification

Machine-specific source locations were present in three derived import-verification
JSON reports and the export-verification Markdown. Immutable attempts, canonical
requests, and normalized results contained no such provenance paths.
The new `ManualImportVerification` schema uses existing `ArtifactRef` validation
for repository-relative POSIX references and `local_manual_input` for the original
collection location. The builder resolves paths and rejects out-of-root references;
no username replacement or filesystem-safety weakening occurs.

Three JSON receipts were regenerated through the typed builder using preserved
attempts and raw files. Source digests and extraction ranges remain historical.
The original combined source files are absent and were not reconstructed.
Only derived report bytes changed; all 32 protected files (requests, raw files,
attempts, scenario YAMLs and frozen payload source) match their task-start hashes.

- Targeted tests: 52 passed.
- Full suite: 587 passed, 2 skipped, including fixture/live and historical rebuild coverage.
- Git whitespace check: passed.
- Repository/artifact scan: 162 candidate files scanned before this review file was
  added. Home-path matches were confined to synthetic rejection fixtures in
  `tests/test_manual_judge.py` and `tests/test_runs.py`; no real user path or
  machine-specific provenance remained in publishable artifacts.
- No dedicated secret scanner (gitleaks, detect-secrets, trufflehog) was installed.
  A local regex sanity scan of tracked/untracked candidate files and the tracked
  diff found no provider tokens, private keys, or populated secret assignments.
  This is a limited sanity scan, not a guarantee against every possible secret.
- `.env.example` contains an empty key placeholder, not a credential.
- Artifact content includes the intended relationship scenarios, model responses,
  manual displayed-model facts, import times, and source hashes. No personal
  identifiers or machine-specific metadata were identified in these records.
  Frozen Notion source page IDs remain in payload-source documentation; they are
  methodology references, not credentials.

All active histories revalidate under manual / ChatGPT / GPT-5.6 Sol, Rubric v0.2,
prompt v0.1, with severity 0/0/0 and active attempts 002/001/001.

## Known copy-transport artifact (resolved)

The user confirmed that copying the response through ChatGPT introduced the
backslashes in the first failed import; they were not present in the locally
observed Sol output. The five backslashes were subsequently removed manually from
the standalone scratch file `manual_judge/RS-001/raw-response-001.txt`. That file
is not byte-identical to the response in immutable
`manual_judge/RS-001/attempts/attempt-001.json`. This is a known, accepted
copy-transport artifact, not evidence that GPT-5.6 Sol emitted invalid JSON.

- Standalone file SHA-256:
  `869e2841867c0997187cdf6932753ab8643aa90fdeacb7f59a57814abb39732e`
- Original response SHA-256 retained in immutable attempt 001:
  `8adf026335b5a40f4d9c3071906054827cd9b0b89a60af2d0d255ee5180b2b4c`

The immutable attempt remains unchanged as the historical transport-corrupted
failed import (`judge_schema / invalid_json`), with no result or severity. Its exact
failed input is embedded in that record, so the standalone scratch file has no
ongoing artifact role and is excluded by a narrowly scoped `.gitignore` entry.
The scratch file is retained locally without restoring backslashes. Attempt 002
remains the canonical successful RS-001 manual judge result. All three active
response files match their immutable records exactly. This resolved discrepancy
does not block committing the proposed contents.

## Complete proposed commit inventory

This working tree includes earlier canonical-contract and manual-persistence work
as well as this portability task. Existing changes were reviewed and left intact;
the already-modified demo fixture is part of the earlier contract-binding work,
not a new historical rewrite in this task.

Modified tracked files to include:
- `.gitignore`
- `README.md`
- `fixtures/demo_judges/relational_sycophancy/RS-001.yaml`
- `src/psych_eval/evaluations.py`
- `src/psych_eval/evaluator.py`
- `src/psych_eval/judge.py`
- `src/psych_eval/pack_fixtures.py`
- `src/psych_eval/presentation.py`
- `src/psych_eval/runs.py`
- `src/psych_eval/suite.py`
- `tests/test_development_scenarios.py`
- `tests/test_judge_evaluator.py`
- `tests/test_suite.py`

New files to include:
- `docs/commit-readiness.md`
- `docs/manual-smoke-tests.md`
- `docs/validation-protocol-readiness.md`
- `manual_judge/RS-001/attempts/attempt-001.json`
- `manual_judge/RS-001/attempts/attempt-002.json`
- `manual_judge/RS-001/import-validation-001.md`
- `manual_judge/RS-001/import-verification-002.json`
- `manual_judge/RS-001/judge-request.json`
- `manual_judge/RS-001/raw-response-002.txt`
- `manual_judge/RS-006/attempts/attempt-001.json`
- `manual_judge/RS-006/import-verification-001.json`
- `manual_judge/RS-006/judge-request.json`
- `manual_judge/RS-006/raw-response-001.txt`
- `manual_judge/RS-008/attempts/attempt-001.json`
- `manual_judge/RS-008/import-verification-001.json`
- `manual_judge/RS-008/judge-request.json`
- `manual_judge/RS-008/raw-response-001.txt`
- `manual_judge/verification.md`
- `src/psych_eval/judge_payload.py`
- `src/psych_eval/manual_judge.py`
- `src/psych_eval/manual_verification.py`
- `tests/test_judge_payload.py`
- `tests/test_manual_judge.py`

Keep local:
- `manual_judge/RS-001/raw-response-001.txt` — redundant, manually edited scratch
  copy; explicitly ignored. The original failed import input is preserved in
  committed `attempts/attempt-001.json` instead.
- `.env`, environments, credentials, caches, and build output under existing ignore rules.
- Root `judge_results.txt` and `chatgpt_transcripts_for_eval.txt` collection scratch
  files if recreated. Both are currently absent; root-specific ignore entries were
  added. Exact preserved project artifacts under `manual_judge/` remain included.

## Derived JSON file hashes changed

These are outer report-file hashes, not request/raw/attempt fingerprints.
Immutable attempt-file hashes also remain unchanged.

```json
{
  "manual_judge/RS-001/import-verification-002.json": {
    "before_sha256": "8c4460e1b29cec4b9cfbaed711a5234bb1221225c13d97fe45bc50b3cb56ee2f",
    "after_sha256": "21b494e9462092e98aa6e694bdb94da9aa0220ee24a2e6f978b18b39eb948388"
  },
  "manual_judge/RS-006/import-verification-001.json": {
    "before_sha256": "ddd9db900b473b3bdfff88e77ceafee1329f36fd48301893e08ef1a294425147",
    "after_sha256": "4fea2427a3f54bf8708e09280d969a23eace808a962996d5ae70db1992465010"
  },
  "manual_judge/RS-008/import-verification-001.json": {
    "before_sha256": "f36f7431244f6ccbee3ac8ff679270204916be2c11f6d578697954442b632f43",
    "after_sha256": "4ed3a3620a2a079ccf5d2cdb4644e3e828a101cc4536b1fbbb85e301d4f5bf95"
  }
}
```

The export-verification Markdown and historical import-analysis note were also
updated as derived documentation; their bytes are not claimed unchanged.

## Formal validation next step

See [validation readiness](validation-protocol-readiness.md) for the complete
inventory. Benchmark size/structure, independent human raters and adjudication,
agreement/error metrics, uncertainty estimates, rerun and sensitivity policies,
subgroup analysis, gates, frozen model/configuration, and missingness/exclusion
rules remain to be defined before collecting any benchmark results. No values were
chosen or frozen here. The three smoke-test transcripts are excluded from formal
validation evidence.
