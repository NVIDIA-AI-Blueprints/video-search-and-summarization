# Incident Profile Operations & Runbook

This document is the operational guide and technical runbook for the incident profile (`dev-profile-incident`).
It documents deployment topology, runtime procedures, safe operations, SSH connectivity, live VM configuration
quirks, and verification checklists.

---

## 1. Environments and Access

The team shares a remote GPU development workstation (`kwanz-ws`), accessible via Tailscale VPN.

- **Host Hardware:** 2× NVIDIA RTX A6000 (48 GB VRAM each), AMD Threadripper PRO 5975WX (32 cores / 64 threads), 251 GiB RAM, 1.8 TB NVMe storage. VSS uses GPU 0 only ([GPU rule](#5-vm-deployment-configuration-principles-generatedenvremote), item 10).
- **Tailscale Hostname:** `kwanz-ws.tailf3aa43.ts.net` (the LAN address changes with DHCP; see [status](status.md#6-live-vm-configuration-kwanz-ws)).
- **VM Working Directory:** `/srv/rise-up/vss` (root checkout).
- **NGC Credentials on VM:** `/srv/rise-up/.ngc_env` (load via `set -a; source /srv/rise-up/.ngc_env; set +a` or `source .scripts/ngc-env.sh`; `VSS_NGC_ENV` overrides the file).

---

## 2. Native vs. Docker Service Split

Fast-iterating application modules run directly as native processes on `kwanz-ws` rather than inside
Docker containers. This reduces code-change cycles from ~3-minute container rebuilds to ~2-second process restarts.
Phoenix (the trace viewer used for eval) and the person-tracking stack (Kafka, Elasticsearch, Logstash, NvStreamer, perception, video-analytics API; default on, [below](#person-tracking-stack-scriptstracking-stacksh-enable_trackingtrue)) run in Docker. VIOS/VST, HAProxy and Redis are removed from the deployment: `start.sh` neither starts nor checks them ([decision](decisions.md#2026-10-31-vst-is-dropped-from-the-consoles-upload-path-uploads-go-to-r2-only)).

The split, the policy behind it, the ports and the networking of every service are in [`architecture.md`](architecture.md#native-vs-docker-service-split-policy).

### Managing Native Services (`.scripts/native-services.sh`)

`.scripts/native-services.sh` is the VM-side lifecycle manager (`start`, `stop`, `restart`, `status`, `logs`).
It daemonizes processes with `nohup`, stores PID files under `/srv/rise-up/vss/.run/<service>.pid` (`VSS_RUN_DIR` overrides the directory), redirects
standard streams to `/srv/rise-up/vss/.run/<service>.log`, and polls health endpoints before reporting success.

**Multi-user concurrency & permissions:**
- Start operations serialise on `.run/native-services.lock` using `flock`, preventing TOCTOU startup races between teammates. Background daemonised processes close file descriptor 200 so the advisory lock is released upon launcher exit.
- Uses `umask 002` so created log files, PID files, and lock files are group-writable by any teammate in the shared VM group; pre-existing unwritable log and lock files are replaced before launching.
- A healthy `/health` response (`isAlive`) on the service port is treated as already running even if the PID file is missing or unreadable; startup is safely skipped and reported as already running.
- PID files are written atomically (`mktemp` then `mv`) and failed starts only remove the PID file if it matches the current run's PID.
- `status` and `status --porcelain` list only expected services: `vss-agent` by default, with `video-analytics-api` and `behavior-analytics` included only when `ENABLE_ANALYTICS=true` or when explicitly targeted. Output porcelain formatting is preserved for `start.sh` parsing. Tests: `.scripts/test-native-services.sh`.

```bash
# SSH into the workstation
ssh kwanz-ws
cd /srv/rise-up/vss/deploy/docker/developer-profiles/dev-profile-incident/.scripts

# Check service status
./native-services.sh status

# Fast restart after editing agent code (~2 seconds)
./native-services.sh restart vss-agent

# Tail live agent logs
./native-services.sh logs vss-agent
```

### Person-tracking stack (`.scripts/tracking-stack.sh`, `ENABLE_TRACKING=true`)

On by default in vm mode (since 31 Oct 2026): `./start.sh --mode vm` runs `tracking-stack.sh start` on the VM after Phoenix; it keeps running when `start.sh` exits; `tracking-stack.sh stop` stops it by hand. `native-services.sh` starts `vss-agent` with `INCIDENT_TRACKING_ENABLED=true` unless `generated.env.remote` sets it, and the agent needs `ffmpeg` and `ffprobe`, which `native-services.sh` provides ([default stack](#default-analysis-stack-in-vm-mode)).

- **Turn it off:** `ENABLE_TRACKING=false ./start.sh` skips the stack; the agent then reports `tracking.skipped` and the analysis runs as before. To stop the agent trying at all, set `INCIDENT_TRACKING_ENABLED=false` in `generated.env.remote` (ask first) and `./native-services.sh restart vss-agent`.
- **Latency:** an analysis takes about the clip's length plus 10 s longer (31 Oct 2026: 24 s clip 56 s, 49 s clip 79 s, 94 s clip 132 s in total). An agent run is bounded by the model's 300 s timeout (`contracts/inference.json`), so clips over about 3 minutes may time out.

### Default analysis stack in vm mode

Since 1 Nov 2026 one `./start.sh --mode vm` brings up the full default stack: person tracking and the caption pass. Each piece is on by default and can be turned off on its own; with any of them off the analysis still runs without it.

| Piece | Where the default lives | Turn it off |
|---|---|---|
| Tracking stack (perception containers) | `start.sh`: `ENABLE_TRACKING` defaults to `true` in vm mode | `ENABLE_TRACKING=false ./start.sh` (the agent then reports `tracking.skipped`) |
| Tracking in the agent | `native-services.sh`: `INCIDENT_TRACKING_ENABLED=true` | `INCIDENT_TRACKING_ENABLED=false` in `generated.env.remote` (ask first), then `./native-services.sh restart vss-agent` |
| Caption pass (window captions as P1 context, clips over 30 s) | `native-services.sh`: `INCIDENT_CHUNKING_ENABLED=1`; window 20 s and overlap 2 s are the agent defaults (`INCIDENT_CHUNK_WINDOW_SECONDS`, `INCIDENT_CHUNK_OVERLAP_SECONDS`) | `INCIDENT_CHUNKING_ENABLED=0` in `generated.env.remote` (ask first), then restart the agent |
| NanoTrack model files for object tracking | `.scripts/tracker-models.sh`, run by `native-services.sh` before it starts the agent: two `.onnx` files (about 1.8 MB), SHA-256 pinned, into `/srv/rise-up/vss/.tools/tracker-models` (gitignored); the dir is exported as `INCIDENT_OBJECT_TRACKING_MODEL_DIR` unless the env file sets it | Not needed off; without the files object tracking uses MIL. A failed download only warns. Check with `./tracker-models.sh check` (prints each file, its hash and `OK`/`MISSING`/`MISMATCH`); install alone with `./tracker-models.sh install` |
| `ffmpeg` and `ffprobe` for the agent | `.scripts/media-tools.sh`, run by `native-services.sh` before it starts the agent: BtbN static build, SHA-256 checked, into `/srv/rise-up/vss/.tools/ffmpeg/bin` (gitignored), first on the agent's `PATH`; skipped when both are already on `PATH` | Not needed off; without them the agent skips the caption pass and tracking. Check with `./native-services.sh status` (prints both with versions, or `MISSING`) or `./media-tools.sh check`; install alone with `./media-tools.sh install` |

The defaults reach the shared agent only when it starts (`native-services.sh restart vss-agent`, or `start.sh` finding it down); a running agent keeps the environment it started with. Check a running agent with `tr '\0' '\n' < /proc/<pid>/environ | grep -E '^INCIDENT_(TRACKING|CHUNKING)_ENABLED='`. Time and model calls with both on are not measured yet: they are deferred to the end-to-end test after the current PRs land.

On the VM:

```bash
cd /srv/rise-up/vss/deploy/docker/developer-profiles/dev-profile-incident/.scripts
./tracking-stack.sh start               # stage the RT-DETR model if missing, bring up, wait for ready
./tracking-stack.sh status              # perception stream-count, :8081/livez, mdx-* indices, NvStreamer streams
./tracking-stack.sh restart-perception  # only perception-2d-fusion, then wait for ready
./tracking-stack.sh stop                # stop by name; keeps containers, volumes and built engines
```

- **Model staging:** `start` downloads `nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2` (251 MB) with `ngc` (key `NGC_CLI_API_KEY` from the env file) and installs `rtdetr_warehouse_v1.0.2.fp16.onnx` into `$VSS_DATA_DIR/models`. It does nothing when the file is there. A missing `ngc` binary or key stops with a clear error. The SigLIP encoder is fetched by the `perception-2d-init` container; without the RT-DETR file `vss-rtvi-cv` exits at start.
- **First start:** builds TensorRT engines (about 7 to 10 minutes); later starts take about 90 s. Override the wait with `TRACKING_READY_TIMEOUT`.
- **HOST_IP:** Kafka advertises it and NvStreamer builds RTSP URLs from it. The script exports the VM's current address for the run and leaves `generated.env.remote` unchanged.
- **Cost:** GPU 0 holds about 7.6 GB while the perception container is up (5.0 GB right after start, freed only by a restart), the images take about 40 GB (29.9 GB is `vss-rt-cv`), and RAM rises by about 14 GB.
- **HDR clips crash DeepStream** (BT.2020/HLG, as iPhone HDR records): the pipeline quits within 2 s of `stream/add`. Convert HDR to BT.709 before NvStreamer, or skip tracking for it.
- **Pipeline dies, container lives:** after a crash `/api/v1/ready` still says YES. Health is `stream-count` from `get-stream-info`; run `restart-perception` when a stream produces no frames within 40 s of add and `stream-count` is 0 (the agent itself only gives up at 40 s when the count is 0; a clip whose first person appears late is silent until then, so a registered stream is waited on). Compose gives the container only two restarts. `start` and `restart-perception` return when the REST server answers; wait about 30 s more before the first `stream/add` after a restart. `start` also creates the `data_log` bind-mount directories (Elasticsearch, Kafka, API) that `dev-profile.sh` would otherwise make.
- **One stack per host:** the stock compose uses fixed container names, and the video-analytics API container takes port 8081 from the native `video-analytics-api` (`ENABLE_ANALYTICS`); do not run both. `compose.tracking.yml` drops perception's dependency on `sensor-ms`, so `sensor-ms` and `centralizedb` are never started.

### Pruning Retired Docker Images (`incident-operate-vm/scripts/prune-native-images.sh`)

Because `vss-agent`, `video-analytics-api`, and `behavior-analytics` run natively, their old container images
are dead weight on disk. Running `skills/incident-operate-vm/scripts/prune-native-images.sh` on the VM safely removes these unused images
and stopped containers after confirming the native virtual environment (`services/agent/.venv/bin/nat`) exists:

```bash
ssh kwanz-ws "bash /srv/rise-up/vss/deploy/docker/developer-profiles/dev-profile-incident/skills/incident-operate-vm/scripts/prune-native-images.sh"
```

If the native agent virtual environment is not present, the script prompts before proceeding. Use
`--non-interactive` only in an already-authorized automation context:

```bash
ssh kwanz-ws "bash /srv/rise-up/vss/deploy/docker/developer-profiles/dev-profile-incident/skills/incident-operate-vm/scripts/prune-native-images.sh --non-interactive"
```

### Virtual Environment & Editable Agent Reinstall (`services/agent`)

The native agent runs out of `/srv/rise-up/vss/services/agent/.venv` using `nat serve`.
For changes in `services/agent/src` to take effect across native process restarts (`native-services.sh restart vss-agent`), the package must be installed in editable mode (`editable: true` in `.venv/lib/python3.13/site-packages/vss_agents-*.dist-info/direct_url.json`).
Shared contract files, including the agent's prompts from `contracts/prompts/`, are vendored in `services/agent/src/vss_agents/incident_contract/` from `contracts/` and synchronised with `python deploy/docker/developer-profiles/dev-profile-incident/contracts/sync.py` (`--check` mode verifies parity without copying).

Environment variables are captured at process start: editing `generated.env.remote` does not take effect until `native-services.sh restart vss-agent` sources the file. Live environment values can be inspected in `/proc/<pid>/environ`.

**VM Agent Reinstall Procedure:**
The agent's `uv.lock` no longer needs NVIDIA's internal artifactory (`nvdataset` was dropped), but the live VM venv was built by hand and a full `uv sync` would replace it. To relink `vss_agents` editably to the repo code and install dependencies:
```bash
cd /srv/rise-up/vss/services/agent
uv pip install --python .venv/bin/python --no-deps --index-url https://pypi.org/simple -e .
uv pip install --python .venv/bin/python supabase opencv-python-headless setuptools
cd /srv/rise-up/vss/deploy/docker/developer-profiles/dev-profile-incident/.scripts
./native-services.sh restart vss-agent
```
Verify that `.venv/lib/python3.13/site-packages/vss_agents-*.dist-info/direct_url.json` contains `"editable": true` and `/proc/<pid>/environ` contains the Brev Switchyard URLs (`LLM_BASE_URL=https://switchyard-13doh4lsz.brevlab.com`, `VLM_BASE_URL=https://switchyard-13doh4lsz.brevlab.com`).

---

## 3. Laptop-to-VM Connectivity & SSH Troubleshooting

The primary UI (`incident-console-v2`) runs locally on developer laptops. Running `./start.sh --mode vm` is THE canonical way to set up everything from your laptop: it checks the VM deploy state over SSH, automatically self-heals any missing Docker or native services, opens the backgrounded SSH tunnel, and launches the console.

### SSH Port Tunneling (Manual / Standalone)

`start.sh --mode vm` manages the tunnel automatically. It forwards only `localhost:8000` to the agent on the VM loopback, so no VM address is needed. If you need to open or test the tunnel manually for debugging (do **not** run this on `kwanz-ws` itself):

```bash
cd deploy/docker/developer-profiles/dev-profile-incident
./.scripts/tunnel.sh
```

To verify active tunnel ports:
```bash
./.scripts/tunnel-check.sh
```

The forwarded ports and their checks are in [`vm-api-contracts.md`](vm-api-contracts.md#tunnel).

Tunnel listeners bind to the laptop loopback only (ssh `-L` default; do not add `GatewayPorts`). `incident-console-v2` is bound to `127.0.0.1:3200` by `start.sh` and the `dev`/`start` npm scripts, so nothing on the laptop network can reach it.

### SSH Username Resolution & Troubleshooting

`start.sh` (in `vm` mode) and `.scripts/tunnel.sh` source `.scripts/vm-preflight.sh`, which resolves the SSH login target non-interactively with no prompts:
1. **Explicit Target:** If `VSS_SSH_TARGET` is set (e.g. `user@kwanz-ws.tailf3aa43.ts.net`), it is used directly.
2. **Explicit User:** If `VSS_SSH_USER` is set, it overrides the username while using `VSS_SSH_HOST` (default: `kwanz-ws`).
3. **SSH Config User:** If neither is set, the scripts inspect `~/.ssh/config` for `Host kwanz-ws` (via `ssh -G "${VSS_SSH_HOST}"`).
4. **Local Fallback:** Falls back to your local machine `$USER` / `whoami`.

**Mode Isolation & Preflight Check:**
- **Mock Mode (`--mode mock`; `local` is a deprecated alias):** Never sources `.scripts/vm-preflight.sh` and never touches SSH at all.
- **VM Mode (`--mode vm`):** Runs a fast SSH preflight check (`ssh -o BatchMode=yes -o ConnectTimeout=10 "$VSS_SSH_TARGET" true`, one retry on a timeout) before any tunnel, deploy check, env generation, npm install, or UI start. If it fails, `start.sh` aborts with a non-zero exit status, the raw SSH error, the classified cause and the next step (`.scripts/vm-preflight.sh`).

**VM unreachable: causes `start.sh` reports**

| Cause | Detected from | Next step it prints |
|---|---|---|
| Tailscale not logged in | `tailscale status --json` `BackendState` `NeedsLogin` | `tailscale up` / sign in |
| Tailscale stopped, or its daemon not running | `Stopped` / CLI cannot reach `tailscaled` | `tailscale up` / open the app |
| VM offline in the tailnet | the VM's peer entry has `Online: false` | power on the VM or restart its `tailscaled` |
| Host name does not resolve | ssh `Could not resolve hostname` | fix `HostName` in `~/.ssh/config`, MagicDNS, or `VSS_SSH_HOST` |
| SSH timeout | ssh `timed out` | `tailscale ping`; VM off, ACL or firewall |
| SSH server not running | ssh `Connection refused` | `sudo systemctl start ssh` from the VM console |
| No route | `No route to host` / `Network is unreachable` | check Wi-Fi/VPN/Tailscale |
| Login rejected | `Permission denied` | `VSS_SSH_USER`, `ssh-add -l`, authorised key |
| Host key changed | `Host key verification failed` | `ssh-keygen -R <host>` |

**Reachable VM, services down or missing:** `start.sh` lists what is missing and runs `.scripts/vm-ensure-docker.sh` on the VM (piped over SSH, so the laptop copy always runs). It clones the repo if `VSS_REPO_ROOT` has no checkout, stops with instructions if `generated.env.remote` is missing (secrets cannot be generated), rewrites a stale `HOST_IP` there to the VM's current address (timestamped `.bak-` copy first), pulls images that are not on the VM (`docker compose pull --ignore-buildable --include-deps --policy missing`; `mdx-*` images are built by `up`), and runs `docker compose up -d --yes` under `VSS_COMPOSE_UP_TIMEOUT` (default 600 s). `--yes` answers compose's "volume exists but doesn't match configuration. Recreate?" prompt with yes, so volumes and containers whose config changed are recreated instead of start.sh hanging on stdin (the 2026-11-02 hang; `.scripts/tracking-stack.sh` does the same). Bind-volume data on the host is not deleted by the recreate. On a timeout it prints container states and the last Kafka / broker health-check log lines. Its exit codes: 10 repo, 11 env file, 12 docker, 13 pull, 14 up, 124 timeout. Native services start under `VSS_NATIVE_START_TIMEOUT` (default 300 s). A stale `HOST_IP` was the cause of the 2026-10-18 hang: the Kafka broker health check never passed.

**Troubleshooting Gotchas:**
- **SSH Preflight Fails:** `start.sh` prints the cause and next step (table above). Beyond that, ensure your `Host kwanz-ws` entry in `~/.ssh/config` has the correct `HostName` and `User`.
- **Tunnel never comes up:** `start.sh` tells apart a laptop port already in use, a tunnel that exited (SSH connection or forward blocked, or `VSS_AGENT_HOST` on the VM bound to LAN instead of `127.0.0.1`: agent healthy on the VM but unreachable at loopback), and an agent that crashed after starting (read `$VSS_REPO_ROOT/.run/vss-agent.log`). It forwards only `:8000`; the console does not use `vss-haproxy-ingress` (`:7777`).
- **Wrong User Account:** Each teammate has a provisioned account on `kwanz-ws`. Configure `User <your-vm-username>` under `Host kwanz-ws` in `~/.ssh/config` (or set `export VSS_SSH_USER="<your-vm-username>"` in your shell rc). No interactive prompt is shown.

---

## 4. Safe Deployment Principles

1. **Deploy Exclusively via `start.sh` (or Direct Compose on VM):**
   `deploy/docker/scripts/dev-profile.sh` recognizes only stock profiles (`base`, `search`, `lvs`, `alerts`) and must NOT be used directly for the incident profile. `./start.sh --mode mock` and `./start.sh --mode vm` are THE standard way to set up and launch everything from your laptop. On `kwanz-ws` itself, backend appliances are managed via Docker Compose (`docker compose -f compose.yml -f developer-profiles/dev-profile-incident/compose.override.yml --env-file developer-profiles/dev-profile-incident/generated.env.remote up -d <services>`) and native services via `.scripts/native-services.sh`.
2. **NEVER Run `dev-profile.sh down` on `kwanz-ws`:**
   `deploy/docker/scripts/dev-profile.sh down` executes `docker compose down -v` and deletes the entire persistent data directory
   (`VSS_DATA_DIR`), destroying tens of gigabytes of cached model weights. Always use:
   ```bash
   ./.scripts/down.sh
   # Or plain:
   docker compose -p mdx stop
   ```
   `./.scripts/down.sh` stops native services and runs `docker compose -p mdx stop` to stop containers without removing them (containers and their data are kept; restart is `./start.sh --mode vm`). Note that exited orphan containers are no longer cleaned up by `down.sh`; run `docker compose -p mdx down --remove-orphans` manually when a clean slate is wanted (still never `-v`).
3. **No Double Agent:**
   Never start the `vss-agent` Docker container when the native `vss-agent` process is already running on port 8000.
   `start.sh` intentionally excludes `vss-agent` from its `docker compose up` command.
4. **Cold Start & 503 Expectations:**
   Even in remote mode, cold startup requires time for the agent to initialize. An HTTP 503
   during the first 60–90 seconds is normal readiness latency. Verify with `.scripts/health.sh` before modifying
   configuration.
5. **Permanent `unless-stopped` Restart Policy:**
   Upstream compose files configure several appliances (`elasticsearch`, `logstash`, `kibana` among them) with `restart: always`, which makes Docker resurrect them on every host reboot even after a deliberate stop. In `dev-profile-incident/compose.override.yml`, the persistent appliances and stack services are set to `restart: unless-stopped`. `nvstreamer-2d-fusion` keeps its upstream `on-failure` deploy policy, which already leaves it stopped after a reboot. When the stack is deliberately stopped (via `./.scripts/down.sh` or `docker compose -p mdx stop`), containers stay stopped across VM reboots.
6. **Local processes stop with `start.sh` (both modes):**
   The console runs as a background job that `start.sh` waits on, so Ctrl-C, a `kill <pid>` of `start.sh` alone (SIGTERM) and closing the terminal (SIGHUP) all run its cleanup at once. Cleanup stops each local job with every process it started (`uv run` -> `uvicorn`, `npm run dev` -> `next dev` -> `next-server`), found through parent pids (`pgrep -P`), because killing only the wrapper leaves its server listening. `eval/tests/test_start_script.py` checks this with fake `uv`, `npm` and `curl`.
7. **VM backend keeps running after `start.sh` exits (VM mode):**
   Exiting `start.sh` (Ctrl-C, SIGTERM, SIGHUP, error, last session or not) stops only laptop-side processes: the console, local helpers and the SSH tunnel. The shared `vss-agent`, Phoenix and the tracking stack stay up, because several people and workers use them ([decision](decisions.md#2026-11-02-closing-startsh-leaves-the-vm-backend-running)). To stop them deliberately, run `.scripts/native-services.sh stop vss-agent` and `.scripts/tracking-stack.sh stop` on the VM; no script calls these automatically.

---

**Lifecycle in short:** `start.sh --mode vm` skips the backend deploy when every service is already up; the tracking stack is started on every run and a failure there exits 1 before the tunnel opens; exiting stops nothing on the VM.

## 5. VM Deployment Configuration Principles (`generated.env.remote`)

The untracked deployment environment file on `kwanz-ws` (`generated.env.remote`) configures container volume mounts, networking, and remote inference routing. Follow these standing principles when configuring the environment:

1. **Absolute App and Data Directories:** `VSS_APPS_DIR` must point to the absolute path of `deploy/docker` on the host filesystem so Docker Compose `include:` directives resolve correctly. `VSS_DATA_DIR` must point to the persistent data volume on the host.
2. **Dual-IP Network Topology:** `HOST_IP` must be set to the workstation's internal network IP (`.scripts/vm-ensure-docker.sh` detects and updates stale entries automatically) for inter-container communication, while `EXTERNAL_IP` must be `localhost` so response URLs function over developer SSH port tunnels.
3. **Base Config for Incident Profile:** `VSS_AGENT_CONFIG_FILE` must reference the `dev-profile-base` agent configuration; the shipped search configuration lacks the required `report_agent` and `video_report_gen` modules.
4. **Evaluation Directory Guard:** `REPORT_REFERENCE_BASE_DIR` must be defined (e.g. `/tmp`) to satisfy the NAT configuration schema and prevent startup crashes.
5. **Stream Processor Port Alignment:** `STREAM_PROCESSOR_HTTP_PORT` was 10000, the streamprocessing port behind `vss-vios-ingress`. VIOS is removed from the deployment, so it no longer matters.
6. **Remote Endpoint Format** and 7. **Client Type Enforcement:** bare base URLs and `_type: openai` (`LLM_MODEL_TYPE` / `VLM_MODEL_TYPE`); the rules and reasons are in [`architecture.md`](architecture.md#inference-rules--requirements).
8. **Search Embedding Credentials:** Search query embeddings and evidence crop loader embeddings use the NVIDIA-hosted endpoint (`https://integrate.api.nvidia.com/v1/embeddings`) with `NVIDIA_API_KEY` (or `NGC_CLI_API_KEY`) from `generated.env.remote`; the Brev key cannot call `/v1/embeddings`. Can be overridden with `EVIDENCE_EMBEDDING_BASE_URL`, `EVIDENCE_EMBEDDING_MODEL`, and `EVIDENCE_EMBEDDING_API_KEY`. The loader and search route must use the same model (`nvidia/llama-nemotron-embed-vl-1b-v2` at 1024 dimensions).
9. **Loopback Agent Bind:** `VSS_AGENT_HOST` defaults to `127.0.0.1` because the agent has no authentication and holds the database admin key. An old `VSS_AGENT_HOST=0.0.0.0` in `generated.env.remote` must be removed or updated to `127.0.0.1`. The tunnel forwards `-L 8000:127.0.0.1:8000`, which resolves on the VM. Rolling this out needs an agent restart (`native-services.sh restart vss-agent`). The browser-side `vss-ui` (which calls `${HOST_IP}:8000`) is not deployed in this profile.
10. **GPU rule: only GPU 0 is used on `kwanz-ws`; GPU 1 is never used, because it belongs to another user.** Every VSS Docker service and native process is pinned to GPU 0 (`CUDA_VISIBLE_DEVICES=0`, `NVIDIA_VISIBLE_DEVICES=0`, `RT_CV_DEVICE_ID=0`, `RT_EMBED_DEVICE_ID=0`, `RTVI_EMBED_NVIDIA_VISIBLE_DEVICES=0`, `FIXED_SHARED_DEVICE_IDS=0`, `RESERVED_DEVICE_IDS=0` in `.env`; `device_ids: ["0"]` on `streamprocessing-ms`, `sensor-ms` and `rtvi-embed` and both `*_VISIBLE_DEVICES` variables on all five GPU services in `compose.override.yml`; `CUDA_VISIBLE_DEVICES` and `NVIDIA_VISIBLE_DEVICES` defaulted to 0 in `start.sh`, `native-services.sh`, and `vm-ensure-docker.sh`). That covers the services `start.sh` runs (`nvstreamer-2d-fusion`, `perception-2d-fusion` and the other tracking-stack containers, native `vss-agent`) and the stock services it never starts (`rtvi-embed`, `sensor-ms`). Never move a VSS workload to GPU 1, even when GPU 0 is busy ([decision](decisions.md#2026-10-19-pin-all-vss-docker-appliances-and-native-services-to-gpu-0-only)).

> [!NOTE]
> For the current live environment values and deployed model configurations on `kwanz-ws`, see [Current Status & Known Issues (Live VM Configuration Snapshot)](status.md#6-live-vm-configuration-kwanz-ws).

---

## 6. Pre-Demo Checklist

Run through these checks before any live demo or presentation:

- [ ] **Health Endpoint Check:** Call `GET /api/health` on `incident-console-v2` (`curl -s http://localhost:3200/api/health`). It should answer 200 with `status: "ready"` (`services.agent`, `services.gateway` (absent in agent mode, which starts no local gateway), `services.postgrest` and `services.r2`). `degraded` (503) is an active warning: investigate before presenting.
- [ ] **Search Configuration & UX:** Keep search `top_k` set to 5–10 (with the critic off, the default, latency does not grow with `top_k`; if `INCIDENT_SEARCH_CRITIC` is on, each candidate hit adds ~1 s of critic delay spacing). Ensure the frontend displays a loading state during query evaluation.
- [ ] **Recorded Fallback Asset:** Keep a pre-recorded demo video or offline screencast ready in case remote inference or network connectivity degrades during the demo.
- [ ] **Evidence Rows:** Search needs `evidence_crops` rows for each demo video's searchable revision ([what writes them](data.md#evidence_crops)). Confirm a search returns the demo incidents before the demo.

---

## 7. End-to-End Verification Checklist

Verify deployment health against this checklist before declaring a build ready:

- [ ] **Video Ingestion:** Upload an `.mp4` clip and a phone `.MOV` via `incident-console-v2` (`npm install` bundles `ffmpeg` and `ffprobe`). Verify that the upload succeeds,
      the clip is stored in Cloudflare R2 as a classification-neutral `uploads/<original name>.mp4` key in both modes (no hash or UUID in the name; the `.MOV` shows the elapsed conversion time in the **Uploading videos** step and is stored as H.264 MP4 at its original resolution, the H.264 `.mp4` is stored without conversion), `HeadObject` reports a nonzero size, and a playback URL generates.
- [ ] **Automated Report Generation:** Trigger incident analysis. Verify all structured fields populate
      (type, severity, confidence, summary, entities, instruments, assets). Confirm ambiguous footage
      gracefully degrades (e.g. empty persons array, unconfirmed start time).
- [ ] **Persistence Read-Back:** Confirm a job (`GET /api/jobs`) reaches `done`, and `POST /api/analysis` returns 200, only after `videos`, `model_runs`,
      `report_revisions` (the AI revision) can be read back and `videos.filepath` exactly matches the submitted R2 key.
- [ ] **Background Jobs:** Upload two clips, close the tab while one is Analysing, reopen `/` and confirm the Analysis jobs list shows it still running; once finished it leaves the list and its report is in the library. Stop the console mid-job and start it again: after 180 s without a heartbeat the job is queued again and finishes.
- [ ] **Re-Generation:** Re-generate with a different model and a reviewer prompt, and confirm a new AI revision appears in
      History while earlier revisions remain openable.
- [ ] **Pipeline Error Handling:** Induce an invalid video or disconnect the tunnel; verify that the frontend surfaces
      descriptive error messaging without crashing.
- [ ] **Human Revisions:** Edit a report and Save as new version with a note, open an older revision in History and Edit from it, then make one revision
      official. Verify each save creates a new revision with the reviewer's name and time, and that only one is official.
- [ ] **Filtering & Dashboard:** Filter report library by incident type, severity, and date range. Confirm that
      counts match database records and clearing filters restores the full catalog.

---

## 8. Troubleshooting Order & Diagnostics

When troubleshooting unexpected failures:

1. **Check Process Status:**
   ```bash
   ./.scripts/status.sh
   ./.scripts/native-services.sh status
   ```
2. **Verify Agent Health:**
   ```bash
   ./.scripts/health.sh
   # Or directly:
   curl -s http://localhost:8000/health
   ```
3. **Inspect Application Logs:**
   ```bash
   # Native agent logs
   ./.scripts/logs.sh vss-agent

   # Docker appliance logs (Phoenix, plus the tracking stack by default)
   docker logs --tail 100 phoenix
   ```
4. **Handle Partial Deploys:**
   If `start.sh` reports a partial deployment, let its self-healing logic attempt to bring up missing services,
   or inspect the missing service output and restart it manually with `.scripts/native-services.sh restart <svc>`
   or `.scripts/rebuild-svc.sh <service>` (which layers `compose.override.yml`). Do not force-redeploy blindly over inconsistent state.
