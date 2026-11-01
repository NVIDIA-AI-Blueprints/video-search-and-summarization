# Incident contract (`incident-contract-v4`)

The single source of truth for incident extraction: one prompt, one output schema and the shared inference settings, used by `eval/`, `incident-console-v2/` and `vss-agent`.

| File | What it is |
|---|---|
| [`incident_report.schema.json`](incident_report.schema.json) | The P1 output schema, sent to the model as a strict `response_format` |
| [`incident_extraction_prompt.txt`](incident_extraction_prompt.txt) | The P1 prompt (field meaning only) |
| [`report_generation_prompt.txt`](report_generation_prompt.txt) | The RP1 prompt (P1 JSON to prose) |
| [`VERSION`](VERSION) | The contract version, stored as `prompt_version` on model runs |
| [`inference.json`](inference.json) | P1 and RP1 settings and the model allowlist |
| [`fixtures/`](fixtures/) | Valid, invalid and policy cases both validators must agree on |
| [`prompts/`](prompts/README.md) | Every other model prompt: the caption pass, the P1 preambles, grounding, the zoomed second look, and the exported chart prompt and allowlist. The agent vendors its prompts; the console reads the reviewer preamble |
| [`golden/`](golden/README.md) | The exact request bodies sent to models (P1, RP1, captions, grounding, zoom, chart); the console, eval and the agent each test theirs against them |
| [`search-contract-v1/`](search-contract-v1/README.md), [`chat-contract-v1/`](chat-contract-v1/README.md) | The MVP2 search and chat HTTP contracts |

## Changing it

1. Edit the files here only, then run `python sync.py` to copy them into the agent, including the agent's prompts from `prompts/` (`python sync.py --check` reports drift).
2. Bump `VERSION` and update every consumer in the same PR; a taxonomy change also needs a migration.
3. Update the request goldens it changes ([`golden/`](golden/README.md#changing-a-request)): the P1 and RP1 cases reference the contract files by sha256.

How the contract is validated, repaired and stored: [`../.docs/analysis-schema.md`](../.docs/analysis-schema.md).

## Experiment prompts (not part of the contract)

[`experiments/`](experiments/README.md) holds the append-only prompt registry for the prompt experiments (PR 6A). Nothing there changes `incident-contract-v4`: `sync.py` does not vendor it, and the production P1 prompt stays `incident_extraction_prompt.txt`. Adopting an experiment prompt in production is a separate decision with a contract version bump.
