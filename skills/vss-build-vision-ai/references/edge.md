# Edge Deployment Reference (DGX Spark, AGX Thor, IGX Thor)

Base-profile deployment guidance for edge platforms.

On all three edge platforms the LLM is
**`nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8`** (slug
`nvidia-nemotron-nano-9b-v2-fp8`). It is served by raw vLLM, not a NIM:

```text
nvcr.io/nvidia/vllm:26.07-py3
```

Unlike the older edge recipes, this LLM **is** wired into the blueprint compose
graph — `deploy/docker/services/nim/nvidia-nemotron-nano-9b-v2-fp8/compose.yml`,
with `hw-DGX-SPARK`, `hw-AGX-THOR` and `hw-IGX-THOR` env pairs — so it deploys
as a normal compose service and no standalone container is needed.

The blueprint's default LLM, `nvidia/nemotron-3.5-lightning-30b-a3b`, ships no
edge `hw-*.env` files and cannot be deployed on these platforms.
`dev-profile.sh` therefore rewrites `LLM_NAME` / `LLM_NAME_SLUG` to the FP8
build whenever the hardware profile is `DGX-SPARK`, `AGX-THOR` or `IGX-THOR`,
no `--llm` was passed, and the LLM is not remote.

## Ask first — the local edge LLM is latency-limited

The edge local LLM — **Nemotron Nano 9B v2 FP8** — runs on the device's shared/unified memory and is **slow** (on DGX Spark it is the main latency bottleneck). **Before deploying, ask the user:**

> The local edge LLM (Nemotron Nano 9B v2 FP8) runs on the device and is latency-limited. If you have a **remote LLM endpoint** (build.nvidia.com / NVIDIA API catalog, or your own OpenAI-compatible server), using it gives noticeably better latency. Use a remote LLM, or run the local one?

- **Remote (recommended for latency):** the user supplies the endpoint + model. Set `LLM_MODE=remote`, `LLM_NAME_SLUG=none`, `LLM_BASE_URL=<endpoint, no trailing /v1>`, `LLM_NAME=<model the endpoint serves>`, and `NVIDIA_API_KEY=<key>` if required; probe `<endpoint>/v1/models` first (see [`credentials.md`](credentials.md)). Only the LLM goes remote; the VLM still deploys locally per the platform's VLM recipe below.
- **Local:** proceed with the platform recipe below; expect higher latency.

## When to pick which

| Situation | LLM path |
|---|---|
| DGX Spark / AGX Thor / IGX Thor, local LLM | In-tree `nvidia-nemotron-nano-9b-v2-fp8` compose service |
| Any edge platform, remote-LLM mode | External endpoint; no local LLM needed |
| Edge platform where 9 B is still too heavy | Standalone small-model vLLM — see [Alternative](#alternative--standalone-small-model-vllm) |
| Non-edge hardware (H100, GB300, L40S, RTX PRO) | Default `nemotron-3.5-lightning-30b-a3b` NIM compose path |

## Prerequisites

- `NGC_API_KEY` or `NGC_CLI_API_KEY` for the NGC container images.
- Docker login to NGC before pulling private NIM images:

  ```bash
  export NGC_API_KEY="${NGC_API_KEY:-$NGC_CLI_API_KEY}"
  echo "$NGC_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin
  ```

- `HF_TOKEN` is required only for the standalone small-model alternative below.
- `NVIDIA_API_KEY` for agent-side NVIDIA API calls when the profile uses them.
- GPU freed: `docker ps` should show no running VSS, NIM, or LLM containers
  before starting. Reboot the device if in doubt.
- System cache cleaner running on DGX Spark / IGX Thor / AGX Thor - see
  [Cache cleaner](#cache-cleaner-every-edge-deploy).

### Cache cleaner (every edge deploy)

Edge platforms (DGX Spark, IGX Thor, AGX Thor) share unified memory between
CPU and GPU. Without periodic `drop_caches`, the kernel's page cache can pin
enough memory that the first inference frame OOMs - most visibly in the
alerts `MODE=2d_cv` path, where Grounding DINO post-processing fails with
`AcceleratorError: CUDA error: out of memory` on the first frame.

This is a platform prerequisite, not a profile-specific one. Every supported
developer profile (`base`, `alerts`, `search`, `lvs`) needs the cleaner
running on edge hardware.

**Install and start (one-time per host):**

```bash
sudo tee /usr/local/bin/sys-cache-cleaner.sh << 'EOF'
#!/bin/bash
set -e
echo 0 | tee /proc/sys/vm/nr_hugepages
echo "Starting cache cleaner"
while true; do
  sync && echo 3 | tee /proc/sys/vm/drop_caches > /dev/null
  sleep 3
done
EOF
sudo chmod +x /usr/local/bin/sys-cache-cleaner.sh
sudo -b /usr/local/bin/sys-cache-cleaner.sh
```

**Verify it is running before any `docker compose up`:**

```bash
pgrep -f sys-cache-cleaner.sh && echo "cache cleaner OK" || echo "cache cleaner NOT RUNNING - start it before deploying"
```

The cleaner is intentionally not a systemd unit, so a `reboot` resets it.
Run this block manually for edge hosts before deployment; the generic
SKILL.md pre-flight smoke test does not install it.

> **IGX Thor only - also boost VIC clocks:**
> ```bash
> sudo nvpmodel -m 0
> sudo jetson_clocks
> # Replace `<VIC_DEVFREQ_PATH>` with the value of `ls /sys/class/devfreq/` that matches `*.vic`
> sudo su -c 'echo performance > <VIC_DEVFREQ_PATH>/governor'
> ```

### Unified-memory GPU budget (reserve ≥ 0.2)
<a id="unified-memory-budget"></a>

On these platforms CPU, GPU, OS page cache, and every container draw from **one**
shared pool, so a GPU-memory *fraction* — `NIM_GPU_MEM_FRACTION` / `NIM_KVCACHE_PERCENT`
for NIM-served models, `--gpu-memory-utilization` for the vLLM-served edge LLM, or
`RTVI_VLLM_GPU_MEMORY_UTILIZATION` for RT-VLM (alerts / lvs / Thor) — is a slice of
memory that is **not all free**.
vLLM measures *free* at startup and aborts before loading the model if free is
below what the fraction asks for (`desired = util × total`):

```text
ValueError: Free memory on device (X/124.61 GiB) on startup is less than desired
GPU memory utilization (0.8, 99.69 GiB). Decrease GPU memory utilization …
```

which surfaces in VSS as `Engine core initialization failed` /
`Failed to load VLM on GPU 0`.

**Rule:** compute each fraction against *actual free* memory and leave **≥ 0.2 of
total** (~20%) as reserve — `util ≤ free/total − 0.2` — and for co-resident
services keep the **sum** of their fractions `≤ 0.8`:

```bash
# DGX Spark reports free/total via nvidia-smi (Thor/Tegra often reports N/A — see below)
set -- $(nvidia-smi --query-gpu=memory.free,memory.total --format=csv,noheader,nounits | head -1 | tr -d ',')
free=$1; total=$2
awk -v f="$free" -v t="$total" 'BEGIN{u=f/t-0.2; if(u<0)u=0; printf "max util ~ %.2f  (free %d / total %d MiB; 0.2 reserve)\n", u, f, t}'
```

The conservative per-service starting point is about `0.4`, so two
co-resident services sum to at most `0.8`. The FP8 LLM's `-shared-gpu` service
runs at `--gpu-memory-utilization 0.40`; set
`RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.4` (DGX Spark) or `0.35` (Thor) in the
build override for RT-VLM. If other tenants are resident, lower the fractions
to fit — for the LLM that means editing `--gpu-memory-utilization` in
`services/nim/nvidia-nemotron-nano-9b-v2-fp8/compose.yml`. If `nvidia-smi`
cannot read free memory (common on Thor/Tegra), start at `0.4` and reduce by
`0.05` after the first `Free … less than desired` abort.

## DGX Spark — in-tree Nano 9B v2 FP8 LLM + integrated RT-VLM

There is no standalone container to start: the LLM is a compose service. Add
these values to `_builds/<name>/override.env`:

| Key | Value | Why |
|---|---|---|
| `LLM_MODE` | `local_shared` | LLM and RT-VLM share the single unified-memory GPU |
| `LLM_NAME` | `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` | The only in-tree LLM with edge env files |
| `LLM_NAME_SLUG` | `nvidia-nemotron-nano-9b-v2-fp8` | Selects `llm_<mode>_<slug>` in `COMPOSE_PROFILES` |
| `HARDWARE_PROFILE` | `DGX-SPARK` | Selects edge image and runtime defaults |
| `VLM_MODE` | `local_shared` | Integrated RT-VLM stays on the shared edge GPU |
| `VLM_NAME` | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` | Model ID advertised by the default integrated RT-VLM path |
| `VLM_NAME_SLUG` | `none` | No standalone VLM NIM profile |
| `VLM_MODEL_TYPE` | `rtvi` | Keep the Foundation's RT-VLM serving path |
| `RTVI_VLM_MODEL_PATH` | `ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` | Integrated checkpoint |
| `RTVI_VLM_MODEL_TO_USE` | `cosmos-reason3` | RT-VLM loader |
| `RTVI_VLLM_GPU_MEMORY_UTILIZATION` | `0.40` | Shared unified-memory starting point |
| `LLM_DEVICE_ID` | `0` | Edge platforms share GPU 0 |
| `RT_VLM_DEVICE_ID` | `0` | Edge platforms share GPU 0 |

`dev-profile.sh` writes the two `LLM_NAME*` values itself on `DGX-SPARK`,
`AGX-THOR` and `IGX-THOR` when `--llm` is not passed and the LLM is not remote;
set them by hand only because a build override is written directly.

The `-shared-gpu` variant of the FP8 service runs
`--gpu-memory-utilization 0.40` and loads `hw-DGX-SPARK-shared.env`; the
dedicated variant runs `0.85` and loads `hw-DGX-SPARK.env`. Both use an init
container to fetch the Nemotron tool-call parser from a public Hugging Face
repo, so no `HF_TOKEN` is involved.

Use the default agent config — the 9 B model handles clarifying questions, so
the small-model prompt override is not wanted here:

```text
VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config.yml
```

Keep the Foundation's `rtvi-vlm` profile key, then follow `SKILL.md` Steps
7–9 to resolve, validate, and deploy the build.

Validate the LLM once the build is up (raw vLLM, so `/health`, not the NIM's
`/v1/health/ready`):

```bash
curl -sf http://localhost:30081/health && echo "LLM ready"
curl -s http://localhost:30081/v1/models | jq -r '.data[].id'
```

Expected model ID is `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8`. If `/v1/models`
returns a different ID, use the returned ID as `LLM_NAME` in the build
override.

## AGX Thor / IGX Thor - Nano 9B v2 FP8 LLM + rtvi-vlm

On Thor, the VLM falls back to **`rtvi-vlm` serving Cosmos Reason3 Nano BF16
in-process**. The standalone `cosmos-reason2-8b` NIM service does not run on
Thor. `rtvi-vlm` loads `ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` itself and
advertises it at `http://${HOST_IP}:8018/v1` under
`VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final` with
`VLM_NAME_SLUG=none`.

Remote VLM and `--vlm` swaps are not supported on Thor for `base` or
`alerts`; this is the only deployed VLM shape documented by this skill.

The Thor LLM is the same in-tree FP8 compose service as DGX Spark —
`hw-AGX-THOR.env` / `hw-AGX-THOR-shared.env` and `hw-IGX-THOR.env` /
`hw-IGX-THOR-shared.env` all exist under
`services/nim/nvidia-nemotron-nano-9b-v2-fp8/` — so nothing has to be started by
hand and no `HF_TOKEN` is involved.

Add these values to `_builds/<name>/override.env`:

| Key | Value |
|---|---|
| `LLM_MODE` | `local_shared` |
| `LLM_NAME` | `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` |
| `LLM_NAME_SLUG` | `nvidia-nemotron-nano-9b-v2-fp8` |
| `HARDWARE_PROFILE` | `AGX-THOR` or `IGX-THOR` |
| `LLM_DEVICE_ID` | `0` |
| `VLM_DEVICE_ID` | `0` |
| `RT_VLM_DEVICE_ID` | `0` |
| `VLM_MODE` | `local_shared` |
| `VLM_NAME` | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` |
| `VLM_NAME_SLUG` | `none` |
| `RTVI_VLM_MODEL_PATH` | `ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` |
| `RTVI_VLM_MODEL_TO_USE` | `cosmos-reason3` |
| `RTVI_VLLM_GPU_MEMORY_UTILIZATION` | `0.35` |
| `VSS_AGENT_CONFIG_FILE` | `./deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config.yml` |

Then follow `SKILL.md` Steps 7–9.

## Alternative — standalone small-model vLLM

If the 9 B FP8 build is still too heavy for the device, the blueprint also
supports running a smaller LLM as a standalone vLLM container and pointing the
agent at it in remote mode. That path is **not** in the compose graph;
`docs/edge-deployment.mdx` is the source of truth for it. In outline:

- Run `nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8` on port `30081` —
  `nvcr.io/nvidia/vllm:26.02-py3` on DGX Spark,
  `ghcr.io/nvidia-ai-iot/vllm:latest-jetson-thor` on Thor — with
  `--gpu-memory-utilization 0.25 --enable-auto-tool-choice --tool-call-parser qwen3_coder`.
- Those weights need `HF_TOKEN`. Verify access before deploying:

  ```bash
  curl -sf -H "Authorization: Bearer $HF_TOKEN" \
      https://huggingface.co/api/models/nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8 \
      >/dev/null && echo "HF_TOKEN works" || echo "HF_TOKEN missing/invalid/no access"
  ```

  If the model is gated, the token's owner must request access on the HF page.
- Then set `LLM_MODE=remote`, `LLM_BASE_URL=http://localhost:30081`,
  `LLM_NAME=nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8`, `LLM_NAME_SLUG=none`, and
  `VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config_edge.yml`
  in the build override.

A user-supplied remote endpoint (build.nvidia.com or their own OpenAI-compatible
server) is the other alternative, and is the better one for latency — see
[Ask first](#ask-first--the-local-edge-llm-is-latency-limited).

## Caveats

- **DGX Spark needs the `-sbsa` container images.** GB10/DGX Spark runs the dGPU/SBSA
  driver (not Tegra/L4T); the default image tags pull the Tegra DeepStream build, which
  crash-loops on missing `libnvbufsurface.so.1.0.0` / `libnvrm_mem.so`. When
  writing the build override for `HARDWARE_PROFILE=DGX-SPARK`, set each image
  tag to its `-sbsa` variant (the commented
  `# …-sbsa` line in the profile's `.env`): `RTVI_VLM_IMAGE_TAG` (RT-VLM),
  `VSS_RT_CV_TAG` (RT-CV), and `LVS_TAG` (LVS).
- **The edge LLM is raw vLLM, not a NIM.** `NIM_*` keys in its `hw-*.env` files
  are inert; the effective knobs are the flags in the compose `command:` block
  (`--gpu-memory-utilization`, `--tensor-parallel-size`, …), and the health
  path is `/health`, not the NIM's `/v1/health/ready`.
- **Confirm the served model ID.** The expected ID is
  `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` (it is the vLLM `--model` value), but
  `/v1/models` is the source of truth for `LLM_NAME`.
- **No `HF_TOKEN` for the in-tree edge LLM.** Its init container fetches the
  Nemotron tool-call parser from a public Hugging Face repo, and the compose
  passes no `HF_TOKEN`. Use `NGC_API_KEY` / `NGC_CLI_API_KEY` for the images.
  `HF_TOKEN` applies only to the standalone small-model alternative.
- **Use `config.yml`, not `config_edge.yml`, with the 9 B FP8 LLM.**
  `config_edge.yml` exists for smaller models and deliberately removes
  clarifying-question behavior. It belongs to the standalone alternative above.
