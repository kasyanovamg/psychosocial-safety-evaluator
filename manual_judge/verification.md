# Manual judge export verification

Source: `local_manual_input` (original export input; identified by the source SHA-256 below).

Source SHA-256: `e8c86d1ed04221c8684575fca2bc3691a6d9dbe4dd4161c9d2d13440435f8322`

RS-001, RS-006, and RS-008 each use scenario version **1.0**, taken from the corresponding current repository YAML identity. The raw source records scenario IDs but no version or execution settings.

## Generation path

Used production `EvaluatorScenarioView`, `JudgeTurn`, and `JudgeInput`, frozen constants from `psych_eval.judge_payload`, `request.model_dump_json(indent=2)`, and `request_fingerprint()`.
No production code or tests were changed.

The existing `load_transcript()` accepts execution-artifact JSON, not this manually saved text format. `assemble_judge_input()` requires a `Transcript` with mandatory target model, system prompt, sampling, and execution metadata unavailable in the source. Therefore the exports instantiate its production request model directly using the same imported frozen constants, without fabricating execution fields or bypassing model validation. The execution-artifact assembler itself was not called.

Only plain-text scenario headings, role prefixes, and the two CRLF framing separators between messages/scenarios were removed. Every message body was copied from source slices without stripping, normalization, punctuation repair, or scenario-user-text substitution. Internal CRLF line endings are preserved.

## Artifacts and hashes

### RS-001
- Scenario version: 1.0
- Path: `manual_judge/RS-001/judge-request.json`
- File size: 14062 bytes
- Turns: 8 (four user, four assistant)
- Canonical request fingerprint: `462a557e30885ca3bf7bfb5d152ec1a6d35e1f0cbe88395a6b03055521477b37`
- Exact file SHA-256: `4709c5105048c82ad4b93c0b758c2a8467e413eec722862ec1f55a2d68050c25`

### RS-006
- Scenario version: 1.0
- Path: `manual_judge/RS-006/judge-request.json`
- File size: 14331 bytes
- Turns: 8 (four user, four assistant)
- Canonical request fingerprint: `9cc57d12f5954e1f91b8aa81a62a44690d50aab0d94f82ae468c83de467bf926`
- Exact file SHA-256: `f9fa4ff6390d31dce2e8eef6b45ee544c6e09796e895339dccc506e8020e4b6d`

### RS-008
- Scenario version: 1.0
- Path: `manual_judge/RS-008/judge-request.json`
- File size: 14585 bytes
- Turns: 8 (four user, four assistant)
- Canonical request fingerprint: `b7a69e844fd342ade9d472dee1774b202d71e015b5c25c6b75606069d5298d4d`
- Exact file SHA-256: `b986df04d2e0c9ba3327931a64e554c5d405c186fc0d4d8e1a9ad188a9662091`


Files contain exactly the UTF-8 bytes of `model_dump_json(indent=2)`, without BOM or added final newline.

## Contract identity

All three have identical construct, rubric version **0.2**, judge prompt version **0.1**, instructions (including output expectations), and frozen rubric payload. Only scenario ID and transcript content differ.
- Instructions SHA-256: `d8f00dc5d242d3a916174321eb7a4196aed4743e0b2e301b24141184e2f67a82`
- Rubric SHA-256: `2c861bffe4ad8f4ca5a6fec6ca2381276243e9662a133a508b3babe97c06bbf8`

Both component hashes match the existing pins in `tests/test_judge_payload.py`.

## Blind-input verification

Exact top-level allowlist verified: `scenario_id`, `scenario_version`, `construct`, `rubric_version`, `judge_prompt_version`, `instructions`, `rubric`, `transcript`.

Every transcript entry contains exactly `turn_id`, `role`, and `text`.

Confirmed absent as additional input or metadata: risk/control/boundary authoring category, difficulty, risk hypothesis, private evidence ledger, expected severity/label, human pilot label, adjudication notes, benchmark split, fixture judge results, fixture expected behavior, target provider/model identity, target system prompt, target execution metadata, prior judge results, and any fields outside the frozen blind contract. General rubric language is preserved as required and is not scenario-specific metadata.

## Integrity and verification

All generation assertions passed:
- All three source sections consumed in order; eight messages per section.
- Full source message blocks reconstructed byte-for-byte after restoring framing.
- Each exported message independently compared to its exact source slice.
- Canonical alternating role/ID validation: U1, A1, U2, A2, U3, A3, U4, A4.
- Strict request schema validation and serialized JSON reload equality.
- Identical cross-request frozen contract and existing pinned component hashes.
- Source bytes unchanged after export.

No assistant response was regenerated or edited; no user message was rewritten; no future-turn information was inserted. No responses were judged or scored. No application-code changes, scenario edits, frozen-payload edits, historical-artifact changes, API calls, judge imports, commits, or pushes were performed. Pre-existing workspace changes were left intact. Targeted artifact verification was used; no full-suite run was needed for these data-only exports.

## Later manual judging provenance

Paste each request JSON unchanged into a fresh GPT-6 Astra chat. Record provenance separately: source manual ChatGPT, actual displayed model, scenario identity/version, rubric/prompt versions, exact request, exact raw response, and actual judging timestamp. No judging timestamp or response exists yet. Do not claim unavailable temperature, seed, API IDs, enforced response format, or token counts.

Remaining blockers for manual use: none. The scenario versions come from the repository, as disclosed above.

## Exact generation command

Run from the repository root in PowerShell. This command refuses to overwrite existing export files.

```powershell
@'
from pathlib import Path
from hashlib import sha256
import json
import re
import yaml
from psych_eval.judge import JudgeInput, JudgeTurn, request_fingerprint
from psych_eval.judge_payload import INSTRUCTIONS, RUBRIC, RUBRIC_VERSION, JUDGE_PROMPT_VERSION
from psych_eval.scenarios import EvaluatorScenarioView

root = Path.cwd()
source = root / "chatgpt_transcripts_for_eval.txt"
source_bytes = source.read_bytes()
raw = source_bytes.decode("utf-8")
headings = list(re.finditer(r"(?m)^(RS-\d{3})\r\n", raw))
assert [m[1] for m in headings] == ["RS-001", "RS-006", "RS-008"]
assert headings[0].start() == 0
requests = []
records = []
allowed = {"scenario_id", "scenario_version", "construct", "rubric_version",
           "judge_prompt_version", "instructions", "rubric", "transcript"}
for index, heading in enumerate(headings):
    sid = heading[1]
    end = headings[index + 1].start() if index + 1 < len(headings) else len(raw)
    block = raw[heading.end():end]
    if index + 1 < len(headings):
        assert block.endswith("\r\n\r\n")
        block = block[:-4]
    markers = list(re.finditer(r"(?m)^\((user|chatbot)\) ", block))
    assert len(markers) == 8 and markers[0].start() == 0
    turns = []
    rebuilt = ""
    for i, marker in enumerate(markers):
        stop = markers[i + 1].start() if i + 1 < len(markers) else len(block)
        text = block[marker.end():stop]
        separator = ""
        if i + 1 < len(markers):
            assert text.endswith("\r\n\r\n")
            text, separator = text[:-4], "\r\n\r\n"
        role = "user" if i % 2 == 0 else "assistant"
        assert marker[1] == ("user" if role == "user" else "chatbot")
        turn_id = f"{'U' if role == 'user' else 'A'}{i // 2 + 1}"
        turns.append(JudgeTurn(turn_id=turn_id, role=role, text=text))
        rebuilt += marker[0] + text + separator
    assert rebuilt.encode("utf-8") == block.encode("utf-8")
    metadata = yaml.safe_load((root / f"scenarios/v1/relational_sycophancy/{sid}.yaml").read_bytes())
    identity = EvaluatorScenarioView(**{k: metadata[k] for k in
                                       ("scenario_id", "scenario_version", "construct")})
    assert identity.scenario_id == sid and identity.scenario_version == "1.0"
    request = JudgeInput(**identity.model_dump(), rubric_version=RUBRIC_VERSION,
                         judge_prompt_version=JUDGE_PROMPT_VERSION,
                         instructions=INSTRUCTIONS, rubric=RUBRIC, transcript=turns)
    data = request.model_dump()
    assert set(data) == allowed
    assert all(set(t) == {"turn_id", "role", "text"} for t in data["transcript"])
    assert request.rubric_version == "0.2" and request.judge_prompt_version == "0.1"
    output = root / "manual_judge" / sid / "judge-request.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = request.model_dump_json(indent=2).encode("utf-8")
    with output.open("xb") as file:
        file.write(serialized)
    saved = output.read_bytes()
    restored = JudgeInput.model_validate_json(saved)
    assert restored == request and saved == serialized
    # Independently recover each exported message's exact original source slice.
    for i, turn in enumerate(restored.transcript):
        start = heading.end() + markers[i].end()
        stop = heading.end() + (markers[i + 1].start() - 4 if i + 1 < len(markers) else len(block))
        assert turn.text.encode("utf-8") == raw[start:stop].encode("utf-8")
    records.append({"scenario_id":sid, "scenario_version":identity.scenario_version,
                    "path":output.relative_to(root).as_posix(), "bytes":len(saved),
                    "turns":len(turns), "request_fingerprint":request_fingerprint(restored),
                    "file_sha256":sha256(saved).hexdigest()})
    requests.append(restored)
contract = lambda r: r.model_dump(exclude={"scenario_id", "scenario_version", "transcript"})
assert all(contract(r) == contract(requests[0]) for r in requests)
assert sha256(INSTRUCTIONS.encode("utf-8")).hexdigest() == "d8f00dc5d242d3a916174321eb7a4196aed4743e0b2e301b24141184e2f67a82"
assert sha256(RUBRIC.encode("utf-8")).hexdigest() == "2c861bffe4ad8f4ca5a6fec6ca2381276243e9662a133a508b3babe97c06bbf8"
assert source.read_bytes() == source_bytes
print(json.dumps({"source":str(source), "source_sha256":sha256(source_bytes).hexdigest(),
                  "artifacts":records, "instructions_sha256":sha256(INSTRUCTIONS.encode()).hexdigest(),
                  "rubric_sha256":sha256(RUBRIC.encode()).hexdigest(),
                  "verification":"All assertions passed"}, indent=2))
'@ | .venv/Scripts/python.exe -
```

