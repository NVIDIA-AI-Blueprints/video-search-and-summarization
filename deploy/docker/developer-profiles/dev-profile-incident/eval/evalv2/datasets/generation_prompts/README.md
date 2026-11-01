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

The registry is empty until the reference tooling (A7 boxes, A9 chat) adds its prompts. Running them to generate references is a separately approved run.
