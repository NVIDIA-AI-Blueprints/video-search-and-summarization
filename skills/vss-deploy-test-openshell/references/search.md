# Two-GPU Search Profile on OpenShell

Use this reference to deploy the developer **search** profile
(`dev-profile-search`, blueprint `bp_developer_search`) on a two-GPU
OpenShell host, then operate it with `vss-search-archive`.

This path is intentionally narrow:

- two GPUs
- local RT-CV on GPU 0
- local RT-Embed on GPU 1
- local LLM sharing GPU 1 with RT-Embed, **or** a remote LLM if the user
  asked for one
- **remote VLM** with a local `vss-rtvi-vlm` OpenAI-compatible proxy on GPU 0
  (shares the device with RT-CV)

Full-catalog search variants, local RT-VLM inference, and single-GPU
remote-all layouts belong to `vss-deploy-profile`. After the stack is up,
natural-language archive search belongs to `vss-search-archive`, not this
file.

Search uses the developer-profile directory
`deploy/docker/developer-profiles/dev-profile-search/` and the same
`.env` + `generated.env` compose flow as `base` / `lvs`.

## Required services

- `vss-agent`, `vss-agent-ui`
- `vss-rtvi-cv` (DeepStream perception)
- `vss-rtvi-embed` (Cosmos Embed1)
- `vss-rtvi-vlm` (always present as the media-processing / remote-VLM proxy)
- `elasticsearch`, `logstash`, `kafka`, `redis`, `phoenix`

RT-VLM must stay in the compose graph even when `VLM_MODE=remote`.

## GPU layout

```ini
RT_CV_DEVICE_ID=0
RT_VLM_DEVICE_ID=0
RT_EMBED_DEVICE_ID=1
LLM_DEVICE_ID=1
```

Default OpenShell eval shape (matches the two-GPU search eval):

```ini
VLM_MODE=remote
VLM_NAME_SLUG=none
VLM_MODEL_TYPE=rtvi
VLM_BASE_URL=<remote-vlm-endpoint>          # no trailing /v1
VLM_NAME=<model served there>
RTVI_VLM_ENDPOINT=<remote-vlm-endpoint>/v1
RTVI_VLM_MODEL_TO_USE=openai-compat
RTVI_VLM_MODEL_PATH=none
```

Keep a local LLM unless the user asked for remote. A remote LLM uses
`LLM_MODE=remote` and `LLM_NAME_SLUG=none` the same way warehouse does.

## 1. Preconditions

```bash
REPO="${REPO:-$HOME/video-search-and-summarization}"
test -f "$REPO/deploy/docker/developer-profiles/dev-profile-search/overrides.env"
test "$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)" -ge 2
docker info >/dev/null
```

`NGC_CLI_API_KEY` is required for RT-CV model pulls. `NVIDIA_API_KEY` is
required for the remote VLM. `HF_TOKEN` speeds the first RT-Embed
Cosmos-Embed1 download. Never print those values.

## 2. Data directory

Follow [`data-directory.md`](data-directory.md), and also:

```bash
mkdir -p "$VSS_DATA_DIR/models"
chmod -R 777 "$VSS_DATA_DIR/models"
```

RT-CV downloads detectors into `$VSS_DATA_DIR/models` on first start.
RT-Embed downloads Cosmos-Embed1 from Hugging Face on first start
(15–25 min extra).

## 3. Create `generated.env`

```bash
PROFILE=search
ENV_SRC=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/.env
ENV_POST=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/overrides.env
ENV_GEN=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/generated.env
cp "$ENV_POST" "$ENV_GEN"
```

Write the GPU layout and remote-VLM proxy keys above, plus `HOST_IP` /
`EXTERNAL_IP` / `VSS_PUBLIC_*` from the main skill flow. Probe the remote
VLM with `scripts/probe_remote_models.sh` before `docker compose up`.

## 4. Deploy

Same dry-run → normalize → `up -d` sequence as `base` / `lvs`, using
`$ENV_SRC` then `$ENV_GEN`. Do not use the warehouse industry-profile
directory.

## 5. Readiness

Wait until all of these succeed:

```bash
curl -sf --max-time 15 http://localhost:8000/health
curl -sf --max-time 15 http://localhost:3000/
curl -sf --max-time 15 http://localhost:8018/v1/models
docker ps --format '{{.Names}}' | grep -qx vss-rtvi-cv
docker ps --format '{{.Names}}' | grep -qx vss-rtvi-embed
docker ps --format '{{.Names}}' | grep -qx vss-rtvi-vlm
```

Confirm `generated.env` has `VLM_MODEL_TYPE=rtvi`, `VLM_NAME_SLUG=none`,
`VLM_MODE=remote`, `RTVI_VLM_MODEL_TO_USE=openai-compat`, and
`RTVI_VLM_MODEL_PATH=none`.

## 6. Operate with `vss-search-archive`

After readiness, load `vss-search-archive`. Bootstrap the project-local
CLI from root `AGENTS.md`, then:

```bash
vss configure --base-url http://localhost:7777
vss search run --help
```

Do not hand-roll `/api/v1/search`. Ingest and query recipes live in
`skills/operations/vss-search-archive/SKILL.md`.
