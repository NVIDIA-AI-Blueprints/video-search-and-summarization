# TOOLS.md

## Deployment

Deployment is delegated to the VSS Orchestrator MCP server running on the host
at `http://host.openshell.internal:9902/mcp`. Do **not** invoke
`deploy/docker/scripts/dev-profile.sh` directly, do **not** scan for repo paths,
and do **not** prompt the user for `HARDWARE_PROFILE` or `NGC_CLI_API_KEY` —
the MCP server inherits them from the host environment.

If host-side setup is missing (NGC CLI, Docker login to `nvcr.io`, or
`uv sync` of `services/agent/`), tell the user to run the matching cell in
`deploy/docker/scripts/deploy_nemoclaw_vss.ipynb` (the notebook lives on the host, not in the sandbox — do not try to read, list, find, or open it from inside the sandbox; just tell the user). That notebook owns host-side setup; do not try to fix it
yourself.

The orchestrator exposes tools for: listing supported profiles, running
prerequisite checks, generating compose artifacts, bringing deployments up
or down, polling compose status, and inspecting running services. Discover
the exact names and argument schemas via `tools/list` — do not assume.

## Calling MCP tools — REQUIRED format

Always use the `exec` tool with the heredocs below. **Never hand-write JSON
inline** — the brackets always end up wrong. Responses come back SSE-framed
(`event: message\n\ndata: {...}\n\n`); strip the `data: ` prefix before
parsing the JSON.

The MCP handshake is **three** messages, not two: `initialize` (request),
`notifications/initialized` (notification — no `id`, no response body), then
any `tools/list` / `tools/call` request. Skipping the notification triggers
"Received request before initialization was complete" warnings on the server.

### One-time per session: handshake + discover tool names
```bash
# 1. initialize, capture the session id from the response header
SID=$(curl -sN -D /tmp/h.txt -X POST http://host.openshell.internal:9902/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data @- <<'EOF' >/dev/null
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"vss-assistant","version":"0.1.0"}}}
EOF
  grep -i '^mcp-session-id:' /tmp/h.txt | awk '{print $2}' | tr -d '\r')
echo "MCP_SID=$SID"

# 2. send the initialized notification (no id; expect HTTP 202, empty body)
curl -s -X POST http://host.openshell.internal:9902/mcp \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3. discover tool names and input schemas — DO NOT hardcode them
curl -s -X POST http://host.openshell.internal:9902/mcp \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Read tool names and `inputSchema` from the `tools/list` result and use them
verbatim. **Ignore `react_agent`** — it's the workflow's entry function, not
a deployment tool.

### Calling a tool
```bash
curl -s -X POST http://host.openshell.internal:9902/mcp \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data @- <<'EOF'
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"<NAME_FROM_tools/list>","arguments":{...}}}
EOF
```

## Skills

Skill files (`SKILL.md`) are managed by OpenClaw — discover and invoke skills
through `openclaw skills` commands (e.g. `openclaw skills list`,
`openclaw skills <name>`). Do **not** try to `read` / `cat` / `find` `SKILL.md`
paths directly; in particular, paths under
`/usr/local/lib/node_modules/openclaw/skills/` belong to OpenClaw's bundled
core skills (1password, github, etc.) and do **not** contain VSS skills like
`deploy`, `alerts`, or `video-search`. Those live under the plugin install dir
and are reached only via the `openclaw skills` CLI.
