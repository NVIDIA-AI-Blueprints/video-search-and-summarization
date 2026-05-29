---
name: vss-deploy-omni-nemotron
description: >
  Deploy the audio-enabled NVIDIA Nemotron-3-Nano-Omni VLM as a standalone
  OpenAI-compatible vLLM server on port 30082. Covers GPU and Hugging Face
  prerequisites, weight download, vLLM serve flags, model-id discovery,
  reachability checks, and teardown. Does not deploy a full VSS profile.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational deployment vlm omni audio nemotron vllm"
---

# Deploy Nemotron Omni VLM (audio-enabled, port 30082)

Use this skill when you need to:

- Serve **NVIDIA Nemotron-3-Nano-Omni** as a standalone OpenAI-compatible VLM that analyzes **video and audio** in a single MP4.
- Expose the model on **port 30082** for a remote VSS agent (`--use-remote-vlm`) or any other OpenAI-compatible client.
- Verify prerequisites (GPU VRAM, vLLM, Hugging Face access) before starting `vllm serve`.
- Discover the live model id from `/v1/models` for later `VLM_NAME` / `--vlm` wiring.

**Trigger phrases:** `vss-deploy-omni-nemotron`, `Nemotron Omni`, `Nemotron-3-Nano-Omni`, `audio-enabled VLM`, `omni nemotron`, `deploy omni on 30082`, `remote VLM with audio`, `ENABLE_AUDIO` + Omni.

**Out of scope:** Full VSS blueprint deploy (`/vss-deploy-profile`, `dev-profile.sh`). RT-VLM dense captioning (`/vss-deploy-dense-captioning`). NIM Cosmos containers on 30082.

## Service Snapshot

| Item | Value |
|---|---|
| **Default checkpoint** | `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` |
| **Alternate checkpoint** | `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16` (higher VRAM) |
| **Runtime** | Host `vllm serve` (not a VSS Compose service) |
| **Listen port** | `30082` (`--host 0.0.0.0 --port 30082`) |
| **API base** | `http://<VLM_HOST>:30082` — **no** trailing `/v1` in env vars like `VLM_ENDPOINT_URL` |
| **Model card** | [Hugging Face — Nemotron-3-Nano-Omni](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16) |
| **Blueprint doc** | [Configure the VLM — Nemotron Omni](https://docs.nvidia.com/vss/latest/vss-agent/configure-vlm.html#using-nemotron-omni-audio-enabled-vlm) |

MoE sizing: **~40 GB VRAM** for FP8 weights alone (30B total params × FP8 × 1.3 overhead). Plan a **dedicated GPU**; do not share with a local LLM unless total VRAM fits both stacks.

## Prerequisites

Run these checks **before** downloading weights or starting vLLM. If any gate fails, stop and fix it (see [`references/deploy-omni-nemotron-vlm.md`](references/deploy-omni-nemotron-vlm.md) and [`../vss-deploy-profile/references/prerequisites.md`](../vss-deploy-profile/references/prerequisites.md)).

### Pre-flight (minimum)

```bash
# GPU visible
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

# Pick a GPU with headroom (example: device 1)
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"

# vLLM CLI available (version must support Nemotron Omni + reasoning parser)
vllm --version || python3 -c "import vllm; print(vllm.__version__)"

# Tools
command -v curl && command -v jq

# Port free
ss -ltn | grep -q ':30082 ' && echo "WARN: 30082 already in use" || echo "30082 free"

# Hugging Face token (gated model — request access on the model card first)
test -n "${HF_TOKEN:-}" && echo "HF_TOKEN set" || echo "HF_TOKEN missing"
```

### Hugging Face access

1. Request access on the [model card](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16) if the repo is gated.
2. Export a token (never paste into chat or commit):

```bash
export HF_TOKEN="<your-hf-token>"
huggingface-cli login --token "$HF_TOKEN" 2>/dev/null || true
```

### GPU / driver (no Docker required for this skill)

- NVIDIA driver + CUDA stack compatible with your installed vLLM build.
- **≥ 48 GB** GPU recommended for the default NVFP4 checkpoint with the flags below; BF16 needs more.
- For driver/Docker/NVIDIA Container Toolkit checks (needed only if you later deploy VSS on the same host), use [`../vss-deploy-profile/references/prerequisites.md`](../vss-deploy-profile/references/prerequisites.md).

### What this skill does **not** require

- `NGC_CLI_API_KEY` / `docker login nvcr.io` — Omni weights come from Hugging Face, not NGC NIM.
- `dev-profile.sh` or any VSS profile — unless the user explicitly asks to wire VSS after Omni is up.

## Deploy Workflow

Execute in order: **preflight → download weights (if needed) → serve → verify**.

### 1. Set reachability variables

```bash
# IP or hostname clients will use (VSS host, RT-VLM, curl tests)
export VLM_HOST="${VLM_HOST:-$(hostname -I | awk '{print $1}')}"
export VLM_PORT=30082
export VLM_MODEL="${VLM_MODEL:-nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4}"
```

Pin a GPU before launch (avoid colliding with an LLM on device 0):

```bash
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
```

### 2. Download weights (first run)

On the **VLM GPU host**, cache weights per the model card (Hugging Face will populate `~/.cache/huggingface` on first `vllm serve` if not pre-downloaded):

```bash
huggingface-cli download "$VLM_MODEL" --local-dir-use-symlinks False
```

Skip if the checkpoint is already local and `vllm serve` can load it.

### 3. Start vLLM on port 30082

Run in a dedicated terminal or process supervisor (`tmux`, `systemd`, etc.). First boot can take many minutes while weights load.

```bash
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES}" vllm serve "${VLM_MODEL}" \
  --host 0.0.0.0 \
  --port "${VLM_PORT}" \
  --max-model-len 131072 \
  --tensor-parallel-size 1 \
  --trust-remote-code \
  --video-pruning-rate 0.5 \
  --max-num-seqs 384 \
  --media-io-kwargs '{"video": {"fps": 2, "num_frames": 256}}' \
  --reasoning-parser nemotron_v3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --kv-cache-dtype fp8 \
  --no-enable-flashinfer-autotune
```

**BF16 variant:** swap `VLM_MODEL` to `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16` and drop `--kv-cache-dtype fp8` if your vLLM build errors on FP8 KV.

### 4. Verify

```bash
# Model id (copy verbatim for VSS VLM_NAME / --vlm later)
curl -fsS "http://${VLM_HOST}:${VLM_PORT}/v1/models" | jq -r '.data[].id'

# Reachability
curl -fsS "http://${VLM_HOST}:${VLM_PORT}/v1/models" | jq .

# Optional smoke: list must be non-empty
test -n "$(curl -fsS "http://${VLM_HOST}:${VLM_PORT}/v1/models" | jq -r '.data[0].id // empty')"
```

A mismatch between downstream `VLM_NAME` and the `id` from `/v1/models` causes **HTTP 404** on chat completions.

### 5. Wire into VSS (optional — user must ask)

Only after Omni is healthy:

- Set `VLM_ENDPOINT_URL=http://<VLM_HOST>:30082` (no trailing `/v1`).
- Set `ENABLE_AUDIO=true` in the profile `.env` when using Omni.
- Deploy or recreate with `--use-remote-vlm` and `--vlm "<id-from-/v1/models>"` via [`../vss-deploy-profile/SKILL.md`](../vss-deploy-profile/SKILL.md).

If VSS was deployed before vLLM was ready, update `--vlm` / `VLM_NAME` and recreate `vss-agent` after step 4 succeeds.

## Tear Down

```bash
# Find and stop the vLLM process (example)
pkill -f "vllm serve.*${VLM_MODEL}" || true

# Confirm port released
ss -ltn | grep ':30082 ' || echo "30082 free"
```

Weights in `~/.cache/huggingface` remain for faster restarts.

## Routing

| User request | Skill |
|---|---|
| Full VSS base/alerts/LVS with local NIM VLM | [`../vss-deploy-profile/SKILL.md`](../vss-deploy-profile/SKILL.md) |
| RT-VLM microservice (port 8018) | [`../vss-deploy-dense-captioning/SKILL.md`](../vss-deploy-dense-captioning/SKILL.md) |
| Cosmos / Qwen NIM on 30082 | [`references/deploy-omni-nemotron-vlm.md`](references/deploy-omni-nemotron-vlm.md) § NIM alternative |
| Video Q&A / reports against deployed VSS | [`../vss-ask-video/SKILL.md`](../vss-ask-video/SKILL.md), [`../vss-generate-video-report/SKILL.md`](../vss-generate-video-report/SKILL.md) |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| OOM on startup | GPU too small or another process on the same GPU | Free the GPU, use NVFP4, lower `--max-model-len`, or use a larger GPU |
| `401` / cannot download weights | Missing `HF_TOKEN` or no model access | Request access on HF; export `HF_TOKEN` |
| Port bind error | 30082 in use (e.g. Cosmos NIM) | Stop the conflicting service or change `--port` consistently everywhere |
| VSS 404 on chat | `VLM_NAME` ≠ `/v1/models` id | Re-run `curl .../v1/models` and update `VLM_NAME` / `--vlm` |
| Audio ignored in VSS | Non-Omni VLM or `ENABLE_AUDIO` false | Confirm `omni` in `VLM_NAME`; set `ENABLE_AUDIO=true` |

More detail: [`references/troubleshooting.md`](references/troubleshooting.md).

## References

| File | When to read |
|---|---|
| [references/README.md](references/README.md) | Table of contents |
| [references/deploy-omni-nemotron-vlm.md](references/deploy-omni-nemotron-vlm.md) | Full deploy reference, vLLM flags, VSS integration notes, NIM/vLLM container alternative |
| [references/troubleshooting.md](references/troubleshooting.md) | Extended diagnostics |
| [../vss-deploy-profile/references/lvs-profile.md](../vss-deploy-profile/references/lvs-profile.md) | Omni sizing and `ENABLE_AUDIO` env block for LVS |
