# Alerts Profile on OpenShell

Use this reference to deploy the developer **alerts** profile
(`dev-profile-alerts`, blueprint `bp_developer_alerts`) on OpenShell, then
operate it with `vss-manage-alerts`.

Two modes:

| Mode | `MODE` | GPUs | How it works |
|---|---|---|---|
| **verification** | `2d_cv` | 2 | RT-CV + behavior analytics generate alerts; alert-bridge asks RT-VLM only to verify clips |
| **real-time** | `2d_vlm` | 1 | No RT-CV. RT-VLM inspects live video continuously; alert-bridge drives always-on rules |

VLM is **always served by local RT-VLM** (port 8018). There is no standalone
Cosmos NIM. `VLM_NAME_SLUG=none` is required. `VLM_NAME` must be the RT-VLM
`/v1/models` basename `nim_nvidia_cosmos3-nano-reasoner_bf16-final`.

OpenShell default: **remote LLM** (`LLM_MODE=remote`, `LLM_NAME_SLUG=none`) so
verification fits on two GPUs and real-time fits on one. After the stack is
up, load `vss-manage-alerts` rather than inventing Alert-Bridge HTTP.

Warehouse agents (`BP_PROFILE=bp_wh`) are a different stack — follow
[`warehouse.md`](warehouse.md). Edge / three-GPU / local-LLM alerts variants
belong to `vss-deploy-profile`.

Alerts uses `deploy/docker/developer-profiles/dev-profile-alerts/` and the
same `.env` + `generated.env` compose flow as `base` / `lvs` / `search`.

## Mode selection

Match the user request, then write these into `generated.env` (do not mutate
`overrides.env`):

| User says | Mode | `MODE` | `ALERT_AGENT_ALWAYS_ON` | `NEXT_PUBLIC_APP_SUBTITLE` | `RTVI_VLM_KAFKA_ENABLED` |
|---|---|---|---|---|---|
| "alert verification" / "CV-driven" / `2d_cv` | verification | `2d_cv` | `false` | `Vision (Alerts - CV)` | `false` |
| "real-time alerts" / "continuous VLM" / `2d_vlm` | real-time | `2d_vlm` | `true` | `Vision (Alerts - VLM)` | comment out (compose default `true`) |

Also set `VST_NOTIFICATION_CONFIG_PATH` to
`notification_config_${MODE}.json` from the profile overrides.

## GPU layout

**Verification (`2d_cv`) — two GPUs:**

```ini
RT_CV_DEVICE_ID=0
RT_VLM_DEVICE_ID=1
LLM_MODE=remote
LLM_NAME_SLUG=none
VLM_MODE=local
VLM_NAME_SLUG=none
VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final
RTVI_VLM_MODEL_TO_USE=cosmos-reason3
```

**Real-time (`2d_vlm`) — one GPU:**

```ini
RT_VLM_DEVICE_ID=0
LLM_MODE=remote
LLM_NAME_SLUG=none
VLM_MODE=local
VLM_NAME_SLUG=none
VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final
RTVI_VLM_MODEL_TO_USE=cosmos-reason3
```

Real-time must **not** start `vss-rtvi-cv` or `vss-behavior-analytics`.

## Required services

Both modes: `vss-agent`, `vss-agent-ui`, `vss-rtvi-vlm`, `vss-alert-bridge`,
`vss-va-mcp`, `vss-vios-nvstreamer`, `elasticsearch`, `kibana`, `kafka`,
`redis`, `phoenix`.

Verification also: `vss-rtvi-cv`, `vss-behavior-analytics`.

## 1. Preconditions

```bash
REPO="${REPO:-$HOME/video-search-and-summarization}"
test -f "$REPO/deploy/docker/developer-profiles/dev-profile-alerts/overrides.env"
test "$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)" -ge 2   # verification only
docker info >/dev/null
```

`NGC_CLI_API_KEY` is required for RT-VLM (and RT-CV in verification). Never
print it.

## 2. Data directory

Follow [`data-directory.md`](data-directory.md), and also:

```bash
mkdir -p \
    "$VSS_DATA_DIR/data_log/vss_video_analytics_api" \
    "$VSS_DATA_DIR/videos/dev-profile-alerts" \
    "$VSS_DATA_DIR/models" \
    "$VSS_APPS_DIR/engines/gdino" \
    "$VSS_APPS_DIR/engines/rtdetr-its"
chmod -R 777 "$VSS_DATA_DIR/models" "$VSS_APPS_DIR/engines"
```

## 3. Create `generated.env`

```bash
PROFILE=alerts
ENV_SRC=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/.env
ENV_POST=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/overrides.env
ENV_GEN=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/generated.env
cp "$ENV_POST" "$ENV_GEN"
```

Write the mode table and GPU layout above, plus `HOST_IP` / `EXTERNAL_IP` /
`VSS_PUBLIC_*` from the main skill flow. Keep `VSS_AGENT_CONFIG_FILE` at
`/vss-agent/deploy/docker/developer-profiles/dev-profile-alerts/vss-agent/configs/config.yml`.

Hard rules:

- `VLM_NAME_SLUG=none` — alerts `COMPOSE_PROFILES` has no `vlm_*_<slug>` segment.
- `VLM_NAME` must match RT-VLM `/v1/models` or alert-bridge returns HTTP 400.
- Do not co-deploy a standalone Cosmos NIM.
- `RTVI_VLM_KAFKA_ENABLED=false` only for verification. Real-time needs Kafka on.

## 4. Deploy

Same dry-run → normalize → `up -d` sequence as `base` / `lvs` / `search`,
using `$ENV_SRC` then `$ENV_GEN`. Do not use the warehouse industry-profile
directory.

## 5. Readiness

Both modes:

```bash
curl -sf --max-time 15 http://localhost:8000/health
curl -sf --max-time 15 http://localhost:3000/
curl -sf --max-time 15 http://localhost:8018/v1/models
docker ps --format '{{.Names}}' | grep -qx vss-alert-bridge
docker ps --format '{{.Names}}' | grep -qx vss-rtvi-vlm
```

Verification also:

```bash
docker ps --format '{{.Names}}' | grep -qx vss-rtvi-cv
docker ps --format '{{.Names}}' | grep -qx vss-behavior-analytics
```

Real-time also:

```bash
docker ps --format '{{.Names}}' | grep -qx vss-rtvi-cv && echo FAIL || echo "rt-cv absent (correct)"
```

RT-VLM first start downloads `cosmos3-nano-reasoner:bf16-final` (~10–20 min).
Verification also builds RT-CV TensorRT engines (3–5 min).

## 6. Operate with `vss-manage-alerts`

After readiness, load `vss-manage-alerts`. Do not hand-roll Alert-Bridge
`/api/v1/realtime` unless that skill says to.
