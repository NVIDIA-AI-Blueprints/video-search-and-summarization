# VSS Alerts Profile — Reference

## Two Modes

| Mode | Flag | How it works | GPU load |
|---|---|---|---|
| **verification** | `-m verification` | CV + behavior analytics generate candidate alerts upstream; VLM reviews each alert clip to reduce false positives | Lower — VLM invoked per alert |
| **real-time** | `-m real-time` | VLM continuously processes live video at periodic intervals; broad coverage without upstream CV dependency | Higher — VLM runs continuously |

## What Gets Deployed

| Service | Purpose |
|---|---|
| NVStreamer | Plays back dataset video to simulate live cameras |
| VIOS | Video ingestion, live streaming, recording, playback |
| RTVI CV | Real-time object detection (Grounding DINO, open-vocabulary) |
| Behavior Analytics | Rule-based alert generation from RTVI CV metadata |
| Alert Verification | VLM-based review of alert video clips |
| Cosmos Reason (NIM) | VLM used by Alert Verification |
| ELK | Log and alert storage |
| VSS Agent | Orchestrates tool calls and queries |
| Nemotron LLM (NIM) | Reasoning and response generation |
| Phoenix | Observability and telemetry |

## GPU Layout (RTXPRO6000BW)

Both GPUs required:

| Device | Role |
|---|---|
| 0 | RT-CV perception (reserved — object detection) |
| 1 | LLM + VLM (`local_shared`) |

## Required env override for `local_shared` mode

When `LLM_MODE=local_shared` and `VLM_MODE=local_shared` for this profile, you
**must** set `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.35` in the `.env` before
`docker compose up`. The profile's `.env` ships this value empty, which
makes vLLM in the `rtvi-vlm` container fall back to its default of `0.9`.
On a 97 GB GPU that reserves ~87 GB for the VLM, leaving the LLM NIM
(asks for 0.4 of the GPU under shared mode) no room for KV cache → the
LLM crashloops with `CUDA out of memory ... Tried to allocate 5.05 GiB ...
GPU 0 has ... 2.76 GiB free`. Compose treats the LLM as an *optional*
dependency of the alerts stack, so the rest of the services come up
healthy and the failure is silent until the agent is asked to answer.

`scripts/dev-profile.sh` applies this override automatically (see the
`alerts + local_shared` branch in the script). The compose-direct
deploy flow in [`SKILL.md`](../SKILL.md) bypasses that, so set it
manually:

```bash
sed -i "s|^RTVI_VLLM_GPU_MEMORY_UTILIZATION=.*|RTVI_VLLM_GPU_MEMORY_UTILIZATION='0.35'|" \
  "$REPO/deploy/docker/developer-profiles/dev-profile-alerts/.env"
```

The same cap applies to all hardware profiles except `OTHER`, `IGX-THOR`,
and `AGX-THOR` (those have separate handling). For `dedicated` /
`local` mode the rtvi-vlm has its own GPU and this override is not
needed.

## Use Cases

- PPE compliance verification (hard hats, safety vests)
- Restricted area monitoring
- Asset presence/absence detection
- Custom object detection scenarios

## First Run Note

Downloads perception and VLM models from NGC on first run — expect extra time.
