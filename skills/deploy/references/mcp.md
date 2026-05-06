# VSS Deploy via Orchestrator MCP

> **STOP — do not follow the main deploy workflow from SKILL.md.** This is the complete workflow. Ask the user the questions in Step 0 before running any commands.

Deploy or tear down VSS profiles by calling the VSS Orchestrator MCP server. This drives the full lifecycle — check prerequisites, generate compose artifacts, bring the stack up, and poll until ready — without manual docker commands.

**Default MCP URL:** `http://host.openshell.internal:9902/mcp`

## Step 0 — Gather required information

Ask the user for the following before doing anything else. Collect all answers upfront.

**1. Profile** — which VSS profile?

| Value | Description |
|---|---|
| `base` | Standard video Q&A with LLM + VLM |
| `search` | Semantic video search (Cosmos Embed1) |
| `lvs` | Long video summarization |
| `alerts` | Alert detection — also ask for mode below |

**2. Alerts mode** *(only when profile = `alerts`)* — which detection mode?

| Value | Description |
|---|---|
| `verification` | CV-based alert verification (default) |
| `real-time` | VLM-based real-time detection |

**3. GPU assignment** *(defaults: LLM=`0`, VLM=`1` — ask only if the user wants to override)*

- Same index for both → `local_shared` mode
- Different indices → `local` (dedicated) mode

**4. Extra env overrides** *(optional)* — any additional `KEY=VALUE` pairs (e.g. `NGC_CLI_API_KEY=...`, `LLM_BASE_URL=...`)?

**5. MCP URL** *(optional)* — accept default `http://host.openshell.internal:9902/mcp` or specify a custom URL.

---

Set shell variables from the user's answers:

```bash
MCP_URL="http://host.openshell.internal:9902/mcp"  # or user override
VSS_PROFILE="base"            # base | search | lvs | alerts
ALERTS_MODE="verification"    # verification | real-time  (alerts only)
LLM_DEVICE_ID="0"             # GPU index — default 0
VLM_DEVICE_ID="1"             # GPU index — default 1
AGENT_DIR="$HOME/video-search-and-summarization/services/agent"
```

---

## Step 1 — Verify the MCP server is reachable

```bash
cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__profiles \
  --url "$MCP_URL" \
  --transport streamable-http
```

Expected: JSON with `profiles` or `supported_profiles`. If it fails, the server is not running — ask the user to start it from the host:

```bash
cd "$AGENT_DIR"
uv run nat mcp serve \
  --config_file src/vss_agents/orchestrator/vss_orchestrator_mcp_config.yml \
  --host 0.0.0.0 \
  --port 9902 &
```

Retry once. If still unreachable, ask whether the user wants to provide a different MCP URL.

---

## Step 2 — Run prerequisite checks

```bash
cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__prereqs \
  --url "$MCP_URL" \
  --transport streamable-http
```

If any check fails (Docker not running, no GPU, missing NGC credentials), report the failure and stop.

---

## Step 3 — Generate compose artifacts

Build the `env_overrides` JSON array:

| GPU assignment | env_overrides |
|---|---|
| Same device (e.g. both `0`) | `"LLM_DEVICE_ID=0"`, `"VLM_DEVICE_ID=0"`, `"LLM_MODE=local_shared"`, `"VLM_MODE=local_shared"` |
| Different devices (e.g. LLM=`0`, VLM=`1`) | `"LLM_DEVICE_ID=0"`, `"VLM_DEVICE_ID=1"`, `"LLM_MODE=local"`, `"VLM_MODE=local"` |
| Not provided (use defaults) | `"LLM_DEVICE_ID=0"`, `"VLM_DEVICE_ID=1"`, `"LLM_MODE=local"`, `"VLM_MODE=local"` |

Append any extra user-supplied overrides to the array.

**All profiles except `alerts`:**

```bash
cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__docker_generate \
  --url "$MCP_URL" \
  --transport streamable-http \
  --json-args '{
    "profile": "'"$VSS_PROFILE"'",
    "env_overrides": ["LLM_DEVICE_ID=0", "VLM_DEVICE_ID=1", "LLM_MODE=local", "VLM_MODE=local"]
  }'
```

**`alerts` profile — include `alerts_mode`:**

```bash
cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__docker_generate \
  --url "$MCP_URL" \
  --transport streamable-http \
  --json-args '{
    "profile": "alerts",
    "alerts_mode": "'"$ALERTS_MODE"'",
    "env_overrides": ["LLM_DEVICE_ID=0", "VLM_DEVICE_ID=1", "LLM_MODE=local", "VLM_MODE=local"]
  }'
```

Save the `docker_compose_id` from the response — needed for Steps 5 and Tear Down.

---

## Step 4 — Confirm before deploying

Summarize for the user: profile, alerts mode (if applicable), env_overrides, docker_compose_id. Ask **"Ready to deploy? (yes / no)"**.

Skip confirmation if the user said "autonomously" or "non-interactive".

---

## Step 5 — Bring the deployment up

```bash
DOCKER_COMPOSE_ID="<id from Step 3>"

cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__docker_up \
  --url "$MCP_URL" \
  --transport streamable-http \
  --json-args '{"docker_compose_id": "'"$DOCKER_COMPOSE_ID"'"}'
```

Save the `docker_compose_ops_id` from the response.

---

## Step 6 — Poll until deployment completes

Poll every ~30 seconds until `"running": false`:

```bash
OPS_ID="<docker_compose_ops_id from Step 5>"

cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__docker_status \
  --url "$MCP_URL" \
  --transport streamable-http \
  --json-args '{"docker_compose_ops_id": "'"$OPS_ID"'", "tail_lines": 200}'
```

- `"status": "success"` → proceed to Step 7
- `"status": "error"` → report the error and stop
- First deploy pulls images + downloads models — allow 10–20 min.

---

## Step 7 — Report endpoints

```bash
HOST_IP=$(hostname -I | awk '{print $1}')
echo "VSS Agent API : http://$HOST_IP:8000  (Swagger: /docs)"
echo "VSS UI        : http://$HOST_IP:3000"
```

| Profile | Extra endpoint |
|---|---|
| `alerts` | VIOS dashboard at `http://$HOST_IP:30888/vst/` |

---

## Tear Down

```bash
DOCKER_COMPOSE_ID="<id>"

cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__docker_down \
  --url "$MCP_URL" \
  --transport streamable-http \
  --json-args '{"docker_compose_id": "'"$DOCKER_COMPOSE_ID"'"}'
```

Poll `vss_orchestrator__docker_status` with the returned `docker_compose_ops_id` until `running` is `false`.

If the `docker_compose_id` is unknown, list running containers first:

```bash
cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__docker_list \
  --url "$MCP_URL" \
  --transport streamable-http
```

---

## Check Logs

```bash
cd "$AGENT_DIR"
uv run nat mcp client tool call vss_orchestrator__docker_logs \
  --url "$MCP_URL" \
  --transport streamable-http \
  --json-args '{"container_name": "<name>"}'
```

---

## Troubleshooting

| Problem | Resolution |
|---|---|
| MCP server unreachable | Start the server on the host (see Step 1). Confirm port 9902 is not blocked. |
| Prerequisites check fails | Report the specific failure. Do not deploy until resolved. |
| `docker_generate` returns error | Verify profile name and that env_overrides are `KEY=VALUE` strings, not dicts. |
| `docker_up` ops shows error | Read `tail_lines` from `docker_status` — last log lines identify the failing container. |
| `uv` not found | Run `export PATH="$HOME/.local/bin:$PATH"` and retry. |
