# Sizing and GPU Placement

Use this guide after selecting the Foundation and effective service set. Treat
the resolved Compose model—not a hardware-profile label—as the deployment
contract.

## Sizing flow

1. Inventory every GPU's model, total memory, free memory, and current users.
2. List every GPU service in the effective build and its model, precision,
   device ID, concurrency or stream count, and whether its footprint is fixed
   or elastic.
3. Place fixed-footprint services first according to the placement and sizing
   contracts for [RT-CV](services/rt-cv.md) and
   [RT-Embed](services/rt-embed.md).
4. Place singleton RT-VLM last, into the capacity step 3 leaves behind; never
   displace or co-pack a fixed service to free a GPU for it. When composition
   must converge different integrated Cosmos3 Nano variants, use BF16 only if a
   GPU remains free after every fixed-footprint service has its preferred
   dedicated device; otherwise use FP8 co-resident on the RT-CV device
   (`RT_VLM_DEVICE_ID = RT_CV_DEVICE_ID`), which retains the most headroom among
   the fixed-footprint services placed in step 3. Share only when the combined
   budget fits. Resolve the atomic variant/placement set without inheriting
   consumer wiring, as specified by the [RT-VLM owner](services/rt-vlm.md). Stock
   mode retains its Foundation's reviewed variant and placement. For continuous
   VLM inference on a shared GPU, reduce `NUM_STREAMS` and verify utilization
   headroom under load.
5. Use a remote endpoint only when the user requested one or approved it after
   local sizing failed.
6. Put every device ID and utilization value the placement **changes** from the
   Foundation default into the build `override.env` (with its derived closure; do
   not repeat unchanged defaults, per `composition.md`), resolve Compose, and
   verify the full effective placement in `resolved.yml`.
7. Under load, watch `nvidia-smi` and model-service logs. Tune one variable at
   a time and regenerate `resolved.yml`.

Never silently substitute a smaller model, lower precision, or remote endpoint.
The placement-driven RT-VLM BF16/FP8 convergence above is part of singleton
resolution, not a fallback substitution.
If the requested shape does not fit, show the measured capacity and required
budget, then ask the user to choose a different placement.

## General memory budget

```text
weights_GB = parameters_billions * bits_per_parameter / 8
model_GB   = weights_GB * 1.3

dedicated fits when model_GB <= 0.85 * GPU_VRAM_GB
shared fits when sum(service budgets) <= 0.85 * GPU_VRAM_GB
```

Use 16 bits for FP16/BF16, 8 for FP8/INT8, and 4 for INT4/NVFP4.
The 30% model overhead covers KV cache and activations; the remaining 15% GPU
reserve covers CUDA graphs, framework allocations, and runtime variance. Do not
tune a discrete-GPU allocation above `0.85`.

| GPU | Memory | 85% budget |
|---|---:|---:|
| H100 / A100 80 GB | 80 GB | 68 GB |
| H200 | 141 GB | 119.85 GB |
| B200 / GB200 | 192 GB | 163.2 GB |
| RTX PRO 6000 Blackwell | 96 GB | 81.6 GB |
| L40S / L40 / RTX 6000 Ada / A40 | 48 GB | 40.8 GB |
| RTX PRO 4500 Blackwell | 32 GB | 27.2 GB |
| DGX Spark / Thor | 128 GB unified | Size from actual free memory; see `edge.md` |

Representative model estimates:

| Model | Precision | Estimated model budget |
|---|---|---:|
| Nemotron 3.5 Lightning 30B-A3B (default) | BF16 | about 78 GB; budget total parameters (30 B), not active parameters (3 B). The NIM's own BF16 profile asks for at least 66 GB per GPU |
| Nemotron 3.5 Lightning 30B-A3B (default) | INT4 | about 45 GB observed; the profile pinned for L40S in `hw-L40S.env` |
| Nemotron Nano 9B v2 FP8 (edge) | FP8 | 11.7 GB |
| Cosmos Reason 1 7B | FP16 | 18.2 GB |
| Cosmos Reason 2 8B | FP16 | 20.8 GB |
| Qwen3-VL 8B | FP16 | 20.8 GB |

The default Cosmos Reason3 Nano checkpoint is now BF16. Older utilization
values were tuned against FP8 variants, so verify its actual startup footprint
and leave additional headroom until the BF16 values are revalidated.

## Effective utilization knobs

| Serving path | Knob |
|---|---|
| LLM NIM | Set both `NIM_KVCACHE_PERCENT` and `NIM_GPU_MEM_FRACTION`; behavior is version-dependent. |
| Current Cosmos Reason3 NIM | `NIM_GPU_MEMORY_UTILIZATION` |
| Legacy Cosmos Reason 1 NIM | `NIM_KVCACHE_PERCENT` |
| RT-VLM | `RTVI_VLLM_GPU_MEMORY_UTILIZATION` |
| Generic vLLM/DLFW | `--gpu-memory-utilization` or its supported env passthrough |
| Default Cosmos-Embed1 RT-Embed | No vLLM utilization knob; size with stream count, workers, and batch size. |
| RT-CV | No model-memory fraction; size with stream count and model family. |

For shared services, the sum of their fractions must stay within the GPU
budget. A common H100 or RTX PRO 6000 starting point for a Nano 9B LLM plus
RT-VLM is `0.40 + 0.40`, leaving 20% unallocated.

## Developer-profile layouts

| Foundation | Starting layout | Important constraints |
|---|---|---|
| Base | One GPU: LLM + integrated RT-VLM shared. Two GPUs: dedicate GPU 0 to LLM and GPU 1 to RT-VLM. | Use `0.40 + 0.40` as the H100/RTX PRO 6000 shared starting point. A 48 GB L40S cannot fit the default FP16/BF16 pair inside its 40.8 GB budget. |
| Alerts `2d_cv` | GPU 0: RT-CV. GPU 1: LLM + RT-VLM; `rtvi-vlm` performs per-clip verification through Alert Bridge. | Set `RESERVED_DEVICE_IDS=0`. Size the shared LLM and RT-VLM against the combined budget. |
| Alerts `2d_vlm` | No RT-CV; default device values co-locate LLM + RT-VLM on GPU 1. Move RT-VLM to the free GPU 0 when possible. | Continuous VLM inference needs more headroom; prefer separate GPUs or a user-approved remote model endpoint. |
| LVS | One GPU: LLM + RT-VLM shared. Two GPUs: LLM on GPU 0 and RT-VLM on GPU 1. | When shared on H100/RTX PRO 6000, set `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.40` and cap the LLM at about `0.40`. |
| Search | GPU 0: RT-CV + RT-VLM FP8 at `0.40`. GPU 1: RT-Embed + LLM. | The stock local profile uses two shared GPUs (`FIXED_SHARED_DEVICE_IDS=0,1`). |

RT-VLM placement and utilization starting values:

| Placement | Example profile and hardware | `RTVI_VLLM_GPU_MEMORY_UTILIZATION` |
|---|---|---:|
| Shared with another GPU service | Search FP8 on H100 or RTX PRO 6000; Alerts/LVS BF16 on H100, RTX PRO 6000, or DGX Spark | 0.40 |
| Dedicated | Alerts/LVS BF16 on H100, RTX PRO 6000, or supported discrete GPUs not listed below | 0.70 |
| Dedicated | Alerts/LVS BF16 on L40S or RTX PRO 4500 | 0.80 |

These values apply when `rtvi-vlm` is in the effective service set, including
stock Alerts `2d_cv` and `2d_vlm`. The BF16 co-resident row is a stock-Foundation
layout (Alerts/LVS share BF16 with the LLM); a generated build that must converge
variants co-resides on FP8, per step 4 of the sizing flow. Do not share the
Alerts LLM and RT-VLM on L40S or RTX PRO 4500. On RTX PRO 4500, use a remote LLM
and start RT-VLM with `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.80` and
`RTVI_VLM_MAX_MODEL_LEN=18000`.

## Search stream sizing

The default Cosmos-Embed1 path uses Triton/ONNX. Reserve about 10 GB for
RT-Embed and give the shared LLM the remaining budget:

```text
LLM fraction = (GPU_VRAM_GB - 10) / GPU_VRAM_GB - 0.15
```

| GPU hosting RT-Embed + LLM | LLM starting fraction |
|---|---:|
| H100 / A100 80 GB | 0.72 |
| H200 | 0.78 |
| RTX PRO 6000 Blackwell | 0.75 |
| L40S | 0.65; verify under load |

Dedicated RT-Embed stream ceilings from its benchmark data:

| GPU | Maximum dedicated streams |
|---|---:|
| H100 80 GB SXM | 140 |
| H100 80 GB PCIe / H100 NVL | 100 |
| RTX PRO 6000 Blackwell | 120 |
| L40S | 60 |
| A40 | 30 |
| Thor / DGX Spark | 30 |

These are dedicated-GPU ceilings, not shared-layout targets. Start Search at
`NUM_STREAMS=16` on H100 or RTX PRO 6000. Use `NUM_STREAMS=8` on L40S, Thor,
or DGX Spark when memory is shared. Reduce `RTVI_EMBED_NUM_VLM_PROCS` from 10
to 4 if RT-Embed crowds out the LLM.

RT-CV memory is driven primarily by `NUM_STREAMS`, `DS_MODEL_FAMILY`, and
`DS_TRACKER_REID`. Start at 16 streams on H100, RTX PRO 6000, or L40S and
reduce the count on smaller or co-located GPUs.

## Edge and unified memory

Read `edge.md` for DGX Spark and AGX/IGX Thor. CPU, GPU, page cache, and
containers share one pool on those systems. Size against actual free memory,
keep the sum of co-resident fractions at or below `0.80`, and preserve at least
20% for the OS and runtime.

Use the platform-specific model and image path from `edge.md`; do not apply
x86 discrete-GPU assumptions. Search is not currently a supported edge layout.

## Validate and tune

Before deployment, verify in `resolved.yml`:

- every GPU service has the intended device ID;
- every shared GPU's utilization fractions stay within its budget;
- `RESERVED_DEVICE_IDS` and `FIXED_SHARED_DEVICE_IDS` match the layout;
- remote services have no unintended local model service;
- RT-VLM does not coexist with an unintended standalone VLM;
- stream counts and worker counts match the budget.

After startup:

1. Confirm model weights load and readiness endpoints pass.
2. Exercise the requested workload while sampling `nvidia-smi`.
3. For startup OOM, reduce the relevant utilization fraction by `0.05`.
4. For inference OOM, also reduce model length or sequence concurrency.
5. For RT-Embed or RT-CV pressure, reduce workers, batch size, or streams.
6. Regenerate and revalidate `resolved.yml` after every adjustment.

Never report a sizing value as validated solely because the container started;
verify it under the requested workload.
