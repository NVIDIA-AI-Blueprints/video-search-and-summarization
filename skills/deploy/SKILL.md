---
name: deploy
description: Deploy or tear down any VSS profile. Use when asked to deploy VSS, start VSS, deploy base/alerts/lvs/search, deploy for incident reports, deploy for video summarization, deploy for video search, or tear down VSS.
metadata:
  { "openclaw": { "emoji": "🚀", "os": ["linux"] } }
---

# VSS Deploy

Deploy any VSS profile from a single skill. Match the user's intent to a profile, dry-run, deploy in the background, and monitor.

## Profile Routing

| User says | Profile flag | GPUs | Reference |
|---|---|---|---|
| "deploy vss" / "deploy base" / "deploy quickstart" | `-p base` | 1 (shared) or 2 (dedicated) | `references/quickstart.md` |
| "deploy alerts" / "alert verification" / "real-time alerts" | `-p alerts -m <mode>` | 2 required | `references/alerts.md` |
| "deploy for incident report" | `-p alerts -m verification` | 2 required | `references/alerts.md` |
| "deploy lvs" / "video summarization" / "deploy for video summarization" | `-p lvs` | 1 (shared) or 2 (dedicated) | `references/lvs.md` |
| "deploy search" / "video search" / "deploy for video search" | `-p search` | 2 required | `references/search.md` |

## When to Use

- Deploy VSS / start VSS / bring up the agent
- Deploy a specific profile (base, alerts, lvs, search)
- Deploy VSS for a use case (incident reports, video summarization, video search)
- Tear down a running deployment (`dev-profile.sh down`)

## Before Deploying

1. **Read `TOOLS.md`** — get repo path and hardware from the VSS section. If missing, run BOOTSTRAP first.
2. **NGC CLI & API key** — see [`references/ngc.md`](references/ngc.md) for install, configure, and verify steps. Check `$NGC_CLI_API_KEY` is set.
3. **System prerequisites** — see [`references/prerequisites.md`](references/prerequisites.md) for GPU driver, Docker, and NVIDIA Container Toolkit checks.
4. **Profile-specific prerequisites:**

| Profile | GPU requirement | Extra |
|---|---|---|
| base | 1 GPU (shared) or 2 (dedicated) | — |
| alerts | 2 GPUs required (device 0: RT-CV, device 1: LLM+VLM) | Must confirm mode: `verification` or `real-time` |
| lvs | 1 GPU (shared) or 2 (dedicated) | — |
| search | 2 GPUs required (device 0: RTVI-Embed, device 1: LLM) | VLM forced remote — no local VLM needed |

### Pre-flight Check

Run these before every deploy. **Do not proceed if any check fails.**

```bash
# 1. GPU visible
nvidia-smi --query-gpu=index,name --format=csv,noheader

# 2. NVIDIA runtime registered in Docker
docker info 2>/dev/null | grep -i "runtimes"

# 3. NVIDIA runtime works end-to-end inside a container
docker run --rm --gpus all ubuntu:22.04 nvidia-smi 2>&1 | head -5
```

**If check 2 or 3 fails** (`unknown or invalid runtime name: nvidia`):

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Then re-run check 3 to confirm before deploying.

> Full details: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

## Deploy — Always Dry-Run First

```bash
bash <repo>/scripts/dev-profile.sh up -p <profile> -H <hardware> [flags] --dry-run
```

Show the full output, then ask: **"Looks good — deploy now?"**

### Profile-Specific Flags

**base:**
```bash
# Dedicated GPUs
--llm-device-id 0 --vlm-device-id 1
# Remote LLM/VLM
--use-remote-llm --use-remote-vlm
# (set LLM_ENDPOINT_URL / VLM_ENDPOINT_URL env vars first)
```

**alerts:**
```bash
# Must specify mode — ask user if not provided
-m verification    # CV-triggered, VLM reviews candidate alerts
-m real-time       # VLM continuously processes live video
```

**lvs:**
```bash
# Dedicated GPUs (optional)
--llm-device-id 0 --vlm-device-id 1
```

**search:**
```bash
# No extra flags — VLM is forced remote by the script
```

## Deploy Command

Deploy runs in the background — it pulls images and starts containers (~10–20 min on first run). Start it, then poll for completion.

```bash
set -a && . ~/.ngc/.env && set +a
LOG=/tmp/vss-deploy.log
nohup bash <repo>/scripts/dev-profile.sh up -p <profile> -H <hardware> [flags] \
  > "$LOG" 2>&1 &
echo "Deploy PID $! — logging to $LOG"
```

## Monitor Progress

Poll every ~60s and report to the user:

```bash
# Last 20 lines of deploy log
tail -20 /tmp/vss-deploy.log

# Container status
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Deploy is complete when `docker ps` shows all `mdx-*` containers with status `Up` and port **3000** is exposed.

## After Deploy

| Profile | Agent UI | REST API | Other |
|---|---|---|---|
| base | `:3000` | `:8000` (Swagger at `/docs`) | — |
| alerts | `:3000` | `:8000` | VIOS dashboard `:30888/vst/` |
| lvs | `:3000` | `:8000` | — |
| search | `:3000` | `:8000` | — |

Use workflow skills after deployment:
- **alerts** / **incident-report** → for alert management and incident queries
- **video-search** → for semantic video search
- **video-summarization** → for long video summarization
- **sensor-ops** → for camera/stream management via VIOS
- **video-analytics** → for Elasticsearch queries

## Tear Down

```bash
bash <repo>/scripts/dev-profile.sh down
```

## Troubleshooting

- `unknown or invalid runtime name: nvidia` → NVIDIA Container Toolkit not installed or Docker daemon not restarted — run pre-flight check above
- NGC auth error → re-export `NGC_CLI_API_KEY` or follow [`references/ngc.md`](references/ngc.md)
- GPU detection error → follow [`references/prerequisites.md`](references/prerequisites.md) or prepend `SKIP_HARDWARE_CHECK=true`
- cosmos-reason2-8b crash → must redeploy the full stack (known issue: cannot restart alone)
