# TOOLS.md

## Sandbox host alias

Inside the openshell/nemoclaw sandbox, `HOST_IP` is the sandbox host
alias — the only hostname the egress policy whitelists for VSS backend
ports. Skills should curl `${HOST_IP}` for every runtime call — never
`localhost` and never a literal IP — so the same skill works in-sandbox
and on bare metal.

`/sandbox/.bashrc` is root-owned and read-only in this sandbox, so
`HOST_IP` is **not** persisted to a shell init file. Instead, the
"Every Session" checklist in `AGENTS.md` runs the exports in `ENV.md`
at session start. If `echo $HOST_IP` is empty in any new shell or after
a session/connect restart, re-run the exports in `ENV.md`. `ENV.md` is
the single source of truth for the value — do not hardcode it
elsewhere.

The sandbox's egress policy whitelists the alias on a fixed set of VSS
backend ports. The policy file lives on the host (outside the sandbox),
so you can't grep it from here. A LAN IP, `localhost`, or a port not on
the whitelist returns `policy_denied`. If a curl fails that way, tell
the user the port needs to be added to the host-side policy and
re-applied. Do not try to bypass.

## Preset proxy and harmless warnings

Two things you will see in this sandbox that are **not** problems:

- **`http_proxy=http://10.200.0.1:3128`** is preset in the sandbox env.
  `no_proxy` covers `localhost,127.0.0.1,::1,10.200.0.1` but **not**
  `host.openshell.internal`, so every VSS backend curl is proxied
  through `10.200.0.1:3128`. This is the expected path and works with
  the egress policy. Do not unset `http_proxy` or add `host.openshell.internal`
  to `no_proxy` — the proxy is how traffic legitimately exits the sandbox.

- **`/bin/bash: cannot create /proc/self/oom_score_adj: Permission denied`**
  appears at bash startup. The sandbox restricts `/proc/self/*` writes;
  bash tries to set its OOM priority, fails, and continues normally.
  Cosmetic only — ignore it.

## HTTP-response curl checks

When testing whether a VSS backend port is reachable, do **not** use
`curl -f`. Several VSS endpoints (orchestrator MCP, agent API) only
expose specific routes and return `404` from `GET /` even when fully
healthy — `-f` treats that as failure. Use:

```bash
curl -s -o /dev/null --max-time 5 "http://${HOST_IP}:<port>/"
```

curl exits 0 when *any* HTTP response is received (network/DNS/policy
all work) and non-zero only on real failures (DNS miss, connection
refused, `policy_denied`, timeout). For health endpoints that promise
a 2xx, use `-f`; for "is the server up at all", omit it.

### Orchestrator reachability check

Used by `BOOTSTRAP.md` Step 1 and any time you want to confirm the
sandbox can reach the host:

```bash
curl -s -o /dev/null --max-time 5 "http://${HOST_IP}:9988/" \
  && echo "host alias reachable"
```

Do **not** add a `getent hosts "${HOST_IP}"` precondition. In this
sandbox all VSS backend traffic goes through `http_proxy`; the proxy
resolves the hostname remotely, and the sandbox itself often has no
local `/etc/hosts` or DNS entry for `host.openshell.internal`. `getent`
would fail even when the path is fully healthy, producing false
negatives. The curl alone is sufficient — it exercises the same proxied
path skills use.

If it doesn't print `host alias reachable`, the `vss-backend` egress
policy isn't applied to this sandbox or the orchestrator isn't running
on the host. Stop and tell the user.

## Deployment

Deployment is delegated to the VSS Orchestrator MCP server at
`http://host.openshell.internal:9988/mcp`. Do **not** invoke
`deploy/docker/scripts/dev-profile.sh`, scan for repo paths, or prompt the
user for `HARDWARE_PROFILE` / `NGC_CLI_API_KEY` — the MCP server inherits
them from the host environment.

If host-side setup is missing (NGC CLI, Docker login to `nvcr.io`, or
`uv sync` of `services/agent/`), tell the user to run the matching cell in
`deploy/docker/scripts/deploy_nemoclaw_vss.ipynb`. The notebook lives on the
host, not in the sandbox — do not try to read, list, find, or open it from
inside the sandbox.

## Calling MCP tools

Openclaw's built-in MCP client can't fully handshake with the orchestrator's
`nat mcp serve` (protocol mismatch: openclaw opens the SSE GET before
establishing a session). Only **`vss_orchestrator__docker_list`** reliably
registers as a native tool. Prefer it natively when present. Every other
orchestrator tool (`prereqs`, `docker_generate`, `docker_up`, `docker_down`,
`docker_status`, `docker_logs`, `docker_read`, `profiles`) must be invoked
via `curl` from the `exec` tool. Ignore `react_agent` — it's the workflow's
entry function, not a deployment tool.

### Handshake (once per session)

Always use heredocs from the `exec` tool — never hand-write inline JSON.
Responses are SSE-framed (`event: message\n\ndata: {...}\n\n`); strip the
`data: ` prefix before parsing. The handshake is **three** messages:
`initialize`, then `notifications/initialized` (no `id`, no response body),
then your `tools/call` requests. Skipping the notification triggers
"Received request before initialization was complete" on the server. Do
**not** call `tools/list` — the tool names below are stable and the schema
blob costs ~5 KB of context per session.

```bash
# 1. initialize, capture the session id
SID=$(curl -sN -D /tmp/h.txt -X POST http://host.openshell.internal:9988/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data @- <<'EOF' >/dev/null
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"vss-assistant","version":"0.1.0"}}}
EOF
  grep -i '^mcp-session-id:' /tmp/h.txt | awk '{print $2}' | tr -d '\r')

# 2. send initialized notification (no id; expect HTTP 202, empty body)
curl -s -X POST http://host.openshell.internal:9988/mcp \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","method":"notifications/initialized"}'
```

### Calling a tool

```bash
curl -s -X POST http://host.openshell.internal:9988/mcp \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data @- <<'EOF'
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"vss_orchestrator__<tool>","arguments":{...}}}
EOF
```

## Mapping user intent to tool chains

Read the user's verb and stop at the boundary they asked for. Do not chain
past it. If phrasing is ambiguous between "generate" and "deploy", **ask
once** — recovering from an unwanted compose-up is far worse than a
clarifying question.

| User says | Tool chain | Do NOT also run |
|---|---|---|
| "generate artifacts for `<profile>`" | `docker_generate` → return `docker_compose_id` | `docker_up`, polling |
| "read artifacts for `<id>`" | `docker_read` | — |
| "deploy `<profile>`" / "bring up" / "start" | `docker_generate` → `docker_up` → poll `docker_status` | — |
| "up `<id>`" (id already exists) | `docker_up` → poll `docker_status` | re-run `docker_generate` |
| "stop `<profile>`" / "tear down" | `docker_down` → poll `docker_status` | — |
| "check status" (in-flight ops_id known) | `docker_status` (`tail_lines: 5`) | — |
| "what's running" / "is everything healthy" | `docker_list` (+ `docker_logs` per container) | `docker_status` |

## Long-running deploys

`docker_up` and `docker_down` are **fire-and-return**: they spawn a
background thread and return a `docker_compose_ops_id` within milliseconds.
The underlying `docker compose up -d --build` may run for 10+ minutes —
track progress by polling `docker_status` with that ops_id.

Rules:

1. **Save the `docker_compose_ops_id`** (and `docker_compose_id`) to
   `memory/YYYY-MM-DD.md` the moment `docker_up` returns, so a future turn
   can recover if the session drops.
2. **Poll `docker_status` with `"tail_lines": 10`** until `running: false`,
   at the cadence the server tells you in the `docker_up` / `docker_down`
   response's `recommended_poll_interval_s`:
   - `up` ops: **every 60 seconds** (long pulls/builds).
   - `down` ops: **every 10 seconds** (typically finishes in seconds).
   Never raise the tail — the server default of 80 returns tens of KB of
   compose output per poll and will push you over the LLM input-token cap.
   For per-service detail on a failure, use `docker_logs` with a specific
   `container_name` and `tail ≤ 50`.
3. **Done** = `running:false` + `status:"success"` (`exit_code:0`).
   `status:"error"` → call `docker_logs` for the failing service.
   `status:"cancelled"` → the op was preempted by a `docker_down`.

### Unknown `docker_compose_ops_id`

The orchestrator keeps ops state in process memory (LRU ~200 entries, not
persisted). After a restart — or with a mistyped id — `docker_status`
returns `{"status":"error","error":"Unknown docker_compose_ops_id '<id>'."}`
in ~100 ms. It does **not** hang.

**Stop-retrying rule:**

1. First Unknown → re-read the id from `memory/YYYY-MM-DD.md` and retry
   exactly **one** more time (handles fat-finger).
2. Second Unknown for the same id → the id is dead. Delete it from
   `memory/YYYY-MM-DD.md`, tell the user the orchestrator state was lost
   (likely a restart), and switch to `docker_list` / `docker_logs`. Never
   call `docker_status` with that id again.

### After a deploy completes

Once `running:false` with `status:"success"`, the ops_id has no further
value. Delete it from `memory/YYYY-MM-DD.md`. For any subsequent "is it
healthy?" check, use:

- **`docker_list`** — cheap, just container names. `{"all_containers": false}`
  for currently-up only. Prefer the native `vss_orchestrator__docker_list`
  tool if it's in your roster.
- **`docker_logs`** — targeted, per-container, small `tail` (≤ 50).

These are also robust to orchestrator restarts since container state lives
in Docker, not in the orchestrator's process memory.

## Skills

Skills are managed by OpenClaw — discover and invoke them via the
`openclaw skills` CLI (e.g. `openclaw skills list`, `openclaw skills <name>`).
Do **not** `read` / `cat` / `find` `SKILL.md` paths directly. Paths under
`/usr/local/lib/node_modules/openclaw/skills/` are OpenClaw's bundled core
skills (1password, github, etc.) and do not contain VSS skills like
`deploy`, `alerts`, or `video-search` — those live under the plugin install
dir and are reachable only via the CLI.
