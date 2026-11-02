# Chat contract (`chat-contract-v2`)

The grounded answer of `POST /api/v1/incidents/chat` when the agent runs with `INCIDENT_CHAT_MODE=grounded`. In that mode a chat model answers an incident question from one report version: the asked one, the official one, or a reviewer's unsaved draft.

- **Request** ([`chat_request.schema.json`](chat_request.schema.json)): the same fields as [`chat-contract-v1`](../chat-contract-v1/README.md).
- **Response** ([`chat_response.schema.json`](chat_response.schema.json)): the v1 answer plus:
  - `message.insufficient_evidence`: the report cannot answer, or contradicts the question's premise. Citations are then empty;
  - `grounding`: `{mode, model, version_id, official, draft, dropped_citations, ids_resolved, prompt_sha256, usage}`. `ids_resolved` lists the item ids the model wrote in the answer although the question did not ask for ids; each was replaced by the item's description.
- **Unchanged:** a template-mode answer (the default) and a library question (no incident) keep the v1 shape exactly.
- **Fixtures** (`fixtures/valid/`, `fixtures/invalid/`) are checked by `../test_schema.py`.

How the route behaves: [`../../.docs/vm-api-contracts.md`](../../.docs/vm-api-contracts.md#post-apiv1incidentschat). The system prompt is `chat/incident-answer` in [`../prompts/`](../prompts/README.md).
