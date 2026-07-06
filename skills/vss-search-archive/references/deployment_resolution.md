# Deployment Resolution for `vss-cli search run`

Use this before every `vss-cli search run` invocation. The host agent owns deployment discovery and must resolve Docker/Helm state into explicit CLI args. The CLI must remain env-free: no process-env fallback and no implicit env-file reads.

## Source Priority

1. **Live runtime in the execution target**: the `vss-agent` container or pod env plus its mounted `$VSS_AGENT_CONFIG_FILE`. This is the best source because `vss-cli search run` runs there and reaches the same in-cluster/container services.
2. **Docker generated env**: `deploy/docker/developer-profiles/dev-profile-search/generated.env`. This is the per-deploy working copy created from `.env` plus user overrides. Use it to resolve/verify values, especially VLM mode, critic, API keys, `HOST_IP`, VST, RTVI, and ES.
3. **Helm rendered state**: the live pod env and mounted configmap. If pod exec is unavailable, use `helm get manifest`, `helm get values`, or `kubectl get configmap` as read-only fallback.
4. **Checked-in defaults**: Docker `.env` or Helm `values.yaml` only before a deployment exists. Do not treat these as the deployed truth after `generated.env` or a running pod/container exists.

## Required Runtime Keys

Pass each resolved value to the CLI as either `--config-env KEY=VALUE` with `--config`, or as explicit runtime flags when no config file is available.

Core non-secret keys:
`HOST_IP`, `ELASTIC_SEARCH_ENDPOINT`, `COSMOS_EMBED_ENDPOINT`, `RTVI_EMBED_BASE_URL`, `RTVI_EMBED_PORT`, `RTVI_EMBED_MODEL`, `RTVI_CV_BASE_URL`, `RTVI_CV_PORT`, `VST_INTERNAL_URL`, `VST_EXTERNAL_URL`, `ELASTIC_SEARCH_INDEX`, `VLM_BASE_URL`, `VLM_NAME`, `VLM_MODEL_TYPE`, `VLM_MODE`, `ENABLE_CRITIC`, `ENABLE_AUDIO`, `EMBED_CONFIDENCE_THRESHOLD`.

Never print, source into a command, or pass API keys through `--config-env` or
`--vlm-api-key`. An authenticated remote VLM must be operated through the
approved secret-managed workflow, outside this skill's shared recipes.

Search-profile keys when present:
`BEHAVIOR_ES_ENDPOINT`, `ELASTIC_SEARCH_INDEX_WILDCARD`, `BEHAVIOR_INDEX`, `BEHAVIOR_INDEX_WILDCARD`, `FRAMES_INDEX`, `FRAMES_INDEX_WILDCARD`, `CRITIC_TIME_FORMAT`, `CRITIC_EVALUATION_COUNT`, `VLM_MAX_FRAMES`, `VLM_MAX_FPS`.

## Docker

Prefer the running container env, then cross-check with `generated.env`.

```bash
docker exec vss-agent sh -lc '
set -eu
printf "VSS_AGENT_CONFIG_FILE=%s\n" "${VSS_AGENT_CONFIG_FILE:-}"
env | sort | grep -E "^(HOST_IP|ELASTIC_SEARCH_ENDPOINT|COSMOS_EMBED_ENDPOINT|RTVI_|VST_|VLM_|LLM_|ENABLE_CRITIC|ENABLE_AUDIO|EMBED_CONFIDENCE_THRESHOLD)=" || true
'
```

If you need the host-side deployment file:

```bash
ENV_GEN=deploy/docker/developer-profiles/dev-profile-search/generated.env
test -s "$ENV_GEN" || ENV_GEN=deploy/docker/developer-profiles/dev-profile-search/.env
```

Only source repo-generated deployment env files, not arbitrary user-provided files:

```bash
set -a
. "$ENV_GEN"
set +a
```

Use `generated.env` to understand what was deployed, but run the final CLI inside `vss-agent` and prefer the container's own env/config path for the actual argv.

## Helm

Prefer pod exec because Helm values can be templated and release-name-dependent.

```bash
kubectl exec -i deploy/vss-agent -- sh -lc '
set -eu
printf "VSS_AGENT_CONFIG_FILE=%s\n" "${VSS_AGENT_CONFIG_FILE:-/etc/vss-agent/config.yml}"
env | sort | grep -E "^(HOST_IP|ELASTIC_SEARCH_ENDPOINT|COSMOS_EMBED_ENDPOINT|RTVI_|VST_|VLM_|LLM_|ENABLE_CRITIC|ENABLE_AUDIO|EMBED_CONFIDENCE_THRESHOLD)=" || true
'
```

If the deployment name is release-prefixed, find it first:

```bash
kubectl get deploy -l app.kubernetes.io/name=vss-agent -o name
```

The mounted config is normally `/etc/vss-agent/config.yml`. If pod exec is not available, inspect the rendered configmap and values:

```bash
kubectl get configmap -l app.kubernetes.io/name=vss-agent -o yaml
helm get values <release> -a
helm get manifest <release>
```

## Media Mode

Derive VLM media behavior from the same resolved env/config:

- `VLM_MODE=local` or `local_shared`: use `--vlm-media-mode video-url`.
- `VLM_MODE=remote` and no audio requirement: use `--vlm-media-mode frame-base64`.
- `VLM_MODE=remote`, `ENABLE_AUDIO=true`, and the VLM name contains `omni`: use `--vlm-media-mode video-base64` and `--vst-clip-enable-audio`.

Never call the VSS agent `/generate` API just to get these values. The host agent resolves deployment state; the CLI executes search directly through `lib.search_core`.
