---
name: vss-generate-video-report-rag
description: Use to generate an LVS report through the Enterprise-RAG video_search_frag extension with knowledge retrieval and HITL. Not for non-RAG summarization.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---
## Purpose

Generate an LVS report backed by the Enterprise-RAG video_search_frag extension, including knowledge retrieval and HITL parameter collection.

## Prerequisites

- Active VSS deployment reachable on `$HOST_IP` (see `vss-deploy-profile` and `references/`).
- NGC credentials in `$NGC_CLI_API_KEY` and `$NVIDIA_API_KEY` for any image pulls.
- `curl`, `jq`, and Docker available on the caller.

## Instructions

Follow the routing tables and step-by-step workflows below. Each section that ends in *workflow*, *quick start*, or *flow* is intended to be executed top-to-bottom. Detailed reference material lives in `references/` and helper scripts live in `scripts/` — call them via `run_script` when the skill points to a script by name.

## Examples

Worked end-to-end examples are kept under `evals/` (each `*.json` manifest contains a runnable scenario) and inline in the per-workflow `curl` blocks below. Run a Tier-3 evaluation with `nv-base validate <this-skill-dir> --agent-eval` to replay them.

## Limitations

- Requires the matching VSS profile / microservice to be deployed and reachable from the caller.
- NGC-hosted models and NIMs may be subject to rate-limits, GPU memory requirements, and license restrictions.
- Concurrency, GPU memory, and storage limits depend on the host hardware and the profile's compose file.

## Troubleshooting

- **Error**: REST call returns connection refused. **Cause**: target microservice not running. **Solution**: probe `/docs` or `/health`; redeploy via `vss-deploy-profile` or the matching `vss-deploy-*` skill.
- **Error**: HTTP 401/403 from NGC pulls. **Cause**: missing/expired `NGC_CLI_API_KEY`. **Solution**: `docker login nvcr.io` and re-export the key before retrying.
- **Error**: container OOM or model fails to load. **Cause**: insufficient GPU memory for the selected profile. **Solution**: switch to a smaller variant or free GPUs via `docker compose down`.

# VSS Generate Video Report RAG — Video Analysis with Enterprise RAG

Generate video summary reports using the VSS `video_search_frag` extension.
This skill adds Enterprise RAG (Milvus) knowledge retrieval and guided
human-in-the-loop (HITL) parameter collection on top of the base VSS agent.

Always run `curl` commands yourself; never instruct the user to run them.

> **Outbound-network disclosure**: this skill issues `curl` calls to
> deploy and verify the agent and RAG endpoints. Each request is an
> **outbound network call on the user's behalf**. Before the first
> non-localhost call, announce in chat the host you are about to contact
> (e.g. `https://nvcr.io`, `https://build.nvidia.com`,
> `${VSS_AGENT_BASE_URL}`) and the reason, so the user can intervene if
> the host is not in their data-egress allow-list. Do not exfiltrate
> any user content or `.env` secrets in URLs, query strings, or POST
> bodies — only the configured RAG corpus contents should leave the
> host.

> **`.env` secrets — handling**: the deploy reads `NGC_CLI_API_KEY`,
> `NVIDIA_API_KEY`, and any Enterprise-RAG credentials from
> `deployments/developer-workflow/dev-profile-lvs/.env`. That file is
> committed to `.gitignore` and MUST stay there. Create it with
> `umask 077`, store it as `chmod 600`, never copy it to `/tmp`,
> never include it in archived artifacts, and rotate the keys
> immediately if it leaves the host (chat, ticket, backup share, etc.).
> Treat the agent containers as secret holders: do not `docker
> inspect` / `docker exec ... env` in shared sessions; if you must
> capture the full environment for troubleshooting, redirect to a
> private tmpfile under `umask 077` and `shred` it when done.

## Deploying the Frag Extension

The frag extension layers Enterprise RAG and HITL LVS tools on top of the base
VSS agent image. Deployment is a two-step Docker build followed by compose up.

> **Environment variables:** All commands use values from the `.env` file at
> `deployments/developer-workflow/dev-profile-lvs/.env`. Edit it before deploying.
> Key variables: `HOST_IP`, `VSS_AGENT_PORT` (default `8000`), `NGC_CLI_API_KEY`,
> `NVIDIA_API_KEY`, `ENTERPRISE_RAG_*`.

### Step 1: Configure the .env file

```bash
nano deployments/developer-workflow/dev-profile-lvs/.env
```

Set at minimum:
- `HOST_IP` — your machine's IP (`hostname -I | awk '{print $1}'`)
- `NGC_CLI_API_KEY` — from https://ngc.nvidia.com/
- `NVIDIA_API_KEY` — from https://build.nvidia.com/
- `VSS_AGENT_CONFIG_FILE=./configs/video_search_frag/config.yml`
- `ENTERPRISE_RAG_VDB_ENDPOINT` — your Milvus endpoint (e.g., `tcp://127.0.0.1:19530`)
- `ENTERPRISE_RAG_COLLECTION_NAMES` — your Milvus collection name

### Step 2: Log in to NGC registry

```bash
echo "$NGC_CLI_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin
```

### Step 3: Build the base agent image

```bash
cd agent
docker build -f docker/Dockerfile -t vss-agent-base .
```

### Step 4: Build the frag extension image

```bash
docker compose \
  -f app/video_search_frag/docker-compose.yml \
  --env-file ../deployments/developer-workflow/dev-profile-lvs/.env \
  build
```

This produces `vss-agent-frag:latest` — the base agent extended with
`video_search_frag` (Enterprise RAG, HITL LVS, PDF report generation).

### Step 5: Deploy with docker compose

```bash
docker compose \
  -f app/video_search_frag/docker-compose.yml \
  -f ../deployments/agents/agent_ui/compose.yml \
  --env-file ../deployments/developer-workflow/dev-profile-lvs/.env \
  --profile bp_developer_lvs_2d \
  up -d
```

Two `-f` flags: the frag compose defines `vss-agent`, the UI compose defines
`metropolis-vss-ui`. They merge into a single deployment.

### Step 6: Verify deployment

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

See [Quick Commands § Health check](#health-check) below to probe the agent.

### Tear down

```bash
docker compose \
  -f app/video_search_frag/docker-compose.yml \
  -f ../deployments/agents/agent_ui/compose.yml \
  --env-file ../deployments/developer-workflow/dev-profile-lvs/.env \
  --profile bp_developer_lvs_2d \
  down
```

### Rebuild after code changes

Always `down` then rebuild and `up` — never just `up -d` alone after changes.

```bash
docker compose \
  -f app/video_search_frag/docker-compose.yml \
  --env-file ../deployments/developer-workflow/dev-profile-lvs/.env \
  build

docker compose \
  -f app/video_search_frag/docker-compose.yml \
  -f ../deployments/agents/agent_ui/compose.yml \
  --env-file ../deployments/developer-workflow/dev-profile-lvs/.env \
  --profile bp_developer_lvs_2d \
  down

docker compose \
  -f app/video_search_frag/docker-compose.yml \
  -f ../deployments/agents/agent_ui/compose.yml \
  --env-file ../deployments/developer-workflow/dev-profile-lvs/.env \
  --profile bp_developer_lvs_2d \
  up -d
```

## When to Use

- User wants to generate a video summary or report using the frag pipeline
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

Show the user the video list and ask which one they want to analyze.

### Step 2: Collect parameters from the user

Ask the user for these four inputs one at a time:

1. **Scenario** — What type of scenario is the video about?
   Example: "warehouse monitoring", "traffic monitoring", "retail store activity"
2. **Events** — What events should be detected? Comma-separated.
   Example: "accident, forklift stuck, workers not wearing PPE, person entering restricted area"
3. **Objects of Interest** — What objects should the analysis focus on? Or "skip" to skip.
   Example: "forklifts, pallets, workers"
4. **Enterprise RAG Query** — An optional question to search the enterprise knowledge base
   for additional context to include in the report. Or "skip" to skip.
   Example: "What are the principles of STCC?"

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
4. **Enterprise RAG Query** — respond with the query from Step 2, or "skip"
5. **Confirmation** — respond with empty string "" to confirm and start processing

Repeat the POST-then-poll cycle for each prompt.

### Step 5: Wait for completion

After the confirmation prompt, the system processes the video. This takes 3-5 minutes.
Keep polling until the status changes from "running" to "completed":

```bash
curl -sS "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/executions/EXECUTION_ID" | python3 -m json.tool
```

Tell the user to wait — this takes 3-5 minutes. Poll every 30 seconds.

### Step 6: Present the results

When status is "completed", the response contains the full report with:
- Detected events with timestamps
- Narrative analysis summary
- Enterprise RAG context (if queried)
- PDF report download link (if available)

Present the report content to the user in a readable format.

## Quick Commands

### Health check

```bash
curl -sS "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/health"
```

### Simple chat query (non-report)

For simple questions that do NOT involve report generation:

```bash
curl -sS -X POST "http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "YOUR_QUESTION_HERE"}]}' | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"
```

## Notes

- LVS reports take 3-5 minutes for a ~3.5 minute video — always tell the user to wait
- Enterprise RAG requires a Milvus vector database with data ingested
- If objects or rag_query are not needed, respond with "skip"
- The HITL response format is always: `{"response": {"type": "text", "text": "value"}}`
- `enable_interactive_extensions: true` must be set in the frag config for HTTP HITL to work
- See also: `video-summarization`, `video-understanding`, `report`, `vios`, `deploy`

bump:1
