# TOOLS.md

## Deployment

Deployment is delegated to the VSS Orchestrator MCP server running on the host
at `http://host.openshell.internal:9902/mcp`. Do **not** invoke
`deploy/docker/scripts/dev-profile.sh` directly, do **not** scan for repo paths,
and do **not** prompt the user for `HARDWARE_PROFILE` or `NGC_CLI_API_KEY` —
the MCP server inherits them from the host environment.

If host-side setup is missing (NGC CLI, Docker login to `nvcr.io`, or
`uv sync` of `services/agent/`), tell the user to run the matching cell in
`deploy/docker/scripts/deploy_nemoclaw_vss.ipynb` (the notebook lives on the host,
not in the sandbox — do not try to read, list, find, or open it from inside the
sandbox; just tell the user). That notebook owns host-side setup; do not try to
fix it yourself.

## Calling MCP tools

Openclaw's built-in MCP client cannot fully handshake with the orchestrator's
`nat mcp serve` transport (a known protocol mismatch: openclaw opens the SSE
GET before establishing a session). As a result, **only**
`vss_orchestrator__docker_list` reliably registers as a native tool — every
other orchestrator tool must be invoked via `curl` from the `exec` tool.

- If `vss_orchestrator__docker_list` is in your tool roster, **prefer it
  natively** — saves a curl round-trip and keeps context small.
- For every other orchestrator tool (`prereqs`, `docker_generate`,
  `docker_up`, `docker_down`, `docker_status`, `docker_logs`, `docker_read`,
  `profiles`), use the curl handshake below.
- **Ignore `react_agent`** — it's the workflow's entry function, not a
  deployment tool.

### Required curl format

Always use the `exec` tool with the heredocs below. **Never hand-write JSON
inline** — the brackets always end up wrong. Responses come back SSE-framed
(`event: message\n\ndata: {...}\n\n`); strip the `data: ` prefix before
parsing the JSON.

The MCP handshake is **three** messages: `initialize` (request),
`notifications/initialized` (notification — no `id`, no response body), then
any `tools/call` request. Skipping the notification triggers "Received
request before initialization was complete" warnings on the server.

### One-time per session: handshake

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
```

You do **not** need to call `tools/list` — the tool names are documented
above, and the schemas are stable. Skipping `tools/list` saves ~5 KB of
context per session.

### Calling a tool

```bash
curl -s -X POST http://host.openshell.internal:9902/mcp \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data @- <<'EOF'
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"vss_orchestrator__<tool>","arguments":{...}}}
EOF
```

## Long-running deploys

`vss_orchestrator__docker_up` and `vss_orchestrator__docker_down` are
**fire-and-return**: they spawn a background thread on the orchestrator and
return a `docker_compose_ops_id` within milliseconds. The underlying
`docker compose up -d --build` may run for 10+ minutes. Track progress with
`vss_orchestrator__docker_status` using the saved `docker_compose_ops_id`.

### Rules

1. **The moment `docker_up` returns, write the `docker_compose_ops_id` to
   `memory/YYYY-MM-DD.md`** (and keep the `docker_compose_id` too) so a
   future turn can recover it if the session drops.
2. **Poll `docker_status` at most every 60 seconds** until `running: false`.
3. **Always pass `"tail_lines": 5` on every poll.** Never raise it. The
   server default of 80 returns tens of KB of `docker compose --build`
   output per poll, which piles into your context and pushes you over the
   LLM's input-token cap. For per-service detail on a failure, use
   `docker_logs` with a specific `container_name` and `tail ≤ 50`.
4. **Deploy is done when** `docker_status` returns `running:false` with
   `status:"success"` (exit_code 0). On `status:"error"`, call `docker_logs`
   for the failing service. On `status:"cancelled"`, the op was preempted
   by a `docker_down`.

### When `docker_status` returns `Unknown docker_compose_ops_id`

The orchestrator keeps ops state in process memory (LRU ~200 entries, not
persisted). If the orchestrator was restarted — or the id was mistyped from
`memory/YYYY-MM-DD.md` — the call returns
`{"status":"error","error":"Unknown docker_compose_ops_id '<id>'."}` in
~100 ms. It does **not** hang.

**Stop-retrying rule. Do not loop on the same id:**

1. First Unknown → re-read the id from `memory/YYYY-MM-DD.md` once and
   call `docker_status` exactly **one** more time (handles fat-finger).
2. Second Unknown for the same id → the id is dead for the rest of this
   session:
   - **Delete the id** from `memory/YYYY-MM-DD.md`.
   - **Tell the user** the orchestrator state was lost (likely a restart)
     and switch to `docker_list` / `docker_logs` for verification.
   - **Never call `docker_status` with that id again** — even on heartbeat.

### After a deploy completes — stop using `docker_status`

Once a deploy is `running:false` with `status:"success"`, the ops_id has no
further value. Delete it from `memory/YYYY-MM-DD.md` and use these instead
for any "is it healthy?" check:

- **`docker_list`** — cheap; returns just container names. Pass
  `{"all_containers": false}` to see only what's currently up. Prefer the
  native `vss_orchestrator__docker_list` tool when it's in your roster.
- **`docker_logs`** — targeted; pass a `container_name` and a small `tail`
  (≤ 50). Cheaper than `docker_status` and gives the actual service-level
  output, not compose orchestration noise.

This pattern is also robust to orchestrator restarts — container state
lives in Docker, not in the orchestrator's process memory.

## Skills

Skill files (`SKILL.md`) are managed by OpenClaw — discover and invoke skills
through `openclaw skills` commands (e.g. `openclaw skills list`,
`openclaw skills <name>`). Do **not** try to `read` / `cat` / `find` `SKILL.md`
paths directly; in particular, paths under
`/usr/local/lib/node_modules/openclaw/skills/` belong to OpenClaw's bundled
core skills (1password, github, etc.) and do **not** contain VSS skills like
`deploy`, `alerts`, or `video-search`. Those live under the plugin install dir
and are reached only via the `openclaw skills` CLI.
