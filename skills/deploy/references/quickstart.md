# VSS Base Profile — Reference

## What Gets Deployed

| Service | Purpose |
|---|---|
| VSS Agent | Orchestrates tool calls and model inference |
| VSS Agent UI | Web UI at port 3000 (chat, video upload, views) |
| VIOS | Video ingestion, recording, playback |
| Nemotron LLM (NIM) | Reasoning, tool selection, response generation |
| Cosmos Reason 2 (NIM) | Vision-language model, physical reasoning |
| Phoenix | Observability and telemetry |

## Default Models

- **LLM:** `nvidia/nvidia-nemotron-nano-9b-v2`
- **VLM:** `nvidia/cosmos-reason2-8b`

Alternate LLMs: `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8`, `nvidia/nemotron-3-nano`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`, `openai/gpt-oss-20b`

Alternate VLMs: `nvidia/cosmos-reason1-7b`, `Qwen/Qwen3-VL-8B-Instruct`

Only the defaults have been verified for local deployment.

## GPU Layout (RTXPRO6000BW)

| Mode | Device 0 | Device 1 |
|---|---|---|
| Shared GPU (default) | LLM + VLM (`local_shared`) | — |
| Dedicated GPU | LLM | VLM |
| Remote LLM+VLM | — | — |

## Endpoints

| Service | URL |
|---|---|
| Agent UI | `http://<HOST_IP>:3000/` |
| Reports | `http://<HOST_IP>:8000/static/agent_report_<DATE>.md` |

## Known Issues

- `cosmos-reason2-8b` NIM cannot restart after a stop or crash — must redeploy the full stack
- Reports are in-memory by default (lost on container restart) — mount a volume to persist
