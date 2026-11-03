---
name: incident-start
description: Use to set up secrets and launch the incident profile from a laptop with start.sh, against the kwanz-ws VM over an SSH tunnel (the local mock-backend + vlm-gateway mode is for mock-backend tests only). Not for changing or repairing services on the VM itself - use incident-operate-vm.
license: Apache-2.0
metadata:
  version: "0.1.2"
  profile: "dev-profile-incident"
  tags: "incident startup local vm tunnel"
---
# Incident Start

`start.sh` is the single laptop entry point. It launches `incident-console-v2` on `http://localhost:3200` in both modes. Run everything below from `deploy/docker/developer-profiles/dev-profile-incident/`.

**Never** run `start.sh`, `.scripts/tunnel.sh` or `.scripts/tunnel-check.sh` on `kwanz-ws` itself. They are laptop-side only.

## Mode routing

| User says | Mode | Why |
|---|---|---|
| "run the mock backend", "offline from the VM" (mock-backend tests only; never for UI or backend work, and never as a fallback when the VM is unreachable - stop and report) | `--mode mock` | Mock VST/agent on `:7777` and the credential-holding gateway on `:8600`. SSH is never touched. `--mode local` still works as a deprecated alias. |
| "run it", "run against the VM", "real agent", "full stack", "demo", "frontend work", any UI or backend work, no preference | `--mode vm` (default) | Real native `vss-agent` and Phoenix on `kwanz-ws`. `start.sh` self-heals missing services: it starts stopped ones, pulls missing images, clones a missing checkout, and fails with a reason instead of hanging. |

Details of each path: [`.docs/architecture.md` §2](../../.docs/architecture.md#2-deployment--connectivity).

## Prerequisites

1. **Tools:** `uv`, Node.js/npm, `npm install` in `incident-console-v2` fetches `ffmpeg` and `ffprobe` (the console converts every upload to an H.264 MP4 and reads video frames for evidence crops), so no separate install is needed on the laptop; a system copy on PATH is used only when the bundled one is missing. For vm mode, add Tailscale (connected) and `ssh`.
2. **Secrets file:** `dev-profile-incident/.env.local`, which is untracked and supplied by the captain. Never commit it and never print its values. Check that it holds non-empty values for the names the services read:

   | Needed by | Variables |
   |---|---|
   | console, mock-backend, eval | `INCIDENT_SUPABASE_URL`, `INCIDENT_SUPABASE_SERVICE_ROLE_KEY` (the **finals** project) |
   | eval, database scripts | `INCIDENT_DB_DSN` (finals session pooler, port 5432) |
   | console, mock-backend, eval | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET` |
   | vlm-gateway (mock mode only) | `VLM_GATEWAY_API_KEY`, optional `VLM_GATEWAY_BASE_URL`. Vm mode needs no gateway key: the VM agent reads dashboard chart requests with its own LLM |

   Check names only, without echoing values:
   ```bash
   for v in INCIDENT_SUPABASE_URL INCIDENT_SUPABASE_SERVICE_ROLE_KEY R2_ACCOUNT_ID R2_ACCESS_KEY R2_SECRET_KEY R2_BUCKET VLM_GATEWAY_API_KEY; do
     grep -q "^${v}=." .env.local && echo "ok      $v" || echo "MISSING $v"
   done
   ```
3. **Symlinks:** every service reads its own `.env.local`; local mode passes `mock-backend/.env.local` to the mock backend and `vlm-gateway/.env.local` to the gateway, and stops with the fix command if either is missing. All three links are required:
   ```bash
   for d in incident-console-v2 vlm-gateway mock-backend; do ln -sfn ../.env.local "$d/.env.local"; done
   ```
   If you ran `.githooks/activate.sh` once for the clone, it keeps these links in sync across worktrees and refreshes a worktree's `.env.local` that differs from the main worktree's copy ([`.githooks/README.md`](../../../../../../.githooks/README.md)).

   The Supabase values must name the finals project. Midterm (`rfevukimyftzbxlehwel`) is a frozen read-only record ([`.docs/architecture.md`](../../.docs/architecture.md#two-supabase-projects-one-per-phase)). Mock mode passes these credentials to the mock backend too, and its analyse route writes `mock-run-*` rows into that database.
4. **vm mode only: SSH identity.** Set up `Host kwanz-ws` with `HostName` and your own `User` in `~/.ssh/config`, or export `VSS_SSH_USER` or `VSS_SSH_TARGET`. Resolution is non-interactive. See [`.docs/incident-profile-operations.md` §3](../../.docs/incident-profile-operations.md#3-laptop-to-vm-connectivity--ssh-troubleshooting).

## Instructions

```bash
./start.sh --mode vm        # VM backend (default; use for all UI and backend work)
./start.sh --mode mock      # mock-backend tests only, never for UI or backend work
./start.sh --mode vm --no-console   # VM side only: backend + tunnel, no Next.js console
```

`start.sh` waits on the console. Ctrl-C, closing the terminal or `kill <start.sh pid>` stops it, along with the backgrounded mock, gateway and tunnel, including the servers those wrappers started (no `next-server` or `uvicorn` is left listening). In vm mode, exiting stops nothing on the VM: the shared `vss-agent`, Phoenix and tracking stack keep running for everyone (stop them by hand with `native-services.sh stop` and `tracking-stack.sh stop`, see `incident-operate-vm`).

**Lifecycle (vm mode):** when every expected Docker and native service is already up, `start.sh` skips the backend deploy and goes straight to the tunnel. The person-tracking stack (`ENABLE_TRACKING`, default on) is started on every run, even when the deploy was skipped, and if it fails `start.sh` exits with status 1 before the tunnel opens; `ENABLE_TRACKING=false ./start.sh` skips it.

Environment overrides such as `ENABLE_ANALYTICS`, `VSS_REPO_ROOT`, `INCIDENT_DOCKER_SERVICES` and `INCIDENT_NATIVE_SERVICES` are documented in the header of [`start.sh`](../../start.sh). The vm-mode tunnel forwards only `localhost:8000` to the agent: uploads go from the console straight to R2, so it needs no `:7777` forward (`VSS_VM_IP` matters only to the standalone `.scripts/tunnel.sh`).

## Verify

| Check | local | vm |
|---|---|---|
| Console readiness: `curl -s localhost:3200/api/health` returns only booleans | yes | yes |
| Mock backend: `curl -s 127.0.0.1:7777/health` | yes | - |
| Search route (always-on): `curl -s -X POST 127.0.0.1:7777/api/v1/incidents/search -H "Content-Type: application/json" -d '{"query":"test","top_k":1}'` | yes | - |
| Chat route: `curl -s -X POST 127.0.0.1:7777/api/v1/incidents/chat -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"test"}]}'` | yes | - |
| Gateway: `curl -s 127.0.0.1:8600/health` returns `{"status":"ok"}` | yes | - |
| Tunnel, agent and search: `./.scripts/tunnel-check.sh` or `curl -s localhost:8000/health` | - | yes |

In vm mode `start.sh` starts no local gateway. Dashboard chart requests go to the VM agent (`POST /api/v1/incidents/chart-requests`), which uses its own configured LLM. The console's health check leaves out gateway readiness in vm mode. HTTP 503 from the VM for the first 60-90 s after a cold start is normal.

Then run one upload to report with [`incident-analyze-video`](../incident-analyze-video/SKILL.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `start.sh` aborts at the SSH preflight | Read the `Cause:` / `Fix:` lines it prints (Tailscale logged out or stopped, VM offline in the tailnet, DNS, SSH timeout or refused, login rejected, host key changed). Table: [`.docs/incident-profile-operations.md` §3](../../.docs/incident-profile-operations.md#3-laptop-to-vm-connectivity--ssh-troubleshooting). |
| `start.sh` aborts with "could not register active run lock" | The backend may currently be stopping from another session's exit. Retry once the stop completes. |
| `start.sh` stops with `[vm] ERROR` or "timed out" | The Docker step on the VM failed: exit 10 repo, 11 `generated.env.remote` missing, 12 docker unusable, 13 image pull (NGC login), 14/124 `compose up` failed or stuck (its output shows container states and logs). Tunables: `VSS_PULL_TIMEOUT`, `VSS_COMPOSE_UP_TIMEOUT`, `VSS_NATIVE_START_TIMEOUT`. |
| Tunnel step fails | `start.sh` says whether a laptop port is taken, the SSH forward is blocked (or `VSS_AGENT_HOST` on the VM is bound to a LAN address instead of `127.0.0.1`), the VM address is wrong (set `VSS_VM_IP`), or the agent is down on the VM. |
| Logged in as the wrong VM user | Set `User` under `Host kwanz-ws`, or `export VSS_SSH_USER=<name>`. |
| Port 3200, 7777, 8000 or 8600 already in use | A previous run is still alive. Stop it, or find it with `lsof -i :<port>`. |
| Console health shows Supabase or R2 not configured | A missing variable or broken symlink. Re-run the prerequisite checks. |
| Saving a revision or marking one official fails with "permission denied" while reads work | `.env.local` still points at the frozen midterm project. Set the 3 finals values (and refresh other worktrees' copies). |
| VM services missing after self-heal | Hand off to [`incident-operate-vm`](../incident-operate-vm/SKILL.md). |
