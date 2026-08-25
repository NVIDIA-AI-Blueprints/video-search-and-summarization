# Deploy troubleshooting

Use this file first when a VSS deploy, runtime probe, `/generate` request, or
skill handoff fails. It consolidates the cross-profile failure modes, the
common-error quick reference, and the diagnostic procedures. After identifying
the failure class here, continue in the matching profile reference:

- `base.md` for base profile agent/VLM/VIOS failures.
- `lvs-profile.md` for long-video summarization and `vss-lvs` / `vss-rtvi-vlm` failures.
- `search.md` for Cosmos Embed1, Elasticsearch, and search-profile failures.
- `alerts.md` for alerts profile failures.
- `warehouse-debug.md` for warehouse profile stream, perception, and analytics failures.

## Quick Triage

Run these checks before changing configuration:

```bash
docker compose -f "$REPO/deploy/docker/resolved.yml" ps
grep -n '\${' "$REPO/deploy/docker/resolved.yml" | head -20
docker logs vss-agent --tail 200
```

If `resolved.yml` does not exist, return to `SKILL.md` Step 3 and run the compose dry-run before deploying.

## Failure Mode Table

| Symptom | Grep / check | Likely cause | Corrective action |
|---|---|---|---|
| REST call / endpoint returns connection refused | `curl -sf http://<host>:<port>/docs` or `/health`; `docker compose ps` | Target microservice is not running — crashed, never started, or wrong port. | Probe `/docs` or `/health`; if down, check the container logs, then redeploy via `vss-deploy-profile` or the matching `vss-deploy-*` skill. |
| `resolved.yml` contains `${...}` | `grep -n '\${' "$REPO/deploy/docker/resolved.yml"` | Compose did not see required env values such as the selected `COMPOSE_PROFILES_WH_*` list or the `LLM_MODE` / `VLM_MODE` model selectors. This can leave the deployment empty or omit the selected NIM. | Fix the missing values in the profile `generated.env`, regenerate `resolved.yml`, re-run the grep check, then deploy. Full procedure under "Unexpanded `${...}`" below. |
| `docker compose up` says no `resolved.yml` | `test -f "$REPO/deploy/docker/resolved.yml"` | The dry-run step was skipped. | Run `docker compose --env-file "$ENV_SRC" --env-file "$ENV_GEN" config > "$REPO/deploy/docker/resolved.yml"` first. |
| NIM container is up but `/generate` or model calls time out | `docker logs <nim-container> --tail 200` and `curl -sf http://<host>:<port>/v1/models` | NIM cold start or model still loading. | Keep polling `/v1/models` or the service health endpoint before retrying the agent request. Do not restart a loading NIM unless logs show a hard failure. |
| `CUDA out of memory` | Search `docker logs <container> 2>&1` for `out of memory`. | LLM, VLM, RT-VLM, or embedding service is too large for the selected GPU placement. | Follow the profile sizing reference. Typical fixes are lowering `NIM_KVCACHE_PERCENT`, lowering `RTVI_VLLM_GPU_MEMORY_UTILIZATION`, lowering max model length / max sequences, reducing streams, switching one side to remote mode with user approval, or freeing GPUs via `docker compose down`. |
| Container exits with code `137` or `OOMKilled` | Search `docker inspect <container>` for `OOMKilled`. | Host RAM or GPU memory pressure. | Check `free -h` and `nvidia-smi`. Reduce workload/model memory, free memory, or pick a larger host/profile placement. |
| RT-VLM/NIM aborts at **startup** — `Engine core initialization failed` / `Failed to load VLM` / `Free … less than desired GPU memory utilization` (distinct from runtime OOM/137) | `docker logs vss-rtvi-vlm --tail 100` for `less than desired` / `Free GPU memory` | On **unified-memory edge** (DGX Spark, AGX/IGX Thor) the GPU fraction (`RTVI_VLLM_GPU_MEMORY_UTILIZATION` / `NIM_GPU_MEM_FRACTION`) asks for more than is actually free in the shared pool. | Compute the fraction against free and leave ≥ 0.2 reserve (sum of co-resident fractions ≤ 0.8) — see [`edge.md` § Unified-memory GPU budget](edge.md#unified-memory-budget). Defaults are LLM 0.4 + RT-VLM 0.4; lower by `0.05` if free is tighter. |
| `authentication required`, `401`, or image pull fails from `nvcr.io` | Search `docker compose logs 2>&1` for `authentication required`, `unauthorized`, `401`, or `nvcr.io`. | Missing, invalid, or expired `NGC_CLI_API_KEY`. | `docker login nvcr.io` and re-export `NGC_CLI_API_KEY`, run the NGC checks in `ngc.md`, then retry login/pull or redeploy. |
| Edge LLM (`nvidia-nemotron-nano-9b-v2-fp8`) exits or never reaches ready | `docker logs nvidia-nemotron-nano-9b-v2-fp8 --tail 200` and `curl -sf http://localhost:30081/health` | Missing NGC credentials, image pull/access failure, or too large a GPU fraction for unified memory. | Follow `edge.md`: verify `NGC_API_KEY`, then lower `--gpu-memory-utilization` in `services/nim/nvidia-nemotron-nano-9b-v2-fp8/compose.yml` by `0.05` and redeploy. This is raw vLLM, so `NIM_*` keys have no effect and health is `/health`. |
| Remote LLM/VLM returns HTTP `401` | Search `docker logs vss-agent --tail 200` for `401`, `unauthorized`, or `authentication`. | Missing or invalid remote endpoint API key, usually `NVIDIA_API_KEY`. | Verify the endpoint key and model name, update `generated.env`, regenerate `resolved.yml`, and redeploy affected services. |
| Remote LLM/VLM returns HTTP `5xx` | Search `docker logs vss-agent --tail 200` for `5xx`, `InternalServerError`, `BadGateway`, or `ServiceUnavailable`. | Remote endpoint unavailable, overloaded, wrong model, or transient provider failure. | Confirm endpoint URL and model name. Retry after the endpoint is healthy, or switch backend placement with user approval. |
| LVS remote VLM hangs or OOMs | Check `VLM_MODE`, `RTVI_VLM_MODEL_PATH`, and `RTVI_VLM_ENDPOINT` in `$ENV_GEN`. | `VLM_MODE=remote` was set but `RTVI_VLM_MODEL_PATH` still points to local weights, so RT-VLM tries to load and proxy. | Set `RTVI_VLM_MODEL_PATH=none`, ensure `RTVI_VLM_ENDPOINT=<endpoint>/v1`, regenerate `resolved.yml`, and redeploy `vss-rtvi-vlm` / `vss-lvs`. |
| Standalone edge vLLM alternative fails to pull weights | `curl -sf -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/whoami-v2` | Missing, invalid, or unauthorized `HF_TOKEN` for gated Hugging Face weights. Only the standalone small-model alternative in `edge.md` needs `HF_TOKEN`. | Set a valid `HF_TOKEN` with model access, rerun the `edge.md` verification, and restart the standalone vLLM container. |
| Edge agent produces planning text instead of tool calls | Search `docker logs vss-agent --tail 200` for `[USER]` or missing tool calls. | `config_edge.yml` is in use; its simplified small-model prompt has no explicit tool-call routing rules. | The in-tree edge LLM uses `config.yml`, not `config_edge.yml` — see `edge.md`, then redeploy/restart the agent. |
| WebSocket query returns `error_message` | `docker logs vss-agent --tail 200` | LLM or VLM backend is not healthy or not reachable from the agent container. | Check model service `/v1/models`, verify `LLM_BASE_URL` / `VLM_BASE_URL` in `resolved.yml`, then restart/redeploy the affected service. |
| Empty report or empty video answer | `docker logs vss-agent --tail 200` | VLM unreachable, bad VST URL, missing video ingest, or backend still cold. | Verify VST upload/listing, VLM `/v1/models`, and agent env URLs. Retry after health checks pass. |
| `video_understanding` returns HTTP `500` (often retried 3×) though VLM `/v1/models` passed | `docker logs vss-agent --tail 200` for `fetch_video_async` / `TimeoutError`; then probe VST **from inside the VLM container** (command in section below) | Bridge-networked VLM/LLM NIM can't reach the host-mode VST (`:30888`) to download the clip — the host firewall (ufw) blocks the Docker bridge subnet. NIM is healthy; the failure is the video fetch, not inference. | Allow the bridge subnets to reach the host — see "VLM `500` / `fetch_video_async TimeoutError`" below. **Do not disable ufw.** |
| Search RT-VLM remote proxy: MP4 re-encode fails (`libavcodec` missing, `VideoWriter_fourcc`, or `MP4 encoding failed`) | `docker logs vss-rtvi-vlm --tail 300` and `docker exec vss-rtvi-vlm printenv REMOTE_VIDEO_INPUT` | In remote mode RT-VLM re-encodes sampled frames into an MP4 for the remote NIM; the image's codec stack (PyNvVideoCodec/PyAV/OpenCV) can't build it, so requests fall back to images and may exceed the endpoint media limit (`422`) or fail (`500`). | Treat as an RT-VLM image/runtime dependency issue, not a config error — a healthy `/v1/models` probe does not mean media inference works. Confirm the deployed `vss-rt-vlm` image version and codec-install completion. |
| `unknown or invalid runtime name: nvidia` | Search `docker info 2>/dev/null` for `runtimes`. | NVIDIA Container Toolkit is not installed or Docker was not restarted. | Follow `prerequisites.md`, restart Docker, and rerun the pre-flight check. |
| GPU not detected | `nvidia-smi` and `docker run --rm --gpus all ubuntu:22.04 nvidia-smi` | Driver, kernel module, or Docker GPU runtime issue. | Load modules with `sudo modprobe nvidia && sudo modprobe nvidia_uvm`, then follow `prerequisites.md` if Docker still cannot see GPUs. |
| `cosmos-reason2-8b` crashes or is restarted in shared GPU mode | `docker logs nvidia-cosmos-reason2-8b --tail 200` | Known CR2/NIM restart limitation in shared GPU mode. Restarting the CR2 container alone may not recover service. | Redeploy the full affected VSS stack, or use the default Cosmos3 path where it is supported. |

## Unexpanded `${...}` in `resolved.yml`

Under the profile-inversion model `COMPOSE_PROFILES` is an explicit list
of service names (e.g. `phoenix,redis,vss-agent,...`), with only the
`llm_*` / `vlm_*` NIM slices still templated. docker compose expands
those at `config` time using the same env file. If an upstream var
(`LLM_MODE`, `LLM_NAME_SLUG`, `VLM_MODE`, `VLM_NAME_SLUG`) is missing,
the matching `llm_*` / `vlm_*` slice drops out and that NIM will not
start. If `COMPOSE_PROFILES` itself is left unexpanded or empty, **no**
service matches (every service is now gated by its own profile), so the
stack comes up empty rather than over-provisioned.

```bash
if grep -q '\${' "$REPO/deploy/docker/resolved.yml"; then
  echo "FAIL: resolved.yml has unexpanded variables:"
  grep -n '\${' "$REPO/deploy/docker/resolved.yml" | head -5
  exit 1
fi
```

If this check fails, re-apply the Step 2 env overrides directly to
the active `generated.env` file, regenerate `resolved.yml` (Step 3),
and re-run this check before continuing.

## NIM endpoint probes
<a id="nim-probes"></a>

Cross-profile LLM/VLM reachability checks for the "Debugging a Deployment"
quick-checks in [`../SKILL.md`](../SKILL.md#debugging-a-deployment). Extract the
selected modes/URLs from `generated.env`, then skip `localhost:3008x` when the
matching `*_MODE=remote` (a connection refused there is expected) and probe the
selected `*_BASE_URL/v1/models` via `scripts/probe_remote_models.sh` instead:

```bash
if [ -n "${ENV_GEN:-}" ] && [ -f "$ENV_GEN" ]; then
  # Use `sub(/^[^=]*=/,""); print` (the whole value after the first '='), NOT
  # `print $2`, so a value containing '=' — e.g. a base URL with a query
  # string like `?api-version=...` — is not truncated at the first '='.
  LLM_MODE="${LLM_MODE:-$(awk -F= '$1=="LLM_MODE"{sub(/^[^=]*=/,""); print}' "$ENV_GEN" | tail -1)}"
  VLM_MODE="${VLM_MODE:-$(awk -F= '$1=="VLM_MODE"{sub(/^[^=]*=/,""); print}' "$ENV_GEN" | tail -1)}"
  LLM_BASE_URL="${LLM_BASE_URL:-$(awk -F= '$1=="LLM_BASE_URL"{sub(/^[^=]*=/,""); print}' "$ENV_GEN" | tail -1)}"
  VLM_BASE_URL="${VLM_BASE_URL:-$(awk -F= '$1=="VLM_BASE_URL"{sub(/^[^=]*=/,""); print}' "$ENV_GEN" | tail -1)}"
  LLM_NAME="${LLM_NAME:-$(awk -F= '$1=="LLM_NAME"{sub(/^[^=]*=/,""); print}' "$ENV_GEN" | tail -1)}"
  VLM_NAME="${VLM_NAME:-$(awk -F= '$1=="VLM_NAME"{sub(/^[^=]*=/,""); print}' "$ENV_GEN" | tail -1)}"
fi

# VLM NIM responding (base/lvs profiles)
# Search exception: always probe local RT-VLM on :8018 (including remote mode,
# where RT-VLM remains as the openai-compat proxy).
if [ "${PROFILE:-}" = "search" ] || [ "${BP_PROFILE:-}" = "bp_developer_search" ]; then
  curl -sf "http://localhost:${RTVI_VLM_PORT:-8018}/v1/models" | python3 -m json.tool
elif [ "${VLM_MODE:-}" = "remote" ]; then
  echo "VLM_MODE=remote — skip localhost:30082; probing ${VLM_BASE_URL:-<remote-vlm-base-url>}/v1/models"
  REMOTE_API_KEY="${NVIDIA_API_KEY:-}" \
    "$REPO/skills/vss-deploy-profile/scripts/probe_remote_models.sh" "$VLM_BASE_URL" "${VLM_NAME:-}"
else
  curl -sf http://localhost:30082/v1/models | python3 -m json.tool
fi

# LLM NIM responding
if [ "${LLM_MODE:-}" = "remote" ]; then
  echo "LLM_MODE=remote — skip localhost:30081; probing ${LLM_BASE_URL:-<remote-llm-base-url>}/v1/models"
  REMOTE_API_KEY="${NVIDIA_API_KEY:-}" \
    "$REPO/skills/vss-deploy-profile/scripts/probe_remote_models.sh" "$LLM_BASE_URL" "${LLM_NAME:-}"
else
  curl -sf http://localhost:30081/v1/models | python3 -m json.tool
fi
```

## VLM `500` / `fetch_video_async TimeoutError` — bridge NIM can't reach host VST

**Symptom.** The agent locates the video, but `video_understanding` returns HTTP
`500` (often retried 3×) with `fetch_video_async ... TimeoutError` in `vss-agent`
logs. The VLM `/v1/models` probe passes — the NIM is healthy; it just can't
**download the clip** from VST.

**Cause.** The VLM/LLM NIMs run on the Compose bridge (`<project>_default`, default `vss_default`) while VST runs in
`network_mode: host`; an active `ufw` blocks the bridge subnet from reaching the
host's VST port, so the fetch times out. This is the **firewall prerequisite** —
if you skipped it, you hit this.

**Diagnose** (no sudo) — host reaches its own VST port but the container can't:

```bash
HOST_IP=$(ip route get 1.1.1.1 | awk '/src/{for(i=1;i<=NF;i++)if($i=="src")print $(i+1)}')  # same as dev-profile.sh
curl -s -o /dev/null -w 'host→VST %{http_code}\n' --max-time 10 "http://$HOST_IP:30888/vst/api/v1/sensor/list"
VLM=$(docker ps --format '{{.Names}}' | grep -iE 'vss-rtvi-vlm|cosmos|nemotron|qwen|vlm' | head -1)
docker exec "$VLM" curl -s -o /dev/null -w 'vlm→VST %{http_code}\n' --max-time 10 "http://$HOST_IP:30888/vst/api/v1/sensor/list"
```

`host→VST 200` but `vlm→VST 000` ⇒ the bridge→host path is firewalled. **Fix:**
apply the [Docker-bridge→host firewall allow](prerequisites.md#firewall) (you can
run it now), then re-run the probe — once `vlm→VST` is `200`, re-issue the query,
no redeploy needed.

## Rule of Thumb

- If the failure appears before `docker compose up`, check env generation and `resolved.yml`.
- If containers start but API calls fail, check service health and `vss-agent` logs.
- If model services fail, check GPU memory, model names, endpoint URLs, and credentials.
- If a corrective action changes env values, regenerate `resolved.yml` before redeploying.
