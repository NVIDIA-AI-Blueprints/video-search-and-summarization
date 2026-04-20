# Edge Deployment Reference (DGX Spark, AGX Thor, IGX Thor)

Base-profile deployment for edge platforms using a compact LLM sized for
unified-memory GPUs. Two supported paths:

1. **NVIDIA Nemotron Edge 4B** (FP8) — fits in ~25% of GPU memory, lets the VLM
   share the remaining budget. Recommended default for strict memory envelopes.
   Uses a simplified planning prompt (`config_edge.yml`) that skips clarifying
   questions.
2. **NVIDIA Nemotron Nano 9B v2 FP8** — larger context and planning quality,
   asks clarifying questions when user queries are ambiguous. Use this when
   memory allows and you want the agent to interact naturally.

## When to pick which

| Situation | Model |
|---|---|
| DGX Spark or IGX/AGX Thor, both LLM+VLM must share single GPU | **Edge 4B** |
| DGX Spark with explicit choice to trade clarifying-Q's for more memory | Edge 4B |
| Unified memory > 96 GB or dedicated GPU for LLM | Nano 9B v2 FP8 |
| Expecting ambiguous / multi-turn user queries | Nano 9B v2 FP8 |
| Running Edge 4B from a remote endpoint (not on-device) | Edge 4B |

## Prerequisites

- `NGC_CLI_API_KEY` (NIM containers)
- `HF_TOKEN` (only for Edge 4B — weights pull from Hugging Face)
- `NVIDIA_API_KEY` (agent-side)
- GPU freed: `docker ps` should show no running VSS or LLM containers before
  starting. Reboot the device if in doubt.

## DGX Spark — Edge 4B + local Cosmos-Reason2-8B VLM

Start the LLM as a standalone vLLM container (port 30081):

```bash
export HF_TOKEN=$HF_TOKEN

docker run --gpus all -d --name nemotron-edge -p 30081:8000 \
    -e HF_TOKEN=$HF_TOKEN \
    nvcr.io/nvidia/vllm:26.02-py3 \
    python3 -m vllm.entrypoints.openai.api_server \
    --model nvidia/NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8 \
    --trust-remote-code \
    --gpu-memory-utilization 0.25 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --port 8000
```

Key flags:
- `--gpu-memory-utilization 0.25` — leaves ~75% for the VLM NIM (which uses
  `NIM_KVCACHE_PERCENT=0.4` on Spark shared).
- `--tool-call-parser qwen3_coder` — Edge 4B is Qwen3-lineage; the parser
  must match the template.
- `--enable-auto-tool-choice` — agent workflow uses tool-calls.

Then deploy the agent workflow (LLM treated as "remote" since it's a
standalone vLLM, not a NIM):

```bash
export NVIDIA_API_KEY=$NVIDIA_API_KEY
export NGC_CLI_API_KEY=$NGC_CLI_API_KEY
export LLM_ENDPOINT_URL=http://localhost:30081
export VSS_AGENT_CONFIG_FILE=./deployments/developer-workflow/dev-profile-base/vss-agent/configs/config_edge.yml

deployments/dev-profile.sh up -p base \
    --use-remote-llm \
    --llm nvidia/NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8 \
    --hardware-profile DGX-SPARK \
    --vlm-env-file deployments/nim/cosmos-reason2-8b/hw-DGX-SPARK-shared.env
```

The `--vlm-env-file` caps the VLM's KV cache at 40% so both models coexist.

## DGX Spark — Nano 9B v2 FP8 (both NIMs, no standalone vLLM)

```bash
# Make sure the Edge vLLM container is not running:
# docker stop nemotron-edge && docker rm nemotron-edge

deployments/dev-profile.sh up -p base \
    --hardware-profile DGX-SPARK \
    --llm nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8 \
    --vlm nvidia/cosmos-reason2-8b
```

Uses the default `config.yml` (full planning prompt with clarifying questions).

## AGX Thor / IGX Thor — Edge 4B + rtvi-vlm

On Thor, the VLM used by the blueprint is `rtvi-vlm` (not cosmos-reason2-8b),
and the LLM runs from a jetson-specific vLLM image:

```bash
export HF_TOKEN=$HF_TOKEN

docker run --gpus all -d --name nemotron-edge -p 30081:8000 \
    --runtime=nvidia \
    -e NVIDIA_VISIBLE_DEVICES=0 \
    -e HF_TOKEN=$HF_TOKEN \
    ghcr.io/nvidia-ai-iot/vllm:latest-jetson-thor \
    python3 -m vllm.entrypoints.openai.api_server \
    --model nvidia/NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8 \
    --trust-remote-code \
    --gpu-memory-utilization 0.25 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --port 8000
```

Then:

```bash
export NVIDIA_API_KEY=$NVIDIA_API_KEY
export NGC_CLI_API_KEY=$NGC_CLI_API_KEY
export LLM_ENDPOINT_URL=http://localhost:30081
export VSS_AGENT_CONFIG_FILE=./deployments/developer-workflow/dev-profile-base/vss-agent/configs/config_edge.yml

# Uses the default 35% GPU budget for rtvi-vlm on Thor
deployments/dev-profile.sh up -p base \
    --use-remote-llm \
    --llm nvidia/NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8 \
    --hardware-profile AGX-THOR
```

For **IGX Thor**: replace `AGX-THOR` with `IGX-THOR` in the `--hardware-profile` flag.

## AGX/IGX Thor — Nano 9B v2 FP8

```bash
# docker stop nemotron-edge && docker rm nemotron-edge
deployments/dev-profile.sh up -p base \
    --hardware-profile AGX-THOR \
    --llm nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8
```

## Caveats

- **Edge 4B skips clarifying questions.** `config_edge.yml` deliberately
  simplifies the planning prompt for smaller models. If the user asks
  ambiguously (e.g. "summarize the video" without specifying which), the
  agent won't ask back — it'll pick one or fail. Switch to Nano 9B v2 FP8
  if this matters for your use case.
- **Edge 4B is not a NIM.** It's a plain vLLM container — no
  `nvcr.io/nim/...` tag. `dev-profile.sh --use-remote-llm` points the
  agent at the local port 30081 as if it were a remote endpoint.
- **Tool-call parser.** Edge 4B requires `--tool-call-parser qwen3_coder`
  (Qwen3-lineage). Omitting it or using `llama3_json` breaks the agent's
  tool calls.
- **HF_TOKEN gate.** Edge 4B weights are pulled from Hugging Face at first
  run; a gated model, so your token needs access.
- **`config_edge.yml` may not be present** in older checkouts — verify
  `deployments/developer-workflow/dev-profile-base/vss-agent/configs/config_edge.yml`
  exists before running. If missing, pull the latest `feat/skills` or
  main branch.

## Known ARM64 gotcha

`nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2:1` (the default `base` NIM
tag) ships a broken arm64 manifest — it declares arm64 but contains
x86_64 binaries. This is why the Edge 4B path is the recommended default
on Spark: it avoids the NIM entirely. If you must use a local NIM for the
LLM, pin to the Spark variant:

```
nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2-dgx-spark:1.0.0-variant
```

(currently not wired into the blueprint's `compose.yml` — follow-up to track).
