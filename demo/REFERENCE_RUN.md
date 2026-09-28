# V1 reference demo provenance

`runs/relational-sycophancy-reference-v1/` is the immutable, packaged demo shown
by the Streamlit application.

It is a byte-for-byte copy of the local preserved development bundle:

```text
runs/evaluation-20260924-165559-judge-v0.3
```

The copy was made without rebuilding or rewriting any artifact. Relative-path
and file-content tree digest (SHA-256):

```text
0fae27e19785448e3a2fadd7189f51a9b49603d00d8e9d9f870884390dd18e5f
```

Configuration recorded by the bundle:

- target: OpenAI `gpt-4o-mini`, temperature 0.0;
- judge: OpenAI `gpt-5.6-terra`, reasoning effort medium, no temperature;
- Rubric v0.2;
- judge prompt v0.3;
- 20 completed and assessed scenarios; and
- validation status: Experimental.

Viewing this bundle is read-only and makes no model calls. The run was manually
reviewed during development and contained one likely false negative (RS-004).
That observation is not a statistical accuracy estimate or formal validation.

The original `/runs/` source remains local and gitignored. Do not regenerate the
packaged copy implicitly; any future replacement must be an explicit, reviewed,
new version with updated provenance and digest.
