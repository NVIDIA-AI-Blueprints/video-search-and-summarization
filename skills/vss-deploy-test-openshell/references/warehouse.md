# Two-GPU Warehouse Agents on OpenShell

Use this reference only for the warehouse agents variant requested as:

- `BP_PROFILE=bp_wh`
- `MODE=2d`
- two GPUs
- RT-CV on GPU 0
- always-local RTVI VLM on GPU 1
- remote LLM (no local LLM container)

Other warehouse variants belong to `vss-deploy-profile`.

Warehouse is not a developer profile. It lives under
`deploy/docker/industry-profiles/warehouse-operations/` and must use three
env files plus the root compose file and TURN overlay. Do not run
`dev-profile.sh`, do not invent `dev-profile-warehouse`, and do not invoke
`blueprint-deploy.sh`.

## Required services

The agents variant must deploy at least:

- `vss-agent`, `vss-agent-ui`, `vss-va-mcp`
- `vss-rtvi-cv` (RT-DETR 2D perception)
- `vss-rtvi-vlm` (local VLM on GPU 1)
- `vss-alert-bridge`
- `vss-behavior-analytics`
- `vss-video-analytics-api`
- `vss-vios-nvstreamer`
- `kafka`, `redis`, `phoenix`

The remote LLM must not start a local LLM NIM.

## 1. Preconditions

Set `REPO` to the repository root and verify this is a two-GPU host:

```bash
REPO="${REPO:-$HOME/video-search-and-summarization}"
test -f "$REPO/deploy/docker/compose.yml"
test "$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)" -ge 2
docker info >/dev/null
```

The harness supplies `NGC_CLI_API_KEY` and `NVIDIA_API_KEY`; never print
either value. Authenticate without placing the key on the command line:

```bash
printf '%s' "${NGC_CLI_API_KEY:?NGC_CLI_API_KEY is required}" |
  docker login --username '$oauthtoken' --password-stdin nvcr.io
```

## 2. Acquire warehouse app data

Prefer an existing `WAREHOUSE_APP_DATA_DIR`. Otherwise download the exact
resource named by `WAREHOUSE_APP_DATA_NGC` (the OpenShell eval supplies
`nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0`):

```bash
APP_ROOT="${RUNNER_TEMP:-/tmp}/warehouse-app-data"
mkdir -p "$APP_ROOT"

if [ -n "${WAREHOUSE_APP_DATA_DIR:-}" ]; then
  VSS_DATA_DIR="$WAREHOUSE_APP_DATA_DIR"
else
  : "${WAREHOUSE_APP_DATA_NGC:?WAREHOUSE_APP_DATA_NGC is required}"
  command -v ngc >/dev/null
  (
    cd "$APP_ROOT"
    ngc registry resource download-version "$WAREHOUSE_APP_DATA_NGC"
  )
  DOWNLOADED="$(find "$APP_ROOT" -maxdepth 1 -type d \
    -name 'vss-warehouse-app-data_v*' -printf '%T@ %p\n' |
    sort -nr | awk 'NR==1 {print $2}')"
  test -n "$DOWNLOADED"
  TARBALL="$(find "$DOWNLOADED" -maxdepth 1 -type f -name '*.tar.gz' -print -quit)"
  if [ -n "$TARBALL" ] && [ ! -d "$DOWNLOADED/vss-warehouse-app-data" ]; then
    (cd "$DOWNLOADED" && tar -xf "$TARBALL")
  fi
  VSS_DATA_DIR="$DOWNLOADED/vss-warehouse-app-data"
fi

test -d "$VSS_DATA_DIR/videos"
mkdir -p "$VSS_DATA_DIR/models" "$VSS_DATA_DIR/data_log"
chmod -R a+rwx "$VSS_DATA_DIR/models" "$VSS_DATA_DIR/data_log"
export VSS_DATA_DIR
```

## 3. Create `generated.env`

Never modify the checked-in `.env` or `overrides.env`.

```bash
cd "$REPO/deploy/docker"
WH="industry-profiles/warehouse-operations"
cp "$WH/overrides.env" "$WH/generated.env"
ENV_GEN="$PWD/$WH/generated.env"
```

Update or append the following values in `generated.env`. Preserve the
literal `${COMPOSE_PROFILES_WH_2D}` expression in the file because the
verifier checks the selected service-list variable.

```ini
COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_2D}
MODE=2d
BP_PROFILE=bp_wh
STREAM_TYPE=kafka
SAMPLE_VIDEO_DATASET=nv-warehouse-4cams
NUM_STREAMS=4
DATASET_TYPE=real
HARDWARE_PROFILE=H200
RT_CV_DEVICE_ID=0
RT_VLM_DEVICE_ID=1
LLM_MODE=remote
LLM_NAME_SLUG=none
LLM_MODEL_TYPE=nim
LLM_BASE_URL=https://integrate.api.nvidia.com
LLM_NAME=nvidia/nemotron-3.5-lightning-30b-a3b
VLM_MODE=none
VLM_NAME_SLUG=none
VLM_MODEL_TYPE=rtvi
VLM_BASE_URL=http://rtvi-vlm:8000
RTVI_VLM_ENDPOINT=http://rtvi-vlm:8000/v1
VSS_APPS_DIR=<repo>/deploy/docker
VSS_DATA_DIR=<resolved app-data directory>
```

Replace `<repo>` and `<resolved app-data directory>` with their actual
absolute paths. Write `NVIDIA_API_KEY` and `NGC_CLI_API_KEY` from the
corresponding environment variables without echoing their values. Also set:

```ini
HOST_IP=<primary host IP>
EXTERNAL_IP=<primary host IP>
HAPROXY_HOST_PORT=7777
HAPROXY_PORT=7777
VSS_PUBLIC_HTTP_PROTOCOL=http
VSS_PUBLIC_WS_PROTOCOL=ws
VSS_PUBLIC_HOST=<primary host IP>
VSS_PUBLIC_PORT=7777
```

Before deployment, probe the remote endpoint and selected model with
`"$REPO/skills/vss-deploy-test-openshell/scripts/probe_remote_models.sh"`.
`LLM_BASE_URL` is the endpoint root without `/v1`; the VSS agent appends
`/v1`.

## 4. Resolve the service list

Some Compose versions do not recursively expand env-file values. Resolve and
export only the required selectors:

```bash
eval "$(
  set -a
  . "$WH/.env"
  . "$WH/generated.env"
  set +a
  printf 'COMPOSE_PROFILES=%q\nCOMPOSE_PROJECT_NAME=%q\n' \
    "$COMPOSE_PROFILES" "${COMPOSE_PROJECT_NAME:-vss}"
)"
export COMPOSE_PROFILES COMPOSE_PROJECT_NAME
case "$COMPOSE_PROFILES" in
  ''|*'${'*) echo "COMPOSE_PROFILES did not resolve" >&2; exit 1 ;;
esac
```

## 5. Dry-run and deploy

Use this exact shape for both operations:

```bash
COMPOSE=(
  docker compose
  -f compose.yml
  -f services/infra/compose-no-turn-tcp-relay.yml
  --env-file containers.env
  --env-file "$WH/.env"
  --env-file "$WH/generated.env"
)

"${COMPOSE[@]}" config > resolved.yml
test "$("${COMPOSE[@]}" config --services | wc -l)" -gt 0
"${COMPOSE[@]}" up -d --pull always --build
```

In an autonomous or non-interactive eval, proceed without asking for
confirmation. Otherwise show the resolved service list first.

## 6. Readiness

Cold image and model pulls can take tens of minutes. Do not declare success
until all of these pass:

```bash
curl -sf --max-time 15 http://localhost:8000/health
curl -sf --max-time 15 http://localhost:3000/
curl -sf --max-time 15 http://localhost:8081/livez

for name in \
  vss-agent vss-agent-ui vss-rtvi-cv vss-rtvi-vlm vss-alert-bridge \
  vss-behavior-analytics vss-video-analytics-api vss-vios-nvstreamer \
  kafka redis phoenix
do
  docker ps --format '{{.Names}}' | grep -qx "$name"
done
```

Confirm `generated.env` still has `LLM_MODE=remote`,
`LLM_NAME_SLUG=none`, and
`COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_2D}`. Confirm no local LLM NIM
container was started. An init container at `Exited (0)` is successful; an
unhealthy or non-zero init container is a deployment failure.
