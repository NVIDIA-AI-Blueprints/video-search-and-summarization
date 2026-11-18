---
name: vss-generate-video-report-rag
description: Generates VSS video summary reports with LVS HITL and optional Enterprise RAG document grounding. Trigger when the user asks for a frag/RAG-assisted video report, knowledge-enhanced analysis, or Enterprise RAG context in a video summary.
license: Apache-2.0
metadata:
  version: "3.3.0-rc0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# VSS Generate Video Report RAG — Video Analysis with Enterprise RAG

Generate video summary reports using the LVS profile's RAG-enabled agent config.
This skill adds Enterprise RAG document grounding and guided human-in-the-loop
(HITL) parameter collection on top of the VSS agent.

Always run `curl` commands yourself; never instruct the user to run them.

## Enable Enterprise RAG on the LVS Profile

The repository ships the RAG-enabled LVS agent config at
`deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config_rag.yml`.
It is a superset of the default LVS config: regular caption retrieval remains
enabled, and `frag_retrieval` adds Enterprise RAG document grounding.

Use `/vss-build-vision-ai` with the LVS Foundation and explicitly retain the
in-stack `vss-agent`, which owns this RAG/HITL workflow. Keep the checked-in
`.env` and `overrides.env` read-only. All deployment artifacts belong under
`_builds/<name>/`; follow the build skill's
[`deployment.md`](../../vss-build-vision-ai/references/deployment.md) for
resolution, validation, host preparation, image pulls, and readiness.

### Step 1: Configure the build override

Add these non-secret settings to `_builds/<name>/override.env`, preserving
`FOUNDATION=lvs`, the effective service set including `vss-agent`, and the
build's hardware, model, path, and dependent settings:

- `HOST_IP` — the observed host address reachable by the selected services
- `VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config_rag.yml`
- `RAG_SERVER_URL` — Enterprise RAG server HTTP endpoint (defaults to `http://rag-server:8081/v1`)
- `KNOWLEDGE_COLLECTION` — default Enterprise RAG collection for `frag_retrieval`

Handle required model credentials through the build skill's
[`credentials.md`](../../vss-build-vision-ai/references/credentials.md). That
workflow resolves required model keys into the deployment artifacts; keep those
artifacts private and never commit them or paste their contents into logs.
Keep `RAG_API_KEY` out of the build override and saved `resolved.yml`: leave
it unset during resolution and supply it only through the runtime override
below when the RAG server requires authentication.

### Step 2: Log in to NGC registry

Prefer an existing authenticated Docker session or a secret-managed login. If a login is required, use `--password-stdin` without printing token values:

```bash
read -rsp "NGC API key: " NGC_CLI_API_KEY
printf '%s\n' "$NGC_CLI_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin
unset NGC_CLI_API_KEY
```

### Step 3: Resolve and deploy the RAG build

Resolve the root Compose graph and any build patches with the build skill's
four ordered env layers into `$BUILD_DIR/resolved.yml`. Normalize and validate
that file and complete data-directory preparation before deployment. Confirm
the resolved `vss-agent` selects `config_rag.yml` and has the requested
`RAG_SERVER_URL` and `KNOWLEDGE_COLLECTION`. Keep `RAG_API_KEY` unset during
resolution and confirm the saved model contains no RAG API key value.

If the RAG server needs no API key, use the shared resolved Compose deployment
lifecycle directly. If it requires authentication, create this build-local
runtime override **after** resolution; do not include it in the build's
`compose.yml` or fold it into the saved `resolved.yml`:

```bash
: "${BUILD_DIR:?Set to the RAG build directory}"
mkdir -p "$BUILD_DIR/patches"
cat > "$BUILD_DIR/patches/rag-secret.runtime.yml" <<'EOF'
services:
  vss-agent:
    environment:
      RAG_API_KEY: ${RAG_API_KEY:?Set RAG_API_KEY only for docker compose up}
EOF
```

Complete the shared image-pull step against `resolved.yml`. Then supply the
RAG key only while bringing up the build:

```bash
read -rsp "RAG API key: " RAG_API_KEY
RAG_API_KEY="$RAG_API_KEY" docker compose \
  -f "$BUILD_DIR/resolved.yml" \
  -f "$BUILD_DIR/patches/rag-secret.runtime.yml" up -d --build
unset RAG_API_KEY
```

This runtime-only credential override is the exception to deploying with just
`resolved.yml`; neither path re-reads profile env files or passes profile flags.
Keep the override with this build for subsequent recreations that need the
key, and inject it again at runtime. Never save expanded output from this
two-file command, print the key, or put it into command history.

### Step 4: Verify deployment

Complete the build skill's readiness checks, then check the RAG agent:

```bash
docker compose -f "$BUILD_DIR/resolved.yml" ps --all

curl -sf --max-time 5 "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/health" >/dev/null \
  && echo "VSS LVS RAG agent is running" \
  || echo "VSS LVS RAG agent is NOT reachable"
```

### Tear down

Use the build skill's teardown workflow for this build's `resolved.yml` and
Compose project. The runtime-only secret override is not needed to stop the
project.

## When to Use

- User wants to generate a video summary or report using the RAG-enabled LVS pipeline
- User asks to analyze a video with Enterprise RAG knowledge context
- User mentions "frag", "enterprise RAG", or "knowledge-enhanced report"

## When NOT to Use

- Simple video understanding queries (use `video-understanding` skill)
- Direct LVS summarization without HITL (use `video-summarization` skill)
- Deployment tasks (use `deploy` skill)
- Real-time alerts (use `alerts` skill)

## Workflow: Generate an LVS Report with Enterprise RAG

### Step 1: List available videos

```bash
curl -sS -X POST "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What videos are available?"}]}' | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"
```

A selected video is required before Step 2. If the user has not already named one, return the short list and stop; resume when the user supplies the video name.

### Step 2: Collect parameters from the user

Required user-provided parameters:

1. **Scenario** — scenario label for the video.
   Example: "warehouse monitoring", "traffic monitoring", "retail store activity"
2. **Events** — comma-separated event names to detect.
   Example: "accident, forklift stuck, workers not wearing PPE, person entering restricted area"
3. **Objects of Interest** — focus objects, or "skip".
   Example: "forklifts, pallets, workers"

If any required value is missing, return a concise missing-fields message and stop; resume the workflow when the user supplies the missing values.

There is no separate Enterprise RAG Query HITL prompt. Document grounding comes
from the RAG-enabled agent config exposing `frag_retrieval`; if the user wants
specific SOP, policy, or procedure context reflected in the report, capture that
context in the original report request or resolve it as a document-grounding
question before starting the HITL report flow.

### Step 3: Start the report (HTTP HITL)

Send a POST to `/v1/chat`. This returns HTTP 202 with an execution_id and the first
HITL prompt. Replace VIDEO_NAME with the chosen video:

```bash
curl -sS -X POST "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Generate a report for VIDEO_NAME using long video summarization"}]}'
```

The response contains:
- `execution_id` — save this, used in all subsequent requests
- `interaction_id` — identifies the current prompt
- `prompt.text` — the HITL prompt text
- `response_url` — the URL to POST the response to

### Step 4: Respond to HITL prompts

For each prompt, POST the user's parameter to the response_url.
Replace EXECUTION_ID, INTERACTION_ID, and the text value:

```bash
curl -sS -X POST \
  "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/executions/EXECUTION_ID/interactions/INTERACTION_ID/response" \
  -H "Content-Type: application/json" \
  -d '{"response": {"type": "text", "text": "USER_VALUE_HERE"}}'
```

Then poll for the next prompt:

```bash
curl -sS "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/executions/EXECUTION_ID" | python3 -m json.tool
```

The HITL prompts come in this order:
1. **Scenario** — respond with the scenario from Step 2
2. **Events** — respond with the events from Step 2
3. **Objects of Interest** — respond with the objects from Step 2, or "skip"
4. **Confirmation** — respond with empty string "" to confirm and start processing

Repeat the POST-then-poll cycle for each prompt.

### Step 5: Wait for completion

After the confirmation prompt, the system processes the video. This takes 3-5 minutes.
Keep polling until the status changes from "running" to "completed":

```bash
curl -sS "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/executions/EXECUTION_ID" | python3 -m json.tool
```

Set the expectation that processing usually takes 3-5 minutes, then poll every 30 seconds.

### Step 6: Present the results

When status is "completed", the response contains the full report with:
- Detected events with timestamps
- Narrative analysis summary
- Enterprise RAG context (if queried)
- PDF report download link (if available)

Present the report content to the user in a readable format.

## Error Handling

- If a deployment, health, or chat request fails, report the failing endpoint, HTTP status or command error, and the most useful next check. Do not continue into HITL without a valid `execution_id`, `interaction_id`, and `response_url`.
- If a HITL response is rejected or the next execution poll omits the expected prompt, stop and show the execution status plus any error payload instead of guessing the next prompt.
- If the execution status becomes `failed`, `cancelled`, or stays `running` without progress beyond the expected processing window, surface the status and recommend checking the `vss-agent` logs before retrying.
- If the final response lacks report text or a PDF link, return the available response fields and clearly state which output was missing.

## Quick Commands

### Simple chat query (non-report)

For simple questions that do NOT involve report generation:

```bash
curl -sS -X POST "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "YOUR_QUESTION_HERE"}]}' | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"
```

## Notes

- LVS reports take 3-5 minutes for a ~3.5 minute video; set that expectation before polling
- Enterprise RAG requires a reachable RAG server with data already ingested in `KNOWLEDGE_COLLECTION`
- If objects are not needed, respond with "skip"
- The HITL response format is always: `{"response": {"type": "text", "text": "value"}}`
- HTTP HITL requires explicit `HITL_ENABLED=true`; the shipped config derives
  its `hitl_enabled` field from that variable and defaults it to false.
- See also: `video-summarization`, `video-understanding`, `report`, `vios`, `deploy`

