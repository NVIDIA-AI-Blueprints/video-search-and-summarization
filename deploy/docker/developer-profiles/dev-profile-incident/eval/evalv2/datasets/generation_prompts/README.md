# Generation prompts (Evaluation V2)

The append-only registry of prompts that **make** V2 references:
- box choices among detector proposals;
- chat questions and answers;
- dashboard requests and specs;
- matching labels.

These prompts are never sent to a subject. Every reference record names the prompt id that made it in `provenance.generator.prompt`. A `validator` prompt is for the blind pass, which never sees the generator's answer.

- **Entries:** [`registry.json`](registry.json) pins each file's SHA-256. Files live under `<purpose>/<id>.txt`.
- **Adding:** use `evalv2.datasets.generation_prompts.add`. It refuses an existing id and any edit. A changed prompt is a new id, optionally naming its `parent`.
- **Reading:** `prompt_text(id)` returns a prompt only when its file still matches the pinned hash.

Registered so far:
- `gen-box-select-v1` and `gen-box-validate-v1`, the box-reference first pass and blind second pass (`evalv2.references.boxes`).
- `gen-chat-wording-v1` and `gen-chat-rederive-v1`, the chat question wording and the blind re-derivation (`evalv2.references.chat`).
- `gen-dashboard-request-v1` and `gen-dashboard-respec-v1`, the chart request wording and the blind spec re-derivation (`evalv2.references.dashboard`).

Running any of them to generate references is a separately approved run.
