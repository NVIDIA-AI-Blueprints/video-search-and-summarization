# Deployment Reference: Nemotron Omni VLM (audio-enabled, port 30082)

Standalone OpenAI-compatible server for [NVIDIA Nemotron-3-Nano-Omni](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16). Analyzes **video and audio** in a single MP4. This path uses **host `vllm serve`**, not VSS Compose.

## GPU Requirements

| Checkpoint | Precision | Est. weight VRAM (30B × bits ÷ 8 × 1.3) | Notes |
|---|---|---|---|
| `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` | NVFP4 / FP8 KV | ~40 GB weights + KV headroom | Default in blueprint docs |
| `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16` | BF16 | Higher than FP8 | Use when NVFP4 unsupported |

- **GPU count:** 1 (`--tensor-parallel-size 1` default).
- **Sharing:** Pin with `CUDA_VISIBLE_DEVICES`; avoid sharing a GPU with a local LLM unless combined VRAM fits both.
- **MoE note:** 30B-A3B means ~3B active per token, but the **full weight set** must fit in VRAM.

## Prerequisites

### Host software

| Requirement | Check |
|---|---|
| NVIDIA driver + CUDA | `nvidia-smi` |
| vLLM with Nemotron Omni support | `vllm --version` |
| `curl`, `jq` | `command -v curl jq` |
| Port 30082 available | `ss -ltn \| grep 30082` |

### Secrets

| Variable | Required | Purpose |
|---|---|---|
| `HF_TOKEN` | Yes (gated HF repo) | Download Nemotron Omni weights |
| `CUDA_VISIBLE_DEVICES` | Recommended | Pin GPU (e.g. `1` when LLM uses `0`) |

Do **not** commit or echo full tokens in agent replies.

### Optional (VSS on same host later)

- `NGC_CLI_API_KEY` — only if also deploying VSS NIMs; see [`../../vss-deploy-profile/references/ngc.md`](../../vss-deploy-profile/references/ngc.md).
- System preflight — [`../../vss-deploy-profile/references/prerequisites.md`](../../vss-deploy-profile/references/prerequisites.md).

## Deploy

### Environment

```bash
export VLM_HOST="<ip-reachable-from-clients>"
export VLM_PORT=30082
export VLM_MODEL="nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4"
export HF_TOKEN="<token>"
export CUDA_VISIBLE_DEVICES=1
```

### Download weights

Follow the [model card](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16) for the checkpoint you serve. Pre-download:

```bash
huggingface-cli download "$VLM_MODEL"
```

### Start server

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

First boot may take a long time while weights load.

### Verify

```bash
curl -fsS "http://${VLM_HOST}:${VLM_PORT}/v1/models" | jq .
MODEL_ID=$(curl -fsS "http://${VLM_HOST}:${VLM_PORT}/v1/models" | jq -r '.data[0].id')
echo "Use this id for VLM_NAME / --vlm: ${MODEL_ID}"
```

## VSS integration (optional)

When the user also wants VSS (not part of standalone deploy):

1. `VLM_ENDPOINT_URL=http://${VLM_HOST}:30082` — **no** trailing `/v1`.
2. `ENABLE_AUDIO=true` in profile `.env`; `VLM_NAME` must contain `omni`.
3. Deploy with `--use-remote-vlm` and `--vlm "${MODEL_ID}"` per [`../../vss-deploy-profile/SKILL.md`](../../vss-deploy-profile/SKILL.md).

If VSS started before vLLM was ready, update `VLM_NAME` / `--vlm` and recreate `vss-agent` after `/v1/models` succeeds.

## NIM / vLLM container alternative

For non-Omni VLMs, the blueprint documents NIM or `nvcr.io/nvidia/vllm` on port 30082. **Omni is documented as host `vllm serve`**, not the Cosmos NIM image. Do not substitute `nvcr.io/nim/nvidia/cosmos-reason2-8b` when the user asked for Nemotron Omni.

## Tear down

Stop the `vllm serve` process; confirm port 30082 is free. Hugging Face cache can remain for faster restarts.
